"""寫出圖片（對應原版 ITWriter）。

- 有損品質限制在 0.05–1；TIFF 用 LZW；JPG／BMP 疊在底色上去掉透明。
- 保留相片資訊時方向設回 1、更新 EXIF 的像素寬高；只移除位置時拿掉 GPS；
  全部移除時只寫解析度（不是 72 DPI 時）。
- HEIC／AVIF 先存暫存 PNG（含 EXIF 與色彩描述檔）再交給 heif-enc。
- PDF：每張圖一頁，頁面大小＝像素 × 72 ÷ DPI。
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import Image

from ... import backends, errors, process
from . import colors, formats, icon_io
from .loader import Frame, Source

log = logging.getLogger(__name__)

KEEP = "keep"
REMOVE_LOCATION = "remove-location"
REMOVE_ALL = "remove-all"

_EXIF_IFD = 0x8769
_GPS_IFD = 0x8825
_INTEROP_IFD = 0xA005
_ORIENTATION = 0x0112
_PIXEL_X = 0xA002
_PIXEL_Y = 0xA003


@dataclass(frozen=True)
class Output:
    """寫出設定。"""

    format: str
    quality: float = 0.92  # 有損格式的品質 0–1
    metadata: str = KEEP  # keep／remove-location／remove-all
    background: colors.Rgba = colors.WHITE  # 不支援透明的格式墊在底下的顏色
    keeps_alpha: bool = True  # False：結果一定不透明，寫出時不帶 alpha


def write(
    frames: Sequence[Frame],
    source: Source | None,
    path: Path,
    output: Output,
    cancel: threading.Event | None = None,
) -> None:
    """把畫格寫成 output.format（只有 GIF 會寫多格）。"""
    if not frames:
        raise errors.empty_content("沒有可寫出的影像。")
    fmt = formats.get(output.format)
    if not formats.is_writable(fmt.id):
        raise errors.write_failed(f"這台電腦無法寫入 {fmt.title} 格式。")
    first = frames[0].image
    if fmt.id == "pdf":
        _write_pdf(prepare(first, output), source.dpi if source else 72.0, path)
    elif fmt.id in ("ico", "icns"):
        base = icon_io.square_icon(first, 256 if fmt.id == "ico" else 1024)
        (icon_io.write_ico if fmt.id == "ico" else icon_io.write_icns)(base, path)
    elif fmt.id == "gif" and len(frames) > 1:
        _write_gif(frames, source, path, output)
    else:
        image = prepare(first, output)
        params = encoder_params(image, source, output)
        if fmt.pil is None:
            _write_heif(image, params, path, output, cancel)
        else:
            _save(image, path, fmt, params)


def write_image(image: Image.Image, source: Source | None, path: Path, output: Output) -> None:
    write([Frame(image, 0.0)], source, path, output)


# ---------------------------------------------------------------- 透明度


def flatten(image: Image.Image, color: colors.Rgba) -> Image.Image:
    """疊在背景色上，去掉透明度（背景本身不透明時才用它，否則用白色）。"""
    if image.mode != "RGBA":
        return image if image.mode == "RGB" else image.convert("RGB")
    bg = Image.new("RGB", image.size, (color if color.is_opaque else colors.WHITE).rgb8())
    bg.paste(image, mask=image.getchannel("A"))
    return bg


def clean(image: Image.Image) -> Image.Image:
    """不帶 image.info 的版本（Pillow 會把 info 裡的註解等資訊自動寫進檔案）。

    呼叫端的圖不會被改到：info 有內容時複製一份再清空。
    """
    if not image.info:
        return image
    copy = image.copy()
    copy.info = {}
    return copy


def prepare(image: Image.Image, output: Output) -> Image.Image:
    """依格式處理透明度，並去掉 info（要寫的資訊一律由 encoder_params 明確指定）。"""
    if image.mode == "RGBA" and (not formats.get(output.format).alpha or not output.keeps_alpha):
        return flatten(image, output.background)
    return clean(image)


# ---------------------------------------------------------------- 相片資訊


def exif_bytes(source: Source | None, mode: str, size: tuple[int, int]) -> bytes | None:
    """要寫進輸出的 EXIF；全部移除或沒有資訊時回傳 None。"""
    if mode == REMOVE_ALL or source is None or not source.exif:
        return None
    try:
        exif = Image.Exif()
        exif.load(bytes(source.exif))
        exif[_ORIENTATION] = 1
        sub = exif.get_ifd(_EXIF_IFD)
        if _PIXEL_X in sub or _PIXEL_Y in sub:
            sub[_PIXEL_X], sub[_PIXEL_Y] = size
        if mode == REMOVE_LOCATION and _GPS_IFD in exif:
            del exif[_GPS_IFD]
        return exif.tobytes()
    except (ValueError, TypeError, KeyError, OSError, SyntaxError) as exc:
        log.warning("相片資訊無法沿用，改為不寫入：%s", exc)
        return None


def _tiff_exif(data: bytes) -> bytes | None:
    """TIFF 只能放第 0 層的標籤（Pillow 的 libtiff 寫不了子目錄指標）。"""
    try:
        exif = Image.Exif()
        exif.load(data)
        flat = Image.Exif()
        for tag, value in exif.items():
            if tag not in (_EXIF_IFD, _GPS_IFD, _INTEROP_IFD) and not isinstance(value, dict):
                flat[tag] = value
        return flat.tobytes()
    except (ValueError, TypeError, OSError, SyntaxError) as exc:
        log.warning("TIFF 相片資訊無法寫入：%s", exc)
        return None


def encoder_params(image: Image.Image, source: Source | None, output: Output) -> dict[str, Any]:
    """Pillow save 的參數（品質、壓縮、色彩描述檔、解析度、EXIF）。"""
    fmt = formats.get(output.format)
    params: dict[str, Any] = {}
    if fmt.lossy:
        params["quality"] = max(5, min(100, round(max(0.05, min(1.0, output.quality)) * 100)))
    if fmt.id == "tiff":
        params["compression"] = "tiff_lzw"
    if fmt.id == "jpeg":
        params["optimize"] = True
    if source is None:
        return params
    if source.icc and fmt.id in ("jpeg", "png", "tiff", "webp", "heic", "avif"):
        params["icc_profile"] = source.icc
    if abs(source.dpi - 72) > 0.01 and fmt.id in ("jpeg", "png", "tiff", "heic", "avif"):
        params["dpi"] = (source.dpi, source.dpi)
    if fmt.metadata:
        data = exif_bytes(source, output.metadata, image.size)
        if data and fmt.id == "tiff":
            data = _tiff_exif(data)
        if data:
            params["exif"] = data
    return params


# ---------------------------------------------------------------- 各格式


def _encode_failed(fmt: formats.ImageFormat) -> errors.QSError:
    return errors.write_failed(f"無法完成 {fmt.title} 編碼（圖片尺寸或色彩格式可能不受支援）。")


def _save(image: Image.Image, path: Path, fmt: formats.ImageFormat, params: dict[str, Any]) -> None:
    try:
        image.save(path, fmt.pil, **params)
        return
    except (OSError, ValueError, RuntimeError) as exc:
        log.warning("%s 寫出失敗，改用較少參數重試：%s", fmt.title, exc)
    retry = {k: v for k, v in params.items() if k not in ("optimize", "exif")}
    try:
        image.save(path, fmt.pil, **retry)
    except (OSError, ValueError, RuntimeError) as exc:
        raise _encode_failed(fmt) from exc


def _write_gif(frames: Sequence[Frame], source: Source | None, path: Path, output: Output) -> None:
    images = [prepare(f.image, output) for f in frames]
    durations = [max(10, round(f.delay * 1000)) for f in frames]
    loop = source.loop if source is not None and source.loop is not None else 0
    disposal = 2 if any(im.mode == "RGBA" for im in images) else 1
    try:
        images[0].save(
            path,
            "GIF",
            save_all=True,
            append_images=images[1:],
            duration=durations,
            loop=loop,
            disposal=disposal,
        )
    except (OSError, ValueError) as exc:
        raise _encode_failed(formats.get("gif")) from exc


def _write_pdf(image: Image.Image, dpi: float, path: Path) -> None:
    page = flatten(image, colors.WHITE)
    try:
        page.save(path, "PDF", resolution=float(dpi), creator="QUICK_SEEMD")
    except (OSError, ValueError) as exc:
        raise errors.write_failed("無法建立 PDF。") from exc


def _write_heif(
    image: Image.Image,
    params: dict[str, Any],
    path: Path,
    output: Output,
    cancel: threading.Event | None,
) -> None:
    exe = backends.require("heif-enc")
    tmp = path.with_name(f".{path.name}.src.png")
    png = {k: params[k] for k in ("icc_profile", "exif", "dpi") if k in params}
    image.save(tmp, "PNG", compress_level=1, **png)
    quality = params.get("quality", 92)
    args = [exe, "-q", str(quality)]
    if output.format == "avif":
        args.append("-A")
    args += ["-o", str(path), str(tmp)]
    try:
        process.run_checked(args, "libheif", cancel=cancel)
    finally:
        tmp.unlink(missing_ok=True)
    if not path.is_file() or path.stat().st_size == 0:
        raise _encode_failed(formats.get(output.format))
