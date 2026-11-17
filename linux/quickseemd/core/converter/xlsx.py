"""手工產生與讀取 XLSX（對應原版 CVDataConverter 的 writeXLSX／readXLSX）。

寫入：第一列粗體並凍結、欄寬自動、看起來是數字的儲存格存成數值、文字用共用字串表。
讀取：依活頁簿關聯找工作表、共用字串含 rich text（略過注音 rPh）、偵測日期格式。
"""

from __future__ import annotations

import math
import posixpath
import re
import zipfile
from collections.abc import Callable, Sequence
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .. import errors
from . import xmldom
from .structured import swift_double
from .tables import Table, looks_numeric

MAX_PART_BYTES = 1024 * 1024 * 1024  # 單一 XML 零件解壓後的上限（防止壓縮炸彈）
WIDTH_SCAN_ROWS = 2000
_SHEET_NAME_BAD = re.compile(r"[\\/?*\[\]:]")
_NS_MAIN = "http://schemas.openxmlformats.org/spreadsheetml/2006/main"
_NS_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"
_NS_PKG_REL = "http://schemas.openxmlformats.org/package/2006/relationships"
_XML_DECL = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n'
_CT = "application/vnd.openxmlformats-officedocument"

_STYLES = (
    f'<styleSheet xmlns="{_NS_MAIN}"><fonts count="2"><font><sz val="12"/><name val="Calibri"/>'
    '<family val="2"/></font><font><b/><sz val="12"/><name val="Calibri"/><family val="2"/></font>'
    '</fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill>'
    '<patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid">'
    '<fgColor rgb="FFEFF2F6"/><bgColor indexed="64"/></patternFill></fill></fills>'
    '<borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders>'
    '<cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs>'
    '<cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
    '<xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/>'
    '</cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/>'
    "</cellStyles></styleSheet>"
)


def column_name(index: int) -> str:
    n = index + 1
    name = ""
    while n > 0:
        r = (n - 1) % 26
        name = chr(65 + r) + name
        n = (n - 1) // 26
    return name


def column_index(ref: str) -> int:
    n = 0
    for ch in ref:
        if not "A" <= ch <= "Z":
            break
        n = n * 26 + ord(ch) - 64
    return max(0, n - 1)


def _display_width(s: str) -> int:
    return sum(2 if ord(ch) > 0x2E80 else 1 for ch in s)


def sheet_names(tables: Sequence[Table]) -> list[str]:
    """\\/?*[]: 換成 _、最多 31 字、不分大小寫重複時加「 (n)」、空白名稱用「工作表{n}」。"""
    used: set[str] = set()
    names: list[str] = []
    for i, t in enumerate(tables):
        name = _SHEET_NAME_BAD.sub("_", t.name) or f"工作表{i + 1}"
        name = name[:31]
        candidate, n = name, 2
        while candidate.lower() in used:
            candidate = f"{name[:28]} ({n})"
            n += 1
        used.add(candidate.lower())
        names.append(candidate)
    return names


# ---------------------------------------------------------------- 寫入


class _SharedStrings:
    def __init__(self) -> None:
        self.index: dict[str, int] = {}
        self.items: list[str] = []
        self.refs = 0

    def id(self, text: str) -> int:
        self.refs += 1
        if text not in self.index:
            self.index[text] = len(self.items)
            self.items.append(text)
        return self.index[text]

    def xml(self) -> str:
        body = "".join(
            f'<si><t xml:space="preserve">{xmldom.escape(t)}</t></si>' for t in self.items
        )
        return (
            f'{_XML_DECL}<sst xmlns="{_NS_MAIN}" count="{self.refs}" '
            f'uniqueCount="{len(self.items)}">{body}</sst>'
        )


def _column_widths(table: Table) -> list[int]:
    count = max((len(r) for r in table.rows), default=0)
    widths = [8] * count
    for row in table.rows[:WIDTH_SCAN_ROWS]:
        for c, cell in enumerate(row):
            longest = max((_display_width(part) for part in cell.split("\n") if part), default=0)
            widths[c] = min(60, max(widths[c], longest + 2))
    return widths


def _cell(ref: str, cell: str, row: int, style: str, shared: _SharedStrings) -> str:
    if row > 0 and looks_numeric(cell):
        value = swift_double(cell)
        if value is not None and math.isfinite(value):
            return f'<c r="{ref}"{style}><v>{xmldom.escape(cell)}</v></c>'
    return f'<c r="{ref}"{style} t="s"><v>{shared.id(cell)}</v></c>'


def _sheet_xml(table: Table, shared: _SharedStrings) -> str:
    parts = [_XML_DECL, f'<worksheet xmlns="{_NS_MAIN}" xmlns:r="{_NS_REL}">']
    frozen = len(table.rows) > 1
    if frozen:
        parts.append(
            '<sheetViews><sheetView workbookViewId="0"><pane ySplit="1" topLeftCell="A2" '
            'activePane="bottomLeft" state="frozen"/></sheetView></sheetViews>'
        )
    else:
        parts.append('<sheetViews><sheetView workbookViewId="0"/></sheetViews>')
    widths = _column_widths(table)
    if widths:
        cols = "".join(
            f'<col min="{i + 1}" max="{i + 1}" width="{w}" customWidth="1"/>'
            for i, w in enumerate(widths)
        )
        parts.append(f"<cols>{cols}</cols>")
    parts.append("<sheetData>")
    for r, row in enumerate(table.rows):
        style = ' s="1"' if r == 0 and frozen else ""
        cells = "".join(
            _cell(f"{column_name(c)}{r + 1}", cell, r, style, shared)
            for c, cell in enumerate(row)
            if cell
        )
        parts.append(f'<row r="{r + 1}">{cells}</row>')
    parts.append("</sheetData></worksheet>")
    return "".join(parts)


def _package_parts(names: list[str]) -> dict[str, str]:
    n = len(names)
    overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" '
        f'ContentType="{_CT}.spreadsheetml.worksheet+xml"/>'
        for i in range(n)
    )
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    sheets = "".join(
        f'<sheet name="{xmldom.escape(name)}" sheetId="{i + 1}" r:id="rId{i + 1}"/>'
        for i, name in enumerate(names)
    )
    rel = f"{_NS_REL}/"
    rels = "".join(
        f'<Relationship Id="rId{i + 1}" Type="{rel}worksheet" Target="worksheets/sheet{i + 1}.xml"/>'
        for i in range(n)
    )
    rels += f'<Relationship Id="rId{n + 1}" Type="{rel}styles" Target="styles.xml"/>'
    rels += f'<Relationship Id="rId{n + 2}" Type="{rel}sharedStrings" Target="sharedStrings.xml"/>'
    return {
        "[Content_Types].xml": (
            f'{_XML_DECL}<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
            '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.'
            'relationships+xml"/><Default Extension="xml" ContentType="application/xml"/>'
            f'<Override PartName="/xl/workbook.xml" ContentType="{_CT}.spreadsheetml.sheet.main+xml"/>'
            f'{overrides}<Override PartName="/xl/styles.xml" ContentType="{_CT}.spreadsheetml.'
            'styles+xml"/><Override PartName="/xl/sharedStrings.xml" ContentType="'
            f'{_CT}.spreadsheetml.sharedStrings+xml"/><Override PartName="/docProps/core.xml" '
            'ContentType="application/vnd.openxmlformats-package.core-properties+xml"/>'
            f'<Override PartName="/docProps/app.xml" ContentType="{_CT}.extended-properties+xml"/>'
            "</Types>"
        ),
        "_rels/.rels": (
            f'{_XML_DECL}<Relationships xmlns="{_NS_PKG_REL}"><Relationship Id="rId1" '
            f'Type="{rel}officeDocument" Target="xl/workbook.xml"/><Relationship Id="rId2" '
            'Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/'
            'core-properties" Target="docProps/core.xml"/><Relationship Id="rId3" '
            f'Type="{rel}extended-properties" Target="docProps/app.xml"/></Relationships>'
        ),
        "docProps/core.xml": (
            f"{_XML_DECL}<cp:coreProperties xmlns:cp="
            '"http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
            'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
            'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><dc:creator>QUICK_SEEMD'
            '</dc:creator><dcterms:created xsi:type="dcterms:W3CDTF">'
            f"{now}</dcterms:created></cp:coreProperties>"
        ),
        "docProps/app.xml": (
            f'{_XML_DECL}<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/'
            '2006/extended-properties"><Application>QUICK_SEEMD</Application></Properties>'
        ),
        "xl/workbook.xml": (
            f'{_XML_DECL}<workbook xmlns="{_NS_MAIN}" xmlns:r="{_NS_REL}"><bookViews>'
            f"<workbookView/></bookViews><sheets>{sheets}</sheets></workbook>"
        ),
        "xl/_rels/workbook.xml.rels": (
            f'{_XML_DECL}<Relationships xmlns="{_NS_PKG_REL}">{rels}</Relationships>'
        ),
        "xl/styles.xml": _XML_DECL + _STYLES,
    }


def write_xlsx(tables: Sequence[Table], out: Path, check: Callable[[], None] | None = None) -> None:
    """寫出 XLSX；check 會在每張工作表之前呼叫（用來檢查取消）。"""
    sheets = list(tables) or [Table("工作表1", ())]
    names = sheet_names(sheets)
    shared = _SharedStrings()
    parts = _package_parts(names)
    for i, table in enumerate(sheets):
        if check is not None:
            check()
        parts[f"xl/worksheets/sheet{i + 1}.xml"] = _sheet_xml(table, shared)
    parts["xl/sharedStrings.xml"] = shared.xml()
    with zipfile.ZipFile(out, "x", zipfile.ZIP_DEFLATED) as z:
        for name, content in parts.items():
            z.writestr(name, content.encode("utf-8"))


# ---------------------------------------------------------------- 讀取


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


class _Package:
    def __init__(self, z: zipfile.ZipFile) -> None:
        self.z = z
        self.names = set(z.namelist())

    def load(self, path: str) -> xmldom.Node | None:
        path = posixpath.normpath(path)
        if path not in self.names:
            return None
        info = self.z.getinfo(path)
        if info.file_size > MAX_PART_BYTES:
            raise errors.invalid_data("XLSX 內容過大。")
        return xmldom.parse(self.z.read(path))


def _string_item(node: xmldom.Node) -> str:
    out: list[str] = []
    for child in node.children:
        if child.local_name == "t":
            out.append(child.text)
        elif child.local_name == "r":
            out.extend(t.text for t in child.children_named("t"))
    return "".join(out)


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
        if fid is not None and fid.isdigit() and code is not None:
            custom[int(fid)] = code
    xfs = styles.children[0].first_child("cellXfs")
    out: set[int] = set()
    for i, xf in enumerate(xfs.children_named("xf") if xfs else []):
        raw = xf.attribute("numFmtId") or "0"
        fid = int(raw) if raw.isdigit() else 0
        if is_date_format(fid, custom.get(fid)):
            out.add(i)
    return out


def _cell_value(c: xmldom.Node, shared: list[str], dates: set[int], date1904: bool) -> str:
    kind = c.attribute("t") or "n"
    v = c.first_child("v")
    if kind == "s":
        idx = (v.text.strip() if v else "").strip()
        return shared[int(idx)] if idx.isdigit() and int(idx) < len(shared) else ""
    if kind == "inlineStr":
        node = c.first_child("is")
        return _string_item(node) if node else ""
    if kind == "b":
        return "TRUE" if v is not None and v.text == "1" else "FALSE"
    value = v.text if v is not None else ""
    style = c.attribute("s")
    if kind == "n" and style is not None and style.isdigit() and int(style) in dates:
        serial = swift_double(value)
        if serial is not None:
            return format_serial_date(serial, date1904)
    return value


def _read_sheet(doc: xmldom.Node, ctx: tuple[list[str], set[int], bool]) -> tuple:
    grid: dict[int, dict[int, str]] = {}
    max_row = max_col = -1
    next_row = 0
    for row_node in doc.descendants("row"):
        raw = row_node.attribute("r")
        r = (int(raw) if raw and raw.isdigit() else next_row + 1) - 1
        next_row = r + 1
        next_col = 0
        for c in row_node.children_named("c"):
            ref = c.attribute("r")
            col = column_index(ref) if ref is not None else next_col
            next_col = col + 1
            value = _cell_value(c, *ctx)
            if not value:
                continue
            grid.setdefault(r, {})[col] = value
            max_row, max_col = max(max_row, r), max(max_col, col)
    rows = [
        [grid.get(r, {}).get(c, "") for c in range(max(max_col, 0) + 1)] for r in range(max_row + 1)
    ]
    used = max((max((i + 1 for i, v in enumerate(row) if v), default=0) for row in rows), default=0)
    return tuple(tuple(row[:used]) for row in rows)


def _open(path: Path) -> zipfile.ZipFile:
    try:
        return zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise errors.unreadable("找不到活頁簿內容，可能不是正確的 XLSX 檔案。") from exc


def read_xlsx(path: Path, check: Callable[[], None] | None = None) -> list[Table]:
    with _open(path) as z:
        pkg = _Package(z)
        workbook = pkg.load("xl/workbook.xml")
        if workbook is None:
            raise errors.unreadable("找不到活頁簿內容，可能不是正確的 XLSX 檔案。")
        targets = _relationships(pkg)
        sst = pkg.load("xl/sharedStrings.xml")
        shared = [_string_item(si) for si in sst.descendants("si")] if sst else []
        pr = workbook.descendants("workbookPr")
        flag = pr[0].attribute("date1904") if pr else None
        ctx = (shared, _date_styles(pkg), flag in ("1", "true"))
        tables: list[Table] = []
        for i, sheet in enumerate(workbook.descendants("sheet")):
            if check is not None:
                check()
            rid = sheet.attribute("id")
            part = targets.get(rid) if rid else None
            doc = pkg.load(part or f"xl/worksheets/sheet{i + 1}.xml")
            if doc is None:
                continue
            tables.append(Table(sheet.attribute("name") or f"工作表{i + 1}", _read_sheet(doc, ctx)))
    if not tables:
        raise errors.empty_content("活頁簿中沒有工作表。")
    return tables
