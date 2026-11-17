"""移除背景：合成、裁切到主體、格式規則；真的去背元件測試標成 slow。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest
from conftest import make_image
from PIL import Image
from test_image_kit import astronaut, run_tool, tool

from quickseemd.core import backends, errors
from quickseemd.core.tools.image import colors, formats, remove_bg


def _fake_masks(box=(100, 80, 300, 400)):
    """取代真的去背元件：box 內是主體。"""

    def make(inputs: list[Path], outputs: list[Path], ctx) -> None:
        for src, dst in zip(inputs, outputs):
            with Image.open(src) as im:
                mask = Image.new("L", im.size, 0)
            if box is not None:
                mask.paste(255, box)
            mask.save(dst)

    return make


@pytest.fixture
def fake_engine(monkeypatch):
    monkeypatch.setattr(remove_bg, "make_masks", _fake_masks())
    monkeypatch.setattr(backends, "rembg_model_ready", lambda: True)


def test_composite_modes():
    img = Image.new("RGB", (40, 20), (200, 10, 10))
    mask = Image.new("L", (40, 20), 0)
    mask.paste(255, (10, 0, 30, 20))
    out = remove_bg.composite(img, mask, "transparent", colors.WHITE)
    assert out.mode == "RGBA" and out.getpixel((2, 2))[3] == 0 and out.getpixel((20, 10))[3] == 255
    assert out.getpixel((20, 10))[:3] == (200, 10, 10)
    white = remove_bg.composite(img, mask, "white", colors.WHITE)
    assert white.getpixel((2, 2))[:3] == (255, 255, 255)
    custom = remove_bg.composite(img, mask, "custom", colors.parse("#00ff00"))
    assert custom.getpixel((2, 2))[:3] == (0, 255, 0)
    blurred = remove_bg.composite(img, mask, "blur", colors.WHITE)
    assert blurred.getpixel((2, 2))[3] == 255


def test_subject_bounds_and_crop():
    mask = Image.new("L", (100, 100), 0)
    mask.paste(255, (20, 30, 60, 50))
    assert remove_bg.subject_bounds(mask) == (20, 30, 60, 50)
    assert remove_bg.subject_bounds(Image.new("L", (10, 10), 100)) is None
    assert remove_bg.crop_box((20, 30, 60, 50), (100, 100)) == (18, 28, 62, 52)


def test_end_to_end_with_fake_engine(png_file, jpg_file, outdir, fake_engine):
    outs, _ = run_tool("image.remove-bg", [jpg_file], outdir, action="image.remove-bg-transparent")
    assert outs[0].name == "相片-去背.png"
    with Image.open(outs[0]) as im:
        assert (
            im.mode == "RGBA" and im.getpixel((5, 5))[3] == 0 and im.getpixel((200, 200))[3] == 255
        )
    outs, _ = run_tool("image.remove-bg", [jpg_file], outdir, background="white", format="jpeg")
    assert outs[0].name == "相片-去背.jpg"
    outs, _ = run_tool("image.remove-bg", [jpg_file], outdir, format="jpeg")
    assert outs[0].suffix == ".png"  # 透明背景不能存 JPG
    outs, _ = run_tool("image.remove-bg", [png_file], outdir, crop_to_subject=True)
    with Image.open(outs[0]) as im:
        assert im.size == (200 + 2 * 13, 320 + 2 * 13)


def test_no_subject_skip(jpg_file, png_file, outdir, monkeypatch):
    monkeypatch.setattr(remove_bg, "make_masks", _fake_masks(None))
    monkeypatch.setattr(backends, "rembg_model_ready", lambda: True)
    with pytest.raises(
        errors.QSError, match="在「相片.jpg」裡找不到明顯的主體（人物、動物或物品）。"
    ):
        run_tool("image.remove-bg", [jpg_file], outdir)
    outs, ctx = run_tool("image.remove-bg", [jpg_file, png_file], outdir)
    assert outs == []
    assert ctx.last_message.startswith("已略過 2 張：相片.jpg：在「相片.jpg」裡找不到")


def test_requires_engine(jpg_file, monkeypatch):
    monkeypatch.setattr(backends, "rembg_model_ready", lambda: False)
    t = tool("image.remove-bg")
    assert t.validate([jpg_file], t.defaults(), {}) == "需要安裝去背元件"
    with pytest.raises(errors.QSError, match="需要安裝去背元件"):
        run_tool("image.remove-bg", [jpg_file])


def test_helper_failure_message(jpg_file, outdir, monkeypatch, tmp_path):
    fake_python = tmp_path / "python"
    fake_python.write_text("#!/bin/sh\necho 'boom happened' >&2\nexit 1\n")
    fake_python.chmod(0o755)
    monkeypatch.setattr(backends, "rembg_python", lambda: fake_python)
    monkeypatch.setattr(backends, "rembg_model_ready", lambda: True)
    with pytest.raises(errors.QSError, match="無法產生去背遮罩：boom happened"):
        run_tool("image.remove-bg", [jpg_file], outdir)


def test_format_choices(monkeypatch):
    t = tool("image.remove-bg")
    opt = t.option("format")
    values = [c.value for c in opt.current_choices(t.defaults(), {})]
    assert values[0] == "png" and "jpeg" not in values
    values = [c.value for c in opt.current_choices({**t.defaults(), "background": "white"}, {})]
    assert "jpeg" in values
    assert opt.note(t.defaults(), {}) == ("PNG 相容性最好；HEIC 檔案較小。兩者都保留透明。", False)
    assert opt.note({"background": "blur"}, {}) == ("背景不透明時也可以存成 JPG。", False)
    monkeypatch.setattr(backends, "find", lambda key: None)
    values = [c.value for c in opt.current_choices(t.defaults(), {})]
    assert values == ["png"]
    assert t.option("color").is_visible({**t.defaults(), "background": "custom"}, {})


@pytest.mark.slow
@pytest.mark.skipif(not backends.rembg_model_ready() or astronaut() is None, reason="沒有去背元件")
def test_real_engine_on_photo(tmp_path, outdir):
    p = tmp_path / "人像.png"
    p.write_bytes(astronaut().read_bytes())
    plain = make_image(tmp_path / "空白.png", size=(64, 64))
    outs, ctx = run_tool("image.remove-bg", [p, plain], outdir)
    assert outs[0].name == "人像-去背.png"
    with Image.open(outs[0]) as im:
        alpha = np.asarray(im.getchannel("A"))
    assert alpha[120, 225] > 200  # 臉
    assert alpha[5, 5] < 30  # 左上角背景（旗子）
    if formats.is_writable("heic"):
        outs, _ = run_tool("image.remove-bg", [p], outdir, format="heic", background="blur")
        assert outs[0].suffix == ".heic"


def test_helper_argument_checks(tmp_path):
    from quickseemd.core.tools.image import rembg_helper

    assert rembg_helper.main(["helper"]) == 2
    assert rembg_helper.main(["helper", "a.png"]) == 2
    assert rembg_helper.main(["helper", "a.png", "b.png"]) == 2  # 必須是絕對路徑
    src, dst = str(tmp_path / "a.png"), str(tmp_path / "b.png")
    assert rembg_helper.main(["helper", src, dst]) == 3  # 系統的 Python 沒有 rembg
    assert not (tmp_path / "b.png").exists()
