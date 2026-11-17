"""加入浮水印：文字、標誌、位置、平鋪、角度、陰影。"""

from __future__ import annotations

import numpy as np
import pytest
from conftest import make_image
from PIL import Image
from test_image_kit import run_tool, tool

from quickseemd.core import errors
from quickseemd.core.tools.image import watermark


def _plain(tmp_path, name="底圖.png", size=(400, 300), color=(40, 80, 120)):
    p = tmp_path / name
    Image.new("RGB", size, color).save(p)
    return p


def _diff(a_path, b_path):
    with Image.open(a_path) as a, Image.open(b_path) as b:
        return np.abs(
            np.asarray(a.convert("RGB"), dtype=np.int16) - np.asarray(b.convert("RGB"), dtype=np.int16)
        ).sum(axis=2)


def test_font_is_traditional_chinese():
    font = watermark.load_font(24, bold=True)
    assert font.getname() == ("Noto Sans CJK TC", "Bold")
    assert watermark.load_font(24, bold=False).getname()[1] == "Regular"


def test_default_text_bottom_right(tmp_path, outdir):
    src = _plain(tmp_path)
    outs, ctx = run_tool("image.watermark", [src], outdir)
    assert outs[0].name == "底圖-浮水印.png"
    d = _diff(src, outs[0])
    assert d[200:, 250:].sum() > 0  # 右下有字
    assert d[:150, :200].sum() == 0  # 左上不變
    ys, xs = np.nonzero(d > 30)
    assert xs.max() <= 400 - 12 + 6 and ys.max() <= 300 - 12 + 8  # 留邊約短邊 4%


@pytest.mark.parametrize("pos,region", [(0, (slice(0, 100), slice(0, 200))), (4, (slice(100, 200), slice(100, 300)))])
def test_grid_positions(tmp_path, outdir, pos, region):
    src = _plain(tmp_path)
    outs, _ = run_tool("image.watermark", [src], outdir, position=pos)
    d = _diff(src, outs[0])
    assert d[region].sum() > 0
    assert d[250:, 300:].sum() == 0


def test_tiled_covers_image(tmp_path, outdir):
    src = _plain(tmp_path, size=(600, 400))
    outs, _ = run_tool("image.watermark", [src], outdir, tiled=True, rotation=45)
    d = _diff(src, outs[0])
    quads = [d[:200, :300], d[:200, 300:], d[200:, :300], d[200:, 300:]]
    assert all(q.sum() > 0 for q in quads)


def test_rotation_custom_and_colour(tmp_path, outdir):
    src = _plain(tmp_path, color=(255, 255, 255))
    outs, _ = run_tool(
        "image.watermark",
        [src],
        outdir,
        rotation="custom",
        rotation_custom=-30,
        color="custom",
        color_custom="#ff0000",
        opacity=1,
        shadow=False,
        text_size=12,
    )
    with Image.open(outs[0]) as im:
        arr = np.asarray(im.convert("RGB"), dtype=np.int16)
    reddish = (arr[..., 0] > 200) & (arr[..., 1] < 80) & (arr[..., 2] < 80)
    assert reddish.sum() > 50


def test_shadow_adds_dark_pixels(tmp_path, outdir):
    src = _plain(tmp_path, color=(128, 128, 128))
    with_shadow, _ = run_tool("image.watermark", [src], outdir, opacity=1, text_size=10)
    without, _ = run_tool("image.watermark", [src], outdir, opacity=1, text_size=10, shadow=False)
    with Image.open(with_shadow[0]) as a, Image.open(without[0]) as b:
        dark_a = (np.asarray(a.convert("L")) < 110).sum()
        dark_b = (np.asarray(b.convert("L")) < 110).sum()
    assert dark_a > dark_b


def test_logo_watermark(tmp_path, outdir, jpg_file):
    logo = tmp_path / "logo.png"
    Image.new("RGBA", (50, 20), (255, 0, 0, 255)).save(logo)
    outs, _ = run_tool(
        "image.watermark", [jpg_file], outdir, kind="image", logo=str(logo), opacity=1, position=0
    )
    assert outs[0].name == "相片-浮水印.jpg"
    with Image.open(outs[0]) as im:
        # 標誌寬＝圖寬 20%（128 px），左上留邊 4%×480≈19 px
        r, g, b = im.getpixel((19 + 64, 19 + 25))
        assert r > 200 and g < 60


def test_validation_and_missing_logo(jpg_file, outdir):
    t = tool("image.watermark")
    d = t.defaults()
    assert t.validate([jpg_file], {**d, "text": "  "}, {}) == "請輸入浮水印文字"
    assert t.validate([jpg_file], {**d, "kind": "image"}, {}) == "請先選擇標誌圖片"
    assert t.validate([jpg_file], {**d, "kind": "image", "logo": "/nope.png"}, {}) == "請先選擇標誌圖片"
    assert t.validate([jpg_file], d, {}) is None
    with pytest.raises(errors.QSError, match="請先選擇標誌圖片"):
        run_tool("image.watermark", [jpg_file], outdir, kind="image")


def test_transparent_png_keeps_alpha(png_file, outdir):
    outs, _ = run_tool("image.watermark", [png_file], outdir)
    with Image.open(outs[0]) as im:
        assert im.mode == "RGBA" and im.getpixel((2, 470))[3] == 0


def test_watermark_options_ui():
    t = tool("image.watermark")
    d = t.defaults()
    assert [c.value for c in t.option("position").choices] == list(range(9))
    assert t.option("position").choices[8].title == "右下"
    assert t.option("text_size").display(5) == "5.0%"
    assert t.option("logo_scale").display(20) == "圖寬 20%"
    assert not t.option("rotation_custom").is_visible(d, {})
    assert t.option("rotation_custom").is_visible({**d, "rotation": "custom"}, {})
    assert t.option("tiled").note({**d, "tiled": True}, {}) == ("重複鋪滿整張圖片，較難被裁掉。", False)
    assert not t.option("position").is_visible({**d, "tiled": True}, {})
    assert not t.option("logo").is_visible(d, {})
