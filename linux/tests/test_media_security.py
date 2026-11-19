"""惡意輸入：播放清單或串接腳本偽裝成影片時，ffmpeg／ffprobe 不可以連網，且要回報無法讀取。"""

from __future__ import annotations

import http.server
import os
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest
from test_media_support import tool

from quickseemd.core import backends, errors, process
from quickseemd.core.model import ToolContext
from quickseemd.core.tools.media import engine
from quickseemd.core.tools.media import probe as mprobe


class _Recorder(http.server.BaseHTTPRequestHandler):
    hits: list[str] = []

    def do_GET(self) -> None:  # noqa: N802 — http.server 的固定名稱
        type(self).hits.append(self.path)
        self.send_response(404)
        self.end_headers()

    def log_message(self, *args: object) -> None:  # 不輸出到終端機
        return


@pytest.fixture
def listener() -> Iterator[tuple[str, list[str]]]:
    """只綁 127.0.0.1 的測試 HTTP 伺服器，記錄所有請求。"""
    hits: list[str] = []
    handler = type("Handler", (_Recorder,), {"hits": hits})
    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{server.server_address[1]}", hits
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=5)


def _hls(base: str) -> str:
    return (
        "#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:2\n#EXT-X-MEDIA-SEQUENCE:0\n"
        f"#EXTINF:2.0,\n{base}/seg0.ts\n#EXTINF:2.0,\n{base}/seg1.ts\n#EXT-X-ENDLIST\n"
    )


def _ffconcat(base: str) -> str:
    return f"ffconcat version 1.0\nfile '{base}/clip.mp4'\nduration 2\n"


HOSTILE = [
    ("x.mp4", _hls),
    ("播放清單.m3u8", _hls),
    ("串接.mp4", _ffconcat),
    ("串接.ffconcat", _ffconcat),
]


@pytest.mark.parametrize(("name", "content"), HOSTILE)
def test_probe_never_touches_network(tmp_path, listener, name, content):
    base, hits = listener
    path = tmp_path / name
    path.write_text(content(base))
    with pytest.raises(errors.QSError, match=f"^無法讀取「{name}」的影音格式"):
        mprobe.probe(path)
    assert mprobe.keyframes(path) == []
    assert hits == []


@pytest.mark.parametrize(("name", "content"), HOSTILE)
def test_tools_never_touch_network(tmp_path, listener, name, content):
    base, hits = listener
    path = tmp_path / name
    path.write_text(content(base))
    outdir = tmp_path / "out"
    outdir.mkdir()
    t = tool("video.convert")
    with pytest.raises(errors.QSError, match=f"^無法讀取「{name}」的影音格式"):
        t.run([path], t.defaults(), ToolContext(outdir))
    assert hits == []
    assert list(outdir.iterdir()) == []


def test_ffmpeg_inputs_are_whitelisted_even_if_probe_passes(tmp_path, listener, video_file):
    """就算資訊讀得到，真正執行的 ffmpeg 也只准讀本機檔案（直接餵播放清單給執行層）。"""
    base, hits = listener
    playlist = tmp_path / "list.m3u8"
    playlist.write_text(_hls(base))
    out = tmp_path / "out.mp4"
    ctx = ToolContext(tmp_path)
    args = engine.input_args(playlist) + ["-c", "copy", "-f", "mp4"]
    with pytest.raises(errors.QSError):
        engine.run_ffmpeg(ctx, args, out, duration=1.0)
    assert hits == []
    assert engine.input_args(video_file)[:2] == ["-protocol_whitelist", "file,pipe"]


def test_every_input_carries_the_whitelist():
    args = engine.input_args(Path("/tmp/a.mp4"), start=1.5)
    i = args.index("-i")
    assert args[:2] == ["-protocol_whitelist", "file,pipe"]
    assert args[i - 2 : i] == ["-ss", "1.500000"]


def test_probe_timeout_reports_unreadable(tmp_path, monkeypatch, video_file):
    """ffprobe 卡住時：逾時結束子程序並回報無法讀取（inspect 不會一直等）。"""
    fakebin = tmp_path / "fakebin"
    fakebin.mkdir()
    hang = fakebin / "ffprobe"
    hang.write_text("#!/bin/sh\nexec /bin/sleep 30\n")
    hang.chmod(0o755)
    monkeypatch.setenv("QUICKSEEMD_TOOL_PATH", f"{fakebin}:{os.environ['QUICKSEEMD_TOOL_PATH']}")
    monkeypatch.setattr(mprobe, "PROBE_TIMEOUT", 0.3)
    backends.refresh()
    try:
        started = time.monotonic()
        with pytest.raises(errors.QSError, match="^無法讀取「影片.mp4」的影音格式"):
            mprobe.probe(video_file)
        assert mprobe.keyframes(video_file) == []
        assert time.monotonic() - started < 10
        assert process.SPAWNED == set()
    finally:
        monkeypatch.undo()
        backends.refresh()
