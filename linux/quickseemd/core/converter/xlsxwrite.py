"""手工產生 XLSX（對應原版 CVDataConverter.writeXLSX）。

第一列粗體並凍結、欄寬自動、看起來是數字的儲存格存成數值、文字用共用字串表。
"""

from __future__ import annotations

import math
import re
import zipfile
from collections.abc import Callable, Sequence
from datetime import datetime, timezone
from pathlib import Path

from . import xmldom
from .structured import swift_double
from .tables import Table, looks_numeric

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


_REL = f"{_NS_REL}/"
_ROOT_RELS = (
    f'{_XML_DECL}<Relationships xmlns="{_NS_PKG_REL}"><Relationship Id="rId1" '
    f'Type="{_REL}officeDocument" Target="xl/workbook.xml"/><Relationship Id="rId2" '
    'Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/'
    'core-properties" Target="docProps/core.xml"/><Relationship Id="rId3" '
    f'Type="{_REL}extended-properties" Target="docProps/app.xml"/></Relationships>'
)
_APP = (
    f'{_XML_DECL}<Properties xmlns="http://schemas.openxmlformats.org/officeDocument/'
    '2006/extended-properties"><Application>QUICK_SEEMD</Application></Properties>'
)


def _content_types(n: int) -> str:
    overrides = "".join(
        f'<Override PartName="/xl/worksheets/sheet{i + 1}.xml" '
        f'ContentType="{_CT}.spreadsheetml.worksheet+xml"/>'
        for i in range(n)
    )
    return (
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
    )


def _core_properties() -> str:
    now = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    return (
        f"{_XML_DECL}<cp:coreProperties xmlns:cp="
        '"http://schemas.openxmlformats.org/package/2006/metadata/core-properties" '
        'xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><dc:creator>QUICK_SEEMD'
        '</dc:creator><dcterms:created xsi:type="dcterms:W3CDTF">'
        f"{now}</dcterms:created></cp:coreProperties>"
    )


def _workbook_parts(names: list[str]) -> dict[str, str]:
    n = len(names)
    sheets = "".join(
        f'<sheet name="{xmldom.escape(name)}" sheetId="{i + 1}" r:id="rId{i + 1}"/>'
        for i, name in enumerate(names)
    )
    rels = "".join(
        f'<Relationship Id="rId{i + 1}" Type="{_REL}worksheet" Target="worksheets/sheet{i + 1}.xml"/>'
        for i in range(n)
    )
    rels += f'<Relationship Id="rId{n + 1}" Type="{_REL}styles" Target="styles.xml"/>'
    rels += f'<Relationship Id="rId{n + 2}" Type="{_REL}sharedStrings" Target="sharedStrings.xml"/>'
    return {
        "xl/workbook.xml": (
            f'{_XML_DECL}<workbook xmlns="{_NS_MAIN}" xmlns:r="{_NS_REL}"><bookViews>'
            f"<workbookView/></bookViews><sheets>{sheets}</sheets></workbook>"
        ),
        "xl/_rels/workbook.xml.rels": (
            f'{_XML_DECL}<Relationships xmlns="{_NS_PKG_REL}">{rels}</Relationships>'
        ),
    }


def _package_parts(names: list[str]) -> dict[str, str]:
    return {
        "[Content_Types].xml": _content_types(len(names)),
        "_rels/.rels": _ROOT_RELS,
        "docProps/core.xml": _core_properties(),
        "docProps/app.xml": _APP,
        **_workbook_parts(names),
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
