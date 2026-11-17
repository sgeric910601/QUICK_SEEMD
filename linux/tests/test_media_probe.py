"""讀取影音資訊（ffprobe）、選項畫面的 inspect 與工具清單。"""

from __future__ import annotations

import os

import pytest
from conftest import make_audio, make_image, make_video
from test_media_support import encode_audio, make_rotated, run_ffmpeg, tool

from quickseemd.core import errors, icons, quickactions, registry
from quickseemd.core.options import Kind
from quickseemd.core.result import format_size
from quickseemd.core.tools.media import probe as mprobe
from quickseemd.core.tools.media.probe import MediaInfo

MEDIA_IDS = registry.ORDER["media"]


def test_probe_video(video_file):
    info = mprobe.probe(video_file)
    assert (info.has_video, info.has_audio) == (True, True)
    assert info.display_size == (320, 240)
    assert info.video_codec == "h264" and info.codec_kind == "h264"
    assert abs(info.duration - 2.0) < 0.1
    assert info.fps == 25
    assert info.video_bitrate > 0
    assert (info.audio_codec, info.sample_rate, info.channels) == ("aac", 44_100, 1)
    assert info.summary == f"320×240 · 0:02 · H.264 · {format_size(video_file.stat().st_size)}"


def test_probe_audio_summary(audio_file):
    info = mprobe.probe(audio_file)
    assert not info.has_video and info.has_audio
    assert info.audio_codec_title == "PCM"
    assert info.summary == f"0:02 · PCM · 44.1 kHz · {format_size(audio_file.stat().st_size)}"
    with pytest.raises(errors.QSError, match="沒有影像軌"):
        info.require_video()


def test_probe_rotated_display_size(video_file, tmp_path):
    info = mprobe.probe(make_rotated(video_file, tmp_path / "r.mp4", ccw=90))
    assert info.rotation == 90
    assert info.display_size == (240, 320)
    assert info.resolution_text == "240×320"


def test_probe_cover_art_is_not_video(tmp_path):
    wav = make_audio(tmp_path / "a.wav", seconds=1)
    cover = make_image(tmp_path / "cover.png", size=(64, 64))
    mp3 = tmp_path / "封面.mp3"
    run_ffmpeg(
        "-i",
        str(wav),
        "-i",
        str(cover),
        "-map",
        "0",
        "-map",
        "1",
        "-c:a",
        "libmp3lame",
        "-c:v",
        "png",
        "-disposition:v",
        "attached_pic",
        str(mp3),
    )
    info = mprobe.probe(mp3)
    assert not info.has_video
    assert info.audio_codec_title == "MP3"
    assert info.audio_bitrate > 0


def test_probe_bit_depth(tmp_path):
    wav = make_audio(tmp_path / "a.wav", seconds=1)
    deep = encode_audio(wav, tmp_path / "24.wav", "-c:a", "pcm_s24le")
    assert mprobe.probe(deep).bit_depth == 24
    flac = encode_audio(wav, tmp_path / "16.flac", "-c:a", "flac")
    assert mprobe.probe(flac).bit_depth == 16
    assert mprobe.probe(wav).bit_depth == 16


def test_probe_errors(tmp_path):
    with pytest.raises(errors.QSError, match="^找不到檔案「沒有.mp4」"):
        mprobe.probe(tmp_path / "沒有.mp4")
    bad = tmp_path / "亂碼.mov"
    bad.write_bytes(os.urandom(4096))
    with pytest.raises(errors.QSError, match="^無法讀取「亂碼.mov」的影音格式"):
        mprobe.probe(bad)


def test_media_info_round_trip(video_file):
    info = mprobe.probe(video_file)
    assert MediaInfo.from_dict(info.to_dict()) == info


def test_keyframes(video_file):
    info = mprobe.probe(video_file)
    assert mprobe.keyframes(video_file) == [0.0, 1.0]
    assert mprobe.keyframe_at_or_before(info, 1.5) == 1.0
    assert mprobe.keyframe_at_or_before(info, 0.9) == 0.0
    assert mprobe.keyframe_at_or_before(info, 1.0) == 1.0
    assert mprobe.keyframe_at_or_before(info, 0) == 0


def test_source_summary_variants(video_file, tmp_path):
    other = make_video(tmp_path / "二.mp4", seconds=1)
    infos = mprobe.probe_all([video_file, other, tmp_path / "不存在.mp4"])
    assert len(infos) == 2
    size = video_file.stat().st_size + other.stat().st_size
    assert mprobe.source_summary(infos, 3) == f"3 個檔案 · 共 0:03 · {format_size(size)}"
    assert mprobe.source_summary([], 0) == "尚未加入檔案"
    assert mprobe.source_summary([], 2) == "2 個檔案"


def test_inspect_contract_for_trim(video_file):
    info = tool("video.trim").inspect([video_file])
    assert info["count"] == 1
    assert abs(info["duration"] - 2.0) < 0.1
    assert (info["width"], info["height"], info["has_audio"]) == (320, 240, True)
    assert info["keyframes"] == [0.0, 1.0]
    assert info["summary"].startswith("320×240 · 0:02 · H.264")


def test_inspect_audio_trim_without_keyframes(audio_file):
    info = tool("audio.trim").inspect([audio_file])
    assert info["has_audio"] and not info["has_video"]
    assert "keyframes" not in info


def test_inspect_unreadable_only(tmp_path):
    bad = tmp_path / "x.mp4"
    bad.write_bytes(b"not a movie")
    info = tool("video.convert").inspect([bad])
    assert info == {"count": 1, "items": [], "summary": "1 個檔案"}


# ---------------------------------------------------------------- 工具清單


def test_all_media_tools_registered_in_order():
    ids = [t.id for t in registry.all_tools() if t.category.id == "media"]
    assert ids == list(MEDIA_IDS)


def test_tool_titles_and_icons():
    titles = {t.id: (t.title, t.icon) for t in registry.all_tools() if t.category.id == "media"}
    assert titles["video.compress"] == ("壓縮影片", "arrow.down.right.and.arrow.up.left")
    assert titles["video.frames"] == ("擷取畫面", "camera.viewfinder")
    assert titles["audio.trim"] == ("剪輯音訊", "waveform.badge.minus")
    for title, icon in titles.values():
        assert icon in icons.SF_TO_FREEDESKTOP
        assert title


def test_presets_match_quick_actions():
    for act in quickactions.ACTIONS:
        if act.tool_id in MEDIA_IDS:
            t = tool(act.tool_id)
            assert t.presets.get(act.id) == act.preset
            values = t.values_for_preset(act.id)
            for key, value in act.preset.items():
                assert values[key] == value


def test_choice_defaults_and_custom_views():
    for tid in MEDIA_IDS:
        t = tool(tid)
        assert t.requires == ("ffmpeg", "ffprobe")
        for opt in t.options:
            if opt.kind in (Kind.CARDS, Kind.CHIPS, Kind.SEGMENTED):
                assert opt.default in [c.value for c in opt.choices], f"{tid}.{opt.key}"
    assert tool("video.trim").custom_view == "media-trim"
    assert tool("audio.trim").custom_view == "audio-trim"
    assert tool("video.merge").minimum_files == 2 and tool("video.merge").reorderable
    trim_keys = {o.key for o in tool("video.trim").options}
    assert {"start", "end", "mode", "method"} <= trim_keys
    audio_keys = {o.key for o in tool("audio.trim").options}
    assert {"start", "end", "mode", "fade_in", "fade_out"} <= audio_keys


def test_action_titles():
    assert tool("video.convert").title_for_action({"format": "prores"}, {}) == "轉成 ProRes 422"
    extract = tool("video.extract-audio")
    assert extract.title_for_action({"format": "flac"}, {}) == "擷取成 FLAC"
    assert tool("audio.convert").title_for_action({"format": "mp3"}, {}) == "轉成 MP3"


def test_input_types_accept_media(video_file, audio_file):
    assert tool("video.convert").accepts(video_file)
    assert not tool("video.convert").accepts(audio_file)
    assert tool("audio.convert").accepts(audio_file)


def test_frame_rate_text_is_sanitized():
    fields = mprobe._video_fields(
        {"avg_frame_rate": "25/1,setpts=0", "r_frame_rate": "x", "width": 2, "height": 2},
        0.0,
        0.0,
    )
    assert fields["fps_rational"] == "30"
    ok = mprobe._video_fields({"avg_frame_rate": "30000/1001"}, 0.0, 0.0)
    assert ok["fps_rational"] == "30000/1001"
    assert abs(ok["fps"] - 29.97) < 0.01


def test_anamorphic_sample_aspect_ratio(tmp_path):
    src = tmp_path / "寬像素.mp4"
    run_ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "testsrc=duration=1:size=320x240:rate=25,setsar=4/3",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv420p",
        str(src),
    )
    info = mprobe.probe(src)
    assert info.sar == pytest.approx(4 / 3)
    assert info.display_size == (427, 240)
