"""Markdown → HTML（對應原版 QSMarkdown）。

用 markdown-it-py（CommonMark＋表格＋刪除線，允許原始 HTML）解析，再依原版的方式重建 HTML：
- 標題不加 id；清單項目裡一律有 <p>；表格沒有 <tbody>，只有置中／靠右才寫 style。
- 行內樣式以「同樣式的一段文字」為單位，由內而外依序包 code、del、em、strong、a。
- 清單項目開頭的「[ ] 」「[x] 」「[X] 」變成停用的勾選框。
- 程式碼區塊依語言提示上色；本機圖片（< 15,000,000 位元組）變成附件 cid:md-img-N，
  找不到就顯示 img-missing 小標籤；網路圖片保留 <img src>（預覽視窗會擋下網路載入）。
- 原始 HTML 經過 sanitize 過濾（Linux 版補充，原版直接放行）。
- 防止惡意內容拖垮預覽（Linux 版補充）：單一段落超過 20,000 字元，或行內解析累計超過 1.2 秒後，
  其餘段落改以純文字顯示，並回報 degraded（頁面會加一則提示）。
"""

from __future__ import annotations

import mimetypes
import os
import stat
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from urllib.parse import unquote, urljoin, urlsplit

from markdown_it import MarkdownIt
from markdown_it.rules_core import StateCore
from markdown_it.token import Token

from . import Attachment, detect, highlight
from .page import escape
from .sanitize import sanitize_html

MAX_IMAGE_BYTES = 15_000_000
INLINE_LIMIT = 20_000
INLINE_BUDGET_SECONDS = 1.2
_DEADLINE_KEY = "quickseemd_inline_deadline"
_DEGRADED_KEY = "quickseemd_degraded"
FRONT_MATTER_LINES = 200
TASK_MARKERS = (("[ ] ", False), ("[x] ", True), ("[X] ", True))
_ZS = " \t             "
_ZS += "  　"
_BAD_SCHEMES = ("javascript:", "vbscript:")

OPEN_TAGS = {
    "paragraph_open": "<p>",
    "bullet_list_open": "<ul>",
    "list_item_open": "<li>",
    "blockquote_open": "<blockquote>",
    "table_open": "<table>",
}
CLOSE_TAGS = {
    "paragraph_close": "</p>\n",
    "bullet_list_close": "</ul>\n",
    "ordered_list_close": "</ol>\n",
    "list_item_close": "</li>\n",
    "blockquote_close": "</blockquote>\n",
    "table_close": "</table>\n",
    "th_close": "</th>",
    "td_close": "</td>",
}


@dataclass(frozen=True)
class MarkdownResult:
    html: str
    attachments: dict[str, Attachment]
    images: int  # 用掉的圖片編號（筆記本接續編號用）
    degraded: bool = False  # 有段落因為太長或太複雜改以純文字顯示


def _validate_link(url: str) -> bool:
    """與 markdown-it 預設相同，但允許 file:（本機圖片要用）。"""
    v = url.strip().lower()
    if v.startswith(_BAD_SCHEMES):
        return False
    if v.startswith("data:"):
        return v.startswith(("data:image/gif;", "data:image/png;", "data:image/jpeg;"))
    return True


def _guarded_inline(state: StateCore) -> None:
    """取代 markdown-it 的行內解析步驟：太長的段落或超過時間預算後，直接當純文字。"""
    deadline = state.env.get(_DEADLINE_KEY)
    for token in state.tokens:
        if token.type != "inline":
            continue
        children: list[Token] = []
        too_long = len(token.content) > INLINE_LIMIT
        if too_long or (deadline is not None and time.monotonic() > deadline):
            plain = Token("text", "", 0)
            plain.content = token.content
            children.append(plain)
            state.env[_DEGRADED_KEY] = True
        else:
            state.md.inline.parse(token.content, state.md, state.env, children)
        token.children = children


@lru_cache(maxsize=1)
def _parser() -> MarkdownIt:
    md = MarkdownIt("commonmark", {"html": True}).enable(["table", "strikethrough"])
    md.validateLink = _validate_link  # type: ignore[method-assign]
    md.core.ruler.at("inline", _guarded_inline)
    return md


def _trim(s: str) -> str:
    return s.strip(_ZS)


def split_front_matter(text: str) -> tuple[str, str] | None:
    """開頭的 YAML 標頭 → （表格 HTML, 其餘內文）；結尾的 --- 或 ... 要在 200 行內。"""
    if not text.startswith(("---\n", "---\r\n")):
        return None
    lines = text.split("\n")
    end_idx = next(
        (
            k
            for k in range(1, min(len(lines), FRONT_MATTER_LINES + 1))
            if _trim(lines[k]) in ("---", "...")
        ),
        None,
    )
    if end_idx is None:
        return None
    rows: list[tuple[str, list[str]]] = []
    for line in lines[1:end_idx]:
        colon = line.find(":")
        if colon < 0 or line.startswith((" ", "-")):
            trimmed = _trim(line)
            if trimmed and rows:
                rows[-1][1].append(escape(trimmed))
            continue
        rows.append((escape(_trim(line[:colon])), [escape(_trim(line[colon + 1 :]))]))
    body = "".join(f"<tr><th>{k}</th><td>{'<br>'.join(v)}</td></tr>" for k, v in rows)
    table = f'<div class="front"><table>{body}</table></div>' if rows else ""
    return table, "\n".join(lines[end_idx + 1 :])


def _plain_text(children: list[Token] | None) -> str:
    """圖片替代文字：只取純文字。"""
    out: list[str] = []
    for c in children or []:
        if c.type in ("text", "code_inline"):
            out.append(c.content)
        elif c.type == "image":
            out.append(_plain_text(c.children))
        elif c.type in ("softbreak", "hardbreak"):
            out.append("\n")
    return "".join(out)


def render(source: str, base_dir: Path | None, first_image: int = 0) -> MarkdownResult:
    """Markdown 原文 → HTML 與附件；base_dir 是相對路徑（連結、圖片）的基準資料夾。"""
    from .mdrender import BlockRenderer  # noqa: PLC0415 — 避免循環匯入

    front = ""
    text = source
    split = split_front_matter(text)
    if split is not None:
        front, text = split
    renderer = BlockRenderer(base_dir, first_image)
    env: dict[str, object] = {_DEADLINE_KEY: time.monotonic() + INLINE_BUDGET_SECONDS}
    html = front + renderer.run(_parser().parse(text, env))
    return MarkdownResult(html, renderer.attachments, renderer.count, bool(env.get(_DEGRADED_KEY)))


# ---------------------------------------------------------------- 圖片與連結（BlockRenderer 共用）


def base_uri(base_dir: Path | None) -> str | None:
    return None if base_dir is None else base_dir.resolve().as_uri().rstrip("/") + "/"


def absolute_url(href: str, base: str | None) -> str:
    return href if base is None else urljoin(base, href)


def local_image_path(src: str, base: str | None) -> Path | None:
    """本機圖片的路徑；網路圖片、data: 或沒有基準資料夾時回傳 None。"""
    parsed = urlsplit(src)
    if parsed.scheme == "file":
        return Path(unquote(parsed.path))
    if parsed.scheme or base is None:
        return None
    return Path(unquote(urlsplit(urljoin(base, src)).path))


def read_image(path: Path) -> tuple[bytes, str] | None:
    """讀本機圖片：只讀一般檔案，且要小於 15,000,000 位元組。"""
    p = Path(os.path.normpath(path))
    try:
        st = os.stat(p)
        if not stat.S_ISREG(st.st_mode) or st.st_size >= MAX_IMAGE_BYTES:
            return None
        with open(p, "rb") as fh:
            data = fh.read(MAX_IMAGE_BYTES)
    except OSError:
        return None
    if len(data) >= MAX_IMAGE_BYTES:
        return None
    return data, mimetypes.guess_type(p.name)[0] or "application/octet-stream"


def code_block(content: str, hint: str | None) -> str:
    lang = detect.for_hint(hint)
    if lang is not None:
        body = highlight.inline(content, lang)
    else:
        body = escape(content[:-1] if content.endswith("\n") else content)
    return f"<pre><code>{body}</code></pre>\n"


__all__ = ["render", "split_front_matter", "MarkdownResult", "sanitize_html"]
