"""檔案類型判斷（原版用 UTType，這裡改用 MIME 與 Gio）。

工具與動作的「接受類型」用字串描述（predicate）：
- "item"        任何檔案或資料夾
- "folder"      資料夾
- "image/*"、"video/*"、"audio/*"、"text/*"   這一大類的 MIME
- "archive"     壓縮檔（ARCHIVE_MIMES）
- "text/plain"  純文字（含程式碼、設定檔、CSV…：Gio 判定屬於 text/plain 的類型）
- "word" "excel" "powerpoint"   Office 文件
- 其他字串      精確的 MIME（或 Gio 判定是它的子類型），例如 "application/pdf"
- ".srt"        以點開頭＝副檔名
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

ARCHIVE_MIMES = frozenset(
    {
        "application/zip",
        "application/x-zip-compressed",
        "application/x-tar",
        "application/x-compressed-tar",
        "application/x-bzip-compressed-tar",
        "application/x-bzip2-compressed-tar",
        "application/x-xz-compressed-tar",
        "application/x-zstd-compressed-tar",
        "application/x-lzma-compressed-tar",
        "application/gzip",
        "application/x-gzip",
        "application/x-bzip",
        "application/x-bzip2",
        "application/x-xz",
        "application/zstd",
        "application/x-7z-compressed",
        "application/vnd.rar",
        "application/x-rar",
        "application/x-rar-compressed",
        "application/x-cpio",
        "application/x-iso9660-image",
        "application/x-xar",
    }
)

WORD_MIMES = frozenset(
    {
        "application/msword",
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        "application/vnd.ms-word.document.macroenabled.12",
        "application/rtf",
        "text/rtf",
        "application/vnd.oasis.opendocument.text",
    }
)
EXCEL_MIMES = frozenset(
    {
        "application/vnd.ms-excel",
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        "application/vnd.ms-excel.sheet.macroenabled.12",
        "application/vnd.oasis.opendocument.spreadsheet",
    }
)
POWERPOINT_MIMES = frozenset(
    {
        "application/vnd.ms-powerpoint",
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        "application/vnd.ms-powerpoint.presentation.macroenabled.12",
        "application/vnd.openxmlformats-officedocument.presentationml.slideshow",
        "application/vnd.oasis.opendocument.presentation",
    }
)

# Gio 不一定把這些歸在 text/plain 底下，但原版視為純文字
_EXTRA_TEXT_MIMES = frozenset(
    {
        "application/json",
        "application/xml",
        "application/x-yaml",
        "application/toml",
        "application/x-shellscript",
        "application/javascript",
        "application/x-desktop",
        "application/x-subrip",
        "application/sql",
        "application/x-php",
        "application/x-perl",
        "application/x-ruby",
    }
)

FOLDER_MIME = "inode/directory"
UNKNOWN_MIME = "application/octet-stream"


@dataclass(frozen=True)
class FileRef:
    """篩選用的最小檔案描述（Nemo 擴充裡也能直接組出來）。"""

    path: str
    mime: str
    is_dir: bool = False

    @property
    def name(self) -> str:
        return os.path.basename(self.path.rstrip("/")) or self.path

    @property
    def ext(self) -> str:
        from .naming import split_name

        return split_name(self.name)[1].lower()


def _gio():
    try:
        from gi.repository import Gio  # noqa: PLC0415

        return Gio
    except (ImportError, ValueError):
        return None


@lru_cache(maxsize=4096)
def _is_a(mime: str, parent: str) -> bool:
    gio = _gio()
    if gio is None:
        return mime == parent
    return bool(gio.content_type_is_a(mime, parent))


def mime_of(path: str | os.PathLike[str]) -> str:
    """檔案的 MIME 類型（依副檔名與內容）；資料夾回傳 inode/directory。"""
    p = Path(path)
    if p.is_dir():
        return FOLDER_MIME
    gio = _gio()
    if gio is None:
        return UNKNOWN_MIME
    try:
        info = gio.File.new_for_path(str(p)).query_info(
            "standard::content-type", gio.FileQueryInfoFlags.NONE, None
        )
        ctype = info.get_content_type() or UNKNOWN_MIME
    except Exception:  # noqa: BLE001 — 讀不到就當成未知類型
        ctype, _uncertain = gio.content_type_guess(p.name, None)
    mime = gio.content_type_get_mime_type(ctype) or ctype
    return mime


def file_ref(path: str | os.PathLike[str]) -> FileRef:
    p = Path(path)
    is_dir = p.is_dir()
    return FileRef(str(p), FOLDER_MIME if is_dir else mime_of(p), is_dir)


def describe(mime: str) -> str:
    """給人看的類型名稱（例如「PDF 文件」）。"""
    gio = _gio()
    if gio is None:
        return mime
    return gio.content_type_get_description(mime) or mime


def is_text_mime(mime: str) -> bool:
    return (
        mime.startswith("text/")
        or mime in _EXTRA_TEXT_MIMES
        or mime.endswith(("+xml", "+json"))
        or _is_a(mime, "text/plain")
    )


def conforms(ref: FileRef, predicate: str) -> bool:
    """這個檔案符合這個類型描述嗎？"""
    if predicate == "item":
        return True
    if predicate == "folder":
        return ref.is_dir
    if ref.is_dir:
        return False
    mime = ref.mime or UNKNOWN_MIME
    if predicate.startswith("."):
        return ref.ext == predicate[1:].lower()
    if predicate.endswith("/*"):
        major = predicate[:-1]
        return mime.startswith(major) or _is_a(mime, predicate)
    if predicate == "archive":
        return mime in ARCHIVE_MIMES
    if predicate == "text/plain":
        return is_text_mime(mime)
    if predicate == "word":
        return mime in WORD_MIMES
    if predicate == "excel":
        return mime in EXCEL_MIMES
    if predicate == "powerpoint":
        return mime in POWERPOINT_MIMES
    return mime == predicate or _is_a(mime, predicate)


def conforms_any(ref: FileRef, predicates: tuple[str, ...]) -> bool:
    """空的類型清單＝接受全部。"""
    if not predicates:
        return True
    return any(conforms(ref, p) for p in predicates)
