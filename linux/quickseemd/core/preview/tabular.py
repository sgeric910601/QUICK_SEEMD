"""CSV／TSV 表格（對應原版 QSCSV）：自己判斷分隔符號與數字欄，固定表頭、固定列號。

解析規則照原版：引號只有出現在欄位開頭才算開始；引號內的「""」是一個引號；
引號外的 CR 丟掉；超過列數上限後只計數不儲存。
Linux 版補充：最多顯示 500 欄（列數 × 欄數決定頁面大小，欄數不設限會讓記憶體暴增）。
"""

from __future__ import annotations

import re

from . import fmt
from .page import Chip, FileInfo, Notice, Page, escape

MAX_ROWS = 2000
MAX_COLUMNS = 500
SNIFF_LINES = 20
NUMERIC_SAMPLE = 200
CELL_LIMIT = 500
CANDIDATES = (",", "\t", ";", "|")
DELIM_NAMES = {",": "逗號", "\t": "Tab", ";": "分號", "|": "直線 |"}
_ZS = " \t             "
_ZS += "  　"
_NUMBER = re.compile(
    r"[+-]?(?:(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?"
    r"|0[xX](?:[0-9a-fA-F]+\.?[0-9a-fA-F]*|\.[0-9a-fA-F]+)(?:[pP][+-]?\d+)?"
    r"|inf|infinity|nan)",
    re.IGNORECASE,
)


def is_number(s: str) -> bool:
    """與 Swift 的 Double(字串) 相同的判斷（不接受底線與前後空白）。"""
    return bool(_NUMBER.fullmatch(s))


def _sniff_counts(text: str, delim: str) -> list[int]:
    """前 20 行（引號外）每行有幾個分隔符號。"""
    counts: list[int] = []
    current = 0
    in_quote = False
    for m in re.finditer(r'["\n]|' + re.escape(delim), text):
        ch = m.group()
        if ch == '"':
            in_quote = not in_quote
        elif in_quote:
            continue
        elif ch == delim:
            current += 1
        else:
            counts.append(current)
            current = 0
            if len(counts) >= SNIFF_LINES:
                return counts
    if current > 0:
        counts.append(current)
    return counts


def detect_delimiter(text: str, ext: str) -> str:
    if ext in ("tsv", "tab"):
        return "\t"
    best, best_score = ",", -1
    for d in CANDIDATES:
        counts = _sniff_counts(text, d)
        if not counts or counts[0] <= 0:
            continue
        score = sum(1 for c in counts if c == counts[0]) * 100 + counts[0]
        if score > best_score:
            best, best_score = d, score
    return best


class _Parser:
    """引號規則與原版相同的串流解析（以正規表示式跳到下一個特殊字元）。"""

    def __init__(self, text: str, delim: str, max_rows: int) -> None:
        self.text = text
        self.special = re.compile(r'["\r\n]|' + re.escape(delim))
        self.delim = delim
        self.max_rows = max_rows
        self.rows: list[list[str]] = []
        self.row: list[str] = []
        self.field: list[str] = []
        self.total = 0
        self.storing = True

    def end_field(self) -> None:
        if self.storing:
            self.row.append("".join(self.field))
        self.field = []

    def end_row(self) -> None:
        self.end_field()
        if self.storing:
            self.rows.append(self.row)
        self.row = []
        self.total += 1
        if self.total >= self.max_rows:
            self.storing = False

    def add(self, chunk: str) -> None:
        if chunk and self.storing:
            self.field.append(chunk)

    def quoted(self, i: int) -> int:
        """引號內：回傳結束引號之後的位置。"""
        text = self.text
        while True:
            q = text.find('"', i)
            if q < 0:
                self.add(text[i:])
                return len(text)
            self.add(text[i:q])
            if q + 1 < len(text) and text[q + 1] == '"':
                self.add('"')
                i = q + 2
                continue
            return q + 1

    def run(self) -> tuple[list[list[str]], int]:
        text, i, n = self.text, 0, len(self.text)
        while i < n:
            m = self.special.search(text, i)
            if m is None:
                self.add(text[i:])
                break
            self.add(text[i : m.start()])
            ch, i = m.group(), m.end()
            if ch == '"':
                if self.field:
                    self.add('"')
                else:
                    i = self.quoted(i)
            elif ch == "\n":
                self.end_row()
            elif ch == self.delim:
                self.end_field()
        if self.field or self.row:
            self.end_row()
        return self.rows, self.total


def parse(text: str, delim: str, max_rows: int) -> tuple[list[list[str]], int]:
    """（前 max_rows 列, 總列數）。"""
    return _Parser(text, delim, max_rows).run()


def _numeric_columns(rows: list[list[str]], columns: int) -> list[bool]:
    sample = rows[:NUMERIC_SAMPLE]
    numeric: list[bool] = []
    for col in range(columns):
        nums = non_empty = 0
        for r in sample:
            if col >= len(r):
                continue
            v = r[col].strip(_ZS)
            if not v:
                continue
            non_empty += 1
            if is_number(v.replace(",", "").replace("%", "")):
                nums += 1
        numeric.append(non_empty > 0 and nums * 10 >= non_empty * 8)
    return numeric


def _cell(v: str) -> str:
    return escape(v[:CELL_LIMIT] + "…" if len(v) > CELL_LIMIT else v)


def _table(header: list[str], data: list[list[str]], columns: int) -> str:
    numeric = _numeric_columns(data, columns)
    h = ['<div class="grid-wrap"><table class="grid"><thead><tr><th class="rn"></th>']
    for col in range(columns):
        name = header[col] if col < len(header) else ""
        h.append(
            f'<th>{escape(name)}<span class="ctype">{"123" if numeric[col] else "abc"}</span></th>'
        )
    h.append("</tr></thead><tbody>")
    for idx, r in enumerate(data[:MAX_ROWS], start=1):
        h.append(f'<tr><td class="rn">{idx}</td>')
        for col in range(columns):
            v = r[col] if col < len(r) else ""
            h.append(('<td class="num">' if numeric[col] else "<td>") + _cell(v) + "</td>")
        h.append("</tr>")
    h.append("</tbody></table></div>")
    return "".join(h)


def render(text: str, info: FileInfo, truncated_file: bool) -> Page:
    """CSV／TSV 頁面（編碼標籤由呼叫端補上）。"""
    delim = detect_delimiter(text, info.ext)
    rows, total = parse(text, delim, MAX_ROWS + 1)
    page = Page(kind="TSV 表格" if delim == "\t" else "CSV 表格", size=(1000, 700))
    if not rows:
        page.body = '<div class="empty">空白的表格</div>'
        return page
    header, data = rows[0], rows[1:]
    columns = max(len(r) for r in rows)
    shown = min(columns, MAX_COLUMNS)
    page.chips = [
        Chip(f"{fmt.number(max(0, total - 1))} 列 × {columns} 欄"),
        Chip(f"分隔：{DELIM_NAMES.get(delim, '?')}"),
    ]
    if total - 1 > MAX_ROWS or truncated_file:
        shown_rows = fmt.number(min(MAX_ROWS, len(data)))
        suffix = "（檔案較大，僅讀取開頭部分）" if truncated_file else ""
        page.notices.append(Notice(f"只顯示前 {shown_rows} 列{suffix}"))
    if columns > MAX_COLUMNS:
        page.notices.append(Notice(f"欄位太多，只顯示前 {MAX_COLUMNS} 欄"))
    page.body = _table(header, data, shown)
    return page
