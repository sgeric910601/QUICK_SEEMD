"""文字編碼轉換工具（other.text-encoding）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from quickseemd.core import errors
from quickseemd.core.model import ToolContext
from quickseemd.core.options import Kind
from quickseemd.core.tools.other import TOOLS

TW = "第一行：繁體中文測試，臺灣的天氣很好。\n第二行：資料處理完成。\n"
CN = "第一行：简体中文测试，今天的天气很好。\n第二行：数据处理完成。\n"


def _tool():
    return next(t for t in TOOLS if t.id == "other.text-encoding")


def _run(files, target, out=None):
    t = _tool()
    values = t.defaults()
    values["target"] = target
    ctx = ToolContext(out)
    try:
        return t.run([Path(f) for f in files], values, ctx)
    finally:
        ctx.cleanup()


def _write(path: Path, data: bytes) -> Path:
    path.write_bytes(data)
    return path


# ------------------------------------------------------------------ 定義


def test_definition():
    t = _tool()
    assert t.title == "文字編碼轉換"
    assert t.subtitle == "亂碼救星：Big5、GB18030、UTF-16 等編碼與 Windows / Mac 換行互轉。"
    assert t.badge == "TXT"
    assert t.action_title == "轉換編碼"
    target = t.option("target")
    assert target is not None and target.kind is Kind.CARDS
    assert [c.value for c in target.choices] == [
        "utf8",
        "utf8-bom",
        "big5",
        "gb18030",
        "utf16",
        "crlf",
        "lf",
    ]
    titles = {c.value: c.title for c in target.choices}
    assert titles["gb18030"] == "GB18030"
    assert titles["crlf"] == "Windows 換行（CRLF）"
    assert titles["lf"] == "Mac / Linux 換行（LF）"
    assert t.defaults()["target"] == "utf8"
    assert t.presets == {"other.to-utf8": {"target": "utf8"}}
    notes = [o.label for o in t.options if o.kind is Kind.NOTE]
    assert notes == ["來源編碼會自動偵測（UTF-8、Big5、GB18030、Shift-JIS、UTF-16…）。"]


# ------------------------------------------------------------------ 轉換


def test_big5_to_utf8_keeps_name_in_output_folder(tmp_path, outdir):
    src = _write(tmp_path / "舊檔.txt", TW.encode("cp950"))
    [out] = _run([src], "utf8", outdir)
    assert out == outdir / "舊檔.txt"
    assert out.read_bytes() == TW.encode("utf-8")


def test_same_folder_gets_number_and_source_untouched(tmp_path):
    raw = TW.encode("cp950")
    src = _write(tmp_path / "a.txt", raw)
    [out] = _run([src], "utf8")
    assert out == tmp_path / "a (1).txt"
    assert src.read_bytes() == raw
    assert out.read_text("utf-8") == TW


def test_utf8_bom(tmp_path, outdir):
    src = _write(tmp_path / "表.csv", "名稱,數量\n蘋果,3\n".encode("cp950"))
    [out] = _run([src], "utf8-bom", outdir)
    assert out.read_bytes() == b"\xef\xbb\xbf" + "名稱,數量\n蘋果,3\n".encode()


def test_utf8_to_big5_round_trip(tmp_path, outdir):
    src = _write(tmp_path / "a.txt", TW.encode())
    [out] = _run([src], "big5", outdir)
    assert out.read_bytes() == TW.encode("cp950")
    back_dir = tmp_path / "back"
    back_dir.mkdir()
    [back] = _run([out], "utf8", back_dir)
    assert back.read_text("utf-8") == TW


def test_gb18030_round_trip(tmp_path, outdir):
    src = _write(tmp_path / "简体.txt", CN.encode())
    [out] = _run([src], "gb18030", outdir)
    assert out.read_bytes() == CN.encode("gb18030")
    other = tmp_path / "o2"
    other.mkdir()
    [back] = _run([out], "utf8", other)
    assert back.read_bytes() == CN.encode()


def test_utf16_output_is_le_with_bom(tmp_path, outdir):
    src = _write(tmp_path / "a.txt", TW.encode())
    [out] = _run([src], "utf16", outdir)
    data = out.read_bytes()
    assert data[:2] == b"\xff\xfe"
    assert data[2:].decode("utf-16-le") == TW


def test_crlf_keeps_legacy_encoding(tmp_path, outdir):
    src = _write(tmp_path / "a.txt", TW.encode("cp950"))
    [out] = _run([src], "crlf", outdir)
    assert out.read_bytes() == TW.replace("\n", "\r\n").encode("cp950")


def test_lf_on_utf16_be_becomes_le_with_bom(tmp_path, outdir):
    text = "line one\r\nline two\r\n"
    src = _write(tmp_path / "a.txt", b"\xfe\xff" + text.encode("utf-16-be"))
    [out] = _run([src], "lf", outdir)
    assert out.read_bytes() == b"\xff\xfe" + "line one\nline two\n".encode("utf-16-le")


def test_lf_keeps_utf8_bom(tmp_path, outdir):
    src = _write(tmp_path / "a.txt", b"\xef\xbb\xbf" + "a\r\nb\r\n".encode())
    [out] = _run([src], "lf", outdir)
    assert out.read_bytes() == b"\xef\xbb\xbfa\nb\n"


@pytest.mark.parametrize(
    ("data", "target", "label"),
    [
        ("已經是 UTF-8\n".encode(), "utf8", "UTF-8"),
        (b"\xef\xbb\xbf" + "x\n".encode(), "utf8-bom", "UTF-8（含 BOM）"),
        (b"\xff\xfe" + "x\n".encode("utf-16-le"), "utf16", "UTF-16"),
        (TW.encode("cp950"), "big5", "Big5"),
        (CN.encode("gb18030"), "gb18030", "GB18030"),
        (b"a\nb\n", "lf", "LF 換行（macOS／Linux）"),
        (b"no newline", "lf", "LF 換行（macOS／Linux）"),
        (b"a\r\nb\r\n", "crlf", "CRLF 換行（Windows）"),
    ],
)
def test_target_equal_to_current_is_refused(tmp_path, data, target, label):
    src = _write(tmp_path / "a.txt", data)
    with pytest.raises(errors.QSError) as exc:
        _run([src], target)
    assert str(exc.value) == f"「a.txt」無法轉換成 {label}。"
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.txt"]


def test_unencodable_character(tmp_path, outdir):
    src = _write(tmp_path / "a.txt", "繁體这是简体\n".encode())
    with pytest.raises(errors.QSError) as exc:
        _run([src], "big5", outdir)
    assert str(exc.value) == "有些字元無法用 Big5 表示（例如「这」），建議改用 UTF-8。"
    assert list(outdir.iterdir()) == []


def test_binary_file_is_refused(tmp_path):
    src = _write(tmp_path / "data.bin", bytes(range(256)) * 4)
    with pytest.raises(errors.QSError) as exc:
        _run([src], "utf8")
    assert str(exc.value) == "「data.bin」無法轉換成 UTF-8。"


def test_several_files(tmp_path, outdir):
    a = _write(tmp_path / "a.txt", TW.encode("cp950"))
    b = _write(tmp_path / "b.txt", CN.encode("gb18030"))
    outs = _run([a, b], "utf8", outdir)
    assert [o.name for o in outs] == ["a.txt", "b.txt"]
    assert outs[1].read_text("utf-8") == CN


def test_large_file_skips_offer_check(tmp_path, outdir, monkeypatch):
    from quickseemd.core.tools.other import encoding

    monkeypatch.setattr(encoding, "OFFER_CHECK_LIMIT", 4)
    src = _write(tmp_path / "a.txt", "已經是 UTF-8\n".encode())
    [out] = _run([src], "utf8", outdir)
    assert out.read_text("utf-8") == "已經是 UTF-8\n"
