"""轉換圖片格式與製作圖示。"""

from __future__ import annotations

import pytest
from PIL import Image
from test_image_kit import CAMERA_TAGS, digest, run_tool, tag_names, tool, with_metadata

from quickseemd.core import backends
from quickseemd.core.tools.image import formats


def _note(t, key, values, info=None):
    opt = t.option(key)
    return opt.note(values, info or {}) if opt.note else None


def test_png_to_jpg_flattens_white(png_file):
    outs, ctx = run_tool("image.convert", [png_file])
    assert [o.name for o in outs] == ["透明.jpg"]
    with Image.open(outs[0]) as im:
        assert im.format == "JPEG"
        assert min(im.getpixel((2, 470))) > 245  # 透明的地方變白色
    assert ctx.last_message == "已儲存：透明.jpg"


def test_same_format_never_overwrites(jpg_file):
    before = digest(jpg_file)
    outs, _ = run_tool("image.convert", [jpg_file])
    assert [o.name for o in outs] == ["相片 (1).jpg"]
    assert digest(jpg_file) == before


def test_presets_png_and_heic(jpg_file, outdir):
    outs, _ = run_tool("image.convert", [jpg_file], outdir, action="image.to-png")
    assert outs[0].name == "相片.png"
    with Image.open(outs[0]) as im:
        assert im.format == "PNG"
    if not formats.is_writable("heic"):
        pytest.skip("沒有 heif-enc")
    outs, _ = run_tool("image.convert", [jpg_file], outdir, action="image.to-heic")
    assert outs[0].name == "相片.heic"
    assert b"ftypheic" in outs[0].read_bytes()[:32]


@pytest.mark.parametrize(
    "fmt,pil", [("tiff", "TIFF"), ("gif", "GIF"), ("bmp", "BMP"), ("webp", "WEBP")]
)
def test_other_bitmap_targets(jpg_file, outdir, fmt, pil):
    outs, _ = run_tool("image.convert", [jpg_file], outdir, format=fmt)
    assert outs[0].suffix == "." + formats.get(fmt).ext
    with Image.open(outs[0]) as im:
        assert im.format == pil


def test_pdf_target(jpg_file, outdir):
    outs, _ = run_tool("image.convert", [jpg_file], outdir, format="pdf")
    assert outs[0].name == "相片.pdf"
    assert outs[0].read_bytes().startswith(b"%PDF")


def test_keep_metadata_toggle(tmp_path, jpg_file, outdir):
    meta = with_metadata(jpg_file, tmp_path / "m.jpg", *CAMERA_TAGS)
    keep, _ = run_tool("image.convert", [meta], outdir, format="png")
    assert "Model" in tag_names(keep[0])
    drop, _ = run_tool("image.convert", [meta], outdir, format="webp", keep_metadata=False)
    assert "Model" not in tag_names(drop[0])


def test_custom_background(png_file, outdir):
    outs, _ = run_tool(
        "image.convert", [png_file], outdir, background="custom", background_custom="#00ff00"
    )
    with Image.open(outs[0]) as im:
        r, g, b = im.getpixel((2, 470))
        assert g > 200 and r < 40 and b < 40


def test_animated_gif_to_gif_keeps_animation(tmp_path, outdir):
    p = tmp_path / "動畫.gif"
    frames = [Image.new("RGB", (16, 16), c) for c in ("red", "lime", "blue")]
    frames[0].save(p, save_all=True, append_images=frames[1:], duration=100, loop=0)
    outs, _ = run_tool("image.convert", [p], outdir, format="gif")
    with Image.open(outs[0]) as im:
        assert im.n_frames == 3


def test_ico_and_icns_targets(png_file, outdir):
    ico, _ = run_tool("image.convert", [png_file], outdir, format="ico")
    assert ico[0].name == "透明.ico"
    with Image.open(ico[0]) as im:
        assert im.format == "ICO"
        assert {s[0] for s in im.info["sizes"]} == {16, 32, 48, 64, 128, 256}
    icns, _ = run_tool("image.convert", [png_file], outdir, format="icns")
    assert icns[0].name == "透明.icns"
    with Image.open(icns[0]) as im:
        assert im.format == "ICNS" and im.size == (1024, 1024)


def test_format_chips_follow_writability(monkeypatch):
    t = tool("image.convert")
    opt = t.option("format")
    values = [c.value for c in opt.current_choices(t.defaults(), {})]
    assert values[:2] == ["jpeg", "png"] and "pdf" in values
    monkeypatch.setattr(backends, "find", lambda key: None)
    values = [c.value for c in opt.current_choices(t.defaults(), {})]
    assert "heic" not in values and "avif" not in values


def test_convert_notes_and_visibility():
    t = tool("image.convert")
    d = t.defaults()
    assert t.option("quality").is_visible(d, {})
    assert not t.option("quality").is_visible({**d, "format": "png"}, {})
    assert _note(t, "quality", d) == ("幾乎看不出差異，檔案較大", False)
    assert _note(t, "quality", {**d, "quality": 0.75}) == ("畫質與大小的平衡點", False)
    assert _note(t, "quality", {**d, "quality": 0.5}) == ("檔案最小，細節會變模糊", False)
    assert t.option("background").is_visible(d, {})
    assert not t.option("background").is_visible({**d, "format": "png"}, {})
    assert _note(t, "background", d, {"has_alpha": False}) == (
        "目前的圖片沒有透明背景，不會用到這個設定。",
        False,
    )
    assert _note(t, "background", d, {"has_alpha": True}) is None
    assert _note(t, "keep_metadata", {**d, "format": "gif"}) == (
        "GIF 格式無法保存相片資訊。",
        False,
    )
    hint = [o for o in t.options if o.key == "_format_hint"][0]
    assert hint.note(d, {})[0] == "最通用的相片格式，檔案小，不支援透明。"


# ------------------------------------------------------------------ 製作圖示


def test_make_icon_icns_default(png_file, outdir):
    outs, ctx = run_tool("image.make-icon", [png_file], outdir)
    assert [o.name for o in outs] == ["透明.icns"]
    with Image.open(outs[0]) as im:
        assert im.format == "ICNS"
        sizes = {s[0] * s[2] for s in im.info["sizes"]}
        assert {16, 32, 64, 128, 256, 512, 1024} <= sizes
        im.size = (1024, 1024)
        im.load()
        assert im.getpixel((2, 2))[3] == 0  # 圓角外是透明
        assert im.getpixel((512, 512))[3] == 255


def test_make_icon_iconset_folder(jpg_file, outdir):
    outs, _ = run_tool("image.make-icon", [jpg_file], outdir, output="iconset")
    folder = outs[0]
    assert folder.name == "相片.iconset" and folder.is_dir()
    names = sorted(p.name for p in folder.iterdir())
    expected = []
    for s in (16, 32, 128, 256, 512):
        expected += [f"icon_{s}x{s}.png", f"icon_{s}x{s}@2x.png"]
    assert names == sorted(expected)
    with Image.open(folder / "icon_16x16@2x.png") as im:
        assert im.size == (32, 32)


def test_make_icon_png_set(jpg_file, outdir):
    outs, _ = run_tool(
        "image.make-icon", [jpg_file], outdir, output="pngSet", shape="circle", background="#ffffff"
    )
    folder = outs[0]
    assert folder.name == "相片-圖示"
    names = {p.name for p in folder.iterdir()}
    assert "apple-touch-icon-180x180.png" in names
    assert "icon-1024x1024.png" in names and "icon-16x16.png" in names
    assert len(names) == 10
    with Image.open(folder / "icon-1024x1024.png") as im:
        assert im.getpixel((5, 5))[3] == 0  # 圓形外透明
        assert im.getpixel((512, 512))[3] == 255


def test_make_icon_ico_original_shape_padding(png_file, outdir):
    outs, _ = run_tool(
        "image.make-icon",
        [png_file],
        outdir,
        output="ico",
        shape="original",
        padding=0.2,
        background="custom",
        background_custom="#ff0000",
    )
    assert outs[0].name == "透明.ico"
    with Image.open(outs[0]) as im:
        im.size = (256, 256)
        im.load()
        rgba = im.convert("RGBA")
        assert rgba.getpixel((5, 5))[3] == 0  # 留白在框外
        assert rgba.getpixel((128, 60))[:3] == (255, 0, 0)  # 框內、圖片上方的底色


def test_make_icon_padding_caption():
    t = tool("image.make-icon")
    d = t.defaults()
    assert t.option("padding").note(d, {}) == ("macOS 圖示建議留白約 10%。", False)
    assert t.option("padding").note({**d, "shape": "circle"}, {}) == (
        "非正方形的圖片會置中。",
        False,
    )
