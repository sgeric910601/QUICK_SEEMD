"""原始 HTML 過濾（Linux 版補充；原版直接把 Markdown 的原始 HTML、筆記本的 HTML／SVG 輸出放進頁面）。

採「允許清單」：只保留明列的元素與屬性，其他一律拿掉。
- 元素：版面與文字用的 HTML 標籤，加上一小組靜態 SVG。不認得的元素只拿掉標籤、保留裡面的文字；
  script、style、iframe、foreignObject、SVG handler 之類連內容一起丟掉。
  SVG 的 image 只接受 cid: 或 data:image/ 網址，否則整個拿掉。
- 屬性：依元素明列；網址屬性要通過網址檢查，style 與 SVG 外觀屬性要通過 CSS 檢查
  （url() 只能指向 #片段、cid: 或 data:image/；不能有 expression(、@import、反斜線跳脫、註解）。
  attributeName、on*、formaction、srcdoc、xlink:*（安全網址的 xlink:href 除外）永遠不保留。
- 註解、<!…> 宣告與處理指令一律丟掉；文字與屬性值重新跳脫。
"""

from __future__ import annotations

import logging
import re
from html.parser import HTMLParser

log = logging.getLogger(__name__)

HTML_ELEMENTS = frozenset(
    (
        "a abbr address article aside b bdi bdo big blockquote br caption center cite code col "
        "colgroup dd del details dfn div dl dt em figcaption figure font footer h1 h2 h3 h4 h5 h6 "
        "header hr i img ins kbd li main mark nav ol p pre q rp rt ruby s samp section small span "
        "strike strong sub summary sup table tbody td tfoot th thead time tr tt u ul var wbr"
    ).split()
)
SVG_ELEMENTS = frozenset(
    (
        "svg g path rect circle ellipse line polyline polygon text tspan defs lineargradient "
        "radialgradient stop clippath mask pattern title desc image"
    ).split()
)
ALLOWED_ELEMENTS = HTML_ELEMENTS | SVG_ELEMENTS
VOID_ELEMENTS = frozenset({"br", "hr", "img", "col", "wbr"})
DROP_WITH_CONTENT = frozenset(
    (
        "script style template iframe noscript noembed noframes textarea select option xmp "
        "plaintext foreignobject handler"
    ).split()
)
GLOBAL_ATTRS = frozenset({"class", "id", "title", "lang", "dir", "style"})
_CELL = frozenset({"colspan", "rowspan", "align", "valign", "width", "height", "headers", "nowrap"})
_ALIGN_ONLY = frozenset({"align"})
_ROW = frozenset({"align", "valign"})
_COLUMN = frozenset({"span", "width", "align", "valign"})
TAG_ATTRS: dict[str, frozenset[str]] = {
    "a": frozenset({"href", "name"}),
    "img": frozenset({"src", "alt", "width", "height", "align"}),
    "ol": frozenset({"start", "type", "reversed"}),
    "ul": frozenset({"type"}),
    "li": frozenset({"value"}),
    "td": _CELL,
    "th": _CELL | {"scope", "abbr"},
    "table": frozenset({"border", "cellpadding", "cellspacing", "width", "align", "summary"}),
    "tr": _ROW,
    "thead": _ROW,
    "tbody": _ROW,
    "tfoot": _ROW,
    "col": _COLUMN,
    "colgroup": _COLUMN,
    "details": frozenset({"open"}),
    "time": frozenset({"datetime"}),
    "blockquote": frozenset({"cite"}),
    "q": frozenset({"cite"}),
    "del": frozenset({"cite", "datetime"}),
    "ins": frozenset({"cite", "datetime"}),
    "font": frozenset({"color", "size", "face"}),
    "hr": frozenset({"width", "size", "align", "noshade"}),
    "image": frozenset({"href", "xlink:href", "preserveaspectratio"}),
    **{tag: _ALIGN_ONLY for tag in ("div", "p", "h1", "h2", "h3", "h4", "h5", "h6")},
}
SVG_ATTRS = frozenset(
    (
        "viewbox width height x y x1 y1 x2 y2 cx cy r rx ry d points fill fill-opacity fill-rule "
        "stroke stroke-width stroke-opacity stroke-linecap stroke-linejoin stroke-dasharray "
        "stroke-dashoffset stroke-miterlimit opacity transform font-family font-size font-weight "
        "font-style text-anchor dominant-baseline alignment-baseline letter-spacing word-spacing "
        "dx dy rotate textlength lengthadjust offset stop-color stop-opacity gradientunits "
        "gradienttransform spreadmethod fx fy clip-path clip-rule mask clippathunits maskunits "
        "maskcontentunits patternunits patterncontentunits patterntransform preserveaspectratio "
        "xmlns version visibility display color shape-rendering text-rendering vector-effect "
        "paint-order"
    ).split()
)
CSS_CHECKED = SVG_ATTRS | {"style"}
URL_ATTRS = frozenset({"href", "src", "cite", "xlink:href"})
_LINK_SCHEMES = frozenset({"http", "https", "mailto", "file", "cid"})
_SRC_SCHEMES = frozenset({"http", "https", "file", "cid"})
_DATA_IMAGE = re.compile(r"^data:image/(?:png|jpeg|jpg|gif|webp|bmp|svg\+xml)[;,]")
_SCHEME = re.compile(r"^([a-z][a-z0-9+.\-]*):")
_STRIP = re.compile(r"[\x00-\x20\x7f]+")
_CSS_URL = re.compile(r"url\(\s*(['\"]?)([^'\")]*)\1\s*\)")
_CSS_BANNED = (
    "\\",
    "/*",
    "expression(",
    "@import",
    "javascript:",
    "vbscript:",
    "behavior",
    "-moz-binding",
    "image-set(",
    "src(",
)
_TEXT_ESC = str.maketrans({"&": "&amp;", "<": "&lt;", ">": "&gt;"})
_ATTR_ESC = str.maketrans({"&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;"})


def _image_only_url(v: str) -> bool:
    return v.startswith("cid:") or bool(_DATA_IMAGE.match(v))


def safe_url(tag: str, name: str, value: str) -> bool:
    """網址屬性的檢查（先去掉瀏覽器會忽略的空白與控制字元再判斷協定）。"""
    v = _STRIP.sub("", value).lower()
    if tag == "image":
        return _image_only_url(v)
    m = _SCHEME.match(v)
    if m is None:
        return True  # 相對路徑或 #片段
    scheme = m.group(1)
    if name == "src":
        return scheme in _SRC_SCHEMES or bool(_DATA_IMAGE.match(v))
    return scheme in _LINK_SCHEMES


def safe_css(value: str) -> bool:
    """style 與 SVG 外觀屬性：url() 只能指向 #片段、cid: 或 data:image/，不能有危險語法。"""
    low = value.lower()
    if any(b in low for b in _CSS_BANNED):
        return False
    targets = _CSS_URL.findall(low)
    if low.count("url(") != len(targets):
        return False  # 有寫不完整、無法判斷的 url(
    return all(t.strip().startswith("#") or _image_only_url(t.strip()) for _q, t in targets)


def _allowed_attrs(tag: str) -> frozenset[str]:
    extra = SVG_ATTRS if tag in SVG_ELEMENTS else frozenset()
    return GLOBAL_ATTRS | TAG_ATTRS.get(tag, frozenset()) | extra


def filter_attrs(tag: str, attrs: list[tuple[str, str | None]]) -> str | None:
    """保留下來的屬性字串；SVG image 沒有安全的圖片網址時回傳 None（整個元素拿掉）。"""
    allowed = _allowed_attrs(tag)
    parts: list[str] = []
    has_image = False
    for name, value in attrs:
        if name not in allowed:
            continue
        if value is None:
            parts.append(f" {name}")
            continue
        if name in URL_ATTRS and not safe_url(tag, name, value):
            continue
        if name in CSS_CHECKED and not safe_css(value):
            continue
        has_image = has_image or name in ("href", "xlink:href")
        parts.append(f' {name}="{value.translate(_ATTR_ESC)}"')
    if tag == "image" and not has_image:
        return None
    return "".join(parts)


class _Sanitizer(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.skip_tag: str | None = None  # 正在連內容一起略過的元素
        self.skip_depth = 0

    def _skip_from(self, tag: str) -> None:
        self.skip_tag, self.skip_depth = tag, 1

    def _start(self, tag: str, attrs: list[tuple[str, str | None]], self_closing: bool) -> None:
        if self.skip_tag is not None:
            if tag == self.skip_tag and not self_closing:
                self.skip_depth += 1  # 同名元素巢狀時要配對到最外層的結束標籤
            return
        if tag in DROP_WITH_CONTENT:
            if not self_closing:
                self._skip_from(tag)
            return
        if tag not in ALLOWED_ELEMENTS:
            return  # 不認得的元素：拿掉標籤、保留內容
        kept = filter_attrs(tag, attrs)
        if kept is None:
            if not self_closing:
                self._skip_from(tag)
            return
        self.out.append(f"<{tag}{kept}{'/>' if self_closing else '>'}")

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(tag, attrs, False)

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self._start(tag, attrs, True)

    def handle_endtag(self, tag: str) -> None:
        if self.skip_tag is not None:
            if tag == self.skip_tag:
                self.skip_depth -= 1
                if self.skip_depth == 0:
                    self.skip_tag = None
            return
        if tag in ALLOWED_ELEMENTS and tag not in VOID_ELEMENTS:
            self.out.append(f"</{tag}>")

    def handle_data(self, data: str) -> None:
        if self.skip_tag is None:
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
