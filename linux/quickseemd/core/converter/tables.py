"""表格資料：CSV／TSV 讀寫、JSON 與表格互轉、HTML 表格（對應原版 CVDataConverter）。"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from .. import errors, naming
from . import textcodec, xmldom
from .structured import Num, Obj, Value, cell_text

# Swift 的 .whitespaces：Tab 加上 Unicode Zs 類空白
_WHITESPACES = "\t" + "".join(
    chr(c) for c in (0x20, 0xA0, 0x1680, *range(0x2000, 0x200B), 0x202F, 0x205F, 0x3000)
)
_NUMERIC = re.compile(r"-?(0|[1-9]\d*)(\.\d+)?([eE][+-]?\d+)?")


@dataclass(frozen=True)
class Table:
    name: str
    rows: tuple[tuple[str, ...], ...]


# ---------------------------------------------------------------- 讀取


def detect_delimiter(text: str) -> str:
    """CSV 有時用分號（歐洲 Excel）或 Tab：看第一行（引號外）哪一種最多，同數時偏好逗號。"""
    counts = {",": 0, ";": 0, "\t": 0}
    in_quotes = False
    for ch in text:
        if ch == '"':
            in_quotes = not in_quotes
        if not in_quotes and ch in "\n\r":
            break
        if not in_quotes and ch in counts:
            counts[ch] += 1
    best = max(counts, key=lambda k: counts[k])  # 同數時取先出現的（逗號優先）
    return best if counts[best] > 0 else ","


class _RowBuilder:
    def __init__(self) -> None:
        self.rows: list[tuple[str, ...]] = []
        self.row: list[str] = []
        self.field: list[str] = []
        self.quoted = False

    def end_field(self) -> None:
        self.row.append("".join(self.field))
        self.field = []
        self.quoted = False

    def end_row(self) -> None:
        self.end_field()
        self.rows.append(tuple(self.row))
        self.row = []


def parse_delimited(text: str, delimiter: str) -> tuple[tuple[str, ...], ...]:
    """RFC 4180：引號只在欄位開頭生效、"" 是引號、引號內可換行、略過開頭 BOM。"""
    b = _RowBuilder()
    in_quotes = False
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if in_quotes:
            if c == '"':
                if i + 1 < n and text[i + 1] == '"':
                    b.field.append('"')
                    i += 2
                    continue
                in_quotes = False
            else:
                b.field.append(c)
        elif c == '"' and not b.field and not b.quoted:
            in_quotes = b.quoted = True
        elif c == delimiter:
            b.end_field()
        elif c in "\r\n":
            b.end_row()
            if c == "\r" and i + 1 < n and text[i + 1] == "\n":
                i += 1
        elif c == "\N{ZERO WIDTH NO-BREAK SPACE}" and not b.rows and not b.row and not b.field:
            pass  # 略過 BOM
        else:
            b.field.append(c)
        i += 1
    if b.field or b.row or b.quoted:
        b.end_row()
    rows = b.rows
    while rows and all(not cell for cell in rows[-1]):
        rows.pop()
    return tuple(rows)


def read_delimited(path: Path, tsv: bool, forced: str | None) -> Table:
    decoded = textcodec.decode_file(path, forced)
    delimiter = "\t" if tsv else detect_delimiter(decoded.text)
    return Table(naming.base_name(path), parse_delimited(decoded.text, delimiter))


# ---------------------------------------------------------------- 表格 ↔ JSON


def _objects_table(items: tuple, name: str) -> Table:
    headers: list[str] = []
    seen: set[str] = set()
    for item in items:
        for k, _ in item.members:
            if k not in seen:
                seen.add(k)
                headers.append(k)
    rows = [tuple(headers)]
    for item in items:
        values = dict(item.members)  # 重複鍵時取最後一個（同原版字典）
        rows.append(tuple(cell_text(values[h]) if h in values else "" for h in headers))
    return Table(name, tuple(rows))


def table_from_json(value: Value, name: str) -> Table:
    if not isinstance(value, tuple):
        if isinstance(value, Obj):
            for key, member in value.members:
                if isinstance(member, tuple):
                    return table_from_json(member, key)
        raise errors.invalid_data("JSON 的最外層必須是陣列（例如 [{…}, {…}]）才能轉成表格。")
    if not value:
        return Table(name, ())
    if all(isinstance(v, Obj) for v in value):
        return _objects_table(value, name)
    if all(isinstance(v, tuple) for v in value):
        return Table(name, tuple(tuple(cell_text(c) for c in v) for v in value))
    return Table(name, (("value",), *((cell_text(v),) for v in value)))


def looks_numeric(s: str) -> bool:
    """符合 JSON 數字語法、且不是前導 0 的整數（0912… 這類編號維持文字）。"""
    return bool(s) and len(s) < 20 and _NUMERIC.fullmatch(s) is not None


def infer_value(s: str) -> Value:
    if s in ("true", "TRUE"):
        return True
    if s in ("false", "FALSE"):
        return False
    if looks_numeric(s):
        return Num(s)
    return s


def _header_keys(header: Sequence[str]) -> list[str]:
    keys: list[str] = []
    used: dict[str, int] = {}
    for i, raw in enumerate(header):
        key = raw.strip(_WHITESPACES) or f"column{i + 1}"
        if key in used:
            used[key] += 1
            key = f"{key}_{used[key]}"
        else:
            used[key] = 1
        keys.append(key)
    return keys


def json_from_table(table: Table) -> Value:
    """第一列當欄位名稱；看起來像數字／布林的值自動轉型。"""
    if not table.rows:
        return ()
    keys = _header_keys(table.rows[0])
    objects = []
    for row in table.rows[1:]:
        members = [(k, infer_value(row[i] if i < len(row) else "")) for i, k in enumerate(keys)]
        members += [(f"column{j + 1}", infer_value(row[j])) for j in range(len(keys), len(row))]
        objects.append(Obj(tuple(members)))
    return tuple(objects)


# ---------------------------------------------------------------- 輸出


def _csv_field(value: str, delimiter: str) -> str:
    needs = (
        delimiter in value
        or '"' in value
        or "\n" in value
        or "\r" in value
        or value.startswith(" ")
        or value.endswith(" ")
    )
    return '"' + value.replace('"', '""') + '"' if needs else value


def delimited(table: Table, delimiter: str) -> str:
    return "".join(
        delimiter.join(_csv_field(cell, delimiter) for cell in row) + "\n" for row in table.rows
    )


_HTML_STYLE = """:root { color-scheme: light dark; }
body { font: 14px/1.5 -apple-system, "PingFang TC", "Helvetica Neue", "Noto Sans CJK TC", sans-serif; margin: 24px; color: #1f2328; background: #fff; }
h1 { font-size: 20px; margin: 0 0 16px; } h2 { font-size: 16px; margin: 28px 0 10px; }
.wrap { overflow: auto; border: 1px solid #d0d7de; border-radius: 10px; }
table { border-collapse: collapse; min-width: 100%; }
th, td { padding: 7px 12px; border-bottom: 1px solid #e5e8eb; text-align: left; white-space: nowrap; }
th { position: sticky; top: 0; background: #f6f8fa; font-weight: 600; }
tbody tr:nth-child(2n) { background: #f9fafb; }
tbody tr:hover { background: #eef4ff; }
td.num { text-align: right; font-variant-numeric: tabular-nums; }
@media (prefers-color-scheme: dark) {
  body { background: #0d1117; color: #e6edf3; }
  .wrap { border-color: #30363d; } th, td { border-color: #21262d; }
  th { background: #161b22; } tbody tr:nth-child(2n) { background: #11161d; } tbody tr:hover { background: #1c2533; }
}"""  # noqa: E501 — 樣式照抄原版（只補 Linux 字型）


def _html_row(row: tuple[str, ...], header: bool) -> str:
    tag = "th" if header else "td"
    cells = []
    for cell in row:
        cls = ' class="num"' if not header and looks_numeric(cell) else ""
        text = xmldom.escape(cell).replace("\n", "<br>")
        cells.append(f"<{tag}{cls}>{text}</{tag}>")
    return "<tr>" + "".join(cells) + "</tr>\n"


def _html_table(table: Table, with_title: bool) -> str:
    parts = [f"<h2>{xmldom.escape(table.name)}</h2>\n"] if with_title else []
    parts.append('<div class="wrap"><table>\n')
    for i, row in enumerate(table.rows):
        if i == 0:
            parts.append("<thead>" + _html_row(row, True) + "</thead>\n")
            continue
        parts.append(("<tbody>" if i == 1 else "") + _html_row(row, False))
    if len(table.rows) > 1:
        parts.append("</tbody>")
    parts.append("</table></div>\n")
    return "".join(parts)


def html_tables(tables: Sequence[Table], title: str) -> str:
    body = "".join(_html_table(t, len(tables) > 1) for t in tables)
    esc = xmldom.escape(title)
    return (
        '<!DOCTYPE html>\n<html lang="zh-Hant">\n<head>\n<meta charset="utf-8">\n'
        '<meta name="viewport" content="width=device-width, initial-scale=1">\n'
        f"<title>{esc}</title>\n<style>\n{_HTML_STYLE}\n</style>\n</head>\n<body>\n"
        f"<h1>{esc}</h1>\n{body}\n</body>\n</html>"
    )
