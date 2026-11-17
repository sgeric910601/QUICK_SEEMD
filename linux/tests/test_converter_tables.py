"""萬用轉換：CSV／TSV、JSON 與表格互轉、HTML 表格、XLSX 讀寫（對應原版 CVDataConverter）。"""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

from quickseemd.core import errors
from quickseemd.core.converter import structured, tables, xlsx
from quickseemd.core.converter.structured import Num, Obj
from quickseemd.core.converter.tables import Table

# ------------------------------------------------------------------ CSV 讀取


def test_detect_delimiter() -> None:
    assert tables.detect_delimiter("a,b,c\n1;2;3;4;5") == ","
    assert tables.detect_delimiter("a;b;c\n1,2") == ";"
    assert tables.detect_delimiter("a\tb\tc;d") == "\t"
    assert tables.detect_delimiter('"x;y;z",b\n') == ","
    assert tables.detect_delimiter("single") == ","
    assert tables.detect_delimiter("a,b;c") == ","  # 同數時偏好逗號


def test_parse_rfc4180() -> None:
    text = (
        '\N{ZERO WIDTH NO-BREAK SPACE}name,note\r\n"Lin, Amy","多行\n備註"\n"say ""hi""",x\n,\n\n'
    )
    rows = tables.parse_delimited(text, ",")
    assert rows == (
        ("name", "note"),
        ("Lin, Amy", "多行\n備註"),
        ('say "hi"', "x"),
    )


def test_parse_quote_only_opens_at_field_start() -> None:
    assert tables.parse_delimited('ab"c,d\n', ",") == (('ab"c', "d"),)
    assert tables.parse_delimited('"a"b,c', ",") == (("ab", "c"),)


def test_parse_keeps_empty_quoted_field_at_end() -> None:
    assert tables.parse_delimited('a,""', ",") == (("a", ""),)
    assert tables.parse_delimited('""', ",") == ()


def test_sample_csv(samples: Path) -> None:
    table = tables.read_delimited(samples / "data.csv", tsv=False, forced=None)
    assert table.name == "data"
    assert table.rows[0] == ("name", "city", "score", "joined")
    assert table.rows[2][0] == "Lin, Amy"
    assert table.rows[3][0] == "多行\n備註"


# ------------------------------------------------------------------ 表格 ↔ JSON


def test_table_from_array_of_objects() -> None:
    v = structured.parse_json('[{"a": 1, "b": "x"}, {"b": "y", "c": [1, 2], "a": null}]')
    t = tables.table_from_json(v, "n")
    assert t.rows == (("a", "b", "c"), ("1", "x", ""), ("", "y", "[1,2]"))


def test_table_from_array_of_arrays_and_scalars() -> None:
    assert tables.table_from_json(structured.parse_json("[[1, 2], [true]]"), "n").rows == (
        ("1", "2"),
        ("true",),
    )
    assert tables.table_from_json(structured.parse_json('[1, "a", null]'), "n").rows == (
        ("value",),
        ("1",),
        ("a",),
        ("",),
    )


def test_table_from_object_uses_first_array_member() -> None:
    v = structured.parse_json('{"meta": 1, "rows": [{"a": 1}], "more": [2]}')
    t = tables.table_from_json(v, "n")
    assert t.name == "rows"
    assert t.rows == (("a",), ("1",))


def test_table_from_non_array_fails() -> None:
    with pytest.raises(errors.QSError) as exc:
        tables.table_from_json(structured.parse_json('{"a": 1}'), "n")
    assert "JSON 的最外層必須是陣列（例如 [{…}, {…}]）才能轉成表格。" in str(exc.value)


def test_empty_array_gives_empty_table() -> None:
    assert tables.table_from_json((), "n").rows == ()


def test_json_from_table_keys_and_inference() -> None:
    t = Table("t", ((" id ", "", "id", "flag"), ("007", "1.5", "-2e3", "TRUE", "extra"), ("x",)))
    v = tables.json_from_table(t)
    assert v == (
        Obj(
            (
                ("id", "007"),
                ("column2", Num("1.5")),
                ("id_2", Num("-2e3")),
                ("flag", True),
                ("column5", "extra"),
            )
        ),
        Obj((("id", "x"), ("column2", ""), ("id_2", ""), ("flag", ""))),
    )


@pytest.mark.parametrize(
    ("s", "numeric"),
    [
        ("0", True),
        ("-12.5", True),
        ("1e10", True),
        ("012", False),
        ("1.", False),
        (".5", False),
        ("1" * 19, True),
        ("1" * 20, False),
        ("", False),
        ("abc", False),
    ],
)
def test_looks_numeric(s: str, numeric: bool) -> None:
    assert tables.looks_numeric(s) is numeric


def test_infer_value() -> None:
    assert tables.infer_value("false") is False
    assert tables.infer_value("False") == "False"
    assert tables.infer_value("12") == Num("12")


# ------------------------------------------------------------------ 表格輸出


def test_delimited_quoting() -> None:
    t = Table("t", (("a,b", 'q"', "x"), (" lead", "multi\nline", "tab\tok")))
    assert tables.delimited(t, ",") == '"a,b","q""",x\n" lead","multi\nline",tab\tok\n'
    assert tables.delimited(t, "\t") == 'a,b\t"q"""\tx\n" lead"\t"multi\nline"\t"tab\tok"\n'


def test_html_tables_single_and_multi() -> None:
    one = tables.html_tables([Table("s", (("h<1>", "n"), ("a\nb", "3.5")))], "標題 & 名稱")
    assert one.startswith('<!DOCTYPE html>\n<html lang="zh-Hant">')
    assert "<title>標題 &amp; 名稱</title>" in one
    assert "<h1>標題 &amp; 名稱</h1>" in one
    assert "<thead><tr><th>h&lt;1&gt;</th><th>n</th></tr>\n</thead>\n" in one
    assert '<tbody><tr><td>a<br>b</td><td class="num">3.5</td></tr>\n</tbody>' in one
    assert "<h2>" not in one
    assert one.endswith("</body>\n</html>")
    two = tables.html_tables([Table("A", (("x",),)), Table("B", ())], "t")
    assert "<h2>A</h2>" in two and "<h2>B</h2>" in two


# ------------------------------------------------------------------ XLSX


def _sheet_xml(path: Path, n: int = 1) -> str:
    with zipfile.ZipFile(path) as z:
        return z.read(f"xl/worksheets/sheet{n}.xml").decode()


def test_xlsx_write_structure(tmp_path: Path) -> None:
    out = tmp_path / "t.xlsx"
    t = Table("資料/表:1", (("名稱", "數量"), ("蘋果", "12"), ("012", "1e400"), ("長" * 40, "")))
    xlsx.write_xlsx([t], out)
    with zipfile.ZipFile(out) as z:
        names = set(z.namelist())
        assert {
            "[Content_Types].xml",
            "_rels/.rels",
            "docProps/core.xml",
            "docProps/app.xml",
            "xl/workbook.xml",
            "xl/_rels/workbook.xml.rels",
            "xl/styles.xml",
            "xl/sharedStrings.xml",
            "xl/worksheets/sheet1.xml",
        } <= names
        wb = z.read("xl/workbook.xml").decode()
        assert '<sheet name="資料_表_1" sheetId="1" r:id="rId1"/>' in wb
        assert "<Application>QUICK_SEEMD</Application>" in z.read("docProps/app.xml").decode()
        assert "<dc:creator>QUICK_SEEMD</dc:creator>" in z.read("docProps/core.xml").decode()
        assert 'rgb="FFEFF2F6"' in z.read("xl/styles.xml").decode()
    sheet = _sheet_xml(out)
    assert 'ySplit="1" topLeftCell="A2"' in sheet
    assert '<c r="A1" s="1" t="s"><v>0</v></c>' in sheet
    assert '<c r="B2"><v>12</v></c>' in sheet  # 數字存成數值
    assert '<c r="A3" t="s">' in sheet  # 前導 0 保留文字
    assert '<c r="B3" t="s">' in sheet  # 無限大不是有限數字
    assert '<col min="1" max="1" width="60" customWidth="1"/>' in sheet
    assert '<col min="2" max="2" width="8" customWidth="1"/>' in sheet


def test_xlsx_sheet_names_unique_and_default(tmp_path: Path) -> None:
    out = tmp_path / "t.xlsx"
    xlsx.write_xlsx(
        [Table("Sheet", ()), Table("sheet", ()), Table("", ()), Table("x" * 40, ())], out
    )
    with zipfile.ZipFile(out) as z:
        wb = z.read("xl/workbook.xml").decode()
    assert 'name="Sheet"' in wb
    assert 'name="sheet (2)"' in wb
    assert 'name="工作表3"' in wb
    assert f'name="{"x" * 31}"' in wb


def test_xlsx_single_row_has_no_frozen_pane(tmp_path: Path) -> None:
    out = tmp_path / "t.xlsx"
    xlsx.write_xlsx([Table("a", (("only", "1"),))], out)
    sheet = _sheet_xml(out)
    assert '<sheetView workbookViewId="0"/>' in sheet
    assert 's="1"' not in sheet
    xlsx.write_xlsx([], tmp_path / "empty.xlsx")
    with zipfile.ZipFile(tmp_path / "empty.xlsx") as z:
        assert 'name="工作表1"' in z.read("xl/workbook.xml").decode()


def test_xlsx_roundtrip(tmp_path: Path) -> None:
    out = tmp_path / "t.xlsx"
    rows = (("a", "b", "c"), ("1", "文字 & <x>", ""), ("", "", "3.25"))
    xlsx.write_xlsx([Table("一", rows), Table("二", (("x",),))], out)
    back = xlsx.read_xlsx(out)
    assert [t.name for t in back] == ["一", "二"]
    assert back[0].rows == rows
    assert back[1].rows == (("x",),)


def _workbook(folder: Path, sheet: str, styles: str = "", date1904: bool = False) -> Path:
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / "hand.xlsx"
    pr = '<workbookPr date1904="1"/>' if date1904 else ""
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(
            "xl/workbook.xml",
            '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
            'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
            f'{pr}<sheets><sheet name="S" sheetId="1" r:id="rId9"/></sheets></workbook>',
        )
        z.writestr(
            "xl/_rels/workbook.xml.rels",
            '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
            '<Relationship Id="rId9" Target="worksheets/data.xml"/></Relationships>',
        )
        z.writestr(
            "xl/sharedStrings.xml",
            '<sst xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
            "<si><t>plain</t></si>"
            '<si><r><t>ri</t></r><r><t xml:space="preserve">ch </t></r><rPh><t>skip</t></rPh></si>'
            "</sst>",
        )
        if styles:
            z.writestr("xl/styles.xml", styles)
        z.writestr("xl/worksheets/data.xml", sheet)
    return path


def test_xlsx_reader_types(tmp_path: Path) -> None:
    sheet = (
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
        '<row r="1"><c r="A1" t="s"><v>0</v></c><c r="C1" t="s"><v>1</v></c></row>'
        '<row r="3"><c r="B3" t="b"><v>1</v></c><c t="b"><v>0</v></c>'
        '<c r="E3" t="inlineStr"><is><t>inl</t></is></c><c r="F3"><v>42</v></c></row>'
        "</sheetData></worksheet>"
    )
    t = xlsx.read_xlsx(_workbook(tmp_path, sheet))[0]
    assert t.name == "S"
    assert t.rows == (
        ("plain", "", "rich ", "", "", ""),
        ("", "", "", "", "", ""),
        ("", "TRUE", "FALSE", "", "inl", "42"),
    )


def test_xlsx_reader_dates(tmp_path: Path) -> None:
    styles = (
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        '<numFmts><numFmt numFmtId="164" formatCode="yyyy/mm/dd hh:mm"/>'
        '<numFmt numFmtId="165" formatCode="&quot;d&quot;0.00"/></numFmts>'
        '<cellXfs><xf numFmtId="0"/><xf numFmtId="14"/><xf numFmtId="164"/><xf numFmtId="165"/>'
        "</cellXfs></styleSheet>"
    )
    sheet = (
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData>'
        '<row r="1"><c r="A1" s="1"><v>45294</v></c><c r="B1" s="2"><v>45294.5</v></c>'
        '<c r="C1" s="2"><v>0.75</v></c><c r="D1" s="3"><v>2.5</v></c><c r="E1" s="0"><v>7</v></c>'
        "</row></sheetData></worksheet>"
    )
    t = xlsx.read_xlsx(_workbook(tmp_path, sheet, styles))[0]
    assert t.rows == (("2024-01-03", "2024-01-03 12:00:00", "18:00:00", "2.5", "7"),)
    t1904 = xlsx.read_xlsx(_workbook(tmp_path / "b", sheet, styles, True))
    assert t1904[0].rows[0][0] == "2028-01-04"


@pytest.mark.parametrize(
    ("fmt_id", "code", "is_date"),
    [
        (14, None, True),
        (22, None, True),
        (27, None, True),
        (46, None, True),
        (58, None, True),
        (0, None, False),
        (164, "yyyy-mm-dd", True),
        (164, '"Day" 0.00', False),
        (164, "[Red]0.00", False),
        (164, "h:mm:ss", True),
        (164, "0.0%", False),
    ],
)
def test_is_date_format(fmt_id: int, code: str | None, is_date: bool) -> None:
    assert xlsx.is_date_format(fmt_id, code) is is_date


def test_xlsx_reader_rejects_non_xlsx(tmp_path: Path) -> None:
    bad = tmp_path / "bad.xlsx"
    bad.write_bytes(b"not a zip")
    with pytest.raises(errors.QSError) as exc:
        xlsx.read_xlsx(bad)
    assert "找不到活頁簿內容，可能不是正確的 XLSX 檔案。" in str(exc.value)


def test_column_names() -> None:
    assert [xlsx.column_name(i) for i in (0, 25, 26, 27, 701, 702)] == [
        "A",
        "Z",
        "AA",
        "AB",
        "ZZ",
        "AAA",
    ]
    assert xlsx.column_index("AB12") == 27


def test_xlsx_escapes_text_and_parts_are_well_formed(tmp_path: Path) -> None:
    from xml.parsers import expat

    out = tmp_path / "esc.xlsx"
    evil = '</t></si><si><t>x</t></si>&"'
    xlsx.write_xlsx([Table('a"<b>&', (("h", "n"), (evil, "=1+1"), ("\x01ctl", "5")))], out)
    with zipfile.ZipFile(out) as z:
        for name in z.namelist():
            expat.ParserCreate().Parse(z.read(name), True)  # 每個零件都要是合法 XML
        assert "</t></si><si><t>x" not in z.read("xl/sharedStrings.xml").decode()
    back = xlsx.read_xlsx(out)[0]
    assert back.name == 'a"<b>&'
    assert back.rows[1] == (evil, "=1+1")  # 公式開頭照原版當成文字存放，不會變成公式
    assert back.rows[2] == ("ctl", "5")


def test_html_table_escapes_markup() -> None:
    page = tables.html_tables([Table("<s>", (("<script>",), ("a&b",)))], "<t>")
    assert "<script>" not in page
    assert "&lt;script&gt;" in page and "a&amp;b" in page
