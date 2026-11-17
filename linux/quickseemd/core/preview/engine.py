"""預覽入口（對應原版 QSPreviewEngine）：決定顯示方式，產生 HTML。

偵測順序與原版相同（head＝檔案開頭 64 KiB）：
Markdown → Jupyter 筆記本 → CSV/TSV → SQLite → ZIP → gzip → xz → tar → 二進位 plist → 文字 → 二進位資訊。
Linux 版補充：資料夾顯示摘要卡；管線、通訊端、裝置等不是一般檔案的東西只顯示資訊，絕不打開。
"""

from __future__ import annotations

import logging
import os
import stat
from functools import lru_cache
from pathlib import Path

from .. import filetypes
from . import Document, archive, archiveview, binary, detect, notebook, pages, sqliteview, tabular
from .page import Chip, FileInfo, Page, build
from .text import DecodedText, decode, looks_binary

log = logging.getLogger(__name__)

HEAD_LIMIT = 64 * 1024
TEXT_LIMIT = pages.TEXT_LIMIT
MARKDOWN_EXTS = frozenset({"md", "markdown", "mdown", "mkd", "mkdn", "mdx", "mdwn"})
CSV_EXTS = frozenset({"csv", "tsv", "tab"})
FONT_MIMES = frozenset(
    {
        "font/ttf",
        "font/otf",
        "font/woff",
        "font/woff2",
        "font/collection",
        "font/sfnt",
        "application/x-font-ttf",
        "application/x-font-otf",
        "application/font-sfnt",
        "application/vnd.ms-opentype",
        "application/font-woff",
    }
)
OFFICE_EXTRA = frozenset(
    {
        "application/vnd.oasis.opendocument.graphics",
        "application/vnd.oasis.opendocument.text-template",
        "application/vnd.oasis.opendocument.spreadsheet-template",
        "application/vnd.oasis.opendocument.presentation-template",
        "application/vnd.oasis.opendocument.graphics-template",
    }
)
OFFICE_MIMES = (
    filetypes.WORD_MIMES | filetypes.EXCEL_MIMES | filetypes.POWERPOINT_MIMES | OFFICE_EXTRA
)
AUDIO_NOT_PLAYABLE = frozenset(
    {"audio/x-mpegurl", "audio/mpegurl", "audio/x-scpls", "audio/x-ms-asx", "audio/x-ms-wax"}
)


# ---------------------------------------------------------------- 分類


@lru_cache(maxsize=1)
def _pixbuf_mimes() -> frozenset[str] | None:
    """GdkPixbuf 讀得了的圖片類型；取不到時回傳 None（就接受全部 image/*）。"""
    try:
        import gi  # noqa: PLC0415

        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf  # noqa: PLC0415

        return frozenset(m for f in GdkPixbuf.Pixbuf.get_formats() for m in f.get_mime_types())
    except (ImportError, ValueError):
        log.debug("無法取得 GdkPixbuf 支援的格式", exc_info=True)
        return None


def _is_image(mime: str) -> bool:
    if not mime.startswith("image/"):
        return False
    supported = _pixbuf_mimes()
    return supported is None or mime in supported


def classify(path: Path) -> str:
    """決定預覽視窗用哪種方式顯示（preview.KINDS 之一）。不是一般檔案時不讀內容。"""
    try:
        st = os.stat(path)
    except OSError:
        return "special"
    if stat.S_ISDIR(st.st_mode):
        return "folder"
    if not stat.S_ISREG(st.st_mode):
        return "special"
    mime = filetypes.mime_of(path)
    if _is_image(mime):
        return "image"
    if mime.startswith("video/"):
        return "video"
    if mime.startswith("audio/") and mime not in AUDIO_NOT_PLAYABLE:
        return "audio"
    if mime == "application/pdf":
        return "pdf"
    if mime in FONT_MIMES:
        return "font"
    if mime in OFFICE_MIMES:
        return "office"
    return "html"


# ---------------------------------------------------------------- 文字頁的判斷


def read_text(info: FileInfo, head: bytes) -> DecodedText | None:
    if looks_binary(head):
        return None
    truncated = info.size > TEXT_LIMIT
    data = head if len(head) >= info.size else info.head(TEXT_LIMIT)
    return decode(data, truncated)


def _is_markdown(info: FileInfo) -> bool:
    if info.ext in MARKDOWN_EXTS:
        return True
    mime = info.mime
    return bool(mime) and mime != "text/plain" and detect.is_a(mime, "text/markdown")


def _is_csv(info: FileInfo) -> bool:
    if info.ext in CSV_EXTS:
        return True
    mime = info.mime or ""
    return detect.is_a(mime, "text/csv") or detect.is_a(mime, "text/tab-separated-values")


def _text_page(info: FileInfo, head: bytes) -> Page | None:
    """依序檢查 Markdown、筆記本、CSV/TSV。"""
    if _is_markdown(info):
        decoded = read_text(info, head)
        if decoded is not None:
            return pages.markdown_page(info, decoded)
    if info.ext == "ipynb":
        decoded = read_text(info, head)
        page = notebook.render(info, decoded.text) if decoded is not None else None
        if page is not None:
            return page
    if _is_csv(info):
        decoded = read_text(info, head)
        if decoded is not None:
            page = tabular.render(decoded.text, info, decoded.truncated)
            page.chips.append(Chip(decoded.encoding))
            return page
    return None


def _container_page(info: FileInfo, head: bytes) -> Page | None:
    """依序檢查 SQLite、ZIP、gzip、xz、tar。"""
    if sqliteview.is_sqlite(head):
        page = sqliteview.render(info)
        if page is not None:
            return page
    if head.startswith((b"PK\x03\x04", b"PK\x05\x06")):
        listing = archive.read_zip(info.path, info.size)
        if listing is not None:
            extra = (
                [Chip(f"{info.ext.upper()} 使用 ZIP 格式")] if info.ext not in ("zip", "") else []
            )
            return archiveview.render(listing, info, extra)
    if head.startswith(b"\x1f\x8b"):
        page = pages.gzip_page(info)
        if page is not None:
            return page
    if head.startswith(b"\xfd7zXZ\x00"):
        page = pages.xz_page(info)
        if page is not None:
            return page
    if archive.looks_like_tar(head) or info.ext == "tar":
        listing = archive.read_tar(info.path)
        if listing is not None:
            return archiveview.render(listing, info)
    return None


def make_page(info: FileInfo) -> Page:
    head = info.head(HEAD_LIMIT)
    page = _text_page(info, head) or _container_page(info, head)
    if page is not None:
        return page
    if head.startswith(b"bplist00"):
        page = pages.binary_plist_page(info)
        if page is not None:
            return page
    decoded = read_text(info, head)
    if decoded is not None:
        return pages.code_page(info, decoded)
    return binary.render(info, head)


# ---------------------------------------------------------------- 對外介面


def render(path: Path) -> Document:
    """預覽網頁（資料夾是摘要卡，不是一般檔案的只顯示資訊）。"""
    from . import special  # noqa: PLC0415

    p = Path(path)
    try:
        st = os.stat(p)
    except OSError:
        return special.special_document(p)
    if stat.S_ISDIR(st.st_mode):
        return special.folder_document(p)
    if not stat.S_ISREG(st.st_mode):
        return special.special_document(p)
    info = FileInfo.of(p)
    try:
        page = make_page(info)
    except (MemoryError, KeyboardInterrupt):
        raise
    except Exception:  # noqa: BLE001 — 解析器的意外錯誤不能讓預覽整個失敗，改顯示資訊頁
        log.exception("預覽內容失敗，改用二進位資訊頁：%s", p)
        page = binary.render(info, info.head(HEAD_LIMIT))
    return build(page, info)


def render_font(path: Path) -> Document:
    """字型樣張。"""
    from . import font  # noqa: PLC0415

    return font.render(Path(path))
