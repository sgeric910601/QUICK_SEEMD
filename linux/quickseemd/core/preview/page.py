"""預覽頁面的組成（對應原版 QSDocument.swift：QSPage、QSChip、QSNotice、QSFile、QSHTML）。

頁面骨架與原版逐字相同：
<!DOCTYPE html><html><head><meta charset="utf-8"><meta name="generator" content="QUICK_SEEMD">
<style>…</style></head><body class="…"><header class="qs-bar">[圖示]<span class="kind">…</span>
{標籤}<span class="spacer"></span><span class="chip">{大小}</span></header>{提示}{內容}</body></html>
"""

from __future__ import annotations

import logging
import os
import stat
from dataclasses import dataclass, field
from datetime import datetime
from functools import cached_property
from pathlib import Path

from . import Attachment, Document, fmt, icons, mimeinfo, style

log = logging.getLogger(__name__)

HEADER_ICON_SIZE = 36
VIRTUAL_DIR = Path("/.quickseemd-virtual")
DEFAULT_SIZE = (900, 680)

_ESCAPES = str.maketrans({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"})


def escape(s: str) -> str:
    """HTML 跳脫 & < > "。"""
    return s.translate(_ESCAPES)


@dataclass(frozen=True)
class Chip:
    """工具列上的狀態小標籤；tone 是 ""、"ok"、"warn"、"err"。"""

    text: str
    tone: str = ""

    @property
    def html(self) -> str:
        return f'<span class="chip {self.tone}">{escape(self.text)}</span>'


@dataclass(frozen=True)
class Notice:
    """工具列下方的提示列。"""

    text: str
    warn: bool = False

    @property
    def html(self) -> str:
        cls = "notice warn" if self.warn else "notice"
        return f'<div class="{cls}">{escape(self.text)}</div>'


def from_timestamp(ts: float | None) -> datetime | None:
    """POSIX 時間 → 本地時間；超出範圍時回傳 None。"""
    if ts is None:
        return None
    try:
        return datetime.fromtimestamp(ts)
    except (OverflowError, OSError, ValueError):
        return None


def _gio_created(path: Path) -> datetime | None:
    try:
        from gi.repository import Gio  # noqa: PLC0415

        info = Gio.File.new_for_path(str(path)).query_info(
            "time::created", Gio.FileQueryInfoFlags.NONE, None
        )
    except Exception:  # noqa: BLE001 — 檔案系統不支援建立時間
        return None
    if not info.has_attribute("time::created"):
        return None
    return from_timestamp(info.get_attribute_uint64("time::created"))


@dataclass(frozen=True)
class FileInfo:
    """被預覽檔案的基本資訊。is_virtual=True 表示只有檔名（例如 gzip 裡的檔案）。"""

    path: Path
    name: str
    ext: str
    size: int
    mode: int | None
    modified: datetime | None
    regular: bool
    is_virtual: bool = False

    @classmethod
    def of(cls, path: Path) -> FileInfo:
        p = Path(path)
        try:
            st = os.stat(p)
        except OSError:
            st = None
        regular = st is not None and stat.S_ISREG(st.st_mode)
        return cls(
            path=p,
            name=p.name,
            ext=_ext(p.name),
            size=st.st_size if st is not None and regular else 0,
            mode=st.st_mode if st is not None else None,
            modified=from_timestamp(st.st_mtime) if st is not None else None,
            regular=regular,
        )

    @classmethod
    def virtual(cls, name: str) -> FileInfo:
        """只有檔名、不存在於磁碟的檔案（語言偵測用）。"""
        return cls(VIRTUAL_DIR / name, name, _ext(name), 0, None, None, False, True)

    @cached_property
    def mime(self) -> str | None:
        if self.is_virtual or not self.regular:
            return None
        from ..filetypes import mime_of  # noqa: PLC0415

        return mime_of(self.path)

    @cached_property
    def created(self) -> datetime | None:
        return None if self.is_virtual else _gio_created(self.path)

    @property
    def kind_description(self) -> str:
        return mimeinfo.describe(self.mime, self.ext)

    def head(self, limit: int) -> bytes:
        """讀檔案開頭最多 limit 位元組；不是一般檔案或讀不到時回傳空的。"""
        if self.is_virtual or not self.regular:
            return b""
        try:
            with open(self.path, "rb") as fh:
                return fh.read(limit)
        except OSError:
            log.debug("讀不到檔案：%s", self.path, exc_info=True)
            return b""


def _ext(name: str) -> str:
    return os.path.splitext(name)[1][1:].lower()


@dataclass
class Page:
    """組頁面用的可變草稿；最後交給 build()。"""

    kind: str
    chips: list[Chip] = field(default_factory=list)
    notices: list[Notice] = field(default_factory=list)
    body: str = ""
    body_class: str = ""
    show_icon: bool = True
    size: tuple[int, int] = DEFAULT_SIZE
    attachments: dict[str, Attachment] = field(default_factory=dict)
    extra_css: str = ""  # Linux 版補充（字型樣張的 @font-face）
    size_label: str | None = None  # 取代工具列右邊的檔案大小（資料夾用總大小）


def header_icon(info: FileInfo) -> Attachment:
    icon = icons.for_mime(info.mime or _fallback_mime(info), HEADER_ICON_SIZE)
    return Attachment("file-icon", icon.data, icon.mime)


def _fallback_mime(info: FileInfo) -> str | None:
    if info.mode is not None and stat.S_ISDIR(info.mode):
        return icons.FOLDER_MIME
    return None


def build(p: Page, info: FileInfo) -> Document:
    """組出完整 HTML 與附件。"""
    atts = dict(p.attachments)
    parts = [
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        '<meta name="generator" content="QUICK_SEEMD"><style>',
        style.css(),
        p.extra_css,
        f'</style></head><body class="{escape(p.body_class)}">',
        '<header class="qs-bar">',
    ]
    if p.show_icon:
        atts["file-icon"] = header_icon(info)
        parts.append('<img class="ico" src="cid:file-icon" alt="">')
    parts.append(f'<span class="kind">{escape(p.kind)}</span>')
    parts.extend(chip.html for chip in p.chips)
    size = p.size_label if p.size_label is not None else fmt.bytes_label(info.size)
    parts.append(f'<span class="spacer"></span><span class="chip">{escape(size)}</span>')
    parts.append("</header>")
    parts.extend(n.html for n in p.notices)
    parts.append(p.body)
    parts.append("</body></html>")
    return Document("".join(parts), p.kind, p.size, atts)
