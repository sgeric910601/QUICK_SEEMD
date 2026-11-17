"""原始 HTML 過濾（Linux 版補充）。

原版會把 Markdown 的原始 HTML、筆記本的 text/html 與 SVG 輸出直接放進頁面。
這裡照樣保留排版用的標籤，但拿掉所有可能執行程式或載入外部頁面的東西：
- 整個丟掉：script（含內容）、iframe、frame、frameset、object、embed、applet、base、link、meta、
  註解、<!…> 宣告與處理指令。
- 屬性：拿掉 on* 事件、srcdoc；網址屬性若是 javascript:、vbscript: 或非圖片的 data: 也拿掉。
文字內容一律重新跳脫。
"""

from __future__ import annotations

import logging
import re
from html.parser import HTMLParser

log = logging.getLogger(__name__)

DROP_WITH_CONTENT = frozenset({"script"})
DROP_TAG = frozenset(
    {"iframe", "frame", "frameset", "object", "embed", "applet", "base", "link", "meta", "portal"}
)
URL_ATTRS = frozenset(
    {
        "href",
        "src",
        "action",
        "formaction",
        "xlink:href",
        "data",
        "poster",
        "background",
        "lowsrc",
        "dynsrc",
        "ping",
        "cite",
        "srcset",
        "manifest",
        "codebase",
    }
)
_SAFE_DATA = re.compile(r"^data:image/(?:png|jpeg|jpg|gif|webp|bmp|svg\+xml);", re.IGNORECASE)
_STRIP = re.compile(r"[\x00-\x20\x7f]+")
_TEXT_ESC = str.maketrans({"&": "&amp;", "<": "&lt;", ">": "&gt;"})
_ATTR_ESC = str.maketrans({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"})
_NAME_OK = re.compile(r"^[a-z][a-z0-9:_.-]*$")


def _unsafe_url(value: str) -> bool:
    v = _STRIP.sub("", value).lower()
    if v.startswith(("javascript:", "vbscript:")):
        return True
    return v.startswith("data:") and not _SAFE_DATA.match(v)


def _keep_attr(name: str, value: str | None) -> bool:
    if not _NAME_OK.match(name) or name.startswith("on") or name == "srcdoc":
        return False
    return not (name in URL_ATTRS and value is not None and _unsafe_url(value))


class _Sanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip = 0  # 在 script 裡面時大於 0

    def _tag(self, tag: str, attrs: list[tuple[str, str | None]], closing: str) -> str:
        parts = [f"<{tag}"]
        for name, value in attrs:
            if not _keep_attr(name, value):
                continue
            parts.append(f" {name}" if value is None else f' {name}="{value.translate(_ATTR_ESC)}"')
        parts.append(closing)
        return "".join(parts)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in DROP_WITH_CONTENT:
            self.skip += 1
            return
        if self.skip or tag in DROP_TAG or not _NAME_OK.match(tag):
            return
        self.out.append(self._tag(tag, attrs, ">"))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if self.skip or tag in DROP_TAG or tag in DROP_WITH_CONTENT or not _NAME_OK.match(tag):
            return
        self.out.append(self._tag(tag, attrs, "/>"))

    def handle_endtag(self, tag: str) -> None:
        if tag in DROP_WITH_CONTENT:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip or tag in DROP_TAG or not _NAME_OK.match(tag):
            return
        self.out.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if not self.skip:
            self.out.append(data.translate(_TEXT_ESC))

    # 註解、宣告、處理指令一律丟掉
    def handle_comment(self, data: str) -> None:
        return

    def handle_decl(self, decl: str) -> None:
        return

    def handle_pi(self, data: str) -> None:
        return

    def unknown_decl(self, data: str) -> None:
        return


def sanitize_html(fragment: str) -> str:
    """過濾一段原始 HTML（可以是不完整的片段，例如單獨的 <span>）。"""
    parser = _Sanitizer()
    try:
        parser.feed(fragment)
        parser.close()
    except Exception:  # noqa: BLE001 — 解析器壞掉時改成整段當文字顯示
        log.debug("HTML 片段解析失敗，改成純文字", exc_info=True)
        return fragment.translate(_TEXT_ESC)
    return "".join(parser.out)
