"""圖片轉換（對應原版 CVImageConverter）：讀寫、ICO／ICNS 多尺寸、SVG 點陣化、圖片轉 PDF。

- 讀取：套用 EXIF 方向、只縮小不放大；SVG 用 GdkPixbuf（librsvg）；HEIC／AVIF 用 heif-convert。
- 寫入：Pillow；HEIC／AVIF 先存 PNG 再交給 heif-enc。JPEG 疊在白底上。
- GIF 與 TIFF 輸出保留多格（GIF 迴圈播放、沿用每格延遲）。
"""

from __future__ import annotations

import io
import logging
import tempfile
from dataclasses import dataclass, field
from pathlib import Path

from .. import backends, errors, naming
from . import capabilities
from .formats import LOSSY_IMAGE_IDS, Format
from .profile import HEIF_EXTS, SVG_EXTS, Profile

log = logging.getLogger(__name__)

ICO_SIDES = (16, 32, 48, 64, 128, 256)
ICNS_SIDES = (16, 32, 64, 128, 256, 512, 1024)
DEFAULT_DELAY_MS = 100
_EXIF_ORIENTATION = 0x0112


@dataclass
class Loaded:
    image: object  # PIL.Image.Image
    dpi: float = 72.0
    duration_ms: int | None = None
    icc: bytes | None = None
    exif: bytes | None = None
    source_jpeg: bytes | None = field(default=None, repr=False)  # 可原封不動放進 PDF 的 JPEG


def _pil():
    from PIL import Image, ImageOps  # noqa: PLC0415

    return Image, ImageOps


def _target_side(long_side: int, max_pixel: int | None) -> int:
    """只縮小、不放大。"""
    return min(max_pixel or long_side, long_side) if long_side > 0 else (max_pixel or 4096)


def normalized_mode(img):  # noqa: ANN001, ANN202
    has_alpha = img.mode in ("RGBA", "LA", "PA", "RGBa", "La") or "transparency" in img.info
    if img.mode in ("RGB", "RGBA"):
        return img
    return img.convert("RGBA" if has_alpha else "RGB")


def scale_down(img, max_pixel: int | None):  # noqa: ANN001, ANN202
    Image, _ = _pil()
    side = _target_side(max(img.size), max_pixel)
    if max(img.size) <= side:
        return img
    scale = side / max(img.size)
    size = (max(1, round(img.width * scale)), max(1, round(img.height * scale)))
    return img.resize(size, Image.Resampling.LANCZOS)


def _exif_bytes(img) -> bytes | None:  # noqa: ANN001
    """EXIF 的副本（方向改成 1，因為像素已經轉正）；不改動原圖物件。"""
    Image, _ = _pil()
    try:
        source = img.getexif()
        if not source:
            return None
        copy = Image.Exif()
        copy.load(source.tobytes())
        copy[_EXIF_ORIENTATION] = 1
        return copy.tobytes()
    except Exception:  # noqa: BLE001 — 壞掉的 EXIF 就不保留
        return None


def _clean_dpi(value: object) -> float:
    """PNG 的解析度以「每公尺像素」儲存，換回 DPI 會變成 150.0124：接近整數時取整數。"""
    try:
        dpi = float(value or 72)
    except (TypeError, ValueError):
        return 72.0
    nearest = round(dpi)
    return float(nearest) if abs(dpi - nearest) < 0.05 else dpi


def _raster_svg(path: Path, max_pixel: int | None) -> Loaded:
    try:
        import gi  # noqa: PLC0415

        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf, GLib  # noqa: PLC0415
    except (ImportError, ValueError) as exc:
        raise errors.unreadable("無法開啟這個向量圖。") from exc
    Image, _ = _pil()
    try:
        _fmt, w, h = GdkPixbuf.Pixbuf.get_file_info(str(path))
        if w <= 0 or h <= 0:
            w, h = 512, 512
        target = max_pixel or int(max(max(w, h) * 2, 1024))
        scale = target / max(w, h)
        pw, ph = max(1, round(w * scale)), max(1, round(h * scale))
        pix = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(path), pw, ph, False)
        ok, data = pix.save_to_bufferv("png", [], [])
    except (GLib.Error, TypeError) as exc:
        raise errors.unreadable("無法開啟這個向量圖。") from exc
    img = Image.open(io.BytesIO(data))
    img.load()
    return Loaded(normalized_mode(img))


def _decode_heif(path: Path, scratch: Path, ctx) -> Path:  # noqa: ANN001
    out = Path(tempfile.mkdtemp(prefix=".heif-", dir=scratch)) / "decoded.png"
    tool = backends.require("heif-convert")
    ctx.run_process([tool, "--quiet", str(path), str(out)], "heif-convert")
    if not out.exists():
        produced = sorted(out.parent.glob("decoded*.png"))
        if not produced:
            raise errors.unreadable("無法解碼這張圖片。")
        return produced[0]
    return out


def load_image(
    path: Path, scratch: Path, ctx, max_pixel: int | None, index: int = 0
) -> Loaded:  # noqa: ANN001, E501
    """開一張圖（第 index 格），轉正方向並依上限縮小。"""
    ext = path.suffix[1:].lower()
    if ext in SVG_EXTS:
        return _raster_svg(path, max_pixel)
    source = _decode_heif(path, scratch, ctx) if ext in HEIF_EXTS else path
    Image, ImageOps = _pil()
    try:
        with Image.open(source) as img:
            if index:
                img.seek(index)
            fmt, mode = img.format, img.mode
            duration = img.info.get("duration")
            dpi = _clean_dpi((img.info.get("dpi") or (72, 72))[0])
            icc = img.info.get("icc_profile")
            orientation = img.getexif().get(_EXIF_ORIENTATION, 1)
            exif = _exif_bytes(img)
            rotated = orientation not in (0, 1)
            oriented = ImageOps.exif_transpose(img) if rotated else img.copy()
    except Exception as exc:  # noqa: BLE001 — Pillow 會丟各種例外
        raise errors.unreadable(f"無法解碼這張圖片（{exc}）。") from exc
    scaled = scale_down(normalized_mode(oriented), max_pixel)
    # 沒有轉向、沒有縮小的 JPEG 可以原封不動放進 PDF（不重新壓縮）
    passthrough = fmt == "JPEG" and index == 0 and not rotated and mode in ("RGB", "L")
    passthrough = passthrough and scaled.size == oriented.size
    jpeg = source.read_bytes() if passthrough else None
    return Loaded(scaled, float(dpi), duration, icc, exif, jpeg)


# ---------------------------------------------------------------- 寫入


def flatten(img, background=(255, 255, 255)):  # noqa: ANN001, ANN201
    """把有透明度的圖片疊在白底上（JPEG 不支援透明）。"""
    Image, _ = _pil()
    if img.mode != "RGBA":
        return img.convert("RGB")
    base = Image.new("RGB", img.size, background)
    base.paste(img, mask=img.getchannel("A"))
    return base


def _quality(q: float) -> int:
    return int(round(max(0.05, min(1.0, float(q))) * 100))


def _save_kwargs(fid: str, loaded: Loaded, quality: float) -> dict:
    kw: dict = {}
    if loaded.icc and fid in ("jpeg", "png", "webp", "tiff", "jp2"):
        kw["icc_profile"] = loaded.icc
    if loaded.exif and fid in ("jpeg", "png", "webp", "tiff"):
        kw["exif"] = loaded.exif
    if fid in ("jpeg", "webp"):
        kw["quality"] = _quality(quality)
    if fid == "jp2":
        q = max(0.05, min(1.0, float(quality)))
        if q < 1.0:
            kw.update(quality_mode="rates", quality_layers=[max(1.0, (1.0 - q) * 100)])
        else:
            kw["irreversible"] = False
    if fid == "tiff":
        kw["compression"] = "tiff_lzw"
    return kw


def _heif_encode(
    img, out: Path, fid: str, quality: float, ctx, scratch: Path
) -> None:  # noqa: ANN001
    """先存成 PNG（在暫存資料夾裡），再交給 heif-enc 編成 HEIC／AVIF。"""
    with tempfile.TemporaryDirectory(prefix=".heif-", dir=scratch) as tmp:
        png = Path(tmp) / "frame.png"
        img.save(png, "PNG")
        tool = backends.require("heif-enc")
        args = [tool, "-q", str(_quality(quality)), "-o", str(out), str(png)]
        if fid == "avif":
            args.insert(1, "-A")
        ctx.run_process(args, "heif-enc")


def write_image(
    loaded: Loaded,
    out: Path,
    fid: str,
    quality: float,
    ctx,
    scratch: Path | None = None,  # noqa: ANN001
) -> None:
    """寫一張圖；scratch 是放中間檔的資料夾（預設是輸出檔所在的暫存資料夾）。"""
    img = loaded.image
    try:
        if fid in capabilities.HEIF_IDS:
            _heif_encode(img, out, fid, quality, ctx, scratch or out.parent)
            return
        if fid == "jpeg":
            img = flatten(img)
        fmt = capabilities.PILLOW_SAVE.get(fid)
        if fmt is None:
            raise errors.write_failed(f"這台電腦無法寫入 {fid.upper()} 格式。")
        with open(out, "x+b") as fh:
            img.save(fh, fmt, **_save_kwargs(fid, loaded, quality))
    except errors.QSError:
        raise
    except Exception as exc:  # noqa: BLE001 — Pillow 編碼失敗時回報中文訊息
        raise errors.write_failed(
            f"無法完成 {fid.upper()} 編碼（圖片尺寸或色彩格式可能不受支援）：{exc}"
        ) from exc


def square_canvas(img, side: int):  # noqa: ANN001, ANN201
    """把圖片等比例縮放後置中放進透明正方形（ICO／ICNS 需要正方形）。"""
    Image, _ = _pil()
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    scale = side / max(img.size)
    size = (max(1, round(img.width * scale)), max(1, round(img.height * scale)))
    fitted = img.convert("RGBA").resize(size, Image.Resampling.LANCZOS)
    canvas.paste(fitted, ((side - size[0]) // 2, (side - size[1]) // 2), fitted)
    return canvas


def write_icon(loaded: Loaded, out: Path, fid: str) -> None:
    sides = ICO_SIDES if fid == "ico" else ICNS_SIDES
    images = [square_canvas(loaded.image, s) for s in sides]
    largest = images[-1]
    try:
        with open(out, "x+b") as fh:
            if fid == "ico":
                largest.save(fh, "ICO", sizes=[(s, s) for s in sides], append_images=images[:-1])
            else:
                largest.save(fh, "ICNS", append_images=images[:-1])
    except Exception as exc:  # noqa: BLE001
        raise errors.write_failed(f"圖示編碼失敗：{exc}") from exc


def _write_frames(frames: list[Loaded], out: Path, fid: str, quality: float) -> None:
    first = frames[0]
    rest = [f.image for f in frames[1:]]
    kw = _save_kwargs(fid, first, quality)
    if fid == "gif":
        kw = {
            "loop": 0,
            "duration": [f.duration_ms or DEFAULT_DELAY_MS for f in frames],
            "disposal": 2,
        }
    try:
        with open(out, "x+b") as fh:
            first.image.save(
                fh, capabilities.PILLOW_SAVE[fid], save_all=True, append_images=rest, **kw
            )
    except Exception as exc:  # noqa: BLE001
        raise errors.write_failed(f"無法完成 {fid.upper()} 編碼：{exc}") from exc


def convert_image(
    path: Path, profile: Profile, fmt: Format, options: dict, work: Path, ctx
) -> list[Path]:  # noqa: ANN001, E501
    """圖片 → 圖片。"""
    out = work / naming.join_name(naming.base_name(path), fmt.ext)
    quality = options.get("image_quality", 0.85)
    max_pixel = options.get("image_max_pixel")
    if fmt.id in ("ico", "icns"):
        write_icon(load_image(path, work, ctx, 1024), out, fmt.id)
        ctx.progress(1)
        return [out]
    multi = fmt.id in ("gif", "tiff") and (profile.is_animated or profile.frame_count > 1)
    count = profile.frame_count if multi else 1
    frames: list[Loaded] = []
    for i in range(max(count, 1)):
        ctx.check_cancelled()
        frames.append(load_image(path, work, ctx, max_pixel, i))
        if count > 1:
            ctx.progress((i + 1) / count * 0.9)
    if len(frames) > 1:
        _write_frames(frames, out, fmt.id, quality)
    else:
        write_image(frames[0], out, fmt.id, quality, ctx)
    ctx.progress(1)
    return [out]


def is_lossy(fid: str) -> bool:
    return fid in LOSSY_IMAGE_IDS


# ---------------------------------------------------------------- 圖片 → PDF


def image_to_pdf(
    path: Path, profile: Profile, fmt: Format, options: dict, work: Path, ctx
) -> list[Path]:  # noqa: ANN001, ARG001, E501
    out = work / naming.join_name(naming.base_name(path), "pdf")
    write_pdf([path], out, options, ctx)
    return [out]


def write_pdf(inputs: list[Path], out: Path, options: dict, ctx) -> None:  # noqa: ANN001
    """把圖片與 PDF 依序合成一份 PDF；每張圖一頁，頁面大小依圖片 DPI（預設 72）。"""
    from .pdfmerge import merge  # noqa: PLC0415 — 用到 pikepdf 與 cairo 才載入

    merge(inputs, out, options, ctx)
