"""萬用轉換：JSON 解析／輸出、YAML 輸出、Plist 讀寫（對應原版 CVStructuredData）。"""

from __future__ import annotations

import plistlib
from datetime import datetime

import pytest

from quickseemd.core import errors
from quickseemd.core.converter import plistdata, structured
from quickseemd.core.converter.structured import Num, Obj


def parse(text: str):
    return structured.parse_json(text)


# ------------------------------------------------------------------ JSON 解析


def test_parse_keeps_order_and_raw_numbers() -> None:
    v = parse('{"b": 1.50, "a": 12345678901234567890, "c": [true, false, null]}')
    assert isinstance(v, Obj)
    assert [k for k, _ in v.members] == ["b", "a", "c"]
    assert v.get("b") == Num("1.50")
    assert v.get("a") == Num("12345678901234567890")
    assert v.get("c") == (True, False, None)


def test_parse_comments_and_trailing_commas() -> None:
    text = """// 開頭註解
    {
      /* 區塊
         註解 */
      "a": [1, 2,],
      "b": "x", // 行尾
    }"""
    v = parse(text)
    assert v.get("a") == (Num("1"), Num("2"))
    assert v.get("b") == "x"


def test_parse_leading_plus_is_stripped() -> None:
    assert parse("+5") == Num("5")
    assert parse("[-1e3, .5]") == (Num("-1e3"), Num(".5"))


def test_parse_string_escapes() -> None:
    v = parse(r'"a\"b\\c\/d\b\f\n\r\t中😀"')
    assert v == 'a"b\\c/d\b\f\n\r\t中😀'


def test_parse_lone_surrogate_becomes_replacement() -> None:
    assert parse(r'"\ud800x"') == "�x"


def test_parse_bom_is_skipped() -> None:
    assert parse("\N{ZERO WIDTH NO-BREAK SPACE}[1]") == (Num("1"),)


def test_parse_duplicate_keys_are_kept() -> None:
    v = parse('{"a": 1, "a": 2}')
    assert v.members == (("a", Num("1")), ("a", Num("2")))


@pytest.mark.parametrize(
    ("text", "message"),
    [
        ('{\n"a": 1,\n"b" 2}', "資料格式不正確：JSON 第 3 行：缺少冒號。"),
        ("[1, 2", "資料格式不正確：JSON 第 1 行：陣列沒有結束。"),
        ("[1 2]", "資料格式不正確：JSON 第 1 行：陣列中缺少逗號。"),
        ('{"a": 1 "b": 2}', "資料格式不正確：JSON 第 1 行：物件中缺少逗號。"),
        ('{"a": 1', "資料格式不正確：JSON 第 1 行：物件沒有結束。"),
        ("{a: 1}", "資料格式不正確：JSON 第 1 行：物件的鍵必須是字串。"),
        ('"abc', "資料格式不正確：JSON 第 1 行：字串沒有結束。"),
        (r'"\q"', "資料格式不正確：JSON 第 1 行：不正確的跳脫字元。"),
        (r'"\u12"', "資料格式不正確：JSON 第 1 行：不正確的 \\u 跳脫。"),
        ("tru", "資料格式不正確：JSON 第 1 行：無法辨識的值。"),
        ("-", "資料格式不正確：JSON 第 1 行：無法辨識的值。"),
        ("", "資料格式不正確：JSON 第 1 行：內容意外結束。"),
        ("[1]\n\nx", "資料格式不正確：JSON 第 3 行有多餘的內容。"),
    ],
)
def test_parse_errors(text: str, message: str) -> None:
    with pytest.raises(errors.QSError) as exc:
        parse(text)
    assert str(exc.value) == message


def test_parse_depth_limit() -> None:
    assert parse("[" * 512 + "]" * 512) is not None
    with pytest.raises(errors.QSError) as exc:
        parse("[" * 513 + "]" * 513)
    assert "巢狀層數太深。" in str(exc.value)


def test_broken_sample_reports_line(samples) -> None:
    text = (samples / "broken.json").read_text("utf-8")
    with pytest.raises(errors.QSError) as exc:
        parse(text)
    assert str(exc.value).startswith("資料格式不正確：JSON 第 ")


# ------------------------------------------------------------------ JSON 輸出


def test_write_pretty_and_compact() -> None:
    v = parse('{"a": [1, {"b": null}], "c": {}, "d": []}')
    assert structured.write_json(v, pretty=True) == (
        '{\n  "a": [\n    1,\n    {\n      "b": null\n    }\n  ],\n  "c": {},\n  "d": []\n}'
    )
    assert structured.write_json(v, pretty=False) == '{"a":[1,{"b":null}],"c":{},"d":[]}'


def test_quote_escapes() -> None:
    s = 'q"b\\n\nr\rt\tb\bf\f\x01\N{LINE SEPARATOR}\N{PARAGRAPH SEPARATOR}中'
    assert structured.quote(s) == '"q\\"b\\\\n\\nr\\rt\\tb\\bf\\f\\u0001\\u2028\\u2029中"'


def test_write_scalar_root() -> None:
    assert structured.write_json(Num("3.10"), pretty=True) == "3.10"
    assert structured.write_json(None, pretty=False) == "null"


# ------------------------------------------------------------------ YAML


def test_yaml_block_style() -> None:
    v = parse('{"name": "QS", "list": [1, "two", {"k": "v", "k2": [true]}], "empty": [], "o": {}}')
    assert structured.write_yaml(v) == (
        "name: QS\n"
        "list:\n"
        "  - 1\n"
        "  - two\n"
        "  - k: v\n"
        "    k2:\n"
        "      - true\n"
        "empty: []\n"
        "o: {}\n"
    )


def test_yaml_nested_arrays() -> None:
    v = parse("[[1, 2], [], 3]")
    assert structured.write_yaml(v) == "- - 1\n  - 2\n- []\n- 3\n"


def test_yaml_scalar_root() -> None:
    assert structured.write_yaml(parse('"hello"')) == "hello\n"
    assert structured.write_yaml(parse("[]")) == "[]\n"


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", '""'),
        ("plain", "plain"),
        ("中文字", "中文字"),
        ("Yes", '"Yes"'),
        ("off", '"off"'),
        ("~", '"~"'),
        (".NaN", '".NaN"'),
        ("-.inf", '"-.inf"'),
        ("123", '"123"'),
        ("1e5", '"1e5"'),
        ("inf", '"inf"'),
        ("0x1F", '"0x1F"'),
        ("0o17", '"0o17"'),
        (" lead", '" lead"'),
        ("trail ", '"trail "'),
        ("-dash", '"-dash"'),
        ("@at", '"@at"'),
        ("`tick", '"`tick"'),
        ("a: b", '"a: b"'),
        ("a #b", '"a #b"'),
        ("key:", '"key:"'),
        ("a:b", "a:b"),
        ("tab\there", '"tab\\there"'),
        ("del\x7f", '"del\\x7f"'),  # 原版原樣輸出 DEL；YAML 規定不可列印，改成跳脫
        ("2024-01-03", '"2024-01-03"'),
        ("2024-01-03T10:00", '"2024-01-03T10:00"'),
        ("v1.2.3", "v1.2.3"),
    ],
)
def test_yaml_quoting(raw: str, expected: str) -> None:
    assert structured.yaml_quote_if_needed(raw) == expected


# ------------------------------------------------------------------ Plist


def test_xml_plist_writer() -> None:
    v = parse(
        '{"name": "a&b<c>\\"", "n": 3, "big": 99999999999999999999, "f": 1.5, '
        '"e": 1e3, "t": true, "skip": null, "arr": [null, false], "emptyA": [], "emptyD": {}}'
    )
    assert plistdata.xml_plist(v) == (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
        '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
        '<plist version="1.0">\n'
        "<dict>\n"
        "\t<key>name</key>\n"
        "\t<string>a&amp;b&lt;c&gt;&quot;</string>\n"
        "\t<key>n</key>\n"
        "\t<integer>3</integer>\n"
        "\t<key>big</key>\n"
        "\t<real>99999999999999999999</real>\n"
        "\t<key>f</key>\n"
        "\t<real>1.5</real>\n"
        "\t<key>e</key>\n"
        "\t<real>1e3</real>\n"
        "\t<key>t</key>\n"
        "\t<true/>\n"
        "\t<key>arr</key>\n"
        "\t<array>\n"
        "\t\t<string></string>\n"
        "\t\t<false/>\n"
        "\t</array>\n"
        "\t<key>emptyA</key>\n"
        "\t<array/>\n"
        "\t<key>emptyD</key>\n"
        "\t<dict/>\n"
        "</dict>\n"
        "</plist>\n"
    )


def test_xml_plist_strips_invalid_control_chars() -> None:
    out = plistdata.xml_plist("a\x01b\tc")
    assert "<string>ab\tc</string>" in out


def test_binary_plist_roundtrip_keeps_types() -> None:
    v = parse('{"z": 1, "a": [1.5, "s", true, null], "n": null}')
    data = plistdata.binary_plist(v)
    assert data.startswith(b"bplist00")
    loaded = plistlib.loads(data)
    assert loaded == {"z": 1, "a": [1.5, "s", True, ""]}


def test_binary_plist_rejects_null_root() -> None:
    with pytest.raises(errors.QSError) as exc:
        plistdata.binary_plist(None)
    assert str(exc.value) == "資料格式不正確：plist 不支援 null 作為根節點。"


def test_read_xml_plist_keeps_order() -> None:
    xml = plistdata.xml_plist(parse('{"z": 1, "a": {"y": 2.5, "b": [true, "x"]}}'))
    v = plistdata.read_plist(xml.encode())
    assert [k for k, _ in v.members] == ["z", "a"]
    inner = v.get("a")
    assert [k for k, _ in inner.members] == ["y", "b"]
    assert inner.get("y") == Num("2.5")
    assert inner.get("b") == (True, "x")


def test_read_xml_plist_dates_and_data() -> None:
    xml = (
        b'<?xml version="1.0" encoding="UTF-8"?><plist version="1.0"><dict>'
        b"<key>d</key><date>2024-01-02T03:04:05Z</date>"
        b"<key>b</key><data>\n  AAEC\n  Aw==\n</data>"
        b"<key>r</key><real>inf</real>"
        b"</dict></plist>"
    )
    v = plistdata.read_plist(xml)
    assert v.get("d") == "2024-01-02T03:04:05Z"
    assert v.get("b") == "AAECAw=="
    assert v.get("r") == "inf"


def test_read_xml_plist_unknown_element() -> None:
    xml = b'<?xml version="1.0"?><plist version="1.0"><dict><key>a</key><weird/></dict></plist>'
    with pytest.raises(errors.QSError) as exc:
        plistdata.read_plist(xml)
    assert str(exc.value) == "資料格式不正確：plist 中有無法辨識的元素 <weird>。"


def test_read_binary_plist_sorts_keys() -> None:
    data = plistlib.dumps(
        {"b": 1, "a": 2.0, "c": 0.25, "d": datetime(2024, 1, 2, 3, 4, 5), "e": b"\x00\x01"},
        fmt=plistlib.FMT_BINARY,
        sort_keys=False,
    )
    v = plistdata.read_plist(data)
    assert [k for k, _ in v.members] == ["a", "b", "c", "d", "e"]
    assert v.get("a") == Num("2.0")
    assert v.get("b") == Num("1")
    assert v.get("c") == Num("0.25")
    assert v.get("d") == "2024-01-02T03:04:05Z"
    assert v.get("e") == "AAE="


def test_read_sample_binary_plist(samples) -> None:
    v = plistdata.read_plist((samples / "settings.plist").read_bytes())
    assert isinstance(v, Obj)
    keys = [k for k, _ in v.members]
    assert keys == sorted(keys)


def test_read_garbage_plist() -> None:
    with pytest.raises(errors.QSError) as exc:
        plistdata.read_plist(b"not a plist at all")
    assert str(exc.value) == "資料格式不正確：不是正確的 plist 檔案。"


def test_entity_declarations_are_refused() -> None:
    xml = (
        b'<?xml version="1.0"?><!DOCTYPE plist [<!ENTITY a "aaaa">]>'
        b"<plist><string>&a;</string></plist>"
    )
    with pytest.raises(errors.QSError):
        plistdata.read_plist(xml)


# ------------------------------------------------------------------ 安全：注入與不可列印字元


def test_plist_integer_cannot_inject_json() -> None:
    xml = (
        b'<?xml version="1.0"?><plist version="1.0"><dict>'
        b'<key>a</key><integer>1, "x": 2</integer>'
        b"<key>b</key><real>1e3</real>"
        b"<key>c</key><real>.5</real>"
        b"<key>d</key><integer> 42 </integer>"
        b"<key>e</key><real>1.0]</real>"
        b"</dict></plist>"
    )
    v = plistdata.read_plist(xml)
    assert v.get("a") == '1, "x": 2'
    assert v.get("b") == Num("1e3")
    assert v.get("c") == Num(".5")
    assert v.get("d") == Num("42")
    assert v.get("e") == "1.0]"
    out = structured.write_json(v, pretty=False)
    assert out == '{"a":"1, \\"x\\": 2","b":1e3,"c":0.5,"d":42,"e":"1.0]"}'


@pytest.mark.parametrize(
    ("raw", "json_text"),
    [
        ("12", "12"),
        ("-0.5e-3", "-0.5e-3"),
        ("007", "7"),
        ("+5", "5"),
        ("1.", "1.0"),
        (".5", "0.5"),
        ("1e400", "1e400"),  # 合乎 JSON 文法（只是超出範圍），原樣保留
        ('1, "x": 2', '"1, \\"x\\": 2"'),
        ("</real><key>", '"</real><key>"'),
    ],
)
def test_writers_validate_number_text(raw: str, json_text: str) -> None:
    assert structured.write_json(Num(raw), pretty=True) == json_text
    assert structured.safe_number_text(raw) == (None if json_text.startswith('"') else json_text)


def test_yaml_and_plist_never_emit_invalid_numbers() -> None:
    bad = Obj((("k", Num("1\nevil: true")), ("m", Num("3"))))
    assert structured.write_yaml(bad) == 'k: "1\\nevil: true"\nm: 3\n'
    xml = plistdata.xml_plist(bad)
    assert "<string>1\nevil: true</string>" in xml
    assert "<integer>3</integer>" in xml
    assert "evil: true</integer>" not in xml


@pytest.mark.parametrize(
    "raw",
    [
        "a\x85b",
        "a\x9fb",
        "\N{ZERO WIDTH NO-BREAK SPACE}x",
        "a\N{LINE SEPARATOR}b",
        "a\N{PARAGRAPH SEPARATOR}b",
        "a\ud800b",
        "a\ufffeb",
        "a\x0bb",
    ],
)
def test_yaml_quotes_non_printable(raw: str) -> None:
    out = structured.yaml_quote_if_needed(raw)
    assert out.startswith('"') and out.endswith('"')
    body = out[1:-1]
    assert all(0x20 <= ord(c) < 0x7F or c in "中" or ord(c) >= 0xA0 for c in body)
    for ch in (
        "\x85",
        "\x9f",
        "\N{ZERO WIDTH NO-BREAK SPACE}",
        "\N{LINE SEPARATOR}",
        "\N{PARAGRAPH SEPARATOR}",
        "\ud800",
        "\ufffe",
        "\x0b",
    ):
        assert ch not in body


def test_yaml_escape_forms() -> None:
    assert structured.yaml_quote_if_needed("a\x85") == '"a\\x85"'
    assert structured.yaml_quote_if_needed("a\N{ZERO WIDTH NO-BREAK SPACE}") == '"a\\ufeff"'
    assert structured.yaml_quote_if_needed("a\x01") == '"a\\u0001"'


def test_json_quote_escapes_invisible_characters() -> None:
    assert (
        structured.quote("a\x85\N{ZERO WIDTH NO-BREAK SPACE}\ud800") == '"a\\u0085\\ufeff\\ud800"'
    )


def test_xml_plist_node_cap(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(plistdata, "MAX_XML_NODES", 10)
    xml = plistdata.xml_plist(tuple(Num(str(i)) for i in range(50))).encode()
    with pytest.raises(errors.QSError) as exc:
        plistdata.read_plist(xml)
    assert str(exc.value) == "資料格式不正確：plist 檔案太大，無法轉換。"
