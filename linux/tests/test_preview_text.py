"""預覽核心：文字解碼、格式化、頁面骨架。"""

from __future__ import annotations

from datetime import datetime
from pathlib import Path

import pytest

from quickseemd.core.preview import fmt, page, text


# ------------------------------------------------------------------ 文字解碼


def test_looks_binary_rules() -> None:
    assert text.looks_binary(b"") is False
    assert text.looks_binary(b"abc\x00def") is True
    assert text.looks_binary("一般文字\n".encode()) is False
    # ESC（27）、Tab、換頁、CR、LF 不算控制字元
    assert text.looks_binary(b"\x1b[31mred\x1b[0m\t\x0c\r\n" * 10) is False
    # 控制字元超過 2% 就是二進位
    assert text.looks_binary(b"\x01" * 3 + b"a" * 97) is True
    assert text.looks_binary(b"\x01" * 2 + b"a" * 98) is False


def test_utf16_without_bom_is_not_binary() -> None:
    data = "hello world, UTF-16".encode("utf-16-le")
    assert text.looks_binary(data) is False
    decoded = text.decode(data, truncated=False)
    assert decoded is not None
    assert decoded.encoding == "UTF-16 LE"
    assert decoded.text == "hello world, UTF-16"
    be = "hello world, UTF-16".encode("utf-16-be")
    assert text.decode(be, truncated=False).encoding == "UTF-16 BE"


@pytest.mark.parametrize(
    ("raw", "label", "expected"),
    [
        (b"plain", "ASCII", "plain"),
        ("中文".encode(), "UTF-8", "中文"),
        (b"\xef\xbb\xbfbom", "UTF-8（BOM）", "bom"),
        (b"\xff\xfe" + "雙".encode("utf-16-le"), "UTF-16 LE", "雙"),
        (b"\xfe\xff" + "雙".encode("utf-16-be"), "UTF-16 BE", "雙"),
        (b"\xff\xfe\x00\x00" + "四".encode("utf-32-le"), "UTF-32 LE", "四"),
        (b"\x00\x00\xfe\xff" + "四".encode("utf-32-be"), "UTF-32 BE", "四"),
    ],
)
def test_decode_labels(raw: bytes, label: str, expected: str) -> None:
    decoded = text.decode(raw, truncated=False)
    assert decoded is not None
    assert decoded.encoding == label
    assert decoded.text == expected


def test_decode_binary_returns_none() -> None:
    assert text.decode(b"\x00\x01\x02\x03" * 10, truncated=False) is None


def test_decode_truncated_utf8_tolerates_cut_character() -> None:
    raw = "你好".encode()[:-1]
    assert text.decode(raw, truncated=False).encoding != "UTF-8"
    decoded = text.decode(raw, truncated=True)
    assert decoded.text == "你"
    assert decoded.encoding == "UTF-8"
    assert decoded.truncated is True


def test_decode_big5_sample(samples: Path) -> None:
    decoded = text.decode((samples / "big5.txt").read_bytes(), truncated=False)
    assert decoded.encoding == "繁體中文（Big 5）"
    assert "繁體中文" in decoded.text


@pytest.mark.parametrize(
    ("src", "out", "label"),
    [
        ("a\r\nb", "a\nb", "CRLF"),
        ("a\rb", "a\nb", "CR"),
        ("a\nb", "a\nb", "LF"),
        ("a\nb\r\n", "a\nb\n", "混合"),
        ("abc", "abc", "LF"),
        ("", "", "LF"),
    ],
)
def test_normalize_newlines(src: str, out: str, label: str) -> None:
    assert text.normalize_newlines(src) == (out, label)


def test_decode_reports_line_ending() -> None:
    decoded = text.decode(b"a\r\nb\r\n", truncated=False)
    assert decoded.text == "a\nb\n"
    assert decoded.line_ending == "CRLF"


# ------------------------------------------------------------------ 格式化


@pytest.mark.parametrize(
    ("n", "label"),
    [
        (0, "0 位元組"),
        (1023, "1023 位元組"),
        (1024, "1 KB"),
        (1500, "2 KB"),
        (8192, "8 KB"),
        (999_999, "1 MB"),
        (1_500_000, "1.5 MB"),
        (2_000_000, "2 MB"),
        (2_097_152, "2.1 MB"),
        (3_250_000_000, "3.25 GB"),
        (5_000_000_000_000, "5 TB"),
    ],
)
def test_bytes(n: int, label: str) -> None:
    assert fmt.bytes_label(n) == label


def test_number_and_permissions() -> None:
    assert fmt.number(1234567) == "1,234,567"
    assert fmt.number(12) == "12"
    assert fmt.permissions(0o755) == "rwxr-xr-x（755）"
    assert fmt.permissions(0o100644) == "rw-r--r--（644）"
    assert fmt.permissions(None) == "—"


def test_dates() -> None:
    assert fmt.date(datetime(2026, 1, 2, 3, 4, 5)) == "2026/01/02 03:04"
    assert fmt.date(None) == "—"
    assert fmt.date_long(datetime(2026, 11, 17, 13, 26, 0)) == "2026年11月17日 下午1:26:00"
    assert fmt.date_long(datetime(2026, 11, 17, 0, 5, 9)) == "2026年11月17日 上午12:05:09"
    assert fmt.date_long(datetime(2026, 11, 17, 12, 0, 0)) == "2026年11月17日 下午12:00:00"
    assert fmt.date_long(None) == "—"


# ------------------------------------------------------------------ 頁面骨架


def test_escape_handles_quotes() -> None:
    assert page.escape('<a href="x">&</a>') == "&lt;a href=&quot;x&quot;&gt;&amp;&lt;/a&gt;"


def test_chip_and_notice_markup() -> None:
    assert page.Chip("✓ 有效 JSON", "ok").html == '<span class="chip ok">✓ 有效 JSON</span>'
    assert page.Chip("<b>").html == '<span class="chip ">&lt;b&gt;</span>'
    assert page.Notice("注意", warn=True).html == '<div class="notice warn">注意</div>'
    assert page.Notice("a&b").html == '<div class="notice">a&amp;b</div>'


def test_build_skeleton(tmp_path: Path) -> None:
    f = tmp_path / "a.txt"
    f.write_bytes(b"x" * 2048)
    info = page.FileInfo.of(f)
    p = page.Page(kind="純文字 <x>")
    p.chips = [page.Chip("1 行")]
    p.notices = [page.Notice("提示")]
    p.body = "<div>body</div>"
    doc = page.build(p, info)
    assert doc.html.startswith(
        '<!DOCTYPE html><html><head><meta charset="utf-8">'
        '<meta name="generator" content="QUICK_SEEMD"><style>'
    )
    assert '</style></head><body class=""><header class="qs-bar">' in doc.html
    assert '<span class="kind">純文字 &lt;x&gt;</span><span class="chip ">1 行</span>' in doc.html
    assert doc.html.endswith(
        '<span class="spacer"></span><span class="chip">2 KB</span></header>'
        '<div class="notice">提示</div><div>body</div></body></html>'
    )
    assert '<img class="ico" src="cid:file-icon" alt="">' in doc.html
    assert "file-icon" in doc.attachments
    assert doc.kind_label == "純文字 <x>"
    assert doc.preferred_size == (900, 680)
    assert "<script" not in doc.html.lower()


def test_build_without_icon(tmp_path: Path) -> None:
    f = tmp_path / "b.bin"
    f.write_bytes(b"\x00")
    p = page.Page(kind="二進位檔", show_icon=False, size=(820, 640))
    doc = page.build(p, page.FileInfo.of(f))
    assert 'class="ico"' not in doc.html
    assert "file-icon" not in doc.attachments
    assert doc.preferred_size == (820, 640)


def test_style_has_linux_fonts() -> None:
    from quickseemd.core.preview import style

    css = style.css()
    assert '"Noto Sans CJK TC", Cantarell' in css
    assert '"Noto Sans Mono", "DejaVu Sans Mono"' in css
    assert "table.grid thead th { position: sticky; top: 36px;" in css
    assert ".md li:has(> p > input.task)" in css
    assert css.startswith(":root {")


def test_file_info(tmp_path: Path) -> None:
    f = tmp_path / "Note.MD"
    f.write_text("# hi", "utf-8")
    f.chmod(0o640)
    info = page.FileInfo.of(f)
    assert info.name == "Note.MD"
    assert info.ext == "md"
    assert info.size == 4
    assert info.mode is not None and info.mode & 0o777 == 0o640
    assert info.modified is not None
    assert info.head(2) == b"# "
    virtual = page.FileInfo.virtual("inner.json")
    assert virtual.ext == "json"
    assert virtual.mime is None
    assert virtual.head(10) == b""


def test_kind_description_fallbacks(tmp_path: Path) -> None:
    f = tmp_path / "x.qzq"
    f.write_bytes(b"\x00\x01\x02")
    assert page.FileInfo.of(f).kind_description == "QZQ 檔案"
    g = tmp_path / "noext"
    g.write_bytes(b"\x00\x01\x02")
    assert page.FileInfo.of(g).kind_description == "文件"
