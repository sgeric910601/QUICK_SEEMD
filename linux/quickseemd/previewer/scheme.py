"""預覽網頁的自訂網址 qs-preview://（純邏輯，不碰 WebKit）。

- 頁面：qs-preview://doc/index.html?v=<代號>
- 附件：qs-preview://doc/att/<名稱>?v=<代號>（網頁裡的 cid:<名稱> 會改寫成這個）

每次顯示新內容都換一個代號，WebKit 的記憶體快取就不會拿到上一個檔案的附件。
內容過濾規則封鎖 qs-preview://doc/ 以外的一切載入（網路、file://、data: …）。
"""

from __future__ import annotations

import html
import json
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass, field
from urllib.parse import parse_qs, quote, unquote, urlsplit

from ..core.preview import Attachment, Document

SCHEME = "qs-preview"
HOST = "doc"
PAGE_PATH = "/index.html"
ATTACHMENT_PREFIX = "/att/"
HTML_MIME = "text/html; charset=utf-8"
FILTER_ID = "quickseemd-preview-only"

# cid: 參照後面可以接的字元（引號、括號、空白、標籤結尾）
_CID_END = r"(?=[\"'\s)>]|$)"


@dataclass(frozen=True)
class Resource:
    data: bytes
    mime: str


@dataclass(frozen=True)
class Page:
    """改寫好、可以提供給網頁元件的一頁。"""

    token: int
    html: str
    attachments: Mapping[str, Attachment] = field(default_factory=dict)


def page_uri(token: int) -> str:
    return f"{SCHEME}://{HOST}{PAGE_PATH}?v={token}"


def attachment_uri(name: str, token: int) -> str:
    return f"{SCHEME}://{HOST}{ATTACHMENT_PREFIX}{quote(name, safe='')}?v={token}"


def rewrite_cid(source: str, names: Iterable[str], token: int) -> str:
    """把 cid:<名稱> 換成自訂網址；只換真的有的附件，名稱要完整相符。"""
    variants: dict[str, str] = {}
    for name in names:
        variants[name] = name
        variants[html.escape(name, quote=True)] = name
    if not variants:
        return source
    alternatives = "|".join(re.escape(v) for v in sorted(variants, key=len, reverse=True))
    pattern = re.compile(rf"cid:({alternatives}){_CID_END}")
    return pattern.sub(lambda m: attachment_uri(variants[m.group(1)], token), source)


def build_page(doc: Document, token: int) -> Page:
    return Page(token, rewrite_cid(doc.html, doc.attachments.keys(), token), dict(doc.attachments))


def _token_of(query: str) -> int | None:
    values = parse_qs(query, keep_blank_values=True).get("v", [])
    if len(values) != 1 or not values[0].isdigit():
        return None
    return int(values[0])


def lookup(uri: str, pages: Mapping[int, Page]) -> Resource | None:
    """自訂網址 → 內容；不認得的網址一律回傳 None（當成找不到）。"""
    parts = urlsplit(uri)
    if parts.scheme != SCHEME or parts.netloc != HOST:
        return None
    token = _token_of(parts.query)
    page = pages.get(token) if token is not None else None
    if page is None:
        return None
    if parts.path == PAGE_PATH:
        return Resource(page.html.encode("utf-8"), HTML_MIME)
    if parts.path.startswith(ATTACHMENT_PREFIX):
        raw = parts.path[len(ATTACHMENT_PREFIX) :]
        if "/" in raw:
            return None
        att = page.attachments.get(unquote(raw))
        if att is not None:
            return Resource(att.data, att.mime)
    return None


def is_allowed_navigation(uri: str, current_page: str) -> bool:
    """只准載入目前這一頁，以及同一頁裡的錨點（SQLite 目錄的 #t1 之類）。"""
    if uri == current_page:
        return True
    base, sep, _fragment = uri.partition("#")
    return bool(sep) and base == current_page


def filter_rules() -> str:
    """WebKit 內容過濾規則：先封鎖全部，再放行自己的網址。"""
    rules = [
        {"trigger": {"url-filter": ".*"}, "action": {"type": "block"}},
        {
            "trigger": {"url-filter": f"^{SCHEME}://{HOST}/"},
            "action": {"type": "ignore-previous-rules"},
        },
    ]
    return json.dumps(rules)
