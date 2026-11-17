"""外部工具偵測（對應原版 CVExternalBackends）。

只在固定的系統資料夾找（預設 /usr/local/bin:/usr/bin:/bin），不吃使用者的 PATH，
避免抓到 conda 之類環境裡的同名程式。測試用 QUICKSEEMD_TOOL_PATH 換掉搜尋路徑。
"""

from __future__ import annotations

import importlib.util
import json
import os
import shutil
import threading
from dataclasses import dataclass
from pathlib import Path

from . import errors

DEFAULT_TOOL_PATH = "/usr/local/bin:/usr/bin:/bin"


@dataclass(frozen=True)
class Backend:
    key: str
    binaries: tuple[str, ...]
    title: str
    summary: str
    apt: str  # 安裝提示用的套件名稱


BACKENDS: dict[str, Backend] = {
    b.key: b
    for b in (
        Backend("ffmpeg", ("ffmpeg",), "FFmpeg", "影片與音訊轉檔", "ffmpeg"),
        Backend("ffprobe", ("ffprobe",), "FFprobe", "讀取影音資訊", "ffmpeg"),
        Backend(
            "soffice",
            ("soffice", "libreoffice"),
            "LibreOffice",
            "Office 文件與試算表、簡報",
            "libreoffice-writer-nogui",
        ),
        Backend("pandoc", ("pandoc",), "Pandoc", "Markdown、EPUB、LaTeX", "pandoc"),
        Backend("magick", ("magick", "convert"), "ImageMagick", "少見的圖片格式", "imagemagick"),
        Backend("tesseract", ("tesseract",), "Tesseract", "文字辨識（OCR）", "tesseract-ocr"),
        Backend("exiftool", ("exiftool",), "ExifTool", "移除相片資訊", "libimage-exiftool-perl"),
        Backend("pngquant", ("pngquant",), "pngquant", "PNG 減色壓縮", "pngquant"),
        Backend("jpegtran", ("jpegtran",), "jpegtran", "JPG 無損旋轉", "libjpeg-turbo-progs"),
        Backend("heif-enc", ("heif-enc",), "libheif 編碼器", "輸出 HEIC／AVIF", "libheif-examples"),
        Backend(
            "heif-convert",
            ("heif-dec", "heif-convert"),
            "libheif 解碼器",
            "讀取 HEIC／AVIF",
            "libheif-examples",
        ),
        Backend("qpdf", ("qpdf",), "qpdf", "PDF 結構處理", "qpdf"),
        Backend("pdftocairo", ("pdftocairo",), "Poppler", "PDF 轉圖片", "poppler-utils"),
        Backend("pdftotext", ("pdftotext",), "Poppler", "PDF 取文字", "poppler-utils"),
        Backend("pdfinfo", ("pdfinfo",), "Poppler", "PDF 資訊", "poppler-utils"),
        Backend("gs", ("gs",), "Ghostscript", "PDF 壓縮備援", "ghostscript"),
        Backend("7z", ("7z", "7za"), "7-Zip", "7z 壓縮檔", "p7zip-full"),
        Backend("bsdtar", ("bsdtar",), "bsdtar", "RAR／ISO 等壓縮檔", "libarchive-tools"),
        Backend("weasyprint", ("weasyprint",), "WeasyPrint", "網頁轉 PDF", "weasyprint"),
        Backend("fc-match", ("fc-match",), "Fontconfig", "尋找字型檔", "fontconfig"),
    )
}

_cache: dict[str, str | None] = {}
_lock = threading.Lock()


def search_path() -> list[str]:
    raw = os.environ.get("QUICKSEEMD_TOOL_PATH") or DEFAULT_TOOL_PATH
    return [p for p in raw.split(os.pathsep) if p]


def find(key: str) -> str | None:
    """外部工具的絕對路徑；沒裝回傳 None。結果會快取，refresh() 清掉。"""
    with _lock:
        if key in _cache:
            return _cache[key]
    backend = BACKENDS.get(key)
    names = backend.binaries if backend else (key,)
    found: str | None = None
    path = os.pathsep.join(search_path())
    for name in names:
        hit = shutil.which(name, path=path)
        if hit:
            found = hit
            break
    with _lock:
        _cache[key] = found
    return found


def require(key: str) -> str:
    """一定要有的外部工具；沒有就丟「找不到 X，這個格式需要先安裝它。」"""
    hit = find(key)
    if hit is None:
        title = BACKENDS[key].title if key in BACKENDS else key
        raise errors.tool_missing(title)
    return hit


def available(key: str) -> bool:
    return find(key) is not None


def refresh() -> None:
    with _lock:
        _cache.clear()


def has_module(name: str) -> bool:
    try:
        return importlib.util.find_spec(name) is not None
    except (ImportError, ValueError):
        return False


def install_hint(key: str) -> str:
    b = BACKENDS.get(key)
    return f"sudo apt install {b.apt}" if b else ""


# ---------------------------------------------------------------- 資料與模型


def data_dir() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "share"
    )
    return Path(base) / "quickseemd"


def cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return Path(base) / "quickseemd"


def models_dir() -> Path:
    return data_dir() / "models"


def _models_manifest() -> dict:
    here = Path(__file__).resolve().parent.parent.parent / "data" / "models.json"
    alt = Path(__file__).resolve().parent.parent / "data" / "models.json"
    for p in (here, alt):
        if p.is_file():
            return json.loads(p.read_text("utf-8"))
    return {}


def yunet_model() -> Path | None:
    name = _models_manifest().get("yunet", {}).get("file", "face_detection_yunet_2022mar.onnx")
    p = models_dir() / name
    return p if p.is_file() else None


def rembg_python() -> Path | None:
    p = data_dir() / "venv-rembg" / "bin" / "python"
    return p if p.is_file() else None


def rembg_model_ready() -> bool:
    rel = (
        _models_manifest()
        .get("rembg", {})
        .get("model_path", "models/isnet-general-use/isnet-general-use.onnx")
    )
    return rembg_python() is not None and (models_dir() / rel).is_file()
