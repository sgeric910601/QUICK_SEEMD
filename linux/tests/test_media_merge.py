"""合併影片與取消長時間的轉檔。"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest
from conftest import make_video
from test_media_support import duration, note_texts, streams, tool, validate, video_stream

from quickseemd.core import errors, process
from quickseemd.core.model import ToolContext


@pytest.mark.slow
def test_merge_letterboxes_and_fills_silence(video_file, tmp_path, outdir):
    portrait = make_video(tmp_path / "直式.mp4", seconds=1, size=(240, 320), audio=False, fps=30)
    t = tool("video.merge")
    ctx = ToolContext(outdir)
    outputs = t.run([video_file, portrait], t.defaults(), ctx)
    out = outputs[0]
    assert out.name == "影片-合併.mp4"
    v = video_stream(out)
    assert (v["width"], v["height"], v["codec_name"]) == (320, 240, "h264")
    a = streams(out, "audio")[0]
    assert (a["codec_name"], a["channels"]) == ("aac", 2)
    assert abs(duration(out) - 3.0) < 0.2
    assert ctx.last_message.startswith("已合併 2 個影片：長度 0:03，320×240（")


@pytest.mark.slow
def test_merge_hd_hevc_without_audio(tmp_path, outdir):
    a = make_video(tmp_path / "a.mov", seconds=1, audio=False)
    b = make_video(tmp_path / "b.mov", seconds=1, audio=False, size=(640, 360))
    t = tool("video.merge")
    values = {**t.defaults(), "size": "hd", "codec": "hevc"}
    outputs = t.run([a, b], values, ToolContext(outdir))
    v = video_stream(outputs[0])
    assert (v["width"], v["height"], v["codec_name"]) == (960, 720, "hevc")
    assert streams(outputs[0], "audio") == []


def test_merge_errors(video_file, audio_file, outdir):
    t = tool("video.merge")
    with pytest.raises(errors.QSError, match="^至少需要 2 個影片才能合併。$"):
        t.run([video_file], t.defaults(), ToolContext(outdir))
    with pytest.raises(errors.QSError, match="^「聲音.wav」沒有影像軌，無法合併。$"):
        t.run([video_file, audio_file], t.defaults(), ToolContext(outdir))
    assert validate("video.merge", [video_file]) == "至少需要 2 個影片"


def test_merge_note(video_file, tmp_path):
    other = make_video(tmp_path / "b.mp4", seconds=1)
    notes = note_texts("video.merge", [video_file, other], size="fhd")
    assert "輸出 1440×1080（MP4）" in notes


# ---------------------------------------------------------------- 取消


@pytest.mark.slow
def test_cancel_long_transcode_leaves_nothing(tmp_path, outdir):
    src = make_video(tmp_path / "長.mp4", seconds=20, size=(640, 480))
    cancel = threading.Event()
    seen: list[float] = []

    def reporter(fraction: float, message: str | None) -> None:
        seen.append(fraction)
        if fraction > 0.02:
            cancel.set()

    ctx = ToolContext(outdir, reporter, cancel)
    t = tool("video.convert")
    values = {**t.defaults(), "format": "mp4hevc"}
    started = time.monotonic()
    with pytest.raises(errors.Cancelled):
        t.run([src], values, ctx)
    ctx.cleanup()
    assert time.monotonic() - started < 30
    assert seen and max(seen) < 0.9
    assert process.SPAWNED == set()
    assert list(outdir.iterdir()) == []
    leftovers = [p for p in Path(tmp_path).iterdir() if p.name.startswith(".quickseemd")]
    assert leftovers == []


def test_cancel_before_start(video_file, outdir):
    cancel = threading.Event()
    cancel.set()
    ctx = ToolContext(outdir, None, cancel)
    t = tool("video.mute")
    with pytest.raises(errors.Cancelled):
        t.run([video_file], t.defaults(), ctx)
    assert list(outdir.iterdir()) == []
