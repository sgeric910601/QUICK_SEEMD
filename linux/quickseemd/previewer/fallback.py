"""預覽核心不可用時的備援：依 MIME 分類，以及各種資訊卡網頁。

資訊卡用在：特殊檔案（管線、裝置）、找不到的檔案、遠端位置、錯誤訊息、
以及預覽核心還沒提供（NotImplementedError）時的基本檔案資訊。
所有放進網頁的文字一律 HTML 跳脫；網頁沒有任何程式碼。
"""

from __future__ import annotations

import html
import os
import stat
from collections.abc import Sequence
from pathlib import Path

from ..core import errors, filetypes
from ..core.preview import Document
from .formatting import format_size, format_time

INFO_SIZE = (640, 420)
INFO_KIND = "檔案資訊"
FONT_MIMES = frozenset(
    {
        "application/x-font-ttf",
        "application/x-font-otf",
        "application/vnd.ms-opentype",
        "application/x-font-type1",
        "application/x-font-pcf",
    }
)
OFFICE_MIMES = filetypes.WORD_MIMES | filetypes.EXCEL_MIMES | filetypes.POWERPOINT_MIMES
_MAJOR_KINDS = (("image/", "image"), ("video/", "video"), ("audio/", "audio"), ("font/", "font"))

STYLE = """
:root { color-scheme: light dark; }
body { margin: 0; padding: 28px; font: 14px/1.6 "Noto Sans CJK TC", "Noto Sans", sans-serif;
       color: #1d1d1f; background: transparent; }
.card { max-width: 560px; margin: 0 auto; padding: 22px 26px; border-radius: 12px;
        background: rgba(127, 127, 127, 0.08); border: 1px solid rgba(127, 127, 127, 0.22); }
h1 { font-size: 18px; margin: 0 0 10px; word-break: break-all; }
.note { margin: 0 0 14px; color: #b25000; }
table { border-collapse: collapse; width: 100%; }
th { text-align: left; white-space: nowrap; padding: 4px 16px 4px 0; color: #6e6e73;
     font-weight: normal; vertical-align: top; }
td { padding: 4px 0; word-break: break-all; }
@media (prefers-color-scheme: dark) {
  body { color: #f2f2f7; } th { color: #a1a1a6; } .note { color: #ff9f0a; }
}
"""


def classify(path: Path) -> str:
    """不靠預覽核心的分類（與 core.preview.KINDS 相同的值）。"""
    try:
        st = os.stat(path)
    except OSError:
        return "html"
    if stat.S_ISDIR(st.st_mode):
        return "folder"
    if not stat.S_ISREG(st.st_mode):
        return "special"
    mime = filetypes.mime_of(path)
    if mime == "application/pdf":
        return "pdf"
    if mime in OFFICE_MIMES:
        return "office"
    if mime in FONT_MIMES:
        return "font"
    return next((kind for prefix, kind in _MAJOR_KINDS if mime.startswith(prefix)), "html")


def info_page(
    title: str, rows: Sequence[tuple[str, str]], note: str | None = None, kind: str = INFO_KIND
) -> Document:
    esc = html.escape
    parts = [f"<h1>{esc(title)}</h1>"]
    if note:
        parts.append(f'<p class="note">{esc(note)}</p>')
    if rows:
        cells = "".join(f"<tr><th>{esc(k)}</th><td>{esc(v)}</td></tr>" for k, v in rows)
        parts.append(f"<table>{cells}</table>")
    page = (
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        f"<style>{STYLE}</style></head>"
        f'<body><main class="card">{"".join(parts)}</main></body></html>'
    )
    return Document(html=page, kind_label=kind, preferred_size=INFO_SIZE)


def _location_row(path: Path | None) -> list[tuple[str, str]]:
    return [("位置", str(path.parent))] if path is not None else []


def file_page(path: Path) -> Document:
    """預覽核心不可用時：只顯示檔案的基本資訊。"""
    p = Path(path)
    rows: list[tuple[str, str]] = []
    try:
        st = os.stat(p)
        is_dir = stat.S_ISDIR(st.st_mode)
        mime = filetypes.FOLDER_MIME if is_dir else filetypes.mime_of(p)
        rows.append(("類型", filetypes.describe(mime)))
        if not is_dir:
            rows.append(("大小", format_size(st.st_size)))
        rows.append(("修改時間", format_time(st.st_mtime)))
    except OSError as exc:
        rows.append(("狀態", exc.strerror or str(exc)))
    rows += _location_row(p)
    return info_page(p.name or str(p), rows, note="這個檔案目前只能顯示基本資訊。")


def special_page(name: str, detail: str, path: Path | None) -> Document:
    rows = [("類型", detail or "特殊檔案")] + _location_row(path)
    return info_page(name, rows, note="這不是一般檔案，無法預覽內容。")


def missing_page(name: str, path: Path | None) -> Document:
    return info_page(name, _location_row(path), note=str(errors.file_not_found(name)))


def remote_page(name: str, uri: str) -> Document:
    return info_page(name, [("位置", uri)], note="無法預覽這個位置。")


def invalid_page(name: str) -> Document:
    return info_page(name or "預覽", [], note="無法預覽這個項目。")


def error_page(name: str, message: str) -> Document:
    return info_page(name, [], note=f"無法預覽這個檔案：{message}")
