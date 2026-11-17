"""要交給 LibreOffice 匯入的 HTML：先完整解析再重新輸出，拿掉所有會讀取外部資源的寫法。

LibreOffice 匯入 HTML 時會去抓 <img>、<link>、CSS 的 url(…) 等資源（網路或本機任意檔案），
所以使用者的 HTML 與自己產生的 HTML 都一律先經過這裡：
1. 判斷編碼（BOM → <meta charset> → 自動偵測）並轉成 UTF-8；含 NUL 字元的直接拒絕。
2. 用 html.parser 逐一讀出標籤與文字，再重新輸出（屬性值重新跳脫），不靠正規表示式切標籤。
3. 會載入資源的寫法：
   - data: 網址只留點陣圖（PNG、JPEG、GIF、WebP、BMP），SVG 與其他類型一律封鎖；
   - 網址、//、絕對路徑、反斜線開頭的一律封鎖；
   - 相對路徑用 realpath 解析，必須是基準資料夾裡的點陣圖檔（大小有上限），換成 data: 內嵌；
   - <link>、<base>、<script>、自動跳轉的 <meta>、註解、處理指令直接拿掉；on… 事件屬性拿掉；
   - <style> 與 style 屬性：@import 拿掉、url(…) 依上面規則處理；含 CSS 跳脫字元（\\）的整段丟棄。
"""

from __future__ import annotations

import base64
import codecs
import html
import mimetypes
import os
import re
import stat
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote

from .. import errors
from . import textcodec

MAX_INLINE_BYTES = 20 * 1024 * 1024
# 只允許點陣圖：SVG 本身又能用 <image href="file://…"> 讀取別的檔案，一律不收
RASTER_TYPES = frozenset({"image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp"})
_DATA_URI = re.compile(r"(?i)^data:([a-z0-9.+-]+/[a-z0-9.+-]+)[;,]")
URL_ATTRS = frozenset(
    {"src", "data", "poster", "background", "dynsrc", "lowsrc", "codebase", "archive"}
    | {"xlink:href", "longdesc", "usemap", "profile", "manifest", "icon", "formaction", "action"}
)
DROP_ATTRS = frozenset({"srcset", "imagesrcset", "ping"})
LINK_HREF_TAGS = frozenset({"a", "area"})  # 只有超連結的 href 保留（不會被載入）
DROPPED_TAGS = frozenset({"link", "base", "script", "noscript", "template"})
SKIP_CONTENT = frozenset({"script", "noscript", "template"})
VOID_TAGS = frozenset(
    {"area", "br", "col", "embed", "hr", "img", "input", "meta", "param", "source", "track", "wbr"}
)
NUL_MESSAGE = "HTML 檔案含有 NUL 字元，為了安全不轉換。"
_ATTR_NAME = re.compile(r"[a-z][a-z0-9_.:-]*")
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_INVISIBLE = re.compile(r"[\x00-\x20\x7f]")
_CSS_IMPORT = re.compile(r"(?i)@import\b[^;]*;?")
_CSS_URL = re.compile(r"(?i)url\(\s*(\"[^\"]*\"|'[^']*'|[^)]*?)\s*\)")
_META_CHARSET = re.compile(rb"(?i)<meta[^>]+charset\s*=\s*[\"']?\s*([A-Za-z0-9_:.+-]+)")
_BOMS = (
    (codecs.BOM_UTF8, "utf-8-sig"),
    (codecs.BOM_UTF32_LE, "utf-32"),
    (codecs.BOM_UTF32_BE, "utf-32"),
    (codecs.BOM_UTF16_LE, "utf-16"),
    (codecs.BOM_UTF16_BE, "utf-16"),
)


# ---------------------------------------------------------------- 編碼


def _meta_codec(raw: bytes) -> str | None:
    m = _META_CHARSET.search(raw[:4096])
    if not m:
        return None
    try:
        codec = codecs.lookup(m.group(1).decode("ascii", "replace")).name
    except LookupError:
        return None
    # HTML 規定：宣告成 UTF-16／32 的 ASCII 相容檔案仍當 UTF-8
    return "utf-8" if codec.startswith(("utf-16", "utf-32")) else codec


def decode_html(raw: bytes) -> str:
    """BOM → <meta charset> → 自動偵測；解出來含 NUL 字元就拒絕。"""
    text: str | None = None
    for bom, codec in _BOMS:
        if raw.startswith(bom):
            text = raw.decode(codec, "replace")
            break
    if text is None:
        codec = _meta_codec(raw)
        if codec is not None:
            text = raw.decode(codec, "replace")
    if text is None:
        text = textcodec.decode_bytes(raw).text
    if "\x00" in text:
        raise errors.invalid_data(NUL_MESSAGE)
    return text


# ---------------------------------------------------------------- 網址


class Resolver:
    def __init__(self, base_dir: Path) -> None:
        self.root = os.path.realpath(base_dir)

    def _inline(self, value: str) -> str | None:
        real = os.path.realpath(os.path.join(self.root, unquote(value)))
        if not (real == self.root or real.startswith(self.root + os.sep)):
            return None
        try:
            st = os.stat(real)
        except OSError:
            return None
        mime = (mimetypes.guess_type(real)[0] or "").lower()
        if not stat.S_ISREG(st.st_mode) or st.st_size > MAX_INLINE_BYTES:
            return None
        if mime not in RASTER_TYPES:
            return None
        with open(real, "rb") as fh:
            return f"data:{mime};base64,{base64.b64encode(fh.read()).decode('ascii')}"

    def resolve(self, raw: str) -> str | None:
        """屬性值或 url() 的內容 → 安全的網址；None＝封鎖。"""
        value = _INVISIBLE.sub("", raw)
        if value.lower().startswith("data:"):
            m = _DATA_URI.match(value)
            return value if m and m.group(1).lower() in RASTER_TYPES else None
        if not value or _SCHEME.match(value) or value.startswith(("/", "\\")):
            return None
        return self._inline(value)

    def css(self, text: str) -> str:
        """CSS：含跳脫字元就整段丟棄（擋 @\\69mport 這類寫法）；@import 拿掉；url() 逐一檢查。"""
        if "\\" in text:
            return ""
        text = _CSS_IMPORT.sub("", text)

        def repl(m: re.Match[str]) -> str:
            inner = m.group(1)
            if len(inner) >= 2 and inner[0] == inner[-1] and inner[0] in "\"'":
                inner = inner[1:-1]
            safe = self.resolve(inner)
            return f'url("{safe}")' if safe else "none"

        return _CSS_URL.sub(repl, text).replace("</", "<\\/")


# ---------------------------------------------------------------- 解析與重新輸出


class _Sanitizer(HTMLParser):
    def __init__(self, resolver: Resolver) -> None:
        super().__init__(convert_charrefs=True)
        self.resolver = resolver
        self.out: list[str] = ['<meta charset="utf-8">\n']
        self.skip: list[str] = []
        self.in_style = False

    def _value(self, tag: str, name: str, value: str) -> str | None:
        """一個屬性清理後的值；None＝拿掉這個屬性。"""
        if not _ATTR_NAME.fullmatch(name) or name.startswith("on") or name in DROP_ATTRS:
            return None
        if tag == "meta" and name in ("charset", "http-equiv"):
            return None
        if name == "style":
            return self.resolver.css(value)
        if name in URL_ATTRS or (name == "href" and tag not in LINK_HREF_TAGS):
            return self.resolver.resolve(value)
        if name == "href" and _INVISIBLE.sub("", value).lower().startswith("javascript:"):
            return None
        return value

    def _attrs(self, tag: str, attrs: list[tuple[str, str | None]]) -> str:
        parts = []
        for name, value in attrs:
            cleaned = self._value(tag, name.lower(), value or "")
            if cleaned is not None:
                parts.append(f' {name.lower()}="{html.escape(cleaned, quote=True)}"')
        return "".join(parts)

    def _open(self, tag: str, attrs: list[tuple[str, str | None]], close: bool) -> None:
        if self.skip:
            if tag == self.skip[-1] and not close:
                self.skip.append(tag)
            return
        if tag in DROPPED_TAGS:
            if tag in SKIP_CONTENT and not close:
                self.skip.append(tag)
            return
        if tag == "meta" and any((n or "").lower() == "http-equiv" for n, _v in attrs):
            return  # 拿掉 refresh 與 content-type（輸出一律是 UTF-8）
        self.out.append(f"<{tag}{self._attrs(tag, attrs)}{' /' if close else ''}>")
        if tag == "style" and not close:
            self.in_style = True

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._open(tag, attrs, False)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._open(tag, attrs, True)

    def handle_endtag(self, tag: str) -> None:
        if self.skip:
            if tag == self.skip[-1]:
                self.skip.pop()
            return
        if tag in DROPPED_TAGS or tag in VOID_TAGS:
            return
        if tag == "style":
            self.in_style = False
        self.out.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self.skip:
            return
        if self.in_style:
            self.out.append(self.resolver.css(data))
        else:
            self.out.append(html.escape(data, quote=False))

    def handle_decl(self, decl: str) -> None:
        if decl.lower().startswith("doctype"):
            self.out.append("<!DOCTYPE html>")

    # 註解、處理指令、CDATA 一律丟掉
    def handle_comment(self, data: str) -> None:  # noqa: ARG002
        return

    def handle_pi(self, data: str) -> None:  # noqa: ARG002
        return

    def unknown_decl(self, data: str) -> None:  # noqa: ARG002
        return


def sanitize(markup: str, base_dir: Path) -> str:
    """解析後重新輸出成安全的 UTF-8 HTML（見模組說明）。"""
    if "\x00" in markup:
        raise errors.invalid_data(NUL_MESSAGE)
    parser = _Sanitizer(Resolver(base_dir))
    parser.feed(markup)
    parser.close()
    return "".join(parser.out)


def sanitize_bytes(raw: bytes, base_dir: Path) -> bytes:
    """使用者的 HTML 檔：先判斷編碼轉成文字，再清理，輸出 UTF-8。"""
    return sanitize(decode_html(raw), base_dir).encode("utf-8")
