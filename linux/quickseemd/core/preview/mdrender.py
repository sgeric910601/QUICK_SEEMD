"""把 markdown-it 的詞元轉成與原版 QSMarkdown 相同結構的 HTML。"""

from __future__ import annotations

from pathlib import Path
from urllib.parse import unquote

from markdown_it.token import Token

from . import Attachment
from .markdown import (
    CLOSE_TAGS,
    OPEN_TAGS,
    TASK_MARKERS,
    _plain_text,
    absolute_url,
    base_uri,
    code_block,
    local_image_path,
    read_image,
)
from .page import escape
from .sanitize import sanitize_html

# 行內片段：("run", 文字, 樣式) / ("raw", HTML, None) / ("image", (網址, 替代文字), None)
Style = tuple[bool, bool, bool, bool, str | None]  # code, del, em, strong, 連結
Item = tuple[str, object, Style | None]
_ALIGN_KEEP = ("text-align:center", "text-align:right")


class BlockRenderer:
    def __init__(self, base_dir: Path | None, first_image: int) -> None:
        self.base = base_uri(base_dir)
        self.attachments: dict[str, Attachment] = {}
        self.count = first_image
        self.out: list[str] = []
        self.stack: list[str] = []
        self.task_check = False
        self.in_thead = False

    def run(self, tokens: list[Token]) -> str:
        for tok in tokens:
            self._block(tok)
        return "".join(self.out)

    # ---------------------------------------------------------------- 區塊

    def _block(self, tok: Token) -> None:
        t = tok.type
        if t.endswith("_open"):
            self._open(tok)
            self.stack.append(t[:-5])
        elif t.endswith("_close"):
            if self.stack:
                self.stack.pop()
            self._close(tok)
        elif t == "inline":
            self.out.append(self._inline(tok.children or []))
        elif t == "hr":
            self.out.append("<hr>")
        elif t == "fence":
            self.out.append(code_block(tok.content, tok.info.strip() or None))
        elif t == "code_block":
            self.out.append(code_block(tok.content, None))
        elif t == "html_block":
            self.out.append(sanitize_html(tok.content))

    def _open(self, tok: Token) -> None:
        t = tok.type
        if t == "paragraph_open":
            self.task_check = bool(self.stack) and self.stack[-1] == "list_item"
        if t in OPEN_TAGS:
            self.out.append(OPEN_TAGS[t])
        elif t == "heading_open":
            self.out.append(f"<{tok.tag}>")
        elif t == "ordered_list_open":
            start = tok.attrGet("start")
            self.out.append(f'<ol start="{start}">' if start not in (None, 1) else "<ol>")
        elif t == "thead_open":
            self.in_thead = True
        elif t == "tr_open":
            self.out.append("<thead><tr>" if self.in_thead else "<tr>")
        elif t in ("th_open", "td_open"):
            style = tok.attrGet("style")
            attr = f' style="{style}"' if style in _ALIGN_KEEP else ""
            self.out.append(f"<{tok.tag}{attr}>")

    def _close(self, tok: Token) -> None:
        t = tok.type
        if t == "paragraph_close":
            self.task_check = False
        if t in CLOSE_TAGS:
            self.out.append(CLOSE_TAGS[t])
        elif t == "heading_close":
            self.out.append(f"</{tok.tag}>\n")
        elif t == "thead_close":
            self.in_thead = False
        elif t == "tr_close":
            self.out.append("</tr></thead>\n" if self.in_thead else "</tr>\n")

    # ---------------------------------------------------------------- 行內

    def _inline(self, children: list[Token]) -> str:
        parts: list[str] = []
        for kind, payload, style in _items(children):
            if kind == "raw":
                parts.append(str(payload))
            elif kind == "image":
                self.task_check = False
                src, alt = payload  # type: ignore[misc]
                parts.append(self._image(src, alt))
            else:
                parts.append(self._run("".join(payload), style))  # type: ignore[arg-type]
        return "".join(parts)

    def _run(self, text: str, style: Style) -> str:
        code, strike, em, strong, href = style
        prefix = ""
        if self.task_check:
            self.task_check = False
            for marker, checked in TASK_MARKERS:
                if text.startswith(marker):
                    prefix = '<input type="checkbox" class="task" disabled'
                    prefix += " checked>" if checked else ">"
                    text = text[len(marker) :]
                    break
        s = escape(text)
        if code:
            s = f"<code>{s}</code>"
        if strike:
            s = f"<del>{s}</del>"
        if em:
            s = f"<em>{s}</em>"
        if strong:
            s = f"<strong>{s}</strong>"
        if href is not None:
            s = f'<a href="{escape(absolute_url(href, self.base))}">{s}</a>'
        return prefix + s

    def _image(self, src: str, alt: str) -> str:
        alt_esc = escape(alt)
        path = local_image_path(src, self.base)
        if path is None:
            return f'<img src="{escape(src)}" alt="{alt_esc}">'
        loaded = read_image(path)
        if loaded is None:
            label = alt_esc or escape(unquote(path.name))
            return f'<span class="img-missing">🖼 {label}</span>'
        data, mime = loaded
        self.count += 1
        name = f"md-img-{self.count}"
        self.attachments[name] = Attachment(name, data, mime)
        return f'<img src="cid:{name}" alt="{alt_esc}">'


def _items(children: list[Token]) -> list[Item]:
    """行內詞元 → 片段清單；樣式相同的相鄰文字合併成一段（與原版的 run 相同）。"""
    items: list[Item] = []
    depth = {"strong": 0, "em": 0, "s": 0}
    links: list[str] = []
    for c in children:
        t = c.type
        if t in ("text", "code_inline"):
            style: Style = (
                t == "code_inline",
                depth["s"] > 0,
                depth["em"] > 0,
                depth["strong"] > 0,
                links[-1] if links else None,
            )
            if items and items[-1][0] == "run" and items[-1][2] == style:
                items[-1][1].append(c.content)  # type: ignore[union-attr]
            else:
                items.append(("run", [c.content], style))
        elif t == "softbreak":
            items.append(("raw", "\n", None))
        elif t == "hardbreak":
            items.append(("raw", "<br>", None))
        elif t == "html_inline":
            items.append(("raw", sanitize_html(c.content), None))
        elif t == "image":
            items.append(("image", (c.attrGet("src") or "", _plain_text(c.children)), None))
        else:
            _track(c, depth, links)
    return items


def _track(c: Token, depth: dict[str, int], links: list[str]) -> None:
    t = c.type
    for key in depth:
        if t == f"{key}_open":
            depth[key] += 1
        elif t == f"{key}_close":
            depth[key] = max(0, depth[key] - 1)
    if t == "link_open":
        links.append(str(c.attrGet("href") or ""))
    elif t == "link_close" and links:
        links.pop()
