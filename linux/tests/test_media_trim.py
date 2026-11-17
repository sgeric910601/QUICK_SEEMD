"""剪輯影片與剪輯音訊。"""

from __future__ import annotations

import pytest
from conftest import make_audio, make_video
from test_media_support import (
    duration,
    encode_audio,
    make_rotated,
    run_tool,
    streams,
    validate,
    video_stream,
)

from quickseemd.core import errors

# ---------------------------------------------------------------- 剪輯影片


def test_fast_keep_snaps_to_keyframe(video_file, outdir):
    outputs, ctx = run_tool("video.trim", [video_file], outdir, start="0.5", end="1.5")
    out = outputs[0]
    assert out.name == "影片-剪輯.mp4"
    assert abs(duration(out) - 1.5) < 0.2
    assert ctx.last_message.startswith(
        "已剪下 0:00.0–0:01.5，長度 0:01.5（起點對齊關鍵影格）（"
    )
    assert streams(out, "audio")


def test_fast_keep_on_keyframe_has_no_note(video_file, outdir):
    outputs, ctx = run_tool("video.trim", [video_file], outdir, start="1", end="")
    assert abs(duration(outputs[0]) - 1.0) < 0.2
    assert ctx.last_message.startswith("已剪下 0:01.0–0:02.0，長度 0:01.0（")
    assert "關鍵影格" not in ctx.last_message


def test_precise_keep_is_exact(video_file, outdir):
    outputs, ctx = run_tool(
        "video.trim", [video_file], outdir, start="0:00.5", end="1.5", method="precise"
    )
    assert abs(duration(outputs[0]) - 1.0) < 0.1
    assert ctx.last_message.startswith("已剪下 0:00.5–0:01.5，長度 0:01.0（")


def test_fast_remove_joins_parts(video_file, outdir):
    outputs, ctx = run_tool("video.trim", [video_file], outdir, start="0.5", end="1.2", mode="remove")
    # 後段起點對齊 1.0 秒的關鍵影格：留下 0–0.5 與 1.0–2.0
    assert abs(duration(outputs[0]) - 1.5) < 0.2
    assert ctx.last_message.startswith("已刪除 0:00.5–0:01.2，剩下約 0:01.3（")


def test_precise_remove_middle(video_file, outdir):
    outputs, _ = run_tool(
        "video.trim", [video_file], outdir, start="0.5", end="1.2", mode="remove", method="precise"
    )
    out = outputs[0]
    assert abs(duration(out) - 1.3) < 0.1
    assert streams(out, "audio")


def test_precise_remove_start_only(video_file, outdir):
    outputs, _ = run_tool(
        "video.trim", [video_file], outdir, start="0", end="0.6", mode="remove", method="precise"
    )
    assert abs(duration(outputs[0]) - 1.4) < 0.1


def test_precise_remove_on_rotated_silent_video(tmp_path, outdir):
    src = make_rotated(make_video(tmp_path / "a.mp4", audio=False), tmp_path / "直.mp4")
    outputs, _ = run_tool(
        "video.trim", [src], outdir, start="0.5", end="1", mode="remove", method="precise"
    )
    v = video_stream(outputs[0])
    assert (v["width"], v["height"]) == (240, 320)
    assert streams(outputs[0], "audio") == []


def test_fast_keep_without_audio_mov(tmp_path, outdir):
    src = make_video(tmp_path / "無聲.mov", audio=False)
    outputs, _ = run_tool("video.trim", [src], outdir, start="1", end="2")
    assert outputs[0].name == "無聲-剪輯.mov"


def test_trim_errors(video_file, outdir):
    with pytest.raises(errors.QSError, match="^「影片.mp4」選取的範圍太短（至少需要 0.1 秒）。$"):
        run_tool("video.trim", [video_file], outdir, start="1", end="1.05")
    with pytest.raises(errors.QSError, match="^「影片.mp4」不能刪除整段影片。$"):
        run_tool("video.trim", [video_file], outdir, start="0", end="", mode="remove")
    with pytest.raises(errors.QSError, match="時間格式不正確"):
        run_tool("video.trim", [video_file], outdir, start="abc")


def test_trim_validation(video_file):
    assert validate("video.trim", [video_file], start="1", end="1.05") == (
        "結束時間必須比開始時間晚至少 0.1 秒"
    )
    assert validate("video.trim", [video_file], start="5") == "開始時間超過檔案長度（0:02.0）"
    assert validate("video.trim", [video_file], mode="remove") == "不能刪除整段內容"
    assert validate("video.trim", [video_file], start="x") == "時間格式不正確，例如 1:05.5 或 65"
    assert validate("video.trim", [video_file], start="0.5", end="1") is None


def test_trim_batch(video_file, tmp_path, outdir):
    other = make_video(tmp_path / "二.mp4", seconds=3)
    outputs, ctx = run_tool("video.trim", [video_file, other], outdir, start="1", end="2")
    assert [p.name for p in outputs] == ["影片-剪輯.mp4", "二-剪輯.mp4"]
    assert ctx.last_message.startswith("已完成 2 個檔案，共 ")


# ---------------------------------------------------------------- 剪輯音訊


def test_audio_trim_wav_keeps_format(audio_file, outdir):
    outputs, ctx = run_tool("audio.trim", [audio_file], outdir, start="0.5", end="1.5")
    out = outputs[0]
    assert out.name == "聲音-剪輯.wav"
    assert streams(out, "audio")[0]["codec_name"] == "pcm_s16le"
    assert abs(duration(out) - 1.0) < 0.02
    assert ctx.last_message.startswith("已剪輯音訊，長度 0:01.0（")


def test_audio_trim_remove_with_fades(audio_file, outdir):
    outputs, ctx = run_tool(
        "audio.trim",
        [audio_file],
        outdir,
        start="0.5",
        end="1",
        mode="remove",
        fade_in=True,
        fade_out=True,
    )
    assert abs(duration(outputs[0]) - 1.5) < 0.02
    assert ctx.last_message.startswith("已剪輯音訊，長度 0:01.5，含淡入淡出（")


@pytest.mark.parametrize(
    ("name", "codec_args", "out_name", "out_codec"),
    [
        ("歌.mp3", ("-c:a", "libmp3lame", "-b:a", "192k"), "歌-剪輯.mp3", "mp3"),
        ("歌.m4a", ("-c:a", "aac", "-b:a", "128k"), "歌-剪輯.m4a", "aac"),
        ("歌.flac", ("-c:a", "flac"), "歌-剪輯.flac", "flac"),
        ("歌.aiff", ("-c:a", "pcm_s16be"), "歌-剪輯.aiff", "pcm_s16be"),
        ("歌.ogg", ("-c:a", "libvorbis"), "歌-剪輯.m4a", "aac"),
    ],
)
def test_audio_trim_matching_format(tmp_path, outdir, name, codec_args, out_name, out_codec):
    src = encode_audio(make_audio(tmp_path / "s.wav", seconds=3), tmp_path / name, *codec_args)
    outputs, _ = run_tool("audio.trim", [src], outdir, start="1", end="")
    assert outputs[0].name == out_name
    assert streams(outputs[0], "audio")[0]["codec_name"] == out_codec
    assert abs(duration(outputs[0]) - 2.0) < 0.1


def test_audio_trim_errors(audio_file, outdir):
    with pytest.raises(errors.QSError, match="不能刪除整段內容"):
        run_tool("audio.trim", [audio_file], outdir, start="0", end="", mode="remove")
    assert validate("audio.trim", [audio_file], start="3") == "開始時間超過檔案長度（0:02.0）"
