"""播放器不能連網：副檔名是 .mp3 但內容是 HLS 播放清單的檔案，不能讓 GStreamer 去抓片段。

GStreamer 的元件排名是整個程序共用的，所以實際播放都在子程序裡做；
本機（127.0.0.1）的測試伺服器記錄收到的請求。
"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
from local_http import RecordingServer

import conftest

PROBE = r"""
import json, sys, time
import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gio, Gst
Gst.init(None)
guarded = sys.argv[2] == "1"
refused = []
if guarded:
    from quickseemd.previewer import mediaguard
    mediaguard.harden_registry()
pb = Gst.ElementFactory.make("playbin", None)
pb.set_property("uri", Gio.File.new_for_path(sys.argv[1]).get_uri())
for prop in ("video-sink", "audio-sink"):
    pb.set_property(prop, Gst.ElementFactory.make("fakesink", None))
if guarded:
    mediaguard.PipelineGuard(refused.append).attach(pb)
pb.set_state(Gst.State.PAUSED)
bus = pb.get_bus()
deadline = time.time() + 6
error = None
kinds = Gst.MessageType.ERROR | Gst.MessageType.ASYNC_DONE | Gst.MessageType.EOS
while time.time() < deadline:
    msg = bus.timed_pop_filtered(200 * Gst.MSECOND, kinds)
    if msg is not None and msg.type == Gst.MessageType.ERROR:
        error = msg.parse_error()[0].message
        break
pb.set_state(Gst.State.NULL)
print(json.dumps({"refused": refused, "error": error}))
"""


def _play(path: Path, guarded: bool) -> dict:
    env = dict(os.environ, PYTHONPATH=str(conftest.LINUX_DIR))
    env.pop("DISPLAY", None)
    res = subprocess.run(
        [sys.executable, "-c", PROBE, str(path), "1" if guarded else "0"],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    )
    assert res.returncode == 0, res.stderr[-3000:]
    return json.loads(res.stdout.strip().splitlines()[-1])


def hls_disguised_as_mp3(path: Path, server: RecordingServer) -> Path:
    playlist = (
        "#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:10\n#EXT-X-MEDIA-SEQUENCE:0\n"
        f"#EXTINF:10.0,\n{server.url('/segment0.ts')}\n"
        f"#EXTINF:10.0,\n{server.url('/segment1.ts')}\n#EXT-X-ENDLIST\n"
    )
    path.write_text(playlist, "utf-8")
    return path


@pytest.mark.slow
def test_unguarded_playbin_would_fetch(tmp_path: Path):
    """對照組：沒有防護時真的會連網（證明這個測試抓得到問題）。"""
    with RecordingServer() as server:
        _play(hls_disguised_as_mp3(tmp_path / "track01.mp3", server), guarded=False)
        assert server.requests


@pytest.mark.slow
def test_guarded_playbin_never_fetches(tmp_path: Path):
    with RecordingServer() as server:
        out = _play(hls_disguised_as_mp3(tmp_path / "track01.mp3", server), guarded=True)
        assert server.requests == []
        assert out["error"]  # 播不了，播放器會顯示錯誤卡


def test_guarded_playbin_still_plays_local_audio(tmp_path: Path):
    wav = conftest.make_audio(tmp_path / "聲音.wav", seconds=1)
    out = _play(wav, guarded=True)
    assert out["error"] is None
    assert out["refused"] == []


PROBE_ELEMENTS = r"""
import json
import gi
gi.require_version("Gst", "1.0")
from gi.repository import Gst
Gst.init(None)
from quickseemd.previewer import mediaguard
names = ["filesrc", "souphttpsrc", "udpsrc", "rtspsrc", "hlsdemux", "dashdemux2", "decodebin",
         "queue", "typefind", "fakesink", "giosrc", "uridecodebin"]
out = {}
for n in names:
    el = Gst.ElementFactory.make(n, None)
    out[n] = None if el is None else mediaguard.is_forbidden(el)
changed = mediaguard.harden_registry()
reg = Gst.Registry.get()
ranks = {n: reg.lookup_feature(n).get_rank() for n in mediaguard.NETWORK_DEMUXERS
         if reg.lookup_feature(n) is not None}
print(json.dumps({"forbidden": out, "ranks": ranks, "again": mediaguard.harden_registry()}))
"""


def test_forbidden_elements_and_ranks():
    env = dict(os.environ, PYTHONPATH=str(conftest.LINUX_DIR))
    res = subprocess.run(
        [sys.executable, "-c", PROBE_ELEMENTS],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    )
    assert res.returncode == 0, res.stderr[-3000:]
    out = json.loads(res.stdout.strip().splitlines()[-1])
    forbidden = {k: v for k, v in out["forbidden"].items() if v is not None}
    assert forbidden["filesrc"] is False
    for name in ("souphttpsrc", "udpsrc", "rtspsrc", "hlsdemux", "dashdemux2", "giosrc"):
        if name in forbidden:
            assert forbidden[name] is True, name
    for name in ("decodebin", "queue", "typefind", "fakesink", "uridecodebin"):
        assert forbidden[name] is False, name
    assert out["ranks"] and set(out["ranks"].values()) == {0}
    assert out["again"] == []  # 第二次不用再改
