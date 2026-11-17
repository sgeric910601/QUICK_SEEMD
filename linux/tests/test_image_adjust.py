"""自訂色預設值、選項連動（adjust）與浮水印字型的尋找順序。"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image
from test_image_kit import run_tool, tool

from quickseemd.core import backends
from quickseemd.core.options import Kind
from quickseemd.core.tools.image import watermark

CUSTOM_DEFAULTS = {
    ("image.convert", "background"): "#f5f0e0",
    ("image.resize", "pad"): "#808080",
    ("image.watermark", "color"): "#e3332e",
    ("image.remove-bg", "color"): "#d9ebff",
    ("image.make-icon", "background"): "#ffffff",
}


@pytest.mark.parametrize("tid,key", sorted(CUSTOM_DEFAULTS))
def test_every_color_option_has_custom_default(tid, key):
    t = tool(tid)
    opt = t.option(key)
    assert opt.kind is Kind.COLOR and opt.custom_key == f"{key}_custom"
    assert opt.custom_default == CUSTOM_DEFAULTS[(tid, key)]
    assert t.defaults()[f"{key}_custom"] == CUSTOM_DEFAULTS[(tid, key)]


def test_no_other_color_options_without_default():
    from quickseemd.core.tools.image import TOOLS

    for t in TOOLS:
        for o in t.options:
            if o.kind is Kind.COLOR:
                assert (t.id, o.key) in CUSTOM_DEFAULTS


def test_custom_default_is_used_when_running(png_file, outdir):
    outs, _ = run_tool("image.convert", [png_file], outdir, background="custom")
    with Image.open(outs[0]) as im:
        r, g, b = im.getpixel((2, 470))
    assert abs(r - 245) <= 3 and abs(g - 240) <= 3 and abs(b - 224) <= 3


# ------------------------------------------------------------------ 連動


def test_make_icon_shape_resets_padding():
    t = tool("image.make-icon")
    d = t.defaults()
    assert t.adjust({**d, "shape": "circle", "padding": 0.2}, "shape", {}) == {"padding": 0.0}
    assert t.adjust({**d, "shape": "rounded", "padding": 0.0}, "shape", {}) == {"padding": 0.1}
    assert t.adjust({**d, "shape": "original"}, "shape", {}) == {"padding": 0.0}
    assert t.adjust({**d, "padding": 0.2}, "padding", {}) == {}


def test_resize_lock_keeps_width_and_height_in_sync():
    t = tool("image.resize")
    info = {"count": 1, "sizes": [[640, 480]]}
    d = {**t.defaults(), "mode": "pixels", "width": 320, "height": 1080}
    assert t.adjust(d, "width", info) == {"height": 240}
    assert t.adjust({**d, "height": 300}, "height", info) == {"width": 400}
    assert t.adjust({**d, "lock_aspect": True}, "lock_aspect", info) == {"height": 240}
    assert t.adjust({**d, "lock_aspect": False}, "width", info) == {}
    assert t.adjust({**d, "lock_aspect": False}, "lock_aspect", info) == {}
    assert t.adjust(d, "width", {}) == {}  # 還沒讀到圖片尺寸
    assert t.adjust({**d, "width": 0}, "width", info) == {}  # 清空欄位時不跟著改
    assert t.adjust({**d, "width": 1}, "width", info) == {"height": 1}
    assert t.adjust(d, "mode", info) == {}


def test_resize_prefill_matches_adjust(jpg_file):
    t = tool("image.resize")
    info = t.inspect([jpg_file])
    values = {**t.defaults(), **t.prefill([jpg_file], info)}
    assert t.adjust(values, "width", info) == {"height": 480}


# ------------------------------------------------------------------ 字型


@pytest.fixture
def fresh_fonts():
    watermark.font_file.cache_clear()
    yield
    watermark.font_file.cache_clear()


def _fake_fc_match(tmp_path: Path, output: str) -> Path:
    exe = tmp_path / "fc-match"
    exe.write_text(f"#!/bin/sh\nprintf '%s' '{output}'\n")
    exe.chmod(0o755)
    return exe


def test_font_found_with_fc_match(tmp_path, monkeypatch, fresh_fonts):
    regular = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"
    if not Path(regular).is_file():
        pytest.skip("沒有 Noto Sans CJK")
    fake = _fake_fc_match(tmp_path, f"{regular}|3")
    monkeypatch.setattr(backends, "find", lambda key: str(fake) if key == "fc-match" else None)
    assert watermark.font_file(True) == (regular, 3)


def test_font_falls_back_to_fixed_paths(tmp_path, monkeypatch, fresh_fonts):
    bold = Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc")
    if not bold.is_file():
        pytest.skip("沒有 Noto Sans CJK")
    monkeypatch.setattr(backends, "find", lambda key: None)
    assert watermark.font_file(True) == (str(bold), 3)
    watermark.font_file.cache_clear()
    fake = _fake_fc_match(tmp_path, "/nope/missing.ttc|0")
    monkeypatch.setattr(backends, "find", lambda key: str(fake) if key == "fc-match" else None)
    assert watermark.font_file(True) == (str(bold), 3)


def test_font_rejects_non_cjk_match(tmp_path, monkeypatch, fresh_fonts):
    dejavu = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
    bold = Path("/usr/share/fonts/opentype/noto/NotoSansCJK-Bold.ttc")
    if not dejavu.is_file() or not bold.is_file():
        pytest.skip("沒有測試用字型")
    fake = _fake_fc_match(tmp_path, f"{dejavu}|0")
    monkeypatch.setattr(backends, "find", lambda key: str(fake) if key == "fc-match" else None)
    assert watermark.font_file(True) == (str(bold), 3)  # fc-match 找到的不是中文字型時不用它


def test_load_font_default_when_nothing_found(monkeypatch):
    monkeypatch.setattr(watermark, "font_file", lambda bold: None)
    font = watermark.load_font(20, bold=True)
    assert font.getlength("A") > 0
