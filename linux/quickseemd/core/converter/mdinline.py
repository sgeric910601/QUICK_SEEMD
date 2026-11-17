"""Markdown 行內語法（對應原版 CVMarkdownRenderer.renderInline）。

跳脫字元、行內程式碼、圖片與連結（行內式與參考式）、<網址>／<email> 自動連結、
裸網址、行內 HTML 標籤原樣保留、* _ 強調（字中間的 _ 不算）、~~刪除線~~。
"""

from __future__ import annotations

# Swift 的 .whitespaces：Tab 加上 Unicode Zs 類空白
_WHITESPACES = "\t" + "".join(
    chr(c) for c in (0x20, 0xA0, 0x1680, *range(0x2000, 0x200B), 0x202F, 0x205F, 0x3000)
)
_ESCAPABLE = set("\\`*_{}[]()#+-.!|~<>\"'")
_URL_TRAILING = set(".,;:!?。，、")


def trim(s: str) -> str:
    return s.strip(_WHITESPACES)


def escape_html(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def escape_attr(s: str) -> str:
    return escape_html(s).replace('"', "&quot;")


def _is_word(ch: str) -> bool:
    return ch.isalpha() or ch.isnumeric()


def _match_pair(s: str, start: int, open_ch: str, close_ch: str) -> tuple[str, int] | None:
    if start >= len(s) or s[start] != open_ch:
        return None
    depth = 0
    i = start
    while i < len(s):
        if s[i] == "\\":
            i += 2
            continue
        if s[i] == open_ch:
            depth += 1
        elif s[i] == close_ch:
            depth -= 1
            if depth == 0:
                return s[start + 1 : i], i
        i += 1
    return None


def parse_destination(raw: str) -> tuple[str, str | None]:
    t = trim(raw)
    if t.startswith("<") and ">" in t:
        end = t.index(">")
        rest = trim(t[end + 1 :])
        return t[1:end], (rest[1:-1] if len(rest) >= 2 else None)
    for idx, ch in enumerate(t):
        if ch in " \t":
            rest = trim(t[idx:])
            if len(rest) >= 2 and rest[0] in "\"'(":
                return t[:idx], rest[1:-1]
            return t[:idx], None
    return t, None


def _find_closing(s: str, delim: str, start: int) -> int | None:
    n = len(delim)
    i = start
    while i <= len(s) - n:
        if s[i] == "`":
            end = s.find("`", i + 1)
            if end >= 0:
                i = end + 1
                continue
        if s[i] == "\\":
            i += 2
            continue
        if s.startswith(delim, i) and i > start and not s[i - 1].isspace():
            nxt = i + n
            if nxt < len(s) and s[nxt] == delim[0]:
                i += 1
                continue
            if delim[0] == "_" and nxt < len(s) and _is_word(s[nxt]):
                i += 1
                continue
            return i
        i += 1
    return None


class Inline:
    def __init__(self, references: dict[str, tuple[str, str | None]]) -> None:
        self.references = references

    def render(self, text: str) -> str:
        out: list[str] = []
        i = 0
        steps = (
            self._escape,
            self._code,
            self._link,
            self._angle,
            self._bare_url,
            self._emphasis,
        )
        while i < len(text):
            for step in steps:
                result = step(text, i)
                if result is not None:
                    out.append(result[0])
                    i = result[1]
                    break
            else:
                out.append(escape_html(text[i]))
                i += 1
        return "".join(out)

    def _escape(self, s: str, i: int) -> tuple[str, int] | None:
        if s[i] == "\\" and i + 1 < len(s) and s[i + 1] in _ESCAPABLE:
            return escape_html(s[i + 1]), i + 2
        return None

    def _code(self, s: str, i: int) -> tuple[str, int] | None:
        if s[i] != "`":
            return None
        ticks = len(s[i:]) - len(s[i:].lstrip("`"))
        fence = "`" * ticks
        end = s.find(fence, i + ticks)
        if end < 0:
            return fence, i + ticks
        code = s[i + ticks : end]
        if code.startswith(" ") and code.endswith(" ") and len(code) > 2:
            code = code[1:-1]
        return f"<code>{escape_html(code)}</code>", end + ticks

    def _make_link(self, image: bool, label: str, url: str, title: str | None) -> str:
        title_attr = f' title="{escape_attr(title)}"' if title is not None else ""
        if image:
            return f'<img src="{escape_attr(url)}" alt="{escape_attr(label)}"{title_attr}>'
        return f'<a href="{escape_attr(url)}"{title_attr}>{self.render(label)}</a>'

    def _link(self, s: str, i: int) -> tuple[str, int] | None:
        image = s[i] == "!" and i + 1 < len(s) and s[i + 1] == "["
        if not image and s[i] != "[":
            return None
        bracket = _match_pair(s, i + 1 if image else i, "[", "]")
        if bracket is None:
            return None
        label, close = bracket
        j = close + 1
        if j < len(s) and s[j] == "(":
            paren = _match_pair(s, j, "(", ")")
            if paren is not None:
                url, title = parse_destination(paren[0])
                return self._make_link(image, label, url, title), paren[1] + 1
        key = label
        if j < len(s) and s[j] == "[":
            ref = _match_pair(s, j, "[", "]")
            if ref is not None:
                key = ref[0] or key
                j = ref[1] + 1
        found = self.references.get(key.lower())
        if found is None:
            return None
        return self._make_link(image, label, found[0], found[1]), j

    def _angle(self, s: str, i: int) -> tuple[str, int] | None:
        if s[i] != "<":
            return None
        end = s.find(">", i + 1)
        if end < 0:
            return None
        inside = s[i + 1 : end]
        if inside.startswith(("http://", "https://", "mailto:")):
            return f'<a href="{escape_attr(inside)}">{escape_html(inside)}</a>', end + 1
        if "@" in inside and " " not in inside:
            return f'<a href="mailto:{escape_attr(inside)}">{escape_html(inside)}</a>', end + 1
        if inside and (inside[0].isalpha() or inside[0] in "/!"):
            return f"<{inside}>", end + 1  # 行內 HTML 標籤原樣保留
        return None

    def _bare_url(self, s: str, i: int) -> tuple[str, int] | None:
        if not s.startswith(("https://", "http://"), i):
            return None
        if i > 0 and (_is_word(s[i - 1]) or s[i - 1] in '"='):
            return None
        j = i
        while j < len(s) and not s[j].isspace() and s[j] not in "<)":
            j += 1
        while j > i and s[j - 1] in _URL_TRAILING:
            j -= 1
        url = s[i:j]
        return f'<a href="{escape_attr(url)}">{escape_html(url)}</a>', j

    def _emphasis(self, s: str, i: int) -> tuple[str, int] | None:
        c = s[i]
        if c not in "*_~":
            return None
        run = len(s[i:]) - len(s[i:].lstrip(c))
        if c == "~" and run < 2:
            return "~", i + 1
        n = 2 if c == "~" else min(run, 3)
        if c == "_" and i > 0 and _is_word(s[i - 1]):
            return "_" * run, i + run  # 底線在字中間不算強調（snake_case）
        after = i + n
        close = None
        if after < len(s) and not s[after].isspace():
            close = _find_closing(s, c * n, after)
        if close is None:
            return escape_html(c * run), i + run
        inner = self.render(s[after:close])
        if c == "~":
            html = f"<del>{inner}</del>"
        elif n == 1:
            html = f"<em>{inner}</em>"
        elif n == 2:
            html = f"<strong>{inner}</strong>"
        else:
            html = f"<strong><em>{inner}</em></strong>"
        return html, close + n
