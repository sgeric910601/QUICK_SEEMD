"""文字編碼核心（textcodec）：偵測順序、換行、編碼輸出、寬鬆解碼。"""

from __future__ import annotations

import pytest

from quickseemd.core import errors
from quickseemd.core.tools.other import textcodec as tc

TW = "這是一份繁體中文的測試文件，包含常見的字詞與標點符號。我們今天要討論臺灣的天氣與交通狀況。\n第二行：資料處理完成。\n"
CN = "这是一份简体中文的测试文件，包含常见的词语与标点符号。我们今天要讨论中国的天气与交通状况。\n第二行：数据处理完成。\n"
JP = "これは日本語のテストファイルです。今日は天気がいいですね。明日も晴れるでしょう。\n"
KR = "이것은 한국어 테스트 파일입니다. 오늘 날씨가 좋네요. 내일도 맑을 거예요.\n"
FR = "Café crème, déjà vu, naïve façade — très bien. Le garçon a mangé.\n"

# ------------------------------------------------------------------ 偵測


def test_empty_is_utf8():
    d = tc.decode(b"")
    assert (d.text, d.encoding, d.had_bom) == ("", tc.UTF8, False)


def test_plain_utf8():
    d = tc.decode(TW.encode("utf-8"))
    assert d.text == TW
    assert d.encoding == tc.UTF8
    assert not d.had_bom
    assert d.display_name == "UTF-8"


def test_utf8_bom():
    d = tc.decode(b"\xef\xbb\xbf" + TW.encode("utf-8"))
    assert d.text == TW
    assert d.encoding == tc.UTF8 and d.had_bom


@pytest.mark.parametrize("codec", ["utf-16-le", "utf-16-be"])
def test_utf16_with_bom(codec):
    bom = b"\xff\xfe" if codec.endswith("le") else b"\xfe\xff"
    d = tc.decode(bom + TW.encode(codec))
    assert d.text == TW
    assert d.encoding == tc.UTF16 and d.had_bom
    assert d.display_name == "UTF-16"


def test_utf32_with_bom():
    d = tc.decode(b"\xff\xfe\x00\x00" + "abc 中文".encode("utf-32-le"))
    assert d.text == "abc 中文"
    assert d.encoding == tc.UTF32 and d.had_bom
    assert d.display_name == "UTF-32"


@pytest.mark.parametrize(("codec", "expected"), [("utf-16-le", tc.UTF16LE), ("utf-16-be", tc.UTF16BE)])
def test_utf16_without_bom(codec, expected):
    text = "Hello world, this is mostly ASCII text.\nSecond line here.\n"
    d = tc.decode(text.encode(codec))
    assert d.text == text
    assert d.encoding == expected
    assert not d.had_bom


@pytest.mark.parametrize(
    ("text", "codec", "name"),
    [
        (TW, "cp950", "Big5"),
        (CN, "gb18030", "GB18030"),
        (JP, "cp932", "Shift-JIS"),
        (KR, "euc_kr", "EUC-KR"),
        (FR, "cp1252", "Windows-1252"),
    ],
)
def test_legacy_encodings(text, codec, name):
    d = tc.decode(text.encode(codec))
    assert d.text == text
    assert d.display_name == name
    assert not d.had_bom


def test_short_ambiguous_prefers_traditional_chinese():
    d = tc.decode("繁體中文測試".encode("cp950"))
    assert d.display_name == "Big5"
    assert d.text == "繁體中文測試"


def test_gb18030_four_byte_characters():
    text = "汉字測試𠀀€\n" * 3
    d = tc.decode(CN.encode("gb18030") + text.encode("gb18030"))
    assert d.display_name == "GB18030"
    assert d.text.endswith(text)


def test_undetectable_raises_chinese_error():
    with pytest.raises(errors.QSError) as exc:
        tc.decode(b"\x81\x20\x81\x20\x81\x20")
    assert str(exc.value) == "無法讀取檔案內容：無法辨識文字編碼，請在選項中手動指定來源編碼。"


def test_forced_encoding():
    assert tc.decode(TW.encode("cp950"), forced=tc.BIG5).text == TW
    with pytest.raises(errors.QSError) as exc:
        tc.decode(b"\x81\x20", forced=tc.BIG5)
    assert str(exc.value) == "無法讀取檔案內容：無法以 Big5 解讀這個檔案，請改用其他來源編碼。"


def test_decode_file_unreadable(tmp_path):
    with pytest.raises(errors.QSError) as exc:
        tc.decode_file(tmp_path / "沒有這個檔案.txt")
    assert str(exc.value).startswith("無法讀取檔案內容：")


def test_decode_file(tmp_path):
    p = tmp_path / "a.txt"
    p.write_bytes(CN.encode("gb18030"))
    assert tc.decode_file(p).text == CN


# ------------------------------------------------------------------ 換行


@pytest.mark.parametrize(
    ("text", "ending"),
    [
        ("a\nb\n", tc.LineEnding.LF),
        ("a\r\nb\r\n", tc.LineEnding.CRLF),
        ("a\rb\r", tc.LineEnding.CR),
        ("a\r\nb\n", tc.LineEnding.MIXED),
        ("沒有換行", tc.LineEnding.NONE),
    ],
)
def test_detect_line_ending(text, ending):
    assert tc.detect_line_ending(text) == ending


def test_line_ending_labels():
    assert tc.LineEnding.MIXED.value == "混合"
    assert tc.LineEnding.NONE.value == "—"


def test_normalize_line_endings():
    src = "a\r\nb\rc\nd"
    assert tc.normalize_line_endings(src, tc.LineEnding.LF) == "a\nb\nc\nd"
    assert tc.normalize_line_endings(src, tc.LineEnding.CRLF) == "a\r\nb\r\nc\r\nd"
    assert tc.normalize_line_endings(src, tc.LineEnding.CR) == "a\rb\rc\rd"


# ------------------------------------------------------------------ 編碼輸出


def test_encode_utf8_and_bom():
    assert tc.encode("中", tc.UTF8, bom=False) == "中".encode()
    assert tc.encode("中", tc.UTF8, bom=True) == b"\xef\xbb\xbf" + "中".encode()


@pytest.mark.parametrize("enc", [tc.UTF16, tc.UTF16LE])
def test_encode_utf16_is_le_with_bom(enc):
    assert tc.encode("A中", enc, bom=False) == b"\xff\xfe" + "A中".encode("utf-16-le")


def test_encode_big5_and_gb18030():
    assert tc.encode(TW, tc.BIG5, bom=False) == TW.encode("cp950")
    assert tc.encode(CN, tc.GB18030, bom=True) == CN.encode("gb18030")


def test_encode_unencodable_reports_first_character():
    with pytest.raises(errors.QSError) as exc:
        tc.encode("繁體与简体", tc.BIG5, bom=False)
    assert str(exc.value) == "有些字元無法用 Big5 表示（例如「与」），建議改用 UTF-8。"


@pytest.mark.parametrize(
    ("enc", "name"),
    [
        ("big5hkscs", "Big5"),
        ("mac_roman", "Mac Roman"),
        ("latin-1", "ISO-8859-1"),
        ("ascii", "ASCII"),
        ("koi8_r", "KOI8-R"),
    ],
)
def test_display_names(enc, name):
    assert tc.display_name(enc) == name


# ------------------------------------------------------------------ 寬鬆解碼（字幕、預覽用）


def test_lenient_rejects_binary():
    assert tc.decode_lenient(b"\x00\x01\x02binary\x00\xff") is None
    assert tc.looks_binary(b"abc\x00def")
    assert not tc.looks_binary(b"")


def test_lenient_normalizes_newlines():
    d = tc.decode_lenient("第一行\r\n第二行\r第三行".encode("cp950"))
    assert d is not None
    assert d.text == "第一行\n第二行\n第三行"
    assert d.line_ending == "混合"
    assert d.encoding == "Big5"


def test_lenient_utf16_guess_and_bom():
    text = "1\n00:00:01,000 --> 00:00:02,000\nHello there\n"
    d = tc.decode_lenient(text.encode("utf-16-le"))
    assert d is not None and d.text == text and d.encoding == "UTF-16 LE"
    d = tc.decode_lenient(b"\xfe\xff" + text.encode("utf-16-be"))
    assert d is not None and d.text == text and d.encoding == "UTF-16 BE"


def test_lenient_utf8_names():
    d = tc.decode_lenient(b"plain ascii\n")
    assert d is not None and d.encoding == "ASCII" and d.line_ending == "LF"
    d = tc.decode_lenient("中文\r\n".encode())
    assert d is not None and d.encoding == "UTF-8" and d.line_ending == "CRLF"
    d = tc.decode_lenient(b"\xef\xbb\xbf" + "中文".encode())
    assert d is not None and d.encoding == "UTF-8（BOM）" and d.text == "中文"
    assert tc.decode_lenient(b"\xef\xbb\xbf\xff\xfe\xfd") is None


def test_lenient_utf32():
    d = tc.decode_lenient(b"\xff\xfe\x00\x00" + "字幕".encode("utf-32-le"))
    assert d is not None and d.text == "字幕" and d.encoding == "UTF-32 LE"
    d = tc.decode_lenient(b"\x00\x00\xfe\xff" + "字幕".encode("utf-32-be"))
    assert d is not None and d.text == "字幕" and d.encoding == "UTF-32 BE"


def test_lenient_latin1_fallback():
    d = tc.decode_lenient(b"\x81\x20\x81\x20\x81\x20")
    assert d is not None
    assert d.text == b"\x81\x20\x81\x20\x81\x20".decode("latin-1")
    assert d.encoding == "ISO Latin-1"


def test_looks_like_text():
    assert tc.looks_like_text(b"")
    assert tc.looks_like_text(b"\xff\xfeA\x00")
    assert tc.looks_like_text("中文\n".encode())
    assert not tc.looks_like_text(b"abc\x00")
    assert not tc.looks_like_text(bytes(range(1, 9)) * 10)
