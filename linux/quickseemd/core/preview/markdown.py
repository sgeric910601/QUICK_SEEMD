"""Markdown → HTML（對應原版 QSMarkdown）。

用 markdown-it-py（見 mdparser）解析，再依原版的方式重建 HTML（見 mdrender）：
- 標題不加 id；清單項目裡一律有 <p>；表格沒有 <tbody>，只有置中／靠右才寫 style。
- 行內樣式以「同樣式的一段文字」為單位，由內而外依序包 code、del、em、strong、a。
- 清單項目開頭的「[ ] 」「[x] 」「[X] 」變成停用的勾選框。
- 程式碼區塊依語言提示上色；本機圖片（< 15,000,000 位元組）變成附件 cid:md-img-N，
  找不到就顯示 img-missing 小標籤；網路圖片保留 <img src>（預覽視窗會擋下網路載入）。
- 原始 HTML 經過 sanitize 過濾（Linux 版補充，原版直接放行）。
Linux 版補充的上限（惡意或超大的檔案不能拖垮預覽）：
- 只排版前 MAX_LINES 行，其餘以純文字顯示（truncated）。
- 段落太長、解析太久、表格太大時改成純文字（degraded，見 mdparser）。
- 同一張本機圖片只讀一次；圖片最多 MAX_IMAGES 張、合計 MAX_IMAGE_TOTAL 位元組。
"""

from __future__ import annotations

import mimetypes
import os
import stat
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urljoin, urlsplit, unquote

from markdown_it.token import Token

from . import Attachment, detect, highlight, mdparser
from .page import escape

MAX_IMAGE_BYTES = 15_000_000
MAX_IMAGES = 200
MAX_IMAGE_TOTAL = 64 * 1024 * 1024
MAX_LINES = 20_000
FRONT_MATTER_LINES = 200
TASK_MARKERS = (("[ ] ", False), ("[x] ", True), ("[X] ", True))
_ZS = " \t\u00a0\u1680\u2000\u2001\u2002\u2003\u2004\u2005\u2006\u2007\u2008\u2009\u200a"
_ZS += "\u202f\u205f\u3000"

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
    truncated: bool = False  # 超過 MAX_LINES 行，後面以純文字顯示


@dataclass
class MarkdownContext:
    """同一份文件（或同一本筆記本的所有儲存格）共用的狀態：時間預算、圖片編號與上限。"""

    deadline: float = field(default_factory=mdparser.new_deadline)
    count: int = 0
    total_bytes: int = 0
    by_path: dict[str, str] = field(default_factory=dict)
    attachments: dict[str, Attachment] = field(default_factory=dict)

    def attach(self, path: Path) -> str | None:
        """本機圖片 → 附件名稱；讀不到或超過上限時回傳 None。同一個檔案只讀一次。"""
        key = os.path.normpath(path)
        if key in self.by_path:
            return self.by_path[key]
        if len(self.by_path) >= MAX_IMAGES:
            return None
        loaded = read_image(Path(key), MAX_IMAGE_TOTAL - self.total_bytes)
        if loaded is None:
            return None
        data, mime = loaded
        self.count += 1
        name = f"md-img-{self.count}"
        self.attachments[name] = Attachment(name, data, mime)
        self.total_bytes += len(data)
        self.by_path[key] = name
        return name


def _trim(s: str) -> str:
    return s.strip(_ZS)


def split_front_matter(text: str) -> tuple[str, str] | None:
    """開頭的 YAML 標頭 → （表格 HTML, 其餘內文）；結尾的 --- 或 ... 要在 200 行內。"""
    if not text.startswith(("---\n", "---\r\n")):
        return None
    lines = text.split("\n", FRONT_MATTER_LINES + 1)  # 最後一項是其餘全文，不逐行切開
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


def _split_lines(text: str, limit: int) -> tuple[str, str]:
    """前 limit 行與其餘內容。"""
    parts = text.split("\n", limit)
    if len(parts) <= limit:
        return text, ""
    return "\n".join(parts[:limit]), parts[limit]


def render(
    source: str,
    base_dir: Path | None,
    first_image: int = 0,
    *,
    context: MarkdownContext | None = None,
) -> MarkdownResult:
    """Markdown 原文 → HTML 與附件；base_dir 是相對路徑（連結、圖片）的基準資料夾。"""
    from .mdrender import BlockRenderer  # noqa: PLC0415 — 避免循環匯入

    ctx = context if context is not None else MarkdownContext(count=first_image)
    front = ""
    text = source
    split = split_front_matter(text)
    if split is not None:
        front, text = split
    head, rest = _split_lines(text, MAX_LINES)
    tokens, degraded = mdparser.parse(head, ctx.deadline)
    html = front + BlockRenderer(base_dir, ctx).run(tokens)
    if rest:
        html += f"<pre>{escape(rest)}</pre>\n"
    attachments = dict(ctx.attachments) if context is None else {}
    return MarkdownResult(html, attachments, ctx.count, degraded, bool(rest))


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


def read_image(path: Path, budget: int = MAX_IMAGE_BYTES) -> tuple[bytes, str] | None:
    """讀本機圖片：只讀一般檔案，要小於 15,000,000 位元組，也不能超過剩下的預算。"""
    p = Path(os.path.normpath(path))
    limit = min(MAX_IMAGE_BYTES, budget + 1)
    try:
        st = os.stat(p)
        if not stat.S_ISREG(st.st_mode) or st.st_size >= limit:
            return None
        with open(p, "rb") as fh:
            data = fh.read(limit)
    except OSError:
        return None
    if len(data) >= limit:
        return None
    return data, mimetypes.guess_type(p.name)[0] or "application/octet-stream"


def code_block(content: str, hint: str | None) -> str:
    lang = detect.for_hint(hint)
    if lang is not None:
        body = highlight.inline(content, lang)
    else:
        body = escape(content[:-1] if content.endswith("\n") else content)
    return f"<pre><code>{body}</code></pre>\n"


__all__ = ["render", "split_front_matter", "MarkdownResult", "MarkdownContext"]
