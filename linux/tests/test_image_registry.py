"""圖片工具的登記：代碼、順序、文字、右鍵預設值、選項預設值。"""

from __future__ import annotations

import pytest
from test_image_kit import tool

from quickseemd.core import quickactions, registry
from quickseemd.core.model import TINTS
from quickseemd.core.options import Kind
from quickseemd.core.tools import image

EXPECTED = {
    "image.convert": (
        "轉換圖片格式",
        "在 JPG、PNG、HEIC、AVIF、TIFF、GIF、ICO、PDF 等格式間互轉，可調品質與透明背景顏色。",
        "arrow.triangle.2.circlepath",
        "JPG",
    ),
    "image.compress": (
        "壓縮圖片",
        "在畫質與檔案大小間取得最佳平衡，JPG、PNG、HEIC 都能大幅縮小；絕不輸出比原檔更大的檔案。",
        "arrow.down.right.and.arrow.up.left",
        "IMG",
    ),
    "image.resize": (
        "調整大小",
        "依像素、百分比或社群常用尺寸（IG、限動、分享預覽）調整圖片大小，可批次處理。",
        "arrow.up.left.and.arrow.down.right",
        "IMG",
    ),
    "image.crop": (
        "裁剪圖片",
        "拖曳框選要保留的範圍，或依 1:1、16:9、A4 等比例一次裁切多張圖片。",
        "crop",
        "IMG",
    ),
    "image.rotate": (
        "旋轉／翻轉",
        "把圖片轉 90°、180°，或水平、垂直翻轉（鏡像），可一次處理多張。",
        "rotate.right",
        "IMG",
    ),
    "image.remove-bg": (
        "移除背景",
        "自動辨識人物、動物或商品，一鍵去背成透明 PNG，或換成白色、自訂顏色、模糊背景。",
        "person.and.background.dotted",
        "PNG",
    ),
    "image.watermark": (
        "加入浮水印",
        "在圖片加上文字或標誌浮水印：可調大小、顏色、透明度、角度，放在九宮格任一位置或整張平鋪。",
        "signature",
        "IMG",
    ),
    "image.blur-faces": (
        "模糊臉部",
        "自動找出照片中的每一張臉，以模糊、馬賽克或黑色色塊遮住，保護隱私後再分享。",
        "face.dashed",
        "IMG",
    ),
    "image.ocr": (
        "圖片文字辨識（OCR）",
        "把截圖、照片、掃描裡的文字辨識成可編輯的文字，支援繁中、簡中、日文、韓文與英文，可存成 TXT 或 Word。",
        "text.viewfinder",
        "TXT",
    ),
    "image.strip-metadata": (
        "移除相片資訊",
        "清除 EXIF、GPS 位置、相機型號、拍攝時間等隱私資訊；JPG 等格式直接移除、不重新壓縮。",
        "location.slash",
        "EXIF",
    ),
    "image.upscale": (
        "放大圖片",
        "以高品質 Lanczos 演算法把小圖放大 2 倍或 4 倍，並銳利化讓邊緣更清楚。",
        "arrow.up.backward.and.arrow.down.forward.square",
        "2×",
    ),
    "image.make-icon": (
        "製作圖示",
        "把圖片做成 macOS 圖示（.icns）、Windows／網站圖示（.ico）或各種尺寸的 PNG 圖示組。",
        "app.dashed",
        "ICNS",
    ),
}

TINT = {
    "image.compress": "excel",
    "image.resize": "word",
    "image.crop": "teal",
    "image.rotate": "slate",
    "image.remove-bg": "video",
    "image.watermark": "audio",
    "image.blur-faces": "slate",
    "image.ocr": "word",
    "image.strip-metadata": "teal",
    "image.upscale": "video",
}


def test_all_twelve_tools_in_order():
    ids = [t.id for t in registry.all_tools() if t.category.id == "image"]
    assert ids == list(registry.ORDER["image"])
    assert len(image.TOOLS) == 12


@pytest.mark.parametrize("tid", sorted(EXPECTED))
def test_tool_texts(tid):
    t = tool(tid)
    title, subtitle, icon, badge = EXPECTED[tid]
    assert (t.title, t.subtitle, t.icon, t.badge) == (title, subtitle, icon, badge)
    assert t.input_types == ("image/*",) and t.input_label == "圖片"
    assert t.tint_rgb == TINTS[TINT.get(tid, "image")]
    assert t.allows_multiple


def test_presets_equal_action_list():
    for act in quickactions.ACTIONS:
        if act.group != "圖片":
            continue
        t = tool(act.tool_id)
        assert t.presets[act.id] == act.preset
        values = t.values_for_preset(act.id)
        for key, value in act.preset.items():
            opt = t.option(key)
            assert opt is not None and values[key] == value
            if opt.kind in (Kind.CARDS, Kind.CHIPS, Kind.SEGMENTED):
                assert value in [c.value for c in opt.choices]


def test_option_defaults_follow_original():
    d = tool("image.convert").defaults()
    assert (d["format"], d["quality"], d["background"], d["keep_metadata"]) == (
        "jpeg",
        0.9,
        "#ffffff",
        True,
    )
    d = tool("image.compress").defaults()
    assert (d["level"], d["target"], d["strip_metadata"]) == ("recommended", "keep", False)
    d = tool("image.resize").defaults()
    assert (d["mode"], d["percent"], d["custom_percent"], d["preset"]) == (
        "percent",
        50,
        60,
        "w1920",
    )
    assert (d["width"], d["height"], d["lock_aspect"], d["fit"], d["pad"]) == (
        1920,
        1080,
        True,
        "fill",
        "#ffffff",
    )
    assert d["no_upscale"] and d["smooth"]
    d = tool("image.crop").defaults()
    assert (d["ratio"], d["crop"], d["anchor"]) == ("free", "", "smart")
    d = tool("image.rotate").defaults()
    assert (d["angle"], d["flip_h"], d["flip_v"]) == (90, False, False)
    d = tool("image.remove-bg").defaults()
    assert (d["background"], d["format"], d["crop_to_subject"]) == ("transparent", "png", False)
    d = tool("image.watermark").defaults()
    assert (d["kind"], d["text"], d["text_size"], d["bold"], d["color"]) == (
        "text",
        "© 版權所有",
        5,
        True,
        "#ffffff",
    )
    assert (d["opacity"], d["rotation"], d["shadow"], d["tiled"], d["position"]) == (
        0.6,
        0,
        True,
        False,
        8,
    )
    assert d["logo_scale"] == 20
    d = tool("image.blur-faces").defaults()
    assert (d["style"], d["strength"], d["shape"], d["expand"]) == ("blur", 6, "ellipse", 0.25)
    d = tool("image.ocr").defaults()
    assert (d["language"], d["accurate"], d["correction"], d["output"], d["combine"]) == (
        "traditional",
        True,
        True,
        "txt",
        False,
    )
    assert tool("image.strip-metadata").defaults()["mode"] == "all"
    d = tool("image.upscale").defaults()
    assert (d["factor"], d["sharpen"], d["denoise"]) == (2, "standard", False)
    d = tool("image.make-icon").defaults()
    assert (d["output"], d["shape"], d["padding"], d["background"]) == (
        "icns",
        "rounded",
        0.1,
        "transparent",
    )


def test_crop_contract_for_app():
    t = tool("image.crop")
    assert t.custom_view == "image-crop"
    crop = t.option("crop")
    assert crop.kind is Kind.TEXT and not crop.is_visible(t.defaults(), {})
    assert [c.value for c in t.option("anchor").choices] == ["smart", "center", "top"]


def test_action_titles():
    rot = tool("image.rotate")
    d = rot.defaults()
    assert rot.title_for_action(d, {}) == "旋轉圖片"
    assert rot.title_for_action({**d, "angle": 0, "flip_h": True}, {}) == "翻轉圖片"
    assert rot.title_for_action({**d, "flip_v": True}, {}) == "旋轉並翻轉"
    conv = tool("image.convert")
    assert conv.title_for_action({**conv.defaults(), "format": "png"}, {}) == "轉成 PNG"
    blur = tool("image.blur-faces")
    titles = [
        blur.title_for_action({**blur.defaults(), "style": s}, {})
        for s in ("blur", "pixelate", "blackout")
    ]
    assert titles == ["模糊臉部", "加上馬賽克", "遮住臉部"]
    strip = tool("image.strip-metadata")
    assert strip.title_for_action({"mode": "location"}, {}) == "移除位置資訊"
    assert strip.title_for_action({"mode": "all"}, {}) == "移除相片資訊"
