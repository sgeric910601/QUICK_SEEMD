"""影片轉 GIF 與擷取畫面。"""

from __future__ import annotations

import pytest
from conftest import make_video
from PIL import Image
from test_media_support import count_frames, make_rotated, note_texts, run_tool, validate

from quickseemd.core import errors

# ---------------------------------------------------------------- GIF


def test_gif_default_preset(video_file, outdir):
    outputs, ctx = run_tool("video.to-gif", [video_file], outdir, preset="video.gif-default")
    out = outputs[0]
    assert out.name == "影片.gif"
    with Image.open(out) as img:
        assert img.size == (320, 240)
        assert img.info.get("loop") == 0
    assert count_frames(out) == 20
    assert ctx.last_message.startswith("GIF 動畫：20 格、10 fps、長 2.0 秒、循環播放（")


def test_gif_options_scale_start_and_no_loop(tmp_path, outdir):
    src = make_video(tmp_path / "大.mp4", seconds=3, size=(640, 480))
    outputs, ctx = run_tool(
        "video.to-gif",
        [src],
        outdir,
        start="1",
        length=1.0,
        fps=5,
        width=320,
        color="compact",
        loop=False,
    )
    out = outputs[0]
    with Image.open(out) as img:
        assert img.size == (320, 240)
        assert "loop" not in img.info
    assert count_frames(out) == 5
    assert ctx.last_message.startswith("GIF 動畫：5 格、5 fps、長 1.0 秒（")


def test_gif_original_width_high_quality(video_file, outdir):
    outputs, _ = run_tool("video.to-gif", [video_file], outdir, width=0, color="high", length=0.5)
    with Image.open(outputs[0]) as img:
        assert img.size == (320, 240)
    assert count_frames(outputs[0]) == 5


def test_gif_start_past_end_is_clamped(video_file, outdir):
    outputs, ctx = run_tool("video.to-gif", [video_file], outdir, start="9")
    # 開始被限制在長度 − 0.1 秒：剩 0.1 秒 → 1 格
    assert count_frames(outputs[0]) == 1
    assert ctx.last_message.startswith("GIF 動畫：1 格、10 fps、長 0.1 秒")


def test_gif_validation_and_note(video_file):
    assert validate("video.to-gif", [video_file], start="1.95") == "開始時間超過影片長度"
    assert validate("video.to-gif", [video_file], length=0.1) == "長度至少 0.2 秒"
    assert validate("video.to-gif", [video_file], start="?") == "時間格式不正確，例如 1:05.5 或 65"
    assert validate("video.to-gif", [video_file]) is None
    notes = note_texts("video.to-gif", [video_file])
    assert "共 20 格，輸出 320×240。GIF 最多 256 色，長片段或大尺寸的檔案會很大。" in notes
    assert "播完自動重頭開始" in notes


def test_gif_bad_start_errors(video_file, outdir):
    with pytest.raises(errors.QSError, match="時間格式不正確"):
        run_tool("video.to-gif", [video_file], outdir, start="1:99")


# ---------------------------------------------------------------- 擷取畫面


def test_frames_count_mode_makes_folder(video_file, outdir):
    outputs, ctx = run_tool("video.frames", [video_file], outdir, count=3)
    folder = outputs[0]
    assert folder.name == "影片-畫面" and folder.is_dir()
    names = sorted(p.name for p in folder.iterdir())
    assert names == ["影片-00m00.3s.jpg", "影片-00m01.0s.jpg", "影片-00m01.6s.jpg"]
    assert ctx.last_message == "已擷取 3 張畫面，存在「影片-畫面」資料夾"
    with Image.open(folder / names[0]) as img:
        assert img.format == "JPEG" and img.size == (320, 240)


def test_frames_single_time(video_file, outdir):
    outputs, ctx = run_tool("video.frames", [video_file], outdir, mode="times", times="0:01")
    out = outputs[0]
    assert out.name == "影片-00m01.0s.jpg"
    assert ctx.last_message.startswith("已擷取 0:01.0 的畫面（")


def test_frames_png_max_side(video_file, outdir):
    outputs, _ = run_tool(
        "video.frames", [video_file], outdir, mode="times", times="0.5, 9", format="png",
        max_side=640,
    )
    names = sorted(p.name for p in outputs[0].iterdir())
    # 9 秒超過長度，限制在長度 − 0.05 秒
    assert names == ["影片-00m00.5s.png", "影片-00m01.9s.png"]
    with Image.open(outputs[0] / names[1]) as img:
        assert img.format == "PNG" and img.size == (320, 240)


def test_frames_interval_and_downscale(tmp_path, outdir):
    src = make_video(tmp_path / "寬.mp4", seconds=2, size=(1280, 720))
    outputs, _ = run_tool("video.frames", [src], outdir, mode="interval", interval=1, max_side=640)
    files = sorted(outputs[0].iterdir())
    assert [p.name for p in files] == ["寬-00m00.0s.jpg", "寬-00m01.0s.jpg"]
    with Image.open(files[0]) as img:
        assert img.size == (640, 360)


def test_frames_rotated_upright(video_file, tmp_path, outdir):
    src = make_rotated(video_file, tmp_path / "直式.mp4")
    outputs, _ = run_tool("video.frames", [src], outdir, mode="times", times="1")
    with Image.open(outputs[0]) as img:
        assert img.size == (240, 320)


def test_frames_same_label_never_overwrites(video_file, outdir):
    outputs, _ = run_tool("video.frames", [video_file], outdir, mode="times", times="1, 1.05")
    names = sorted(p.name for p in outputs[0].iterdir())
    assert names == ["影片-00m01.0s (1).jpg", "影片-00m01.0s.jpg"]


def test_frames_errors_and_validation(video_file, outdir):
    with pytest.raises(errors.QSError, match="^時間點格式不正確，請用逗號分隔，例如 0:05, 1:30。$"):
        run_tool("video.frames", [video_file], outdir, mode="times", times="abc")
    assert validate("video.frames", [video_file], mode="times", times="abc") == (
        "時間點格式不正確，例如 0:05, 1:30"
    )
    assert validate("video.frames", [video_file]) is None


def test_frames_notes(video_file, tmp_path):
    assert "將擷取 10 張，存成「影片-畫面」資料夾。" in note_texts("video.frames", [video_file])
    one = note_texts("video.frames", [video_file], mode="times", times="1")
    assert "將擷取 1 張圖片。" in one
    bad = note_texts("video.frames", [video_file], mode="times", times="x")
    assert "格式不正確，請用逗號分隔，例如 0:05, 1:30" in bad
    other = make_video(tmp_path / "b.mp4", seconds=1)
    many = note_texts("video.frames", [video_file, other])
    assert "每個影片擷取多張時，會存成一個「影片名稱-畫面」資料夾。" in many
