"""圖片格式表（對應原版 ITFormat）。

能不能寫出依這台電腦實際情況決定：Pillow 能寫的格式直接用 Pillow；
HEIC 與 AVIF 要有 libheif 的 heif-enc。
"""

from __future__ import annotations

from dataclasses import dataclass

from PIL import Image

from ... import backends


@dataclass(frozen=True)
class ImageFormat:
    id: str
    title: str
    ext: str
    lossy: bool  # 有損格式（有品質滑桿）
    alpha: bool  # 能保存透明背景
    metadata: bool  # 能保存 EXIF
    bitmap: bool  # 一般單張點陣圖（不是圖示或 PDF）
    pil: str | None  # Pillow 的格式名稱；None＝用 heif-enc


def _f(fid: str, title: str, ext: str, lossy: bool, alpha: bool, meta: bool, bitmap: bool,
       pil: str | None) -> ImageFormat:  # fmt: skip
    return ImageFormat(fid, title, ext, lossy, alpha, meta, bitmap, pil)


FORMATS: dict[str, ImageFormat] = {
    f.id: f
    for f in (
        _f("jpeg", "JPG", "jpg", True, False, True, True, "JPEG"),
        _f("png", "PNG", "png", False, True, True, True, "PNG"),
        _f("heic", "HEIC", "heic", True, True, True, True, None),
        _f("avif", "AVIF", "avif", True, True, True, True, None),
        _f("tiff", "TIFF", "tiff", False, True, True, True, "TIFF"),
        _f("gif", "GIF", "gif", False, True, False, True, "GIF"),
        _f("bmp", "BMP", "bmp", False, False, False, True, "BMP"),
        _f("webp", "WebP", "webp", True, True, True, True, "WEBP"),
        _f("ico", "ICO", "ico", False, True, False, False, "ICO"),
        _f("icns", "ICNS", "icns", False, True, False, False, "ICNS"),
        _f("pdf", "PDF", "pdf", False, True, False, False, "PDF"),
    )
}

# 各格式的說明（原版 ITConvertSession.hint）
HINTS: dict[str, str] = {
    "jpeg": "最通用的相片格式，檔案小，不支援透明。",
    "png": "無損、支援透明，適合截圖、圖表與標誌。",
    "heic": "Apple 裝置的高效率格式，同畫質約為 JPG 的一半大小。",
    "avif": "新一代網頁格式，壓縮率很高，較新的瀏覽器才支援。",
    "tiff": "無損（LZW 壓縮），適合印刷與後製。",
    "gif": "最多 256 色；動畫 GIF 會保留動畫。",
    "bmp": "未壓縮的點陣圖，給舊程式使用。",
    "webp": "網頁常用格式。",
    "ico": "Windows／網站圖示，自動產生 16–256 px 多種尺寸。",
    "icns": "macOS 圖示，自動產生 16–1024 px 多種尺寸。",
    "pdf": "每張圖片一頁，依圖片解析度決定頁面大小。",
}

# Pillow 讀到的格式名稱 → 我們的格式
_FROM_PILLOW: dict[str, str] = {
    "JPEG": "jpeg",
    "MPO": "jpeg",
    "PNG": "png",
    "TIFF": "tiff",
    "GIF": "gif",
    "BMP": "bmp",
    "DIB": "bmp",
    "WEBP": "webp",
    "ICO": "ico",
    "ICNS": "icns",
}


def get(fid: str) -> ImageFormat:
    """格式代碼 → 格式；不認得的代碼丟 KeyError。"""
    return FORMATS[fid]


def from_pillow(name: str | None) -> str | None:
    return _FROM_PILLOW.get(name or "")


def is_writable(fid: str) -> bool:
    """這台電腦能不能寫出這個格式。"""
    fmt = FORMATS.get(fid)
    if fmt is None:
        return False
    if fmt.pil is None:
        return backends.available("heif-enc")
    Image.init()
    return fmt.pil in Image.SAVE


def writable_ids() -> tuple[str, ...]:
    return tuple(fid for fid in FORMATS if is_writable(fid))


def title(fid: str) -> str:
    fmt = FORMATS.get(fid)
    return fmt.title if fmt else fid.upper()
