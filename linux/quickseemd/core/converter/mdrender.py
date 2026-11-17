"""輕量 Markdown → HTML（對應原版 CVMarkdownRenderer，CommonMark／GFM 常用子集）。

標題、段落、強調、刪除線、行內程式碼、程式碼區塊、引言、巢狀清單、待辦清單、表格、
分隔線、連結、圖片、自動連結。行內語法在 mdinline.py。
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from .mdinline import Inline, escape_attr, escape_html, trim

CHECKED = "\N{BALLOT BOX WITH CHECK}\N{VARIATION SELECTOR-15} "
UNCHECKED = "\N{BALLOT BOX} "

SCREEN_CSS = """:root { color-scheme: light dark; }
body { margin: 0; background: #fff; color: #1f2328; font: 16px/1.65 -apple-system, BlinkMacSystemFont, "PingFang TC", "Helvetica Neue", "Microsoft JhengHei", "Noto Sans CJK TC", sans-serif; }
.markdown-body { max-width: 860px; margin: 0 auto; padding: 40px 32px 80px; }
h1, h2, h3, h4, h5, h6 { line-height: 1.3; margin: 1.6em 0 0.6em; font-weight: 650; }
h1 { font-size: 2em; border-bottom: 1px solid #d1d9e0; padding-bottom: .3em; }
h2 { font-size: 1.5em; border-bottom: 1px solid #d1d9e0; padding-bottom: .3em; }
h3 { font-size: 1.25em; }
p, ul, ol, blockquote, pre, table { margin: 0 0 1em; }
a { color: #0969da; text-decoration: none; } a:hover { text-decoration: underline; }
code { font: 0.875em/1.5 "SF Mono", Menlo, Consolas, "Noto Sans Mono CJK TC", monospace; background: rgba(129,139,152,.15); padding: .15em .4em; border-radius: 6px; }
pre { background: #f6f8fa; padding: 14px 16px; border-radius: 10px; overflow: auto; }
pre code { background: none; padding: 0; font-size: 0.85em; }
blockquote { color: #59636e; border-left: 4px solid #d1d9e0; padding: 0 1em; margin-left: 0; }
table { border-collapse: collapse; display: block; overflow: auto; }
th, td { border: 1px solid #d1d9e0; padding: 6px 13px; }
th { background: #f6f8fa; font-weight: 600; }
tr:nth-child(2n) td { background: #f6f8fa80; }
hr { border: 0; height: 2px; background: #d1d9e0; margin: 2em 0; }
img { max-width: 100%; }
li > ul, li > ol { margin: .25em 0 0; }
li + li { margin-top: .2em; }
li.task { list-style: none; margin-left: -1.2em; }
@media (prefers-color-scheme: dark) {
  body { background: #0d1117; color: #e6edf3; }
  h1, h2, th, td, blockquote { border-color: #3d444d; }
  hr { background: #3d444d; }
  a { color: #4493f8; }
  pre, th { background: #151b23; }
  tr:nth-child(2n) td { background: #151b2380; }
  blockquote { color: #9198a1; }
}"""  # noqa: E501 — 樣式照抄原版（只補 Linux 字型）

IMPORT_CSS = """body { font-family: "Helvetica Neue", "PingFang TC", "Noto Sans CJK TC", sans-serif; font-size: 11pt; line-height: 1.5; color: #1f2328; }
h1 { font-size: 22pt; margin: 14pt 0 8pt; } h2 { font-size: 17pt; margin: 12pt 0 6pt; } h3 { font-size: 14pt; margin: 10pt 0 4pt; }
h4, h5, h6 { font-size: 12pt; margin: 8pt 0 4pt; }
p { margin: 0 0 8pt; }
li > ul, li > ol { margin: 0; }
code { font-family: Menlo, "Noto Sans Mono CJK TC", monospace; font-size: 9.5pt; color: #b3261e; }
pre { font-family: Menlo, "Noto Sans Mono CJK TC", monospace; font-size: 9.5pt; background-color: #f3f4f6; padding: 8pt; margin: 0 0 8pt; }
pre code { color: #1f2328; }
blockquote { color: #555555; margin: 0 0 8pt 12pt; padding-left: 8pt; border-left: 3px solid #cccccc; }
table { border-collapse: collapse; margin: 0 0 8pt; }
th, td { border: 1px solid #bbbbbb; padding: 4pt 8pt; }
th { background-color: #eeeeee; }
a { color: #0969da; }"""  # noqa: E501 — 樣式照抄原版（只補 Linux 字型）

_REFERENCE = re.compile(r"^ {0,3}\[([^\]]+)\]:\s*<?(\S+?)>?(?:\s+[\"'(](.+)[\"')])?\s*$")
_TRAILING_HASHES = re.compile(r"\s+#+\s*$")
_HTML_TAGS = (
    "div table pre p ul ol dl section article header footer nav details summary figure "
    "blockquote h1 h2 h3 h4 h5 h6 hr center iframe video audio !--"
).split()


# ---------------------------------------------------------------- 行的判斷


def indent_width(line: str) -> int:
    w = 0
    for c in line:
        if c == " ":
            w += 1
        elif c == "\t":
            w += 4 - (w % 4)
        else:
            break
    return w


def strip_indent(line: str, count: int) -> str:
    removed = idx = 0
    while idx < len(line) and removed < count:
        c = line[idx]
        if c == " ":
            removed += 1
        elif c == "\t":
            removed += 4 - (removed % 4)
        else:
            break
        idx += 1
    return line[idx:]


def is_blank(s: str) -> bool:
    return not trim(s)


def is_horizontal_rule(line: str) -> bool:
    t = trim(line)
    if not t or t[0] not in "-*_":
        return False
    chars = t.replace(" ", "")
    return len(chars) >= 3 and all(c == t[0] for c in chars)


@dataclass(frozen=True)
class ListMarker:
    ordered: bool
    start: int
    content_indent: int
    content: str


def _bullet_marker(t: str, indent: int) -> ListMarker | None:
    rest = t[1:]
    if not rest:
        return ListMarker(False, 1, indent + 2, "")
    if rest[0] not in " \t":
        return None
    spaces = len(rest) - len(rest.lstrip(" "))
    skip = min(max(spaces, 1), 4)
    return ListMarker(False, 1, indent + 1 + skip, rest[skip:])


def _ordered_marker(t: str, indent: int) -> ListMarker | None:
    m = re.match(r"[0-9]{1,9}", t)
    if not m or (len(t) > m.end() and t[m.end()] in "0123456789"):
        return None
    digits = m.group(0)
    after = t[len(digits) :]
    if not after or after[0] not in ".)":
        return None
    rest = after[1:]
    if not rest:
        return ListMarker(True, int(digits), indent + len(digits) + 2, "")
    if rest[0] != " ":
        return None
    spaces = len(rest) - len(rest.lstrip(" "))
    skip = min(spaces, 4)
    return ListMarker(True, int(digits), indent + len(digits) + 1 + skip, rest[skip:])


def list_marker(line: str) -> ListMarker | None:
    indent = indent_width(line)
    t = line.lstrip(" \t")
    if t and t[0] in "-*+":
        return _bullet_marker(t, indent)
    return _ordered_marker(t, indent)


def heading_level(line: str) -> tuple[int, str] | None:
    t = line.lstrip(" ")
    if indent_width(line) >= 4 or not t.startswith("#"):
        return None
    hashes = len(t) - len(t.lstrip("#"))
    if hashes > 6:
        return None
    rest = t[hashes:]
    if rest and rest[0] not in " \t":
        return None
    text = trim(rest)
    m = _TRAILING_HASHES.search(text)
    if m:
        text = text[: m.start()]
    elif all(c == "#" for c in text):
        text = ""
    return hashes, text


def fence_info(line: str) -> tuple[str, str] | None:
    if indent_width(line) >= 4:
        return None
    t = trim(line)
    for ch in ("`", "~"):
        if t.startswith(ch * 3):
            count = len(t) - len(t.lstrip(ch))
            info = trim(t[count:])
            if ch == "`" and "`" in info:
                return None
            return ch * count, info
    return None


def split_table_row(line: str) -> list[str]:
    t = trim(line)
    if t.startswith("|"):
        t = t[1:]
    if t.endswith("|") and not t.endswith("\\|"):
        t = t[:-1]
    cells: list[str] = []
    current: list[str] = []
    in_code = escaped = False
    for c in t:
        if escaped:
            current.append("|" if c == "|" else "\\" + c)
            escaped = False
            continue
        if c == "\\":
            escaped = True
            continue
        if c == "`":
            in_code = not in_code
        if c == "|" and not in_code:
            cells.append("".join(current))
            current = []
            continue
        current.append(c)
    if escaped:
        current.append("\\")
    cells.append("".join(current))
    return [trim(c) for c in cells]


def is_table_separator(line: str) -> bool:
    t = trim(line)
    if "-" not in t or not ("|" in t or t.startswith(":") or t.startswith("-")):
        return False
    cells = split_table_row(t)
    return bool(cells) and all(
        c and all(ch in "-:" for ch in c) and "-" in c for c in (trim(x) for x in cells)
    )


def html_block_start(t: str) -> bool:
    lower = t.lower()
    return any(lower.startswith("<" + tag) or lower.startswith("</" + tag) for tag in _HTML_TAGS)


def starts_block(line: str) -> bool:
    """是否開始一個新的區塊（用來結束段落）。"""
    if heading_level(line) or fence_info(line) or is_horizontal_rule(line):
        return True
    t = trim(line)
    if t.startswith(">"):
        return True
    m = list_marker(line)
    if m and m.content and indent_width(line) < 4:
        return not m.ordered or m.start == 1
    return t.startswith("<") and html_block_start(t)


def slug(text: str) -> str:
    out = []
    for c in text.lower():
        if c.isalpha() or c.isnumeric():
            out.append(c)
        elif c in " -":
            out.append("-")
    return escape_attr("".join(out))


# ---------------------------------------------------------------- 區塊


class Renderer:
    def __init__(self, references: dict[str, tuple[str, str | None]]) -> None:
        self.inline = Inline(references)

    def blocks(self, lines: list[str]) -> str:
        html: list[str] = []
        i = 0
        while i < len(lines):
            line = lines[i]
            if is_blank(line):
                i += 1
                continue
            for handler in (
                self._fence,
                self._indented,
                self._heading,
                self._rule,
                self._quote,
                self._list,
                self._html,
                self._table,
            ):
                result = handler(lines, i)
                if result is not None:
                    html.append(result[0])
                    i = result[1]
                    break
            else:
                text, i = self._paragraph(lines, i)
                html.append(text)
        return "\n".join(html)

    def _fence(self, lines: list[str], i: int) -> tuple[str, int] | None:
        fence = fence_info(lines[i])
        if fence is None:
            return None
        marker, info = fence
        indent = indent_width(lines[i])
        body: list[str] = []
        i += 1
        while i < len(lines):
            t = trim(lines[i])
            if t.startswith(marker) and all(c == marker[0] for c in t):
                i += 1
                break
            body.append(strip_indent(lines[i], indent))
            i += 1
        words = [w for w in info.split(" ") if w]
        cls = f' class="language-{escape_html(words[0])}"' if words else ""
        return f"<pre><code{cls}>{escape_html(chr(10).join(body))}\n</code></pre>", i

    def _indented(self, lines: list[str], i: int) -> tuple[str, int] | None:
        if indent_width(lines[i]) < 4:
            return None
        body: list[str] = []
        while i < len(lines) and (indent_width(lines[i]) >= 4 or is_blank(lines[i])):
            body.append(strip_indent(lines[i], 4))
            i += 1
        while body and is_blank(body[-1]):
            body.pop()
        return f"<pre><code>{escape_html(chr(10).join(body))}\n</code></pre>", i

    def _heading(self, lines: list[str], i: int) -> tuple[str, int] | None:
        found = heading_level(lines[i])
        if found is None:
            return None
        level, text = found
        return f'<h{level} id="{slug(text)}">{self.inline.render(text)}</h{level}>', i + 1

    def _rule(self, lines: list[str], i: int) -> tuple[str, int] | None:
        return ("<hr>", i + 1) if is_horizontal_rule(lines[i]) else None

    def _quote(self, lines: list[str], i: int) -> tuple[str, int] | None:
        if not trim(lines[i]).startswith(">"):
            return None
        inner: list[str] = []
        while i < len(lines):
            # 原版只略過空格；行首是 Tab 時會卡住，這裡一併略過 Tab
            t = lines[i].lstrip(" \t")
            if t.startswith(">"):
                rest = t[1:]
                inner.append(rest[1:] if rest.startswith(" ") else rest)
            elif (
                not is_blank(lines[i])
                and inner
                and not is_blank(inner[-1])
                and not starts_block(lines[i])
            ):
                inner.append(lines[i])  # 延續段落
            else:
                break
            i += 1
        return f"<blockquote>\n{self.blocks(inner)}\n</blockquote>", i

    def _list(self, lines: list[str], i: int) -> tuple[str, int] | None:
        first = list_marker(lines[i])
        if first is None:
            return None
        return self._render_list(lines, i, first)

    def _list_item(self, lines: list[str], i: int, base: int, marker: ListMarker) -> tuple:
        """收集一個清單項目的各行；回傳（各行, 下一行, 是否鬆散, 結尾是否有空行）。"""
        item = [marker.content]
        content_indent = marker.content_indent
        blank_pending = loose = False
        while i < len(lines):
            line = lines[i]
            if is_blank(line):
                blank_pending = True
                item.append("")
                i += 1
                continue
            indent = indent_width(line)
            if indent >= content_indent or (indent > base and list_marker(line) is not None):
                stripped = strip_indent(line, min(indent, content_indent))
                if blank_pending and list_marker(stripped) is None:
                    loose = True
                blank_pending = False
                item.append(stripped)
                i += 1
            elif not blank_pending and list_marker(line) is None and not starts_block(line):
                item.append(trim(line))  # 延續行
                i += 1
            else:
                break
        while item and is_blank(item[-1]):
            item.pop()
        return item, i, loose, blank_pending

    def _render_list(self, lines: list[str], start: int, first: ListMarker) -> tuple[str, int]:
        base = indent_width(lines[start])
        items: list[list[str]] = []
        loose_any = blank_between = False
        i = start
        while i < len(lines):
            marker = list_marker(lines[i])
            indent = indent_width(lines[i])
            if marker is None or marker.ordered != first.ordered or not base <= indent < base + 4:
                break
            item, i, loose, blank_pending = self._list_item(lines, i + 1, base, marker)
            nxt = list_marker(lines[i]) if blank_pending and i < len(lines) else None
            if nxt is not None and nxt.ordered == first.ordered:
                blank_between = True
            items.append(item)
            loose_any = loose_any or loose
        is_loose = blank_between or loose_any
        if first.ordered:
            html = f'<ol start="{first.start}">' if first.start != 1 else "<ol>"
        else:
            html = "<ul>"
        for item in items:
            html += self._list_html(item, is_loose)
        return html + ("\n</ol>" if first.ordered else "\n</ul>"), i

    def _list_html(self, item: list[str], is_loose: bool) -> str:
        content = list(item)
        prefix = cls = ""
        if content:
            lower = content[0].lower()
            if lower.startswith(("[ ] ", "[x] ")) or lower in ("[ ]", "[x]"):
                cls = ' class="task"'
                prefix = CHECKED if lower.startswith("[x]") else UNCHECKED
                content[0] = trim(content[0][3:])
        if is_loose:
            inner = self.blocks(content)
        else:
            # 緊湊清單：第一段不包 <p>
            rest_start = len(content)
            for n, line in enumerate(content):
                if n > 0 and (starts_block(line) or list_marker(line) is not None):
                    rest_start = n
                    break
            inner = self.paragraph(content[:rest_start])
            if rest_start < len(content):
                inner += "\n" + self.blocks(content[rest_start:])
        return f"\n<li{cls}>{prefix}{inner}</li>"

    def _html(self, lines: list[str], i: int) -> tuple[str, int] | None:
        t = trim(lines[i])
        if not (t.startswith("<") and html_block_start(t)):
            return None
        block: list[str] = []
        while i < len(lines) and not is_blank(lines[i]):
            block.append(lines[i])
            i += 1
        return "\n".join(block), i

    def _table(self, lines: list[str], i: int) -> tuple[str, int] | None:
        line = lines[i]
        if "|" not in line or i + 1 >= len(lines) or not is_table_separator(lines[i + 1]):
            return None
        header = split_table_row(line)
        aligns = [_align(c) for c in split_table_row(lines[i + 1])]
        rows: list[list[str]] = []
        i += 2
        while i < len(lines) and not is_blank(lines[i]) and not starts_block(lines[i]):
            rows.append(split_table_row(lines[i]))
            i += 1

        def attr(n: int) -> str:
            return aligns[n] if n < len(aligns) else ""

        out = ["<table>\n<thead>\n<tr>"]
        out += [f"<th{attr(n)}>{self.inline.render(c)}</th>" for n, c in enumerate(header)]
        out.append("</tr>\n</thead>\n<tbody>\n")
        for row in rows:
            cells = [row[n] if n < len(row) else "" for n in range(len(header))]
            tds = "".join(f"<td{attr(n)}>{self.inline.render(c)}</td>" for n, c in enumerate(cells))
            out.append(f"<tr>{tds}</tr>\n")
        out.append("</tbody>\n</table>")
        return "".join(out), i

    def _paragraph(self, lines: list[str], i: int) -> tuple[str, int]:
        para = [lines[i]]
        i += 1
        level = 0
        while i < len(lines):
            line = lines[i]
            if is_blank(line):
                break
            t = trim(line)
            if t and all(c == "=" for c in t):
                level, i = 1, i + 1
                break
            if t and all(c == "-" for c in t) and len(t) >= 2:
                level, i = 2, i + 1
                break
            if starts_block(line):
                break
            if "|" in line and i + 1 < len(lines) and is_table_separator(lines[i + 1]):
                break
            para.append(line)
            i += 1
        if level:
            text = " ".join(trim(p) for p in para)
            return f'<h{level} id="{slug(text)}">{self.inline.render(text)}</h{level}>', i
        return f"<p>{self.paragraph(para)}</p>", i

    def paragraph(self, lines: list[str]) -> str:
        out: list[str] = []
        for n, raw in enumerate(lines):
            line = raw.lstrip(" \t")
            hard = False
            if n < len(lines) - 1:
                if line.endswith("  "):
                    hard = True
                elif line.endswith("\\"):
                    hard, line = True, line[:-1]
            out.append(self.inline.render(trim(line)) + ("<br>" if hard else ""))
        return "\n".join(out)


def _align(cell: str) -> str:
    c = trim(cell)
    if c.startswith(":") and c.endswith(":"):
        return ' style="text-align:center"'
    if c.endswith(":"):
        return ' style="text-align:right"'
    if c.startswith(":"):
        return ' style="text-align:left"'
    return ""


# ---------------------------------------------------------------- 對外介面


def _strip_front_matter(lines: list[str]) -> list[str]:
    if not lines or trim(lines[0]) != "---":
        return lines
    for end in range(1, len(lines)):
        if trim(lines[end]) in ("---", "..."):
            return lines[end + 1 :]
    return lines


def _collect_references(lines: list[str]) -> tuple[list[str], dict[str, tuple[str, str | None]]]:
    refs: dict[str, tuple[str, str | None]] = {}
    kept: list[str] = []
    in_fence = False
    for line in lines:
        t = trim(line)
        if t.startswith("```") or t.startswith("~~~"):
            in_fence = not in_fence
        m = None if in_fence else _REFERENCE.match(line)
        if m:
            refs[m.group(1).lower()] = (m.group(2), m.group(3))
            continue
        kept.append(line)
    return kept, refs


def render_body(markdown: str) -> str:
    """只產生 <body> 內容。"""
    normalized = markdown.replace("\r\n", "\n").replace("\r", "\n")
    lines, refs = _collect_references(_strip_front_matter(normalized.split("\n")))
    return Renderer(refs).blocks(lines)


def render_document(markdown: str, title: str, for_import: bool) -> str:
    """完整 HTML 文件；for_import 時用匯入 Word／PDF 的樣式（不含深色模式）。"""
    return (
        '<!DOCTYPE html>\n<html lang="zh-Hant">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{escape_html(title)}</title>\n<style>\n"
        f"{IMPORT_CSS if for_import else SCREEN_CSS}\n</style>\n</head>\n<body>\n"
        f'<article class="markdown-body">\n{render_body(markdown)}\n</article>\n'
        "</body>\n</html>"
    )


def first_heading(markdown: str) -> str | None:
    """取第一個標題作為文件標題。"""
    for line in re.split(
        r"\r\n|[\n\r\N{LINE SEPARATOR}\N{PARAGRAPH SEPARATOR}\x85\x0b\x0c]", markdown
    ):
        found = heading_level(line)
        if found and found[1]:
            return found[1].replace("*", "").replace("`", "")
    return None
