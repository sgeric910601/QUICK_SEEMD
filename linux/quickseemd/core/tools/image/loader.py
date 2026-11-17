"""讀取圖片（對應原版 ITLoader）。

- 一般格式用 Pillow；HEIC／AVIF 用 libheif 的 heif-convert 先解成暫存 PNG；
  SVG 與其他 Pillow 讀不懂的格式交給 GdkPixbuf（librsvg）點陣化。
- 像素依 EXIF 方向轉正（90° 倍數的無損搬移）。
- 色彩描述檔：RGB 類的保留；灰階與 CMYK 轉成 sRGB（不再帶描述檔）。
- Pillow 沒有 16 位元 RGB；16 位元灰階會縮成 8 位元再轉 RGB。
- GIF 只有要求時才讀所有畫格（輸出也是 GIF 的時候）。
"""

from __future__ import annotations

import io
import logging
import tempfile
from dataclasses import dataclass, replace
from pathlib import Path

import numpy as np
from PIL import Image, ImageCms, ImageSequence, UnidentifiedImageError

from ... import backends, errors, process
from . import formats

log = logging.getLogger(__name__)

# 與原版畫布上限（32768 × 32768）相同，超過才當成惡意的「解壓縮炸彈」
Image.MAX_IMAGE_PIXELS = 32768 * 32768

_HEIC_BRANDS = frozenset({b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1"})
_AVIF_BRANDS = frozenset({b"avif", b"avis"})
_RGB_MODES = frozenset({"RGB", "RGBA", "P", "PA", "RGBX", "RGBa"})
_DEEP_MODES = frozenset({"I;16", "I;16B", "I;16L", "I;16N", "I"})
_ROTATED_ORIENTATIONS = frozenset({5, 6, 7, 8})
# EXIF 方向 → 轉正需要的搬移（與 Pillow ImageOps.exif_transpose 相同）
_TRANSPOSE: dict[int, Image.Transpose] = {
    2: Image.Transpose.FLIP_LEFT_RIGHT,
    3: Image.Transpose.ROTATE_180,
    4: Image.Transpose.FLIP_TOP_BOTTOM,
    5: Image.Transpose.TRANSPOSE,
    6: Image.Transpose.ROTATE_270,
    7: Image.Transpose.TRANSVERSE,
    8: Image.Transpose.ROTATE_90,
}
_READ_ERRORS = (UnidentifiedImageError, OSError, SyntaxError, ValueError, Image.DecompressionBombError)


@dataclass(frozen=True)
class Frame:
    """一格畫面（動畫 GIF 會有多格）；delay 單位為秒。"""

    image: Image.Image
    delay: float = 0.0


@dataclass(frozen=True)
class Source:
    """讀進來的圖片：像素已轉正，保留色彩描述檔與相片資訊（原始位元組）。"""

    path: Path
    frames: tuple[Frame, ...]
    format: str | None
    has_alpha: bool
    orientation: int = 1
    loop: int | None = None
    file_size: int = 0
    dpi: float = 72.0
    icc: bytes | None = None
    exif: bytes | None = None

    @property
    def image(self) -> Image.Image:
        return self.frames[0].image

    @property
    def width(self) -> int:
        return self.image.width

    @property
    def height(self) -> int:
        return self.image.height

    @property
    def size(self) -> tuple[int, int]:
        return self.image.size

    @property
    def animated(self) -> bool:
        return len(self.frames) > 1

    def preferred_format(self) -> str:
        """「維持原格式」時實際要寫出的格式（無法寫回的格式改用 PNG 或 JPG）。"""
        fmt = formats.FORMATS.get(self.format or "")
        if fmt is not None and fmt.bitmap and formats.is_writable(fmt.id):
            return fmt.id
        return "png" if self.has_alpha else "jpeg"

    def output_extension(self, fid: str) -> str:
        """同格式時沿用原檔副檔名的寫法（.jpeg 不會變成 .jpg）。"""
        ext = self.path.suffix.lstrip(".")
        if fid == self.format and ext and formats.get(fid).bitmap:
            return ext.lower()
        return formats.get(fid).ext

    def with_frames(self, frames: tuple[Frame, ...]) -> Source:
        return replace(self, frames=frames)


# ---------------------------------------------------------------- 對外


def load(path: str | Path, all_frames: bool = False) -> Source:
    """讀取並轉正；讀不到時丟出中文錯誤。"""
    p = Path(path)
    if not p.is_file():
        raise errors.file_not_found(p.name)
    size = p.stat().st_size
    brand = heif_kind(p)
    if brand is not None:
        return _load_heif(p, brand, size)
    try:
        with Image.open(p) as im:
            return _from_pillow(p, im, all_frames, size)
    except Image.DecompressionBombError as exc:
        raise errors.unreadable(f"「{p.name}」太大，無法處理。") from exc
    except _READ_ERRORS as exc:
        log.debug("Pillow 讀不到 %s：%s", p, exc)
    src = _load_pixbuf(p, size)
    if src is None:
        raise errors.unreadable(f"「{p.name}」不是可辨識的圖片。")
    return src


def pixel_size(path: str | Path) -> tuple[int, int] | None:
    """轉正後的像素尺寸（只讀檔頭，給選項面板用）。"""
    p = Path(path)
    if not p.is_file():
        return None
    if heif_kind(p) is None:
        try:
            with Image.open(p) as im:
                w, h = im.size
                orientation = _orientation(im) if im.format in ("JPEG", "MPO", "TIFF", "WEBP") else 1
                return (h, w) if orientation in _ROTATED_ORIENTATIONS else (w, h)
        except _READ_ERRORS:
            pass
    return _pixbuf_size(p)


def quick_has_alpha(path: str | Path) -> bool:
    """只讀檔頭判斷有沒有透明度（給選項面板用）。"""
    try:
        with Image.open(path) as im:
            return im.mode in ("RGBA", "LA", "PA", "RGBa") or "transparency" in im.info
    except _READ_ERRORS:
        return False


def heif_kind(path: Path) -> str | None:
    """HEIF 家族的檔案回傳 "heic" 或 "avif"（看 ftyp 的品牌）。"""
    try:
        with open(path, "rb") as fh:
            head = fh.read(64)
    except OSError:
        return None
    if len(head) < 16 or head[4:8] != b"ftyp":
        return None
    box = int.from_bytes(head[0:4], "big")
    brands = {head[8:12]}
    brands.update(head[i : i + 4] for i in range(16, min(box, len(head)) - 3, 4))
    if brands & _AVIF_BRANDS:
        return "avif"
    if brands & _HEIC_BRANDS:
        return "heic"
    return None


# ---------------------------------------------------------------- Pillow


def _orientation(im: Image.Image) -> int:
    try:
        value = int(im.getexif().get(0x0112, 1) or 1)
    except (TypeError, ValueError, OSError, SyntaxError):
        return 1
    return value if 1 <= value <= 8 else 1


def _from_pillow(p: Path, im: Image.Image, all_frames: bool, size: int) -> Source:
    fmt = formats.from_pillow(im.format)
    orientation = _orientation(im)
    raw_frames = _raw_frames(im, all_frames and fmt == "gif")
    info = im.info
    icc = info.get("icc_profile") or None
    exif = info.get("exif") if fmt != "tiff" else None
    dpi = _dpi(info.get("dpi"))
    frames: list[Frame] = []
    keep_icc = icc
    for raw, delay in raw_frames:
        image, keep_icc = normalize(raw, icc)
        if orientation in _TRANSPOSE:
            image = image.transpose(_TRANSPOSE[orientation])
        # 畫格不帶任何原檔資訊（註解、XMP、IPTC、PNG 文字…）；要寫出的資訊只從 Source 明確取用
        image.info = {}
        frames.append(Frame(image, delay))
    loop = info.get("loop") if fmt == "gif" else None
    return Source(
        path=p,
        frames=tuple(frames),
        format=fmt,
        has_alpha=frames[0].image.mode == "RGBA",
        orientation=orientation,
        loop=loop if isinstance(loop, int) else None,
        file_size=size,
        dpi=dpi,
        icc=keep_icc,
        exif=exif if isinstance(exif, (bytes, bytearray)) else None,
    )


def _raw_frames(im: Image.Image, every: bool) -> list[tuple[Image.Image, float]]:
    if every and getattr(im, "n_frames", 1) > 1:
        out = []
        for frame in ImageSequence.Iterator(im):
            frame.load()
            out.append((frame.copy(), _delay(frame.info.get("duration"))))
        return out
    im.load()
    return [(im.copy(), _delay(im.info.get("duration")))]


def _delay(ms: object) -> float:
    try:
        value = float(ms) / 1000  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.1
    return value if value > 0 else 0.1


def _dpi(value: object) -> float:
    try:
        x = float(value[0])  # type: ignore[index]
    except (TypeError, ValueError, IndexError):
        return 72.0
    if x <= 1:
        return 72.0
    # PNG 以「每公尺像素」儲存，150 DPI 讀回來是 149.99；接近整數時取整數
    return float(round(x)) if abs(x - round(x)) < 0.1 else round(x, 3)


def normalize(im: Image.Image, icc: bytes | None) -> tuple[Image.Image, bytes | None]:
    """轉成 RGB 或 RGBA；回傳（圖, 還能用的色彩描述檔）。"""
    mode = im.mode
    if mode in ("RGB", "RGBA"):
        return im, icc
    rgb_icc = icc if mode in _RGB_MODES else None
    if mode == "CMYK":
        return _cmyk_to_rgb(im, icc), None
    if mode in _DEEP_MODES:
        return _deep_to_rgb(im), None
    if mode == "F":
        arr = np.clip(np.asarray(im), 0, 255).astype(np.uint8)
        return Image.fromarray(arr).convert("RGB"), None
    alpha = mode in ("LA", "PA", "La", "RGBa") or "transparency" in im.info
    return im.convert("RGBA" if alpha else "RGB"), rgb_icc


def _cmyk_to_rgb(im: Image.Image, icc: bytes | None) -> Image.Image:
    if icc:
        try:
            src = ImageCms.ImageCmsProfile(io.BytesIO(icc))
            dst = ImageCms.createProfile("sRGB")
            out = ImageCms.profileToProfile(im, src, dst, outputMode="RGB")
            if out is not None:
                return out
        except (ImageCms.PyCMSError, OSError, ValueError) as exc:
            log.debug("CMYK 色彩描述檔轉換失敗，改用簡單轉換：%s", exc)
    return im.convert("RGB")


def _deep_to_rgb(im: Image.Image) -> Image.Image:
    arr = np.asarray(im)
    if arr.dtype != np.uint8:
        arr = np.clip(arr.astype(np.int64) >> 8, 0, 255) if arr.max() > 255 else arr
        arr = arr.astype(np.uint8)
    return Image.fromarray(arr, "L").convert("RGB")


# ---------------------------------------------------------------- HEIF


def _load_heif(p: Path, kind: str, size: int) -> Source:
    exe = backends.find("heif-convert")
    if exe is None:
        src = _load_pixbuf(p, size)
        if src is None:
            raise errors.tool_missing(backends.BACKENDS["heif-convert"].title)
        return replace(src, format=kind)
    with tempfile.TemporaryDirectory(prefix="qs-heif-") as tmp:
        out = Path(tmp) / "decoded.png"
        process.run_checked([exe, "--quiet", str(p.resolve()), str(out)], "libheif")
        if not out.is_file():
            pngs = sorted(Path(tmp).glob("*.png"))
            if not pngs:
                raise errors.unreadable(f"無法解碼「{p.name}」。")
            out = pngs[0]
        with Image.open(out) as im:
            src = _from_pillow(p, im, False, size)
    return replace(src, format=kind)


# ---------------------------------------------------------------- GdkPixbuf（SVG 等）


def _gdk_pixbuf():  # noqa: ANN202 — gi 型別只在執行時才有
    try:
        import gi  # noqa: PLC0415

        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf, GLib  # noqa: PLC0415
    except (ImportError, ValueError):
        return None, None
    return GdkPixbuf, GLib


def _load_pixbuf(p: Path, size: int) -> Source | None:
    gdk, glib = _gdk_pixbuf()
    if gdk is None:
        return None
    try:
        pb = gdk.Pixbuf.new_from_file(str(p))
    except glib.Error as exc:
        log.debug("GdkPixbuf 讀不到 %s：%s", p, exc)
        return None
    pb = pb.apply_embedded_orientation() or pb
    mode = "RGBA" if pb.get_has_alpha() else "RGB"
    data = pb.read_pixel_bytes().get_data()
    image = Image.frombuffer(
        mode, (pb.get_width(), pb.get_height()), data, "raw", mode, pb.get_rowstride(), 1
    ).copy()
    is_svg = p.suffix.lower() in (".svg", ".svgz")
    if is_svg and mode == "RGB":
        image = image.convert("RGBA")
    image.info = {}
    return Source(
        path=p,
        frames=(Frame(image, 0.0),),
        format=None,
        has_alpha=is_svg or mode == "RGBA",
        file_size=size,
    )


def _pixbuf_size(p: Path) -> tuple[int, int] | None:
    gdk, _glib = _gdk_pixbuf()
    if gdk is None:
        return None
    info = gdk.Pixbuf.get_file_info(str(p))
    if not info or info[0] is None:
        return None
    return int(info[1]), int(info[2])
