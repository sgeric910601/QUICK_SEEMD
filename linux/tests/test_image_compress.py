"""壓縮圖片：品質表、調色盤 PNG、絕不輸出更大的檔案、摘要文字。"""

from __future__ import annotations

import numpy as np
import pytest
from conftest import make_image
from PIL import Image
from test_image_kit import CAMERA_TAGS, digest, run_tool, tag_names, tool, with_metadata

from quickseemd.core import backends
from quickseemd.core.result import format_size
from quickseemd.core.tools.image import compress, formats, palette


def _photo(tmp_path, name="相片.jpg", quality=95, **save):
    rng = np.random.default_rng(1)
    base = np.linspace(0, 255, 400 * 300 * 3).reshape(300, 400, 3)
    noise = rng.normal(0, 12, base.shape)
    img = Image.fromarray(np.clip(base + noise, 0, 255).astype(np.uint8))
    p = tmp_path / name
    if p.suffix == ".png":
        img.save(p, compress_level=0)
    else:
        img.save(p, quality=quality, **save)
    return p


def test_quality_table():
    q = compress.quality
    assert (q("jpeg", "maximum"), q("jpeg", "recommended"), q("jpeg", "light")) == (0.5, 0.7, 0.85)
    for f in ("heic", "avif", "webp"):
        assert (q(f, "maximum"), q(f, "recommended"), q(f, "light")) == (0.4, 0.55, 0.72)


def test_summary_texts():
    assert compress.summary(2_000_000, 500_000) == "省下 75%（2 MB → 500 KB）"
    assert compress.summary(2_000_000, 500_000, total=True) == "共省下 75%（2 MB → 500 KB）"
    assert compress.summary(100, 200) == "已是最精簡的大小，保留原始內容"


def test_jpeg_recommended(tmp_path, outdir):
    src = _photo(tmp_path)
    before = digest(src)
    outs, ctx = run_tool("image.compress", [src], outdir, preset="image.compress-recommended")
    out = outs[0]
    assert out.name == "相片-已壓縮.jpg"
    assert out.stat().st_size < src.stat().st_size
    assert ctx.last_message.startswith("省下 ")
    assert f"（{format_size(src.stat().st_size)} → {format_size(out.stat().st_size)}）" in ctx.last_message
    assert digest(src) == before


def test_never_larger_keeps_original(tmp_path, outdir):
    src = _photo(tmp_path, "小.jpg", quality=30, optimize=True)
    outs, ctx = run_tool("image.compress", [src], outdir, level="light")
    assert outs[0].name == "小-已壓縮.jpg"
    assert digest(outs[0]) == digest(src)
    assert ctx.last_message == f"已是最精簡的大小，保留原始內容（{format_size(src.stat().st_size)}）"


def test_never_larger_with_strip_uses_stripped_copy(tmp_path, outdir):
    small = _photo(tmp_path, "s.jpg", quality=30)
    meta = with_metadata(small, tmp_path / "有資訊.jpg", *CAMERA_TAGS)
    outs, ctx = run_tool("image.compress", [meta], outdir, level="light", strip_metadata=True)
    assert outs[0].stat().st_size <= meta.stat().st_size
    assert "Model" not in tag_names(outs[0])


def test_png_palette_keeps_alpha(png_file, outdir):
    outs, ctx = run_tool("image.compress", [png_file], outdir)
    out = outs[0]
    assert out.name == "透明-已壓縮.png"
    with Image.open(out) as im:
        assert im.mode == "P"
        rgba = im.convert("RGBA")
        assert rgba.getpixel((2, 470))[3] == 0
        assert len(rgba.getcolors(1 << 16)) <= 256
    assert out.stat().st_size < png_file.stat().st_size


def test_maximum_uses_128_colours(tmp_path, outdir):
    p = _photo(tmp_path, "grad.png")
    outs, _ = run_tool("image.compress", [p], outdir, level="maximum")
    with Image.open(outs[0]) as im:
        assert len(im.convert("RGBA").getcolors(1 << 16)) <= 128


def test_light_png_exact_palette_is_lossless(tmp_path, outdir):
    p = tmp_path / "few.png"
    img = Image.new("RGBA", (300, 200), (255, 255, 255, 255))
    for i, color in enumerate([(255, 0, 0, 255), (0, 0, 255, 128), (0, 255, 0, 0)]):
        img.paste(color, (i * 100, 0, i * 100 + 100, 200))
    img.save(p, compress_level=0)
    outs, _ = run_tool("image.compress", [p], outdir, level="light")
    with Image.open(outs[0]) as im:
        assert im.mode == "P"
        back = np.asarray(im.convert("RGBA"))
    orig = np.asarray(img).copy()
    orig[orig[..., 3] < 8] = 0
    assert np.array_equal(back, orig)


def test_light_png_many_colours_saved_normally(tmp_path, outdir):
    p = tmp_path / "many.png"
    rng = np.random.default_rng(2)
    Image.fromarray(rng.integers(0, 255, (64, 64, 3), dtype=np.uint8)).save(p, compress_level=0)
    outs, _ = run_tool("image.compress", [p], outdir, level="light")
    with Image.open(outs[0]) as im:
        assert im.mode == "RGB"


def test_targets_jpeg_and_heic(tmp_path, outdir):
    png = _photo(tmp_path, "照片.png")
    outs, _ = run_tool("image.compress", [png], outdir, target="jpeg")
    assert outs[0].name == "照片-已壓縮.jpg"
    if formats.is_writable("heic"):
        outs, _ = run_tool("image.compress", [png], outdir, target="heic")
        assert outs[0].suffix == ".heic"


def test_heic_target_falls_back_to_jpeg(tmp_path, outdir, monkeypatch):
    png = _photo(tmp_path, "照片.png")
    real = backends.find
    monkeypatch.setattr(backends, "find", lambda key: None if key == "heif-enc" else real(key))
    outs, _ = run_tool("image.compress", [png], outdir, target="heic")
    assert outs[0].suffix == ".jpg"


def test_bmp_becomes_png(tmp_path, outdir):
    p = make_image(tmp_path / "舊.bmp")
    outs, _ = run_tool("image.compress", [p], outdir)
    assert outs[0].name == "舊-已壓縮.png"


def test_multiple_files_total_summary(tmp_path, outdir):
    a = _photo(tmp_path, "a.jpg")
    b = _photo(tmp_path, "b.jpg")
    outs, ctx = run_tool("image.compress", [a, b], outdir)
    assert len(outs) == 2
    assert ctx.last_message.startswith("共省下 ")


def test_strip_metadata_option(tmp_path, outdir):
    meta = with_metadata(_photo(tmp_path), tmp_path / "m.jpg", *CAMERA_TAGS)
    outs, _ = run_tool("image.compress", [meta], outdir, strip_metadata=True)
    assert "Model" not in tag_names(outs[0])
    outs, _ = run_tool("image.compress", [meta], outdir)
    assert "Model" in tag_names(outs[0])


def test_palette_fallback_without_pngquant(png_file, tmp_path, monkeypatch):
    real = backends.find
    monkeypatch.setattr(backends, "find", lambda key: None if key == "pngquant" else real(key))
    from quickseemd.core.tools.image import loader

    s = loader.load(png_file)
    out = tmp_path / "p.png"
    assert palette.write(s.image, 256, lossless_only=False, dpi=72, icc=None, path=out)
    with Image.open(out) as im:
        assert im.mode == "P"


def test_compress_notes():
    t = tool("image.compress")
    d = t.defaults()
    png = [o for o in t.options if o.key == "_png_note"][0]
    assert png.is_visible(d, {"has_png": True})
    assert not png.is_visible({**d, "target": "jpeg"}, {"has_png": True})
    assert png.note(d, {"has_png": True})[0].startswith("PNG 會減色成調色盤（最多 256 色）")
    assert "128" in png.note({**d, "level": "maximum"}, {})[0]
    assert png.note({**d, "level": "light"}, {})[0] == (
        "PNG：顏色不超過 256 色時改存成調色盤 PNG（完全無損）。"
    )
    shield = [o for o in t.options if o.key == "_never_larger"][0]
    text = shield.note(d, {"total_size": 2_100_000})[0]
    assert text == (
        "如果壓縮後反而變大，會保留原始內容，絕不輸出比原檔更大的檔案。\n目前共 2.1 MB。"
    )
    assert t.option("target").note(d, {})[0] == "JPG 維持 JPG、PNG 維持 PNG；BMP 會改存成 PNG。"


@pytest.mark.parametrize("level", ["maximum", "recommended", "light"])
def test_gif_stays_gif(tmp_path, outdir, level):
    p = tmp_path / "a.gif"
    frames = [Image.new("RGB", (40, 40), c) for c in ("red", "blue")]
    frames[0].save(p, save_all=True, append_images=frames[1:], duration=100, loop=0)
    outs, _ = run_tool("image.compress", [p], outdir, level=level)
    assert outs[0].suffix == ".gif"
