"""要交給 LibreOffice 匯入的 HTML：拿掉所有會讀取外部資源的參照（對應安全審查 HIGH 3a）。

LibreOffice 匯入 HTML 時會去抓 <img>、<link>、CSS 的 url(…) 等資源（網路或本機任意檔案）。
這裡逐一檢查：
- data: 網址照留；網址、//、絕對路徑、反斜線開頭的一律封鎖。
- 相對路徑用 realpath 解析，必須是基準資料夾裡的圖片檔（大小有上限），直接換成 data: 網址內嵌，
  這樣轉出的文件也真的帶著圖片；其他一律封鎖。
- 封鎖的屬性改名成 data-blocked-*；CSS 的 url(…) 換成 none；@import、<base>、自動跳轉的
  <meta refresh> 直接拿掉。
屬性值會先解開 HTML 實體並去掉空白控制字元再判斷（擋「&#104;ttp:」這類寫法）。
"""

from __future__ import annotations

import base64
import html
import mimetypes
import os
import re
import stat
from pathlib import Path
from urllib.parse import unquote

MAX_INLINE_BYTES = 20 * 1024 * 1024
_URL_ATTRS = "src|data|poster|background|dynsrc|lowsrc|codebase|srcset|xlink:href|archive"
_ATTR = re.compile(rf"(?i)(?<=[\s\"'/])({_URL_ATTRS})(\s*=\s*)(\"[^\"]*\"|'[^']*'|[^\s>\"']+)")
_TAG = re.compile(r"(?s)<[A-Za-z][^>]*>")
_STYLE_BLOCK = re.compile(r"(?is)(<style\b[^>]*>)(.*?)(</style>)")
_HREF = re.compile(r"(?i)(?<=[\s\"'/])(href)(\s*=\s*)(\"[^\"]*\"|'[^']*'|[^\s>\"']+)")
_CSS_URL = re.compile(r"(?i)url\(\s*(\"[^\"]*\"|'[^']*'|[^)]*?)\s*\)")
_CSS_IMPORT = re.compile(r"(?i)@import\b[^;<]*;?")
_DROP_TAGS = re.compile(r"(?is)<base\b[^>]*>|<meta\b[^>]*http-equiv\s*=\s*[\"']?refresh[^>]*>")
_INVISIBLE = re.compile(r"[\x00-\x20\x7f]")
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")


def _unquote_value(raw: str) -> str:
    if len(raw) >= 2 and raw[0] == raw[-1] and raw[0] in "\"'":
        raw = raw[1:-1]
    return _INVISIBLE.sub("", html.unescape(raw))


class Sanitizer:
    def __init__(self, base_dir: Path) -> None:
        self.root = os.path.realpath(base_dir)

    def _inline(self, value: str) -> str | None:
        """相對路徑 → data: 網址（必須是基準資料夾裡的圖片）；不允許時回傳 None。"""
        real = os.path.realpath(os.path.join(self.root, unquote(value)))
        if not (real == self.root or real.startswith(self.root + os.sep)):
            return None
        try:
            st = os.stat(real)
        except OSError:
            return None
        mime = mimetypes.guess_type(real)[0] or ""
        if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_INLINE_BYTES:
            return None
        if not mime.startswith("image/"):
            return None
        with open(real, "rb") as fh:
            data = base64.b64encode(fh.read()).decode("ascii")
        return f"data:{mime};base64,{data}"

    def resolve(self, raw: str) -> str | None:
        """屬性或 url() 的值 → 安全的網址；None＝封鎖。"""
        value = _unquote_value(raw)
        if not value or value.startswith("#"):
            return value
        if value.lower().startswith("data:"):
            return value
        if _SCHEME.match(value) or value.startswith(("/", "\\")):
            return None
        return self._inline(value)

    def _attr(self, m: re.Match[str]) -> str:
        name, eq, raw = m.group(1), m.group(2), m.group(3)
        if name.lower() != "srcset":
            safe = self.resolve(raw)
            if safe is not None:
                return f'{name}{eq}"{html.escape(safe)}"'
        return f"data-blocked-{name.replace(':', '-')}{eq}{raw}"

    def _css(self, m: re.Match[str]) -> str:
        safe = self.resolve(m.group(1))
        return f'url("{safe}")' if safe else "none"

    def _tag(self, m: re.Match[str]) -> str:
        """只改標籤裡的屬性（內文的「src=」字樣不動）；<link> 的 href 一律封鎖。"""
        tag = m.group(0)
        if tag[:5].lower() == "<link":
            tag = _HREF.sub(lambda h: f"data-blocked-href{h.group(2)}{h.group(3)}", tag)
        tag = _CSS_URL.sub(self._css, tag)  # style="background: url(…)"
        return _ATTR.sub(self._attr, tag)

    def _style(self, m: re.Match[str]) -> str:
        css = _CSS_URL.sub(self._css, _CSS_IMPORT.sub("", m.group(2)))
        return m.group(1) + css + m.group(3)

    def clean(self, markup: str) -> str:
        markup = _DROP_TAGS.sub("", markup)
        markup = _STYLE_BLOCK.sub(self._style, markup)
        return _TAG.sub(self._tag, markup)


def block_remote(markup: str, base_dir: Path) -> str:
    """交給 LibreOffice 前的 HTML 清理（見模組說明）。"""
    return Sanitizer(base_dir).clean(markup)


def block_remote_bytes(data: bytes, base_dir: Path) -> bytes:
    """使用者的 HTML 檔可能不是 UTF-8：以 latin-1 逐位元組處理，其餘位元組原封不動。"""
    return block_remote(data.decode("latin-1"), base_dir).encode("latin-1")
