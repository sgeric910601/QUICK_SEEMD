"""調整大小、旋轉／翻轉、放大圖片。"""

from __future__ import annotations

import numpy as np
import pytest
from conftest import make_image
from PIL import Image
from test_image_kit import CAMERA_TAGS, digest, exiftool_json, run_tool, tool, with_metadata

from quickseemd.core import backends, errors
from quickseemd.core.tools.image import resize, rotate

# ------------------------------------------------------------------ 調整大小


def _plan(w, h, **kw):
    values = {**tool("image.resize").defaults(), **kw}
    return resize.plan(w, h, resize.ResizeOptions.from_values(values))


def test_plan_math():
    assert _plan(640, 480).canvas == (320, 240)
    assert _plan(640, 480, percent=0, custom_percent=200).canvas == (640, 480)
    assert _plan(640, 480, percent=0, custom_percent=200, no_upscale=False).canvas == (1280, 960)
    px = {"mode": "pixels"}
    assert _plan(640, 480, **px, width=100, height=100).canvas == (100, 75)
    assert _plan(640, 480, **px, width=320, height=0).canvas == (320, 240)
    assert _plan(640, 480, **px, width=0, height=120).canvas == (160, 120)
    assert _plan(640, 480, **px, width=100, height=50, lock_aspect=False).canvas == (100, 50)
    pre = {"mode": "preset"}
    fill = _plan(640, 480, **pre, preset="ig1080")
    assert fill.canvas == (480, 480) and fill.crops
    assert _plan(640, 480, **pre, preset="ig1080", no_upscale=False).canvas == (1080, 1080)
    fit = _plan(640, 480, **pre, preset="ig1080", fit="fit", no_upscale=False)
    assert fit.canvas == (1080, 1080) and fit.scaled == (1080, 810) and fit.offset == (0, 135)
    small_fit = _plan(640, 480, **pre, preset="ig1080", fit="fit")
    assert small_fit.canvas == (640, 640) and small_fit.scaled == (640, 480)
    assert _plan(640, 480, **pre, preset="story", fit="stretch").canvas == (640, 480)
    assert _plan(4000, 3000, **pre, preset="w1920").canvas == (1920, 1440)


def test_resize_half_preset(jpg_file, outdir):
    outs, ctx = run_tool("image.resize", [jpg_file], outdir, action="image.resize-half")
    assert outs[0].name == "相片-已調整大小.jpg"
    with Image.open(outs[0]) as im:
        assert im.size == (320, 240)


def test_resize_fit_pad_transparent_png(png_file, outdir):
    outs, _ = run_tool(
        "image.resize",
        [png_file],
        outdir,
        mode="preset",
        preset="icon512",
        fit="fit",
        pad="transparent",
        no_upscale=False,
    )
    with Image.open(outs[0]) as im:
        assert im.size == (512, 512) and im.mode == "RGBA"
        assert im.getpixel((256, 10))[3] == 0


def test_resize_fit_pad_colour_jpeg(jpg_file, outdir):
    outs, _ = run_tool(
        "image.resize",
        [jpg_file],
        outdir,
        mode="preset",
        preset="icon512",
        fit="fit",
        pad="custom",
        pad_custom="#ff0000",
        no_upscale=False,
    )
    with Image.open(outs[0]) as im:
        r, g, b = im.getpixel((256, 5))
        assert r > 200 and g < 50 and b < 50


def test_resize_nearest_neighbour(tmp_path, outdir):
    p = tmp_path / "px.png"
    Image.fromarray(np.array([[0, 255], [255, 0]], dtype=np.uint8)).convert("RGB").save(p)
    outs, _ = run_tool(
        "image.resize", [p], outdir, percent=0, custom_percent=400, no_upscale=False, smooth=False
    )
    with Image.open(outs[0]) as im:
        assert set(np.asarray(im.convert("L")).ravel().tolist()) == {0, 255}


def test_resize_validation_and_prefill(jpg_file):
    t = tool("image.resize")
    d = {**t.defaults(), "mode": "pixels"}
    assert t.validate([jpg_file], {**d, "width": 0, "height": 0}, {}) == "請輸入寬度或高度"
    assert t.validate([jpg_file], {**d, "width": 40000}, {}) == "尺寸上限為 32768 px"
    assert (
        t.validate([jpg_file], {**d, "lock_aspect": False, "height": 0}, {})
        == "不鎖定比例時請輸入寬度與高度"
    )
    assert t.validate([jpg_file], t.defaults(), {}) is None
    info = t.inspect([jpg_file])
    assert t.prefill([jpg_file], info) == {"width": 640, "height": 480}
    note = [o for o in t.options if o.key == "_preview"][0]
    assert note.note(t.defaults(), info) == ("640 × 480 → 320 × 240 px", False)
    assert note.note(t.defaults(), {**info, "count": 2})[0].startswith("第一張：")
    lock_note = t.option("lock_aspect").note
    assert lock_note(d, {"count": 2}) == ("每張圖片都會等比例縮放到這個範圍內。", False)
    assert lock_note(d, {"count": 1}) is None
    assert t.option("fit").is_visible({**d, "mode": "preset", "preset": "ig1080"}, {})
    assert not t.option("fit").is_visible({**d, "mode": "preset", "preset": "w1920"}, {})


# ------------------------------------------------------------------ 旋轉／翻轉


def test_jpeg_rotate_right_is_lossless(tmp_path, outdir):
    src = with_metadata(make_image(tmp_path / "a.jpg", quality=90), tmp_path / "相片.jpg", *CAMERA_TAGS)
    before = digest(src)
    outs, _ = run_tool("image.rotate", [src], outdir, action="image.rotate-right")
    out = outs[0]
    assert out.name == "相片-已旋轉.jpg"
    assert exiftool_json(out)["EXIF:Model"] == "Cam 1"
    with Image.open(src) as a, Image.open(out) as b:
        assert b.size == (480, 640)
        expect = np.asarray(a.transpose(Image.Transpose.ROTATE_270), dtype=np.int16)
        assert np.abs(expect - np.asarray(b, dtype=np.int16)).mean() < 1.5
    assert digest(src) == before


def test_jpeg_imperfect_size_falls_back(tmp_path, outdir):
    src = make_image(tmp_path / "奇.jpg", size=(641, 479), quality=90)
    outs, _ = run_tool("image.rotate", [src], outdir, angle=180)
    with Image.open(outs[0]) as im:
        assert im.size == (641, 479)


def test_jpeg_with_orientation_uses_pixels(tmp_path, outdir):
    src = with_metadata(make_image(tmp_path / "a.jpg", size=(200, 100), quality=90), tmp_path / "o.jpg", "-Orientation=6", "-n")
    outs, _ = run_tool("image.rotate", [src], outdir, angle=90)
    with Image.open(outs[0]) as im:
        assert im.size == (200, 100)  # 轉正後 100×200，再轉 90° 回到 200×100
        assert im.getexif().get(0x0112, 1) == 1


@pytest.mark.parametrize(
    "angle,fh,fv",
    [(90, False, False), (270, False, False), (180, False, False), (0, True, False), (0, False, True), (90, True, False), (270, True, True)],
)
def test_png_rotations_match_numpy(tmp_path, outdir, angle, fh, fv):
    rng = np.random.default_rng(3)
    arr = rng.integers(0, 255, (5, 7, 3), dtype=np.uint8)
    p = tmp_path / "r.png"
    Image.fromarray(arr).save(p)
    outs, _ = run_tool("image.rotate", [p], outdir, angle=angle, flip_h=fh, flip_v=fv)
    expect = arr
    if fh:
        expect = expect[:, ::-1]
    if fv:
        expect = expect[::-1]
    expect = np.rot90(expect, -angle // 90)
    with Image.open(outs[0]) as im:
        assert np.array_equal(np.asarray(im), expect)
    assert outs[0].name == ("r-已翻轉.png" if angle == 0 else "r-已旋轉.png")


def test_rotate_op_table():
    assert rotate.combined_op(90, False, False) == "rot90"
    assert rotate.combined_op(0, True, True) == "rot180"
    assert rotate.combined_op(90, True, False) == "transverse"
    assert rotate.combined_op(270, True, False) == "transpose"
    assert rotate.combined_op(0, False, False) == "identity"


def test_rotate_validation(jpg_file):
    t = tool("image.rotate")
    assert t.validate([jpg_file], {"angle": 0, "flip_h": False, "flip_v": False}, {}) == (
        "請選擇旋轉或翻轉方式"
    )
    assert t.validate([jpg_file], t.defaults(), {}) is None


def test_rotate_without_jpegtran(tmp_path, outdir, monkeypatch, jpg_file):
    real = backends.find
    monkeypatch.setattr(backends, "find", lambda key: None if key == "jpegtran" else real(key))
    outs, _ = run_tool("image.rotate", [jpg_file], outdir)
    with Image.open(outs[0]) as im:
        assert im.size == (480, 640)


# ------------------------------------------------------------------ 放大圖片


def test_upscale_2x_and_4x(tmp_path, outdir):
    p = make_image(tmp_path / "小.png", size=(64, 48))
    outs, _ = run_tool("image.upscale", [p], outdir)
    assert outs[0].name == "小-放大2倍.png"
    with Image.open(outs[0]) as im:
        assert im.size == (128, 96)
    outs, _ = run_tool("image.upscale", [p], outdir, factor=4, sharpen="strong", denoise=True)
    assert outs[0].name == "小-放大4倍.png"
    with Image.open(outs[0]) as im:
        assert im.size == (256, 192)


def test_upscale_gif_outputs_png(tmp_path, outdir):
    p = tmp_path / "a.gif"
    frames = [Image.new("RGB", (16, 16), c) for c in ("red", "blue")]
    frames[0].save(p, save_all=True, append_images=frames[1:], duration=100)
    outs, _ = run_tool("image.upscale", [p], outdir)
    assert outs[0].name == "a-放大2倍.png"


def test_upscale_limit(tmp_path, outdir):
    big = tmp_path / "大.png"
    Image.new("RGB", (9000, 10), "white").save(big)
    small = make_image(tmp_path / "小.png", size=(32, 32))
    with pytest.raises(errors.QSError, match="放大後為 18000 × 20 px，超過上限 16384 px"):
        run_tool("image.upscale", [big], outdir)
    outs, ctx = run_tool("image.upscale", [big, small], outdir)
    assert [o.name for o in outs] == ["小-放大2倍.png"]
    assert ctx.last_message == (
        "已略過：大.png：放大後為 18000 × 20 px，超過上限 16384 px，請改用較小的倍數"
    )
    t = tool("image.upscale")
    info = t.inspect([big])
    assert t.validate([big], t.defaults(), info) == "放大後超過 16384 px，請改用較小的倍數"


def test_upscale_factor_subtitles(tmp_path):
    p = make_image(tmp_path / "a.png", size=(64, 48))
    t = tool("image.upscale")
    info = t.inspect([p])
    subs = [c.subtitle for c in t.option("factor").current_choices(t.defaults(), info)]
    assert subs == ["128 × 96 px", "256 × 192 px"]
    subs = [c.subtitle for c in t.option("factor").current_choices(t.defaults(), {})]
    assert subs == ["寬高各放大 2 倍", "寬高各放大 4 倍"]
