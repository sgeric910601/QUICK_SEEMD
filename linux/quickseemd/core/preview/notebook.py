"""Jupyter Notebook（對應原版 QSNotebook）：Markdown 儲存格、程式碼、輸出（文字、圖片、HTML、錯誤）。

與原版的差異：
- 各 Markdown 儲存格的圖片編號接續下去（原版每格都從 md-img-1 開始，第二張會顯示成第一張）。
- text/html 與 SVG 輸出經過 sanitize 過濾。
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import re

from . import Attachment, detect, highlight, markdown
from .languages import BY_ID
from .page import Chip, FileInfo, Page, escape
from .sanitize import sanitize_html

log = logging.getLogger(__name__)

_ANSI = re.compile(r"\x1B\[[0-9;]*[A-Za-z]")
IMAGE_TYPES = (("image/png", "image/png"), ("image/jpeg", "image/jpeg"))


def _joined(v: object) -> str | None:
    """字串，或全是字串的清單（接起來）；其他型別回傳 None。"""
    if isinstance(v, str):
        return v
    if isinstance(v, list) and all(isinstance(x, str) for x in v):
        return "".join(v)
    return None


def _text(v: object) -> str:
    return _joined(v) or ""


def _strip_ansi(s: str) -> str:
    return _ANSI.sub("", s)


def _count(v: object) -> int | None:
    if isinstance(v, bool):
        return None
    if isinstance(v, int):
        return v
    if isinstance(v, float) and v.is_integer():
        return int(v)
    return None


class _NotebookRenderer:
    def __init__(self, info: FileInfo) -> None:
        self.base_dir = None if info.is_virtual else info.path.parent
        self.attachments: dict[str, Attachment] = {}
        self.images = 0
        self.md_images = 0
        self.parts: list[str] = ['<div class="nb">']
        self.code_cells = 0

    def markdown_cell(self, src: str) -> None:
        r = markdown.render(src, self.base_dir, self.md_images)
        self.md_images = r.images
        for k, v in r.attachments.items():
            self.attachments.setdefault(k, v)
        self.parts.append(
            f'<div class="cell"><div class="prompt"></div><div class="md">{r.html}</div></div>'
        )

    def code_cell(self, cell: dict, lang_id: str) -> None:
        self.code_cells += 1
        n = _count(cell.get("execution_count"))
        label = str(n) if n is not None else " "
        self.parts.append(
            f'<div class="cell"><div class="prompt in">In [{label}]:</div><div class="cellsrc">'
        )
        self.parts.append(
            highlight.code_view(_text(cell.get("source")), BY_ID[lang_id], False, False)[0]
        )
        self.parts.append("</div></div>")
        outputs = cell.get("outputs")
        for out in outputs if isinstance(outputs, list) else []:
            if not isinstance(out, dict):
                continue
            body = self.output(out)
            if not body:
                continue
            count = _count(out.get("execution_count"))
            prompt = f"Out [{count}]:" if count is not None else ""
            self.parts.append(
                f'<div class="cell"><div class="prompt">{prompt}</div><div class="out">{body}</div></div>'
            )

    def output(self, out: dict) -> str:
        kind = out.get("output_type")
        if kind == "stream":
            cls = ' class="err"' if out.get("name") == "stderr" else ""
            return f"<pre{cls}>{escape(_strip_ansi(_text(out.get('text'))))}</pre>"
        if kind == "error":
            tb = out.get("traceback")
            lines = (
                [_strip_ansi(x) for x in tb if isinstance(x, str)] if isinstance(tb, list) else []
            )
            text = "\n".join(lines) or f"{out.get('ename', '')}: {out.get('evalue', '')}"
            return f'<pre class="err">{escape(text)}</pre>'
        data = out.get("data")
        return self.rich(data if isinstance(data, dict) else {})

    def rich(self, data: dict) -> str:
        for key, mime in IMAGE_TYPES:
            if key not in data:
                continue
            try:
                img = base64.b64decode(_text(data[key]), validate=False)
            except (binascii.Error, ValueError):
                continue
            self.images += 1
            name = f"nb-img-{self.images}"
            self.attachments[name] = Attachment(name, img, mime)
            return f'<img src="cid:{name}" alt="">'
        for key in ("image/svg+xml", "text/html"):
            if key in data:
                return sanitize_html(_text(data.get(key)))
        if "text/markdown" in data:
            return f'<div class="md">{markdown.render(_text(data.get("text/markdown")), None).html}</div>'
        if "text/plain" in data:
            return f"<pre>{escape(_strip_ansi(_text(data.get('text/plain'))))}</pre>"
        return ""


def _language(root: dict) -> str:
    meta = root.get("metadata")
    meta = meta if isinstance(meta, dict) else {}
    info = meta.get("language_info")
    spec = meta.get("kernelspec")
    name = info.get("name") if isinstance(info, dict) else None
    if not isinstance(name, str):
        name = spec.get("language") if isinstance(spec, dict) else None
    if not isinstance(name, str):
        name = "python"
    lang = detect.for_hint(name)
    return lang.id if lang is not None else "python"


def _load(text: str) -> tuple[dict, list[dict]] | None:
    try:
        root = json.loads(text)
    except (ValueError, RecursionError):
        return None
    if not isinstance(root, dict):
        return None
    cells = root.get("cells")
    if not isinstance(cells, list) or not all(isinstance(c, dict) for c in cells):
        return None
    return root, cells


def render(info: FileInfo, text: str) -> Page | None:
    """筆記本頁面；不是筆記本（沒有 cells 陣列）時回傳 None。"""
    loaded = _load(text)
    if loaded is None:
        return None
    root, cells = loaded
    lang_id = _language(root)
    r = _NotebookRenderer(info)
    for cell in cells:
        src = _text(cell.get("source"))
        kind = cell.get("cell_type")
        if kind == "markdown":
            r.markdown_cell(src)
        elif kind == "code":
            r.code_cell(cell, lang_id)
        else:
            r.parts.append(
                f'<div class="cell"><div class="prompt"></div><div class="out"><pre>{escape(src)}</pre></div></div>'
            )
    r.parts.append("</div>")
    page = Page(kind="Jupyter Notebook", size=(980, 800))
    page.chips = [
        Chip(BY_ID[lang_id].name),
        Chip(f"{len(cells)} 個儲存格（{r.code_cells} 個程式碼）"),
    ]
    page.body = "".join(r.parts)
    page.attachments = r.attachments
    return page
