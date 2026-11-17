"""字幕轉換（other.subtitles）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from quickseemd.core import errors
from quickseemd.core.model import ToolContext
from quickseemd.core.options import Kind
from quickseemd.core.tools.other import TOOLS
from quickseemd.core.tools.other.subtitles import convert

SRT = (
    "1\r\n"
    "00:00:01,000 --> 00:00:02,500\r\n"
    "第一句字幕\r\n"
    "\r\n"
    "2\r\n"
    "00:00:03,250 --> 00:00:05,000\r\n"
    "第二句\r\n"
    "兩行\r\n"
)

VTT = (
    "WEBVTT - 標題\n"
    "Kind: captions\n"
    "\n"
    "NOTE 這是註解\n"
    "\n"
    "intro\n"
    "00:01.000 --> 00:02.500 align:start position:10%\n"
    "Hello\n"
    "\n"
    "01:00:03.250 --> 01:00:05.000\n"
    "World\n"
)


def _tool():
    return next(t for t in TOOLS if t.id == "other.subtitles")


def _run(files, out=None, **values):
    t = _tool()
    vals = t.defaults()
    vals.update(values)
    ctx = ToolContext(out)
    try:
        return t.run([Path(f) for f in files], vals, ctx)
    finally:
        ctx.cleanup()


# ------------------------------------------------------------------ 定義


def test_definition():
    t = _tool()
    assert t.title == "字幕轉換"
    assert t.badge == "SRT"
    assert t.action_title == "轉換字幕"
    target = t.option("target")
    assert target.kind is Kind.CARDS
    assert [(c.value, c.title) for c in target.choices] == [
        ("vtt", "WebVTT（.vtt）"),
        ("srt", "SubRip（.srt）"),
    ]
    shift = t.option("shift")
    assert shift.kind is Kind.SLIDER
    assert (shift.minimum, shift.maximum, shift.step, shift.default) == (-10, 10, 0.1, 0.0)
    assert shift.label == "整體提前 / 延後"
    assert shift.display(0.0) == "不調整"
    assert shift.display(1.5) == "+1.5 秒"
    assert shift.display(-0.5) == "-0.5 秒"
    assert shift.display(1e-12) == "不調整"
    assert t.defaults() == {"target": "vtt", "shift": 0.0}


def test_accepts_subtitle_files(tmp_path):
    a = tmp_path / "a.srt"
    a.write_text(SRT)
    b = tmp_path / "b.vtt"
    b.write_text(VTT)
    t = _tool()
    assert t.accepts(a) and t.accepts(b)


# ------------------------------------------------------------------ 轉換規則


def test_srt_to_vtt():
    assert convert(SRT.replace("\r\n", "\n"), to_vtt=True, shift=0) == (
        "WEBVTT\n\n"
        "00:00:01.000 --> 00:00:02.500\n第一句字幕\n\n"
        "00:00:03.250 --> 00:00:05.000\n第二句\n兩行\n"
    )


def test_vtt_to_srt_strips_settings_and_renumbers():
    assert convert(VTT, to_vtt=False, shift=0) == (
        "1\n00:00:01,000 --> 00:00:02,500\nHello\n\n"
        "2\n01:00:03,250 --> 01:00:05,000\nWorld\n"
    )


def test_shift_and_clamp():
    text = "1\n00:00:00,500 --> 00:00:01,999\nA\n"
    assert convert(text, to_vtt=False, shift=-1.0) == "1\n00:00:00,000 --> 00:00:00,999\nA\n"
    assert convert(text, to_vtt=False, shift=0.5) == "1\n00:00:01,000 --> 00:00:02,499\nA\n"
    big = "1\n00:59:59,900 --> 01:00:00,100\nB\n"
    assert convert(big, to_vtt=True, shift=0.2) == (
        "WEBVTT\n\n01:00:00.100 --> 01:00:00.300\nB\n"
    )


def test_millisecond_rounding_caps_at_999():
    text = "1\n00:00:01,000 --> 00:00:02,000\nA\n"
    out = convert(text, to_vtt=False, shift=0.9996)
    assert "00:00:01,999 --> 00:00:02,999" in out


def test_blocks_without_arrow_are_dropped():
    text = "垃圾\n\n1\n00:00:01,000 --> 00:00:02,000\nA\n\n\n\n2\nno time\n"
    assert convert(text, to_vtt=False, shift=0) == "1\n00:00:01,000 --> 00:00:02,000\nA\n"


def test_empty_input():
    assert convert("", to_vtt=True, shift=0) == "WEBVTT\n\n\n"
    assert convert("", to_vtt=False, shift=0) == "\n"


# ------------------------------------------------------------------ 工具


def test_tool_srt_to_vtt_names_and_encoding(tmp_path, outdir):
    src = tmp_path / "電影.srt"
    src.write_bytes(SRT.encode("cp950"))
    [out] = _run([src], outdir)
    assert out == outdir / "電影.vtt"
    assert out.read_bytes().decode("utf-8").startswith("WEBVTT\n\n00:00:01.000")
    assert "第二句\n兩行" in out.read_text("utf-8")


def test_tool_same_extension_gets_tag(tmp_path):
    src = tmp_path / "a.srt"
    src.write_bytes(SRT.encode())
    [out] = _run([src], target="srt", shift=2.0)
    assert out == tmp_path / "a-已調整.srt"
    assert "00:00:03,000 --> 00:00:04,500" in out.read_text("utf-8")
    assert src.read_bytes() == SRT.encode()


def test_tool_utf16_input(tmp_path, outdir):
    src = tmp_path / "b.vtt"
    src.write_bytes(b"\xff\xfe" + VTT.encode("utf-16-le"))
    [out] = _run([src], outdir, target="srt")
    assert out.name == "b.srt"
    assert out.read_text("utf-8").startswith("1\n00:00:01,000 --> 00:00:02,500\nHello")


def test_tool_binary_is_unreadable(tmp_path):
    src = tmp_path / "bad.srt"
    src.write_bytes(b"\x00\x01\x02\x03" * 100)
    with pytest.raises(errors.QSError) as exc:
        _run([src])
    assert str(exc.value) == "無法讀取檔案內容：bad.srt"
