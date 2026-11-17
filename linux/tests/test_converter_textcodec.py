"""萬用轉換：文字編碼偵測、換行判斷與編碼輸出（對應原版 CVTextCodec）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from quickseemd.core import errors
from quickseemd.core.converter import textcodec as tc


def test_utf8_bom_is_detected_first() -> None:
    d = tc.decode_bytes(b"\xef\xbb\xbf\xe4\xb8\xad\xe6\x96\x87\n")
    assert d.text == "中文\n"
    assert d.encoding == "utf-8"
    assert d.had_bom is True
    assert d.encoding_name == "UTF-8"


def test_utf16_le_bom() -> None:
    d = tc.decode_bytes(b"\xff\xfe" + "測試\n".encode("utf-16-le"))
    assert d.text == "測試\n"
    assert d.encoding_name == "UTF-16"
    assert d.had_bom is True


def test_utf16_be_bom() -> None:
    d = tc.decode_bytes(b"\xfe\xff" + "測試".encode("utf-16-be"))
    assert d.text == "測試"
    assert d.encoding_name == "UTF-16"


def test_utf32_bom_before_utf16() -> None:
    data = "ab".encode("utf-32")  # 開頭 FF FE 00 00
    d = tc.decode_bytes(data)
    assert d.text == "ab"
    assert d.encoding_name == "UTF-32"


def test_empty_is_utf8_without_bom() -> None:
    d = tc.decode_bytes(b"")
    assert (d.text, d.encoding, d.had_bom) == ("", "utf-8", False)
    assert d.line_ending is tc.LineEnding.NONE


def test_plain_utf8() -> None:
    d = tc.decode_bytes("繁體 English".encode())
    assert d.encoding_name == "UTF-8"
    assert d.had_bom is False


def test_bomless_utf16_le_by_zero_pattern() -> None:
    # 純 ASCII 的 UTF-16 同時也是合法的 UTF-8（原版同樣如此），所以放非 ASCII 字元；
    # 門檻是整數除法（樣本 / 50），太短的檔案原版也判斷不出來
    d = tc.decode_bytes(("hello wörld, café text " * 5).encode("utf-16-le"))
    assert d.text.startswith("hello wörld")
    assert d.encoding == "utf-16-le"
    assert d.had_bom is False


def test_bomless_utf16_be_by_zero_pattern() -> None:
    d = tc.decode_bytes(("hello wörld, café text " * 5).encode("utf-16-be"))
    assert d.text.startswith("hello wörld")
    assert d.encoding == "utf-16-be"


def test_big5_sample(samples: Path) -> None:
    d = tc.decode_file(samples / "big5.txt")
    assert d.encoding_name == "Big5"
    assert "繁體中文" in d.text


@pytest.mark.parametrize(
    ("text", "codec", "name"),
    [
        ("简体中文测试，这是一个文件。我们今天去公园散步。", "gb18030", "GB18030"),
        ("繁體中文測試，這是一個檔案。我們今天去公園散步。", "cp950", "Big5"),
        ("日本語のテキストです。テストを行います。", "cp932", "Shift-JIS"),
        ("한국어 텍스트입니다. 테스트를 합니다.", "euc_kr", "EUC-KR"),
    ],
)
def test_statistical_detection(text: str, codec: str, name: str) -> None:
    d = tc.decode_bytes(text.encode(codec))
    assert d.encoding_name == name
    assert d.text == text


def test_short_big5_prefers_chinese() -> None:
    d = tc.decode_bytes("測試".encode("cp950"))
    assert d.encoding_name == "Big5"
    assert d.text == "測試"


def test_forced_encoding_is_used() -> None:
    raw = "測試".encode("cp950")
    d = tc.decode_bytes(raw, forced="big5hkscs")
    assert d.text == "測試"
    assert d.encoding_name == "Big5"


def test_forced_encoding_failure_message() -> None:
    with pytest.raises(errors.QSError) as exc:
        tc.decode_bytes(b"\xff\xff\xff", forced="utf-8")
    assert str(exc.value) == "無法讀取檔案內容：無法以 UTF-8 解讀這個檔案，請改用其他來源編碼。"


def test_forced_codec_mapping() -> None:
    assert tc.forced_codec("auto") is None
    assert tc.forced_codec(None) is None
    assert tc.forced_codec("big5") == "big5hkscs"
    assert tc.forced_codec("shift_jis") == "cp932"
    assert tc.forced_codec("euc-kr") == "euc_kr"
    assert tc.forced_codec("cp1252") == "cp1252"
    assert tc.forced_codec("mac_roman") == "mac_roman"
    assert tc.forced_codec("utf-16") == "utf-16"
    # 原版命令列的寫法也接受
    assert tc.forced_codec("shiftJIS") == "cp932"
    assert tc.forced_codec("windows1252") == "cp1252"
    assert tc.forced_codec("nonsense") is None


def test_undecodable_raises() -> None:
    # 每一種候選編碼都解不開：0x80 在 cp1252 有定義，但 0x81 沒有，加上 NUL 讓 UTF-16 判斷不成立
    data = b"\x81\x81\x81\x81\x81\x8d\x8f\x90\x9d" * 3
    with pytest.raises(errors.QSError) as exc:
        tc.decode_bytes(data)
    assert "無法辨識文字編碼，請在選項中手動指定來源編碼。" in str(exc.value)


@pytest.mark.parametrize(
    ("text", "ending"),
    [
        ("a\nb\n", tc.LineEnding.LF),
        ("a\r\nb\r\n", tc.LineEnding.CRLF),
        ("a\rb\r", tc.LineEnding.CR),
        ("a\r\nb\n", tc.LineEnding.MIXED),
        ("abc", tc.LineEnding.NONE),
    ],
)
def test_detect_line_ending(text: str, ending: tc.LineEnding) -> None:
    assert tc.detect_line_ending(text) is ending


def test_line_ending_labels() -> None:
    assert tc.LineEnding.MIXED.value == "混合"
    assert tc.LineEnding.NONE.value == "—"


def test_normalize_line_endings() -> None:
    assert tc.normalize_line_endings("a\r\nb\rc\n", tc.LineEnding.LF) == "a\nb\nc\n"
    assert tc.normalize_line_endings("a\nb\r\n", tc.LineEnding.CRLF) == "a\r\nb\r\n"
    assert tc.normalize_line_endings("a\nb", tc.LineEnding.CR) == "a\rb"


def test_encode_utf8_with_and_without_bom() -> None:
    assert tc.encode("中", "utf-8", bom=True) == b"\xef\xbb\xbf\xe4\xb8\xad"
    assert tc.encode("中", "utf-8", bom=False) == b"\xe4\xb8\xad"


def test_encode_utf16_is_always_le_with_bom() -> None:
    assert tc.encode("A", "utf-16", bom=False) == b"\xff\xfeA\x00"
    assert tc.encode("A", "utf-16-be", bom=True) == b"\xff\xfeA\x00"


def test_encode_failure_names_first_bad_char() -> None:
    with pytest.raises(errors.QSError) as exc:
        tc.encode("abc😀def", "cp950", bom=False)
    assert str(exc.value) == "有些字元無法用 Big5 表示（例如「😀」），建議改用 UTF-8。"


@pytest.mark.parametrize(
    ("codec", "name"),
    [
        ("utf-8", "UTF-8"),
        ("utf-16-le", "UTF-16"),
        ("utf-32", "UTF-32"),
        ("big5hkscs", "Big5"),
        ("cp950", "Big5"),
        ("gb18030", "GB18030"),
        ("cp932", "Shift-JIS"),
        ("euc_kr", "EUC-KR"),
        ("cp1252", "Windows-1252"),
        ("mac_roman", "Mac Roman"),
        ("latin_1", "ISO-8859-1"),
        ("ascii", "ASCII"),
        ("koi8_r", "KOI8-R"),
    ],
)
def test_display_names(codec: str, name: str) -> None:
    assert tc.display_name(codec) == name


def test_decode_file_missing_raises_unreadable(tmp_path: Path) -> None:
    with pytest.raises(errors.QSError) as exc:
        tc.decode_file(tmp_path / "nope.txt")
    assert str(exc.value).startswith("無法讀取檔案內容：")


def test_utf16_sample(samples: Path) -> None:
    d = tc.decode_file(samples / "utf16.txt")
    assert d.encoding_name == "UTF-16"
    assert d.had_bom
    assert d.line_ending is tc.LineEnding.LF
