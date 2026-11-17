"""裁剪圖片（比例、框選、智慧位置）與模糊臉部。"""

from __future__ import annotations

import json

import numpy as np
import pytest
from conftest import make_image
from PIL import Image
from test_image_kit import astronaut, run_tool, tool

from quickseemd.core import backends, errors
from quickseemd.core.tools.image import blur_faces, crop, faces

ASTRONAUT = astronaut()
needs_face = pytest.mark.skipif(ASTRONAUT is None, reason="沒有人臉測試照片")


@pytest.fixture
def face_photo(tmp_path):
    if ASTRONAUT is None:
        pytest.skip("沒有人臉測試照片")
    p = tmp_path / "太空人.png"
    p.write_bytes(ASTRONAUT.read_bytes())
    return p


# ------------------------------------------------------------------ 裁剪：計算


def test_pixel_rect_ratios_and_anchors():
    pr = crop.pixel_rect
    assert pr(640, 480, "free", None, "center") == (0, 0, 640, 480)
    assert pr(640, 480, "square", None, "center") == (80, 0, 480, 480)
    assert pr(640, 480, "r16x9", None, "center") == (0, 60, 640, 360)
    assert pr(640, 480, "r16x9", None, "top") == (0, 0, 640, 360)
    assert pr(640, 480, "r16x9", None, "smart", focus=(0.5, 0.1)) == (0, 0, 640, 360)
    assert pr(640, 480, "r16x9", None, "smart", focus=(0.5, 0.95)) == (0, 120, 640, 360)
    assert pr(640, 480, "square", None, "smart", focus=None) == (80, 0, 480, 480)
    a4 = pr(1000, 1000, "a4Portrait", None, "center")
    assert a4[3] == 1000 and abs(a4[2] - 707) <= 1


def test_pixel_rect_user_box():
    pr = crop.pixel_rect
    assert pr(640, 480, "free", (10, 20, 100, 50), "center") == (10, 20, 100, 50)
    assert pr(640, 480, "square", (10, 20, 100, 50), "center") == (10, 20, 100, 100)
    assert pr(640, 480, "free", (600, 450, 100, 50), "center") == (540, 430, 100, 50)
    assert pr(640, 480, "free", (0, 0, 9000, 9000), "center") == (0, 0, 640, 480)


def test_parse_crop_value():
    assert crop.parse_rect(json.dumps({"x": 1, "y": 2, "w": 3, "h": 4})) == (1, 2, 3, 4)
    assert crop.parse_rect("") is None
    assert crop.parse_rect("{bad") is None
    assert crop.parse_rect(json.dumps({"x": 1, "y": 2, "w": 0, "h": 4})) is None
    assert crop.parse_rect(json.dumps({"x": -5, "y": 2, "w": 3, "h": 4})) is None
    assert crop.parse_rect({"x": 1, "y": 2, "w": 3, "h": 4}) == (1, 2, 3, 4)


# ------------------------------------------------------------------ 裁剪：執行


def test_single_file_user_box(png_file, outdir):
    box = json.dumps({"x": 10, "y": 20, "w": 100, "h": 50})
    outs, _ = run_tool("image.crop", [png_file], outdir, crop=box)
    assert outs[0].name == "透明-已裁剪.png"
    with Image.open(png_file) as a, Image.open(outs[0]) as b:
        assert b.size == (100, 50)
        assert np.array_equal(np.asarray(a.crop((10, 20, 110, 70))), np.asarray(b))


def test_single_file_ratio_without_box_centres(jpg_file, outdir):
    outs, _ = run_tool("image.crop", [jpg_file], outdir, ratio="square")
    with Image.open(outs[0]) as im:
        assert im.size == (480, 480)


def test_multiple_files_ratio(tmp_path, outdir, jpg_file):
    other = make_image(tmp_path / "直.png", size=(300, 600))
    outs, _ = run_tool("image.crop", [jpg_file, other], outdir, ratio="r16x9", anchor="top")
    sizes = []
    for o in outs:
        with Image.open(o) as im:
            sizes.append(im.size)
    assert sizes == [(640, 360), (300, 169)]


def test_crop_validation(jpg_file, png_file):
    t = tool("image.crop")
    d = t.defaults()
    assert t.validate([jpg_file, png_file], d, {}) == "多張圖片請選擇裁剪比例（會從中間裁切）"
    assert t.validate([jpg_file], d, {}) == "請拖曳裁剪框，選擇要保留的範圍"
    full = json.dumps({"x": 0, "y": 0, "w": 640, "h": 480})
    assert t.validate([jpg_file], {**d, "crop": full}, {"sizes": [[640, 480]]}) == (
        "請拖曳裁剪框，選擇要保留的範圍"
    )
    part = json.dumps({"x": 0, "y": 0, "w": 320, "h": 480})
    assert t.validate([jpg_file], {**d, "crop": part}, {"sizes": [[640, 480]]}) is None
    assert t.validate([jpg_file], {**d, "ratio": "square"}, {}) is None
    with pytest.raises(errors.QSError, match="請拖曳裁剪框"):
        run_tool("image.crop", [jpg_file])


def test_crop_inspect_and_notes(jpg_file):
    t = tool("image.crop")
    info = t.inspect([jpg_file])
    assert info["sizes"] == [[640, 480]]
    size_note = t.option("_crop_size").note({**t.defaults(), "ratio": "square"}, info)
    assert size_note == ("輸出尺寸：480 × 480 px　原始尺寸：640 × 480 px", False)
    assert t.option("_ratio_hint").note({"ratio": "r16x9"}, {}) == ("影片、簡報、桌布", False)
    assert t.option("anchor").is_visible(t.defaults(), {"count": 2})
    assert not t.option("anchor").is_visible(t.defaults(), {"count": 1})


@needs_face
def test_smart_anchor_follows_face(face_photo, outdir, tmp_path):
    focus = faces.focus_point(Image.open(face_photo).convert("RGB"))
    assert focus is not None and focus[0] < 0.5  # 太空人的臉在左半邊
    other = make_image(tmp_path / "b.png", size=(512, 512))
    outs, _ = run_tool("image.crop", [face_photo, other], outdir, ratio="r9x16")
    with Image.open(outs[0]) as im:
        assert im.size == (288, 512)
    x = crop.pixel_rect(512, 512, "r9x16", None, "smart", focus=focus)[0]
    assert x < 112  # 往左移、把臉留在畫面中（置中是 112）


def test_saliency_focus_without_faces():
    img = Image.new("RGB", (400, 200), "white")
    img.paste((255, 0, 0), (300, 80, 360, 140))
    focus = faces.focus_point(img)
    assert focus is not None and focus[0] > 0.6


# ------------------------------------------------------------------ 模糊臉部


def test_expand_box_rules():
    r = blur_faces.expand_box((100, 100, 40, 50), 0.2, 1000, 1000)
    assert r == pytest.approx((96, 92, 48, 63))
    assert blur_faces.expand_box((0, 0, 40, 50), 0.5, 1000, 1000)[:2] == (0, 0)


def test_masks():
    m = np.asarray(blur_faces.face_mask((40, 30), (0, 0, 40, 30), "ellipse", soft=True))
    assert m[15, 20] == 255 and m[0, 0] == 0
    r = np.asarray(blur_faces.face_mask((40, 30), (10, 10, 20, 10), "rectangle", soft=False))
    assert r[15, 20] == 255 and r[2, 2] == 0


@needs_face
@pytest.mark.parametrize(
    "style,tag", [("blur", "模糊臉部"), ("pixelate", "馬賽克"), ("blackout", "遮蓋臉部")]
)
def test_blur_faces_styles(face_photo, outdir, style, tag):
    outs, ctx = run_tool("image.blur-faces", [face_photo], outdir, style=style)
    assert outs[0].name == f"太空人-{tag}.png"
    assert ctx.last_message == "共處理 1 張臉"
    with Image.open(face_photo) as a, Image.open(outs[0]) as b:
        a_arr = np.asarray(a.convert("RGB"), dtype=np.int16)
        b_arr = np.asarray(b.convert("RGB"), dtype=np.int16)
    face = (slice(80, 160), slice(190, 260))  # 臉的中央
    assert np.abs(a_arr[face] - b_arr[face]).mean() > 5
    assert np.array_equal(a_arr[480:, 480:], b_arr[480:, 480:])  # 遠處不變
    if style == "blackout":
        assert b_arr[120, 225].max() < 10


@needs_face
def test_blur_faces_rectangle_and_skip(face_photo, png_file, outdir):
    outs, ctx = run_tool("image.blur-faces", [face_photo, png_file], outdir, shape="rectangle")
    assert len(outs) == 1
    assert ctx.last_message == "共處理 1 張臉；1 張圖片沒有找到臉孔，已略過"
    with pytest.raises(errors.QSError, match="在「透明.png」裡找不到臉孔，未產生檔案。"):
        run_tool("image.blur-faces", [png_file], outdir)


@needs_face
def test_haar_fallback(face_photo, monkeypatch):
    monkeypatch.setattr(backends, "yunet_model", lambda: None)
    found = faces.detect(Image.open(face_photo).convert("RGB"))
    assert len(found) >= 1


def test_blur_faces_ui():
    t = tool("image.blur-faces")
    d = t.defaults()
    assert t.option("strength").is_visible(d, {})
    assert not t.option("strength").is_visible({**d, "style": "pixelate"}, {})
    assert t.option("block_size").is_visible({**d, "style": "pixelate"}, {})
    assert not t.option("block_size").is_visible({**d, "style": "blackout"}, {})
    assert t.option("expand").display(0.25) == "+25%"
