"""讀取 XLSX（對應原版 CVDataConverter.readXLSX）；寫入在 xlsxwrite.py。

依活頁簿關聯找工作表、共用字串含 rich text（略過注音 rPh）、偵測日期格式。
工作表與共用字串用串流方式解析（不建整棵樹），並設下各種上限，避免惡意檔案拖垮程式：
座標不能超出 Excel 範圍、展開後的儲存格總數、共用字串數、工作表數、每個零件解壓後的大小。
"""

from __future__ import annotations

import math
import posixpath
import re
import zipfile
from collections.abc import Callable
from datetime import datetime, timedelta
from pathlib import Path
from xml.parsers import expat

from .. import errors
from . import xmldom
from .structured import swift_double
from .tables import Table
from .xlsxwrite import column_name, sheet_names, write_xlsx  # noqa: F401 — 對外一併提供

MAX_ROWS = 1_048_576
MAX_COLS = 16_384
MAX_CELLS = 5_000_000  # 整本活頁簿展開成表格後的儲存格上限
MAX_SHARED_STRINGS = 5_000_000
MAX_SHEETS = 1000
MAX_PART_BYTES = 200 * 1024 * 1024  # 單一零件解壓後的上限（防止壓縮炸彈）
MAX_TOTAL_BYTES = 1024 * 1024 * 1024
MAX_SMALL_PART_NODES = 2_000_000  # 活頁簿、關聯、樣式這些小零件的元素上限
_DIGITS = re.compile(r"[0-9]+")
_LETTERS = re.compile(r"[A-Z]+")
_NOT_XLSX = "找不到活頁簿內容，可能不是正確的 XLSX 檔案。"


def _out_of_range() -> errors.QSError:
    return errors.invalid_data("XLSX 的儲存格位置超出 Excel 的範圍。")


def _too_large(what: str) -> errors.QSError:
    return errors.invalid_data(f"XLSX {what}過大，無法轉換。")


def column_index(ref: str) -> int:
    """「AB12」→ 27；超過 XFD 時回傳 MAX_COLS（由呼叫端判定超出範圍）。"""
    m = _LETTERS.match(ref)
    n = 0
    for ch in m.group(0) if m else "":
        n = n * 26 + ord(ch) - 64
        if n > MAX_COLS:
            return MAX_COLS
    return max(0, n - 1)


def _row_number(raw: str | None, fallback: int) -> int:
    """列號（從 0 起算）；只接受 ASCII 數字，超出 Excel 範圍就拒絕。"""
    if raw is None or not _DIGITS.fullmatch(raw):
        return fallback
    if len(raw) > 7 or not 1 <= int(raw) <= MAX_ROWS:
        raise _out_of_range()
    return int(raw) - 1


def is_date_format(fmt_id: int, code: str | None) -> bool:
    if 14 <= fmt_id <= 22 or 27 <= fmt_id <= 36 or 45 <= fmt_id <= 47 or 50 <= fmt_id <= 58:
        return True
    if code is None:
        return False
    stripped = re.sub(r'"[^"]*"', "", code)
    stripped = re.sub(r"\[[^\]]*\]", "", stripped).lower()
    return re.search("[ymdhs]", stripped) is not None and "0.0" not in stripped


def _swift_round(x: float) -> float:
    return math.copysign(math.floor(abs(x) + 0.5), x)


def format_serial_date(serial: float, date1904: bool) -> str:
    base = datetime(1904, 1, 1) if date1904 else datetime(1899, 12, 30)
    try:
        moment = base + timedelta(seconds=_swift_round(serial * 86_400))
    except (OverflowError, ValueError):
        return repr(serial)
    has_time = serial != math.floor(serial)
    if serial < 1 and has_time:
        return moment.strftime("%H:%M:%S")
    return moment.strftime("%Y-%m-%d %H:%M:%S" if has_time else "%Y-%m-%d")


# ---------------------------------------------------------------- 套件


class _Package:
    """只讀取 zip 裡的零件；拒絕絕對路徑與「..」，並限制解壓後大小。"""

    def __init__(self, z: zipfile.ZipFile) -> None:
        self.z = z
        self.names = set(z.namelist())
        self.total = 0

    def read(self, path: str) -> bytes | None:
        norm = posixpath.normpath(path)
        if norm.startswith(("/", "../")) or norm == ".." or norm not in self.names:
            return None
        info = self.z.getinfo(norm)
        if info.file_size > MAX_PART_BYTES:
            raise _too_large("內容")
        with self.z.open(info) as fh:
            data = fh.read(MAX_PART_BYTES + 1)
        if len(data) > MAX_PART_BYTES:
            raise _too_large("內容")
        self.total += len(data)
        if self.total > MAX_TOTAL_BYTES:
            raise _too_large("內容")
        return data

    def load(self, path: str) -> xmldom.Node | None:
        data = self.read(path)
        if data is None:
            return None
        try:
            return xmldom.parse(data, MAX_SMALL_PART_NODES)
        except xmldom.XMLTooLarge as exc:
            raise _too_large("內容") from exc


def _relationships(pkg: _Package) -> dict[str, str]:
    targets: dict[str, str] = {}
    rels = pkg.load("xl/_rels/workbook.xml.rels")
    for r in rels.descendants("Relationship") if rels else []:
        rid, target = r.attribute("Id"), r.attribute("Target")
        if rid is None or target is None:
            continue
        targets[rid] = target[1:] if target.startswith("/") else "xl/" + target
    return targets


def _date_styles(pkg: _Package) -> set[int]:
    styles = pkg.load("xl/styles.xml")
    if styles is None or not styles.children:
        return set()
    custom: dict[int, str] = {}
    for f in styles.descendants("numFmt"):
        fid, code = f.attribute("numFmtId"), f.attribute("formatCode")
        if fid is not None and _DIGITS.fullmatch(fid) and code is not None:
            custom[int(fid)] = code
    xfs = styles.children[0].first_child("cellXfs")
    out: set[int] = set()
    for i, xf in enumerate(xfs.children_named("xf") if xfs else []):
        raw = xf.attribute("numFmtId") or "0"
        fid = int(raw) if _DIGITS.fullmatch(raw) else 0
        if is_date_format(fid, custom.get(fid)):
            out.add(i)
    return out


# ---------------------------------------------------------------- 串流解析


def _local(name: str) -> str:
    return name.rsplit(":", 1)[-1]


def _stream(data: bytes, start: Callable, end: Callable, chars: Callable) -> None:
    parser = xmldom.new_parser()
    parser.StartElementHandler = start
    parser.EndElementHandler = end
    parser.CharacterDataHandler = chars
    try:
        parser.Parse(data, True)
    except (expat.ExpatError, xmldom.XMLRefused) as exc:
        raise errors.unreadable(_NOT_XLSX) from exc


def _shared_strings(pkg: _Package) -> list[str]:
    """<si><t>…</t></si> 或 <si><r><t>…</t></r>…</si>；略過注音／振假名 <rPh>。"""
    data = pkg.read("xl/sharedStrings.xml")
    if data is None:
        return []
    items: list[str] = []
    state = {"si": False, "t": False, "rph": 0, "buf": []}

    def start(name: str, _attrs: dict) -> None:
        local = _local(name)
        if local == "si":
            state["si"], state["buf"] = True, []
        elif local == "rPh":
            state["rph"] += 1
        elif local == "t" and state["si"] and not state["rph"]:
            state["t"] = True

    def end(name: str) -> None:
        local = _local(name)
        if local == "si" and state["si"]:
            if len(items) >= MAX_SHARED_STRINGS:
                raise _too_large("共用字串")
            items.append("".join(state["buf"]))
            state["si"] = False
        elif local == "rPh":
            state["rph"] -= 1
        elif local == "t":
            state["t"] = False

    def chars(text: str) -> None:
        if state["t"]:
            state["buf"].append(text)

    _stream(data, start, end, chars)
    return items


class _SheetReader:
    """邊解析邊收集非空白儲存格（稀疏），最後才展開成表格。"""

    def __init__(self, shared: list[str], dates: set[int], date1904: bool) -> None:
        self.shared, self.dates, self.date1904 = shared, dates, date1904
        self.grid: dict[int, dict[int, str]] = {}
        self.max_row = self.max_col = -1
        self.next_row = 0
        self.row = 0
        self.next_col = 0
        self.cell: dict[str, str] | None = None
        self.capture: list[str] | None = None
        self.v: str | None = None
        self.inline: list[str] | None = None
        self.rph = 0

    def start(self, name: str, attrs: dict[str, str]) -> None:
        local = _local(name)
        if local == "row":
            self.row = _row_number(attrs.get("r"), self.next_row)
            self.next_row, self.next_col = self.row + 1, 0
        elif local == "c":
            self.cell, self.v, self.inline = attrs, None, None
        elif local == "v" and self.cell is not None:
            self.capture = []
        elif local == "is" and self.cell is not None:
            self.inline = []
        elif local == "rPh":
            self.rph += 1
        elif local == "t" and self.inline is not None and not self.rph:
            self.capture = []

    def end(self, name: str) -> None:
        local = _local(name)
        if local == "v" and self.capture is not None:
            self.v, self.capture = "".join(self.capture), None
        elif local == "t" and self.capture is not None and self.inline is not None:
            self.inline.append("".join(self.capture))
            self.capture = None
        elif local == "rPh":
            self.rph -= 1
        elif local == "c" and self.cell is not None:
            self._store()
            self.cell = None

    def chars(self, text: str) -> None:
        if self.capture is not None:
            self.capture.append(text)

    def _value(self) -> str:
        kind = self.cell.get("t") or "n"
        v = self.v
        if kind == "s":
            idx = (v or "").strip()
            ok = _DIGITS.fullmatch(idx) and len(idx) < 12 and int(idx) < len(self.shared)
            return self.shared[int(idx)] if ok else ""
        if kind == "inlineStr":
            return "".join(self.inline or [])
        if kind == "b":
            return "TRUE" if v == "1" else "FALSE"
        value = v or ""
        style = self.cell.get("s")
        if kind == "n" and style and _DIGITS.fullmatch(style) and int(style) in self.dates:
            serial = swift_double(value)
            if serial is not None:
                return format_serial_date(serial, self.date1904)
        return value

    def _store(self) -> None:
        ref = self.cell.get("r")
        col = column_index(ref) if ref is not None else self.next_col
        if col >= MAX_COLS:
            raise _out_of_range()
        self.next_col = col + 1
        value = self._value()
        if not value:
            return
        self.grid.setdefault(self.row, {})[col] = value
        self.max_row, self.max_col = max(self.max_row, self.row), max(self.max_col, col)

    def table_rows(self, budget: int) -> tuple[tuple[str, ...], ...]:
        if self.max_row < 0:
            return ()
        if (self.max_row + 1) * (self.max_col + 1) > budget:
            raise errors.invalid_data(f"試算表太大（超過 {MAX_CELLS:,} 個儲存格），無法轉換。")
        width = self.max_col + 1
        rows = []
        for r in range(self.max_row + 1):
            cells = self.grid.get(r, {})
            rows.append(tuple(cells.get(c, "") for c in range(width)))
        return tuple(rows)


def _open(path: Path) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise errors.unreadable(_NOT_XLSX) from exc


def _sheet_parts(pkg: _Package, workbook: xmldom.Node) -> list[tuple[str, str]]:
    targets = _relationships(pkg)
    sheets = workbook.descendants("sheet")
    if len(sheets) > MAX_SHEETS:
        raise errors.invalid_data(f"活頁簿的工作表太多（超過 {MAX_SHEETS} 張），無法轉換。")
    out = []
    for i, sheet in enumerate(sheets):
        rid = sheet.attribute("id")
        part = (targets.get(rid) if rid else None) or f"xl/worksheets/sheet{i + 1}.xml"
        out.append((sheet.attribute("name") or f"工作表{i + 1}", part))
    return out


def read_xlsx(path: Path, check: Callable[[], None] | None = None) -> list[Table]:
    with _open(path) as z:
        pkg = _Package(z)
        workbook = pkg.load("xl/workbook.xml")
        if workbook is None:
            raise errors.unreadable(_NOT_XLSX)
        parts = _sheet_parts(pkg, workbook)
        shared = _shared_strings(pkg)
        pr = workbook.descendants("workbookPr")
        flag = pr[0].attribute("date1904") if pr else None
        dates = _date_styles(pkg)
        tables: list[Table] = []
        budget = MAX_CELLS
        for name, part in parts:
            if check is not None:
                check()
            data = pkg.read(part)
            if data is None:
                continue
            reader = _SheetReader(shared, dates, flag in ("1", "true"))
            _stream(data, reader.start, reader.end, reader.chars)
            rows = reader.table_rows(budget)
            budget -= sum(len(r) for r in rows)
            tables.append(Table(name, _trim_columns(rows)))
    if not tables:
        raise errors.empty_content("活頁簿中沒有工作表。")
    return tables


def _trim_columns(rows: tuple[tuple[str, ...], ...]) -> tuple[tuple[str, ...], ...]:
    """去掉每列尾端多餘的空白欄。"""
    used = 0
    for row in rows:
        for i in range(len(row) - 1, -1, -1):
            if row[i]:
                used = max(used, i + 1)
                break
    return tuple(row[:used] for row in rows)
