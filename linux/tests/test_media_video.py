"""影片工具（轉換、壓縮、移除聲音、旋轉、速度、尺寸）的整合測試：實際跑 ffmpeg，用 ffprobe 驗證。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from conftest import make_video
from test_media_support import (
    duration,
    make_noisy_video,
    make_rotated,
    rotation,
    run_tool,
    sha,
    streams,
    validate,
    video_stream,
)

from quickseemd.core import errors
from quickseemd.core.result import format_size

# ---------------------------------------------------------------- 轉換影片格式


def test_convert_default_remuxes_with_same_ext_tag(video_file, outdir):
    before = sha(video_file)
    outputs, ctx = run_tool("video.convert", [video_file], outdir)
    assert [p.name for p in outputs] == ["影片-已轉換.mp4"]
    out = outputs[0]
    assert ctx.last_message == (
        f"已轉成 MP4 (H.264)（直接轉封裝，畫質無損）（{format_size(out.stat().st_size)}）"
    )
    assert video_stream(out)["codec_name"] == "h264"
    assert streams(out, "audio")[0]["codec_name"] == "aac"
    assert sha(video_file) == before


def test_convert_never_overwrites(video_file, outdir):
    first, _ = run_tool("video.convert", [video_file], outdir)
    first_hash = sha(first[0])
    second, _ = run_tool("video.convert", [video_file], outdir)
    assert second[0].name == "影片-已轉換 (1).mp4"
    assert sha(first[0]) == first_hash


def test_convert_writes_next_to_input_by_default(video_file):
    outputs, _ = run_tool("video.convert", [video_file], None, format="mov")
    assert outputs[0] == video_file.parent / "影片.mov"
    assert not [p for p in video_file.parent.iterdir() if p.name.startswith(".quickseemd")]


def test_convert_to_hevc_reencodes(video_file, outdir):
    outputs, ctx = run_tool("video.convert", [video_file], outdir, format="mp4hevc")
    out = outputs[0]
    v = video_stream(out)
    assert (v["codec_name"], v["codec_tag_string"]) == ("hevc", "hvc1")
    assert out.name == "影片-已轉換.mp4"
    assert ctx.last_message.startswith("已轉成 MP4 (HEVC)，320×240（")


def test_convert_without_audio_tags_silent(video_file, outdir):
    outputs, ctx = run_tool("video.convert", [video_file], outdir, keep_audio=False)
    assert outputs[0].name == "影片-無聲.mp4"
    assert streams(outputs[0], "audio") == []
    assert "直接轉封裝" in ctx.last_message


def test_convert_scales_down_short_side(tmp_path, outdir):
    src = make_video(tmp_path / "寬.mp4", seconds=1, size=(1280, 720))
    outputs, ctx = run_tool("video.convert", [src], outdir, resolution=480)
    assert outputs[0].name == "寬-480p.mp4"
    v = video_stream(outputs[0])
    assert (v["width"], v["height"]) == (852, 480)
    assert ctx.last_message.startswith("已轉成 MP4 (H.264)，852×480（")


def test_convert_never_upscales(video_file, outdir):
    outputs, ctx = run_tool("video.convert", [video_file], outdir, resolution=1080)
    assert outputs[0].name == "影片-已轉換.mp4"
    assert "直接轉封裝" in ctx.last_message


def test_convert_reencode_when_remux_disabled(video_file, outdir):
    outputs, ctx = run_tool("video.convert", [video_file], outdir, allow_remux=False)
    assert ctx.last_message.startswith("已轉成 MP4 (H.264)，320×240")
    assert abs(duration(outputs[0]) - 2.0) < 0.15


def test_convert_prores(video_file, outdir):
    outputs, _ = run_tool("video.convert", [video_file], outdir, format="prores")
    out = outputs[0]
    assert out.name == "影片.mov"
    assert video_stream(out)["codec_name"] == "prores"
    assert streams(out, "audio")[0]["codec_name"] == "pcm_s16le"


def test_convert_rotated_source_comes_out_upright(video_file, tmp_path, outdir):
    rotated = make_rotated(video_file, tmp_path / "直式.mp4")
    outputs, ctx = run_tool("video.convert", [rotated], outdir, allow_remux=False)
    v = video_stream(outputs[0])
    assert (v["width"], v["height"]) == (240, 320)
    assert rotation(v) == 0
    assert "240×320" in ctx.last_message


def test_convert_preset_and_batch_summary(tmp_path, outdir):
    a = make_video(tmp_path / "a.mov", seconds=1)
    b = make_video(tmp_path / "b.mov", seconds=1)
    outputs, ctx = run_tool("video.convert", [a, b], outdir, preset="video.convert-mp4")
    assert [p.name for p in outputs] == ["a.mp4", "b.mp4"]
    total = sum(p.stat().st_size for p in outputs)
    assert ctx.last_message == f"已完成 2 個檔案，共 {format_size(total)}"


def test_convert_rejects_audio_only(audio_file, outdir):
    with pytest.raises(errors.QSError, match="沒有影像軌"):
        run_tool("video.convert", [audio_file], outdir)


def test_convert_rejects_garbage(tmp_path, outdir):
    bad = tmp_path / "壞掉.mp4"
    bad.write_bytes(os.urandom(2048))
    with pytest.raises(errors.QSError, match="無法讀取「壞掉.mp4」的影音格式"):
        run_tool("video.convert", [bad], outdir)


def test_convert_bad_option_value(video_file, outdir):
    with pytest.raises(errors.QSError, match="選項「format」的值不正確"):
        run_tool("video.convert", [video_file], outdir, format="avi")


# ---------------------------------------------------------------- 壓縮影片


@pytest.mark.slow
def test_compress_balanced_shrinks(tmp_path, outdir):
    src = make_noisy_video(tmp_path / "大.mp4")
    outputs, ctx = run_tool("video.compress", [src], outdir, preset="video.compress-medium")
    out = outputs[0]
    assert out.name == "大-已壓縮.mp4"
    old, new = src.stat().st_size, out.stat().st_size
    saved = int((1 - new / old) * 100)
    assert ctx.last_message == f"從 {format_size(old)} 縮小到 {format_size(new)}，省下 {saved}%"
    assert new < old * 0.95


@pytest.mark.slow
def test_compress_hevc_target_size(tmp_path, outdir):
    src = make_noisy_video(tmp_path / "大.mov")
    outputs, _ = run_tool(
        "video.compress", [src], outdir, codec="hevc", use_target=True, target_mb="0.2"
    )
    out = outputs[0]
    assert out.name == "大-已壓縮.mov"
    assert video_stream(out)["codec_name"] == "hevc"
    assert out.stat().st_size < 260_000


def test_compress_not_smaller_gives_error_and_no_file(tmp_path, outdir):
    # 影像低於壓縮下限（80 kbps）、聲音已是 64 kbps：照計畫壓不會小 5% 以上
    src = make_noisy_video(tmp_path / "精簡.mp4", bitrate="30k", audio_bitrate="64k")
    with pytest.raises(errors.QSError) as exc:
        run_tool("video.compress", [src], outdir)
    assert str(exc.value) == (
        "「精簡.mp4」已經很精簡，壓縮後沒有明顯變小（不到 5%），因此保留原檔、沒有產生新檔案。"
        "可以試試「高度壓縮」、HEVC 或指定目標大小。"
    )
    assert list(outdir.iterdir()) == []


def test_compress_target_too_small(video_file, outdir):
    with pytest.raises(errors.QSError, match="^「影片.mp4」目標太小：這段影片至少需要約 0.0 MB。$"):
        run_tool("video.compress", [video_file], outdir, use_target=True, target_mb="0.001")


def test_compress_target_already_smaller(video_file, outdir):
    with pytest.raises(errors.QSError) as exc:
        run_tool("video.compress", [video_file], outdir, use_target=True, target_mb="25")
    assert str(exc.value) == (
        "「影片.mp4」已經小於目標大小（25 MB）或無法再明顯變小，因此保留原檔、沒有產生新檔案。"
    )


def test_compress_validation(video_file):
    assert validate("video.compress", [video_file], use_target=True, target_mb="abc") == (
        "請輸入目標大小（MB）"
    )
    assert validate("video.compress", [video_file], use_target=True, target_mb="0.001") == (
        "目標太小：這段影片至少需要約 0.0 MB。"
    )
    assert validate("video.compress", [video_file], use_target=True, target_mb="3,5") is None
    assert validate("video.compress", [video_file]) is None


# ---------------------------------------------------------------- 移除聲音


def test_mute_copies_video(video_file, outdir):
    outputs, ctx = run_tool("video.mute", [video_file], outdir, preset="video.remove-audio")
    out = outputs[0]
    assert out.name == "影片-無聲.mp4"
    assert streams(out, "audio") == []
    assert video_stream(out)["codec_name"] == "h264"
    assert ctx.last_message.startswith("已移除聲音，影像未重新編碼、畫質不變（")


def test_mute_keeps_mov_container(tmp_path, outdir):
    src = make_video(tmp_path / "片.mov", seconds=1)
    outputs, _ = run_tool("video.mute", [src], outdir)
    assert outputs[0].name == "片-無聲.mov"


# ---------------------------------------------------------------- 旋轉／翻轉


def test_rotate_fast_sets_display_matrix(video_file, outdir):
    outputs, ctx = run_tool("video.rotate", [video_file], outdir, preset="video.rotate-right")
    out = outputs[0]
    assert out.name == "影片-已旋轉.mp4"
    v = video_stream(out)
    assert (v["width"], v["height"], rotation(v)) == (320, 240, -90)
    assert ctx.last_message.startswith("向右轉 90°完成（只改方向標記，未重新編碼）（")


def test_rotate_fast_combines_with_existing(video_file, tmp_path, outdir):
    rotated = make_rotated(video_file, tmp_path / "直式.mp4", ccw=-90)
    outputs, _ = run_tool("video.rotate", [rotated], outdir, rotation="right")
    assert abs(rotation(video_stream(outputs[0]))) == 180


def test_rotate_reencode_left(video_file, outdir):
    outputs, ctx = run_tool("video.rotate", [video_file], outdir, rotation="left", method="reencode")
    v = video_stream(outputs[0])
    assert (v["width"], v["height"], rotation(v)) == (240, 320, 0)
    assert ctx.last_message.startswith("向左轉 90°完成（")
    assert "只改方向標記" not in ctx.last_message


def test_flip_always_reencodes(video_file, outdir):
    outputs, ctx = run_tool("video.rotate", [video_file], outdir, rotation="flip_h", method="fast")
    assert outputs[0].name == "影片-已翻轉.mp4"
    v = video_stream(outputs[0])
    assert (v["width"], v["height"]) == (320, 240)
    assert ctx.last_message.startswith("水平翻轉完成（")


def test_rotate_action_title_and_visibility():
    from test_media_support import tool

    t = tool("video.rotate")
    assert t.title_for_action({"rotation": "flip_v", "method": "fast"}, {}) == "翻轉影片"
    assert t.title_for_action({"rotation": "right", "method": "fast"}, {}) == "旋轉影片"
    method = t.option("method")
    assert method.is_visible({"rotation": "right"}, {})
    assert not method.is_visible({"rotation": "flip_h"}, {})


# ---------------------------------------------------------------- 速度


def test_speed_double(video_file, outdir):
    outputs, ctx = run_tool("video.speed", [video_file], outdir)
    out = outputs[0]
    assert out.name == "影片-2倍速.mp4"
    assert abs(duration(out) - 1.0) < 0.15
    assert streams(out, "audio")
    assert ctx.last_message.startswith("2× 速度：0:02 → 0:01（")


@pytest.mark.slow
def test_speed_slow_motion_varispeed(video_file, outdir):
    outputs, ctx = run_tool("video.speed", [video_file], outdir, rate=0.5, keep_pitch=False)
    out = outputs[0]
    assert out.name == "影片-0.5倍速.mp4"
    assert abs(duration(out) - 4.0) < 0.2
    assert abs(float(video_stream(out)["avg_frame_rate"].split("/")[0]) - 25) < 1
    assert ctx.last_message.startswith("0.5× 速度：0:02 → 0:04（")


def test_speed_quarter_without_audio(video_file, outdir):
    outputs, _ = run_tool("video.speed", [video_file], outdir, rate=4.0, keep_audio=False)
    assert streams(outputs[0], "audio") == []
    assert abs(duration(outputs[0]) - 0.5) < 0.15


def test_speed_rejects_one(video_file, outdir):
    with pytest.raises(errors.QSError):
        run_tool("video.speed", [video_file], outdir, rate=1.0)


# ---------------------------------------------------------------- 調整尺寸


def test_resize_default_upscales_to_720(video_file, outdir):
    outputs, ctx = run_tool("video.resize", [video_file], outdir)
    out = outputs[0]
    assert out.name == "影片-960x720.mp4"
    v = video_stream(out)
    assert (v["width"], v["height"]) == (960, 720)
    assert ctx.last_message.startswith("已調整為 960×720（")


@pytest.mark.parametrize("fit", ["letterbox", "crop"])
def test_resize_aspect(video_file, outdir, fit):
    outputs, _ = run_tool("video.resize", [video_file], outdir, aspect="wide", resolution=480, fit=fit)
    out = outputs[0]
    assert out.name == "影片-16x9-480p.mp4"
    v = video_stream(out)
    assert (v["width"], v["height"]) == (852, 480)


def test_resize_validation_and_note(video_file):
    from test_media_support import note_texts

    assert validate("video.resize", [video_file], aspect="original", resolution=0) == (
        "請選擇新的比例或解析度"
    )
    assert validate("video.resize", [video_file]) is None
    assert "320×240 → 960×720" in note_texts("video.resize", [video_file])


def test_tools_reject_missing_file(tmp_path, outdir):
    with pytest.raises(errors.QSError, match="找不到檔案「不見了.mp4」"):
        run_tool("video.mute", [tmp_path / "不見了.mp4"], outdir)


def test_odd_size_source_rounds_to_even(tmp_path, outdir):
    from test_media_support import run_ffmpeg

    src = tmp_path / "奇數.mp4"
    run_ffmpeg(
        "-f",
        "lavfi",
        "-i",
        "testsrc=duration=1:size=321x241:rate=25",
        "-c:v",
        "libx264",
        "-pix_fmt",
        "yuv444p",
        str(src),
    )
    outputs, _ = run_tool("video.convert", [src], outdir, allow_remux=False)
    v = video_stream(outputs[0])
    assert (v["width"], v["height"]) == (320, 240)


def test_inputs_untouched_after_all(video_file, outdir):
    before = sha(video_file)
    mtime = os.stat(video_file).st_mtime_ns
    run_tool("video.mute", [video_file], outdir)
    run_tool("video.rotate", [video_file], outdir)
    assert sha(video_file) == before
    assert os.stat(video_file).st_mtime_ns == mtime
    assert Path(video_file).exists()
