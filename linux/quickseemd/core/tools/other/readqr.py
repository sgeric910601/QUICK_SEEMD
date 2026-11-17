"""讀取 QR Code（對應原版 OTQRJob）。

- 圖片取第一格；PDF 取前 20 頁，以 2 倍大小畫在白底上（pdftocairo），長邊最多 4000 像素；
  MediaBox 大於 PDF 規格上限（14400 點＝200 吋）或幾乎為 0 的頁面視為異常而略過。
  每頁畫完就掃描，記憶體裡同時只有一頁。
- 解碼用 zbar（pyzbar），設成「不轉換」模式拿原始位元組，再自己判斷文字編碼
  （zbar 自己猜編碼時常把 UTF-8 中文誤當成 Shift-JIS）；zbar 找不到時再用 OpenCV 的 QR 偵測。
- 每個檔案內去除重複；多個檔案時文字檔裡每段前面加「# 檔名」。
- 工作程序不能持有剪貼簿：用 ctx.set_clipboard() 交出要拷貝的文字（結果的 clipboard），
  由介面程序在完成後放進剪貼簿；clipboard_payload() 保留給只拿到文字檔的舊呼叫端。
"""

from __future__ import annotations

import logging
import math
import os
import re
from collections.abc import Iterator
from ctypes import c_void_p, cast
from pathlib import Path

from PIL import Image, ImageOps

from ... import backends, errors, filetypes, process
from ...model import OTHER, TINTS, ToolContext, ToolDefinition
from ...options import Values
from . import textcodec

log = logging.getLogger(__name__)

NOT_FOUND = "在圖片中找不到 QR Code 或條碼。"
PDF_PAGE_LIMIT = 20
PDF_SCALE = 2  # 原版以 2 倍大小繪製
MAX_RENDER_PX = 4000  # 繪製後的長邊上限（約 64 MB 的 RGBA 畫面）
MAX_PAGE_POINTS = 14400.0  # PDF 規格的頁面尺寸上限
MIN_PAGE_POINTS = 1.0
PDFINFO_TIMEOUT = 30  # 秒：讀頁面資訊；超過就當成讀不了
RENDER_TIMEOUT = 60  # 秒：畫一頁；超過就略過這一頁
SMALL_IMAGE = 400  # 長邊小於這個像素時放大再掃一次
ZBAR_CFG_BINARY = 4  # zbar 0.23 起的 ZBAR_CFG_BINARY：不把資料轉成文字
_HEIF_EXTS = frozenset({"heic", "heif", "avif"})


# ---------------------------------------------------------------- 文字


def decode_payload(data: bytes) -> str:
    """條碼內容 → 文字：UTF-8，否則猜舊編碼（Big5、GB18030、Shift-JIS…），最後 Latin-1。"""
    try:
        return data.decode("utf-8")
    except UnicodeDecodeError:
        pass
    codec = textcodec.detect_legacy_encoding(data)
    if codec is not None:
        return data.decode(codec)
    return data.decode("latin-1")


def report_text(found: list[tuple[str, list[str]]], multiple: bool) -> str:
    text = ""
    for name, codes in found:
        if not codes:
            continue
        if multiple:
            text += f"# {name}\n"
        text += "\n".join(codes) + "\n\n"
    return text.strip() + "\n"


def clipboard_payload(report: str) -> str:
    """相容用：從結果文字檔取出要放進剪貼簿的內容（去掉「# 檔名」標題與空行）。

    新的呼叫端請改用執行結果的 clipboard（內容與原版拷貝的完全相同）。
    """
    lines = [ln for ln in report.splitlines() if ln and not ln.startswith("# ")]
    return "\n".join(lines)


# ---------------------------------------------------------------- 掃描


def _zbar_binary(gray: Image.Image) -> list[bytes]:
    from pyzbar import pyzbar as pz  # noqa: PLC0415
    from pyzbar.wrapper import ZBarSymbol  # noqa: PLC0415

    pixels, width, height = pz._pixel_data(gray)  # noqa: SLF001 — 需要設定 zbar 的二進位模式
    with pz._image_scanner() as scanner:  # noqa: SLF001
        pz.zbar_image_scanner_set_config(scanner, ZBarSymbol.QRCODE, ZBAR_CFG_BINARY, 1)
        with pz._image() as img:  # noqa: SLF001
            pz.zbar_image_set_format(img, pz._FOURCC["L800"])  # noqa: SLF001
            pz.zbar_image_set_size(img, width, height)
            pz.zbar_image_set_data(img, cast(pixels, c_void_p), len(pixels), None)
            if pz.zbar_scan_image(scanner, img) < 0:
                return []
            symbols = pz._decode_symbols(pz._symbols_for_image(img))  # noqa: SLF001
            return [s.data for s in symbols]


def _zbar_scan(gray: Image.Image) -> list[bytes]:
    try:
        return _zbar_binary(gray)
    except ImportError:
        log.warning("沒有 pyzbar，只能用 OpenCV 讀 QR Code")
        return []
    except AttributeError:
        from pyzbar import pyzbar as pz  # noqa: PLC0415 — 內部介面改了就用公開的 decode

        return [d.data for d in pz.decode(gray)]


def _opencv_scan(gray: Image.Image) -> list[bytes]:
    try:
        import cv2  # noqa: PLC0415
        import numpy as np  # noqa: PLC0415

        ok, texts, _points, _codes = cv2.QRCodeDetector().detectAndDecodeMulti(np.asarray(gray))
    except Exception as exc:  # noqa: BLE001 — OpenCV 失敗只是少一個後備方法
        log.info("OpenCV 讀不了 QR Code：%s", exc)
        return []
    return [t.encode("utf-8") for t in texts if t] if ok else []


def _variants(gray: Image.Image) -> list[Image.Image]:
    """原圖，以及加白邊（小圖再放大）的版本：裁得太緊或太小的 QR Code 也讀得到。"""
    w, h = gray.size
    padded = ImageOps.expand(gray, border=max(w, h) // 10 + 8, fill=255)
    if max(w, h) < SMALL_IMAGE:
        factor = math.ceil(SMALL_IMAGE / max(w, h, 1))
        padded = padded.resize((padded.width * factor, padded.height * factor), Image.NEAREST)
    return [gray, padded]


def scan_image(gray: Image.Image) -> list[str]:
    """一張灰階圖裡的所有條碼內容（去除重複、保持順序）。"""
    for variant in _variants(gray):
        raw = _zbar_scan(variant) or _opencv_scan(variant)
        if raw:
            return list(dict.fromkeys(decode_payload(r) for r in raw))
    return []


# ---------------------------------------------------------------- 讀圖


def _flatten(img: Image.Image) -> Image.Image:
    """透明背景鋪白後轉成灰階。"""
    if img.mode in ("RGBA", "LA", "PA") or (img.mode == "P" and "transparency" in img.info):
        rgba = img.convert("RGBA")
        canvas = Image.new("RGBA", rgba.size, "white")
        canvas.alpha_composite(rgba)
        return canvas.convert("L")
    return img.convert("L")


def _open_image(path: Path) -> Image.Image:
    with Image.open(path) as img:
        img.seek(0)
        img.load()
        return _flatten(img)


def _heif_to_png(path: Path, ctx: ToolContext) -> Path | None:
    tool = backends.find("heif-convert")
    if tool is None:
        return None
    out = ctx.make_work_dir(ctx.output_directory(path)) / "frame.png"
    res = process.run([tool, os.path.abspath(path), str(out)], cancel=ctx.cancel_event)
    if res.returncode != 0:
        return None
    return out if out.exists() else next(iter(sorted(out.parent.glob("*.png"))), None)


def _load_image(path: Path, ctx: ToolContext) -> Image.Image:
    try:
        return _open_image(path)
    except (OSError, ValueError, Image.DecompressionBombError) as exc:
        log.info("Pillow 讀不了 %s：%s", path.name, exc)
    if path.suffix.lower().lstrip(".") in _HEIF_EXTS:
        png = _heif_to_png(path, ctx)
        if png is not None:
            return _open_image(png)
    raise errors.unreadable(path.name)


Box = tuple[float, float, float, float]
_PAGES_RE = re.compile(r"^Pages:\s+(\d+)", re.M)
_BOX_RE = re.compile(r"^Page\s+(\d+)\s+(MediaBox|CropBox):\s+(\S+)\s+(\S+)\s+(\S+)\s+(\S+)", re.M)


def _box_size(box: Box) -> tuple[float, float]:
    return abs(box[2] - box[0]), abs(box[3] - box[1])


def page_is_sane(box: Box) -> bool:
    """頁面大小合理：每邊至少 1 點、最多 14400 點。"""
    w, h = _box_size(box)
    return min(w, h) >= MIN_PAGE_POINTS and max(w, h) <= MAX_PAGE_POINTS


def render_long_side(width_pts: float, height_pts: float) -> int:
    """繪製後的長邊像素：原版的 2 倍，但不超過 MAX_RENDER_PX。"""
    return min(MAX_RENDER_PX, math.ceil(max(width_pts, height_pts) * PDF_SCALE))


def _pdf_boxes(path: Path, ctx: ToolContext) -> dict[int, dict[str, Box]]:
    """前 20 頁的 MediaBox／CropBox（pdfinfo -box）。"""
    tool = backends.require("pdfinfo")
    args = [tool, "-f", "1", "-l", str(PDF_PAGE_LIMIT), "-box", os.path.abspath(path)]
    try:
        res = process.run(args, cancel=ctx.cancel_event, timeout=PDFINFO_TIMEOUT)
    except errors.Cancelled:
        raise
    except errors.QSError as exc:  # 逾時
        log.info("pdfinfo 逾時：%s", exc)
        raise errors.unreadable(path.name) from exc
    if res.returncode != 0:
        log.info("pdfinfo 失敗：%s", process.last_lines(res.stderr))
        raise errors.unreadable(path.name)
    found = _PAGES_RE.search(res.stdout)
    count = min(int(found.group(1)) if found else 0, PDF_PAGE_LIMIT)
    boxes: dict[int, dict[str, Box]] = {n: {} for n in range(1, count + 1)}
    for m in _BOX_RE.finditer(res.stdout):
        try:
            box = (float(m.group(3)), float(m.group(4)), float(m.group(5)), float(m.group(6)))
        except ValueError:
            continue
        boxes.get(int(m.group(1)), {})[m.group(2)] = box
    return boxes


def _render_page(
    tool: str, path: Path, page: int, long_side: int, work: Path, ctx: ToolContext
) -> Path | None:
    out = work / f"page-{page}"
    args = [tool, "-png", "-singlefile", "-scale-to", str(long_side)]
    args += ["-f", str(page), "-l", str(page), os.path.abspath(path), str(out)]
    try:
        res = process.run(args, cancel=ctx.cancel_event, timeout=RENDER_TIMEOUT)
    except errors.Cancelled:
        raise
    except errors.QSError as exc:  # 逾時：略過這一頁
        log.info("第 %d 頁畫太久，略過：%s", page, exc)
        return None
    png = work / f"page-{page}.png"
    if res.returncode != 0 or not png.exists():
        log.info("第 %d 頁畫不出來：%s", page, process.last_lines(res.stderr))
        return None
    return png


def _pdf_pages(path: Path, ctx: ToolContext) -> Iterator[Path]:
    """逐頁畫成 PNG（白底、2 倍、長邊最多 4000 像素），尺寸異常的頁面略過。"""
    boxes = _pdf_boxes(path, ctx)
    tool = backends.require("pdftocairo")
    work = ctx.make_work_dir(ctx.output_directory(path))
    for page, page_boxes in boxes.items():
        ctx.check_cancelled()
        media = page_boxes.get("MediaBox")
        crop = page_boxes.get("CropBox", media)
        if media is None or crop is None or not (page_is_sane(media) and page_is_sane(crop)):
            log.info("略過尺寸異常的第 %d 頁：%s", page, media)
            continue
        png = _render_page(tool, path, page, render_long_side(*_box_size(crop)), work, ctx)
        if png is not None:
            yield png


def _is_pdf(path: Path) -> bool:
    if path.suffix.lower() == ".pdf":
        return True
    return filetypes.mime_of(path) == "application/pdf"


def _images(path: Path, ctx: ToolContext) -> Iterator[Image.Image]:
    if _is_pdf(path):
        for page in _pdf_pages(path, ctx):
            yield _open_image(page)
    else:
        yield _load_image(path, ctx)


def scan_file(path: Path, ctx: ToolContext) -> list[str]:
    """一個檔案裡所有條碼的內容（去除重複、保持順序）；PDF 每頁畫完就掃。"""
    codes: list[str] = []
    for img in _images(path, ctx):
        ctx.check_cancelled()
        codes.extend(c for c in scan_image(img) if c not in codes)
    return codes


# ---------------------------------------------------------------- 工具


def run(files: list[Path], values: Values, ctx: ToolContext) -> list[Path]:  # noqa: ARG001
    found: list[tuple[str, list[str]]] = []
    for i, f in enumerate(files):
        ctx.check_cancelled()
        sub = ctx.segment(i, len(files))
        sub.progress(0.1, f"掃描：{f.name}")
        found.append((f.name, scan_file(f, sub)))
    payloads = [code for _, codes in found for code in codes]
    if not payloads:
        raise errors.empty_content(NOT_FOUND)
    # 同原版：所有內容以換行連接放進剪貼簿（不含「# 檔名」）；由介面程序在完成後拷貝
    ctx.set_clipboard("\n".join(payloads))
    tmp = ctx.make_work_dir(ctx.output_directory(files[0])) / "qr.txt"
    with open(tmp, "xb") as out:
        out.write(report_text(found, len(files) > 1).encode("utf-8"))
    ctx.progress(1, f"找到 {len(payloads)} 個，已複製到剪貼簿")
    return [ctx.deliver(tmp, ctx.output_name(files[0], "QR內容", "txt"), files[0])]


TOOL = ToolDefinition(
    id="other.read-qr",
    category=OTHER,
    title="讀取 QR Code",
    subtitle="從圖片或螢幕截圖找出 QR Code 與條碼，內容存成文字並複製到剪貼簿。",
    icon="qrcode.viewfinder",
    run=run,
    input_types=("image/*", "application/pdf"),
    input_label="圖片",
    action_title="讀取",
    keywords=("qr", "barcode", "條碼", "掃描"),
    tint=TINTS["image"],
    badge="QR",
    presets={"other.read-qr": {}},
)
