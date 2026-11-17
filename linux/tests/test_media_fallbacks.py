"""無法直接複製串流時改用重新編碼，以及選項畫面的動態說明。"""

from __future__ import annotations

from pathlib import Path

import pytest
from test_media_support import (
    duration,
    make_noisy_video,
    note_texts,
    run_ffmpeg,
    run_tool,
    streams,
    tool,
    video_stream,
)

from quickseemd.core.tools.media import probe as mprobe


@pytest.fixture
def ffv1_mkv(tmp_path: Path) -> Path:
    """MKV 裡的 FFV1 影像：不能直接放進 MP4，快速模式一定會失敗。"""
    path = tmp_path / "無損.mkv"
    run_ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "testsrc=duration=2:size=320x240:rate=25",
        "-f",
        "lavfi",
        "-i",
        "sine=duration=2",
        "-c:v",
        "ffv1",
        "-c:a",
        "pcm_s16le",
        "-shortest",
        str(path),
    )
    return path


def test_probe_mkv_estimates_video_bitrate(ffv1_mkv):
    info = mprobe.probe(ffv1_mkv)
    assert info.video_codec_title == "FFV1"
    assert info.codec_kind == "other"
    assert info.video_bitrate > 0


def test_mute_falls_back_to_reencode(ffv1_mkv, outdir):
    outputs, ctx = run_tool("video.mute", [ffv1_mkv], outdir)
    out = outputs[0]
    assert out.name == "無損-無聲.mp4"
    assert video_stream(out)["codec_name"] == "h264"
    assert streams(out, "audio") == []
    assert ctx.last_message.startswith("已移除聲音（")


def test_rotate_fast_falls_back_to_reencode(ffv1_mkv, outdir):
    outputs, ctx = run_tool("video.rotate", [ffv1_mkv], outdir, rotation="upside_down")
    v = video_stream(outputs[0])
    assert (v["width"], v["height"], v["codec_name"]) == (320, 240, "h264")
    assert ctx.last_message.startswith("旋轉 180°完成（")
    assert "只改方向標記" not in ctx.last_message


def test_trim_fast_falls_back_to_precise(ffv1_mkv, outdir):
    outputs, ctx = run_tool("video.trim", [ffv1_mkv], outdir, start="0.5", end="1.5")
    assert outputs[0].name == "無損-剪輯.mp4"
    assert abs(duration(outputs[0]) - 1.0) < 0.1
    assert ctx.last_message.startswith("已剪下 0:00.5–0:01.5，長度 0:01.0（")


def test_convert_other_codec_transcodes_with_audio(ffv1_mkv, outdir):
    outputs, ctx = run_tool("video.convert", [ffv1_mkv], outdir)
    out = outputs[0]
    assert out.name == "無損.mp4"
    assert streams(out, "audio")[0]["codec_name"] == "aac"
    assert ctx.last_message.startswith("已轉成 MP4 (H.264)，320×240（")


def test_compress_notes(tmp_path):
    src = make_noisy_video(tmp_path / "大.mp4")
    notes = note_texts("video.compress", [src])
    estimate = next(n for n in notes if n.startswith("預估輸出"))
    assert "約可節省" in estimate
    # 聲音不超過來源 AAC 的實際位元率（約 122 kbps）
    assert "320×240 · 影像 150 kbps · 聲音 12" in estimate
    assert "幾乎所有裝置、瀏覽器與通訊軟體都能播放。" in notes
    hevc = note_texts("video.compress", [src], codec="hevc")
    assert "HEVC 檔案約再小 35%，但較舊的裝置或 Windows 可能無法播放。" in hevc
    tiny = note_texts("video.compress", [src], use_target=True, target_mb="0.01")
    assert any("目標太小" in n for n in tiny)


def test_compress_level_disabled_with_target():
    level = tool("video.compress").option("level")
    assert level.is_enabled({"use_target": False}, {})
    assert not level.is_enabled({"use_target": True}, {})
    assert tool("video.compress").option("target_mb").is_visible({"use_target": True}, {})


def test_speed_and_convert_notes(video_file):
    notes = note_texts("video.speed", [video_file], rate=0.5)
    assert "長度 0:02 → 0:04" in notes
    assert "人聲不會變尖或變低沉" in notes
    varispeed = note_texts("video.speed", [video_file], keep_pitch=False)
    assert "像錄音帶一樣，速度與音高一起改變" in varispeed
    silent = note_texts("video.speed", [video_file], keep_audio=False)
    assert not any("音高" in n for n in silent)
    convert = note_texts("video.convert", [video_file], format="mov")
    assert "QuickTime 影片（H.264），適合 Mac 與 iPhone 剪輯。" in convert
    assert any(n.startswith("320×240 · 0:02 · H.264") for n in convert)


def test_bad_option_values_are_rejected(video_file, outdir):
    from quickseemd.core import errors

    with pytest.raises(errors.QSError, match="選項「rate」的值不正確"):
        run_tool("video.speed", [video_file], outdir, rate="fast")
    with pytest.raises(errors.QSError, match="選項「resolution」的值不正確"):
        run_tool("video.resize", [video_file], outdir, resolution=True)
    with pytest.raises(errors.QSError, match="請輸入目標大小（MB）"):
        run_tool("video.compress", [video_file], outdir, use_target=True, target_mb="")
