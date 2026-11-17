"""PDF → 圖片（每頁一張）、純文字（沒有文字層時自動 OCR）、帶格式文字（對應原版 CVPDFConverter）。

用 poppler 的 pdftocairo 畫頁面、pdftotext 取文字層；頁數、頁面大小與旋轉用 pikepdf 讀。
"""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from pathlib import Path

from .. import backends, errors, naming
from . import ocr
from .formats import Format
from .image import Loaded, normalized_mode, scale_down, write_image
from .pdfmerge import open_pdf
from .profile import Profile

MAX_SIDE = 12000.0  # 避免超大頁面吃光記憶體：最長邊上限
OCR_DPI = 220
PAGE_JOIN = "\n\n\f\n"


@dataclass(frozen=True)
class PageBox:
    width: float  # 已套用旋轉的點數大小
    height: float


def _rotation(node) -> int:  # noqa: ANN001
    """頁面的 /Rotate（可能繼承自上層的 /Pages）。"""
    for _ in range(64):
        if node is None:
            break
        value = node.get("/Rotate")
        if value is not None:
            try:
                return int(value)
            except (TypeError, ValueError):
                return 0
        node = node.get("/Parent")
    return 0


def page_boxes(path: Path) -> list[PageBox]:
    """每頁裁切框大小（套用旋轉）；同時檢查密碼與頁數。"""
    with open_pdf(path) as pdf:
        boxes = []
        for page in pdf.pages:
            x0, y0, x1, y1 = (float(v) for v in page.cropbox)
            w, h = abs(x1 - x0), abs(y1 - y0)
            rotate = _rotation(page.obj) % 180
            boxes.append(PageBox(h, w) if rotate == 90 else PageBox(w, h))
    if not boxes:
        raise errors.empty_content("PDF 沒有任何頁面。")
    return boxes


def render_page(
    path: Path, number: int, box: PageBox, dpi: float, transparent: bool, out_base: Path, ctx
) -> Path:  # noqa: ANN001, E501
    """用 pdftocairo 把一頁畫成 PNG（白底，png／tiff 目標保留透明）；最長邊不超過 12000 px。"""
    scale = min(dpi / 72.0, MAX_SIDE / max(box.width, box.height, 1.0))
    width = max(1, round(box.width * scale))
    height = max(1, round(box.height * scale))
    args = [backends.require("pdftocairo"), "-png", "-singlefile", "-cropbox"]
    args += ["-f", str(number), "-l", str(number)]
    args += ["-scale-to-x", str(width), "-scale-to-y", str(height)]
    if transparent:
        args.append("-transp")
    args += [str(path), str(out_base)]
    ctx.run_process(args, "pdftocairo")
    produced = out_base.with_name(out_base.name + ".png")
    if not produced.exists():
        raise errors.QSError(f"第 {number} 頁繪製失敗。")
    return produced


def _load_png(png: Path, max_pixel: int | None) -> Loaded:
    from PIL import Image  # noqa: PLC0415

    with Image.open(png) as img:
        img.load()
        return Loaded(scale_down(normalized_mode(img), max_pixel))


def folder_label(fmt: Format) -> str:
    """多頁時的資料夾名稱後綴：格式名稱的第一個字（PNG、JPEG、TIFF、HEIC）。"""
    return fmt.display_name.split(" ")[0] or fmt.ext.upper()


def to_images(
    path: Path, profile: Profile, fmt: Format, options: dict, work: Path, ctx
) -> list[Path]:  # noqa: ANN001, ARG001, E501
    boxes = page_boxes(path)
    base = naming.base_name(path)
    dpi = float(options.get("pdf_dpi") or 150)
    transparent = fmt.id in ("png", "tiff")
    scratch = Path(tempfile.mkdtemp(prefix=".pages-", dir=work))
    count = len(boxes)
    if count == 1:
        targets = [work / naming.join_name(base, fmt.ext)]
        result = targets[0]
    else:
        result = work / f"{base}-{folder_label(fmt)}"
        result.mkdir()
        digits = max(2, len(str(count)))
        targets = [
            result / naming.join_name(f"{base}-{n:0{digits}d}", fmt.ext)
            for n in range(1, count + 1)
        ]
    for n, (box, target) in enumerate(zip(boxes, targets, strict=True), start=1):
        ctx.check_cancelled()
        png = render_page(path, n, box, dpi, transparent, scratch / f"p{n}", ctx)
        loaded = _load_png(png, options.get("image_max_pixel"))
        write_image(loaded, target, fmt.id, options.get("image_quality", 0.85), ctx, scratch)
        ctx.progress(n / count)
    return [result]


# ---------------------------------------------------------------- 文字


def text_layer(path: Path, ctx) -> list[str]:  # noqa: ANN001
    """pdftotext 取每頁文字（頁與頁以換頁字元分隔）。"""
    args = [backends.require("pdftotext"), "-enc", "UTF-8", str(path), "-"]
    res = ctx.run_process(args, "pdftotext")
    pages = res.stdout.split("\f")
    if pages and not pages[-1].strip():
        pages = pages[:-1]
    return pages


def ocr_pages(path: Path, boxes: list[PageBox], work: Path, ctx) -> list[str]:  # noqa: ANN001
    scratch = Path(tempfile.mkdtemp(prefix=".ocr-", dir=work))
    pages = []
    for n, box in enumerate(boxes, start=1):
        ctx.check_cancelled()
        png = render_page(path, n, box, OCR_DPI, False, scratch / f"o{n}", ctx)
        pages.append(ocr.recognize_file(png, ctx))
        ctx.progress(n / len(boxes) * 0.95)
    return pages


def page_texts(path: Path, work: Path, ctx) -> list[str]:  # noqa: ANN001
    """每頁文字；整份都沒有文字層（掃描檔）時改用 OCR。"""
    boxes = page_boxes(path)
    pages = text_layer(path, ctx)
    if any(p.strip() for p in pages):
        return pages
    if not backends.available("tesseract"):
        raise errors.empty_content("PDF 中沒有文字，OCR 也辨識不到內容。")
    pages = ocr_pages(path, boxes, work, ctx)
    if not any(p.strip() for p in pages):
        raise errors.empty_content("PDF 中沒有文字，OCR 也辨識不到內容。")
    return pages


def to_text(
    path: Path, profile: Profile, fmt: Format, options: dict, work: Path, ctx
) -> list[Path]:  # noqa: ANN001, ARG001, E501
    out = work / naming.join_name(naming.base_name(path), "txt")
    pages = page_texts(path, work, ctx)
    text = PAGE_JOIN.join(p.strip("\r\n") for p in pages)
    out.write_text(text + "\n", "utf-8")
    ctx.progress(1)
    return [out]
