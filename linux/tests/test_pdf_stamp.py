"""加入浮水印、加入頁碼（疊加圖層，原頁面內容不動）。"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pikepdf
import pytest
from PIL import Image

from conftest import make_pdf
from quickseemd.core import errors
from quickseemd.core.tools.pdf import overlay, stamp
from test_pdf_helpers import digest, make_rich_pdf, outline_titles, pdftotext, run_tool, tool


def _render(path: Path, page: int, dpi: int = 36) -> Image.Image:
    base = path.parent / f"render-{path.stem}-{page}"
    subprocess.run(
        [
            "/usr/bin/pdftocairo",
            "-png",
            "-cropbox",
            "-singlefile",
            "-r",
            str(dpi),
            "-f",
            str(page),
            "-l",
            str(page),
            str(path),
            str(base),
        ],
        check=True,
    )
    return Image.open(str(base) + ".png").convert("RGB")


def _dark_box(img: Image.Image, threshold: int = 200) -> tuple[int, int, int, int] | None:
    gray = img.convert("L").point(lambda v: 255 if v < threshold else 0)
    return gray.getbbox()


def _rotated_pdf(path: Path, rotate: int, crop=True) -> Path:
    make_pdf(path, pages=2, size=(400, 600))
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        for p in pdf.pages:
            p.obj.Rotate = rotate
            if crop:
                p.obj.CropBox = [20, 30, 380, 570]
        pdf.save(path)
    return path


# ------------------------------------------------------------------ 座標與排版（純函式）


def test_canvas_matrix_all_rotations():
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(400, 300))
    page = pdf.pages[0]
    page.obj.CropBox = [10, 20, 210, 120]
    expect = {
        0: [(0, 0, 10, 20), (200, 100, 210, 120)],
        90: [(0, 0, 210, 20), (100, 200, 10, 120)],
        180: [(0, 0, 210, 120), (200, 100, 10, 20)],
        270: [(0, 0, 10, 120), (100, 200, 210, 20)],
    }
    for rot, points in expect.items():
        page.obj.Rotate = rot
        m = overlay.canvas_matrix(page)
        for x, y, ux, uy in points:
            got = (m[0] * x + m[2] * y + m[4], m[1] * x + m[3] * y + m[5])
            assert got == pytest.approx((ux, uy)), (rot, x, y)
    page.obj.UserUnit = 2
    page.obj.Rotate = 0
    m = overlay.canvas_matrix(page)
    assert (m[0] * 400 + m[4], m[3] * 200 + m[5]) == pytest.approx((210, 120))


def test_grid_center_and_single_fit():
    assert stamp.grid_center(0, (100, 20), (600, 800), 36) == (86, 754)
    assert stamp.grid_center(4, (100, 20), (600, 800), 36) == (300, 400)
    assert stamp.grid_center(8, (100, 20), (600, 800), 36) == (514, 46)
    box, fit = stamp.single_box(1000, 100, 45, (600, 800), 36)
    assert fit < 1
    assert box[0] * fit <= 600 - 72 + 1e-6 and box[1] * fit <= 800 - 72 + 1e-6
    box0, fit0 = stamp.single_box(100, 20, 0, (600, 800), 36)
    assert box0 == pytest.approx((100, 20)) and fit0 == 1


def test_page_number_labels():
    label = stamp.number_label
    assert [label("plain", 1, False, i, 3) for i in range(3)] == ["1", "2", "3"]
    assert label("chinese", 1, False, 0, 3) == "第 1 頁"
    assert label("slash", 1, False, 1, 3) == "2 / 3"
    assert label("chinese_total", 5, False, 0, 3) == "第 5 頁，共 7 頁"
    assert label("english", 1, False, 2, 3) == "Page 3"
    assert label("plain", 1, True, 0, 3) is None
    assert label("slash", 1, True, 1, 3) == "1 / 2"
    assert label("slash", 10, True, 2, 3) == "11 / 11"


def test_text_line_metrics():
    line = overlay.TextLine("機密文件", "sans", True, 60)
    assert line.width > 200 and line.ascent > 40 and line.descent > 5
    serif = overlay.TextLine("Page 1", "serif", False, 11)
    assert 20 < serif.width < 60


# ------------------------------------------------------------------ 浮水印


def test_watermark_defaults_keep_text(tmp_path):
    src = make_rich_pdf(tmp_path / "浮.pdf", pages=3)
    before = digest(src)
    run = run_tool("pdf.watermark", [src])
    out = run.outputs[0]
    assert out.name == "浮-浮水印.pdf"
    text = pdftotext(out)
    assert "第 1 頁" in text and "第 3 頁" in text
    assert "機密文件" in "".join(text.split())  # 斜放的字會被拆成一字一行
    assert [t for _d, t, _p in outline_titles(out)] == ["第一章", "1.1", "第二章"]
    with pikepdf.open(out) as pdf:
        assert "/StructTreeRoot" in pdf.Root
        assert str(pdf.docinfo.Producer) == "QUICK_SEEMD"
    img = _render(out, 1)
    w, h = img.size
    assert digest(src) == before
    # 正中央有半透明的灰色字
    row = [img.getpixel((x, h // 2)) for x in range(w // 3, 2 * w // 3)]
    assert any(p != (255, 255, 255) and p[0] == p[1] == p[2] for p in row)


def test_watermark_page_range_and_underlay(tmp_path):
    src = make_pdf(tmp_path / "w.pdf", pages=3)
    run = run_tool(
        "pdf.watermark",
        [src],
        {
            "text": "草稿",
            "pages": "2",
            "behind": True,
            "layout": "tiled",
            "font": "serif",
            "bold": False,
        },
    )
    out = run.outputs[0]
    assert "草稿" not in pdftotext(out, 1)
    assert "草" in pdftotext(out, 2) and "稿" in pdftotext(out, 2)
    assert "第 2 頁" in pdftotext(out, 2)
    with pikepdf.open(out) as pdf:
        first = pdf.pages[1].obj.Contents
        data = (first[0] if isinstance(first, pikepdf.Array) else first).read_bytes()
        assert b"Do" in data  # 下方圖層畫在最前面


@pytest.mark.parametrize("rotate", [90, 180, 270])
def test_watermark_position_on_rotated_page(tmp_path, rotate):
    src = _rotated_pdf(tmp_path / f"r{rotate}.pdf", rotate)
    run = run_tool(
        "pdf.watermark",
        [src],
        {"text": "X", "position": 0, "rotation": 0, "opacity": 100, "ink": "#000000", "size": 40},
    )
    img = _render(run.outputs[0], 1)
    w, h = img.size
    box = _dark_box(img.crop((0, 0, w // 3, h // 3)))
    assert box is not None, "左上角應該看得到浮水印"
    # 左上角距離邊緣約 36pt（36 dpi 下約 18 px）
    assert box[0] < 40 and box[1] < 40


def test_watermark_validation(pdf_file):
    t = tool("pdf.watermark")
    info = t.inspect([pdf_file])
    assert t.validate([pdf_file], {**t.defaults(), "text": "  "}, info) == "請輸入浮水印文字"
    assert t.validate([pdf_file], {**t.defaults(), "pages": "8"}, info) == "頁碼範圍不正確"
    assert t.validate([pdf_file], t.defaults(), info) is None
    assert not t.option("position").is_visible({**t.defaults(), "layout": "tiled"}, info)
    behind = t.option("behind").note
    assert behind({"behind": True}, {}) == ("頁面有白色底色時，下方的浮水印會被蓋住。", False)
    assert behind({"behind": False}, {}) is None
    assert t.option("size").display(60) == "60 pt"
    assert t.option("opacity").display(30) == "30%"


def test_watermark_errors(tmp_path, pdf_file):
    with pytest.raises(errors.QSError, match="頁碼範圍「9」不正確"):
        run_tool("pdf.watermark", [pdf_file], {"pages": "9"})


# ------------------------------------------------------------------ 頁碼


def test_page_numbers_default_preset(tmp_path):
    src = make_rich_pdf(tmp_path / "頁.pdf", pages=3)
    t = tool("pdf.page-numbers")
    assert t.presets["pdf.add-page-numbers"] == {}
    run = run_tool("pdf.page-numbers", [src], t.values_for_preset("pdf.add-page-numbers"))
    out = run.outputs[0]
    assert out.name == "頁-已加頁碼.pdf"
    assert "第 2 頁" in pdftotext(out, 2)
    lines = [ln.strip() for ln in pdftotext(out, 2).splitlines() if ln.strip()]
    assert "2" in lines
    img = _render(out, 1)
    w, h = img.size
    assert _dark_box(img.crop((w // 3, h - 40, 2 * w // 3, h))) is not None  # 下方置中


def test_page_numbers_format_and_skip_first(tmp_path):
    src = make_pdf(tmp_path / "n.pdf", pages=3)
    run = run_tool(
        "pdf.page-numbers",
        [src],
        {"format": "chinese_total", "skip_first": True, "start": 1, "position": 2},
    )
    out = run.outputs[0]
    assert "共" not in pdftotext(out, 1)
    assert "第 1 頁，共 2 頁" in pdftotext(out, 2)
    assert "第 2 頁，共 2 頁" in pdftotext(out, 3)


def test_page_numbers_on_rotated_page(tmp_path):
    src = _rotated_pdf(tmp_path / "rot.pdf", 90)
    run = run_tool("pdf.page-numbers", [src], {"size": 30})
    img = _render(run.outputs[0], 1)
    w, h = img.size
    assert _dark_box(img.crop((w // 3, h - 40, 2 * w // 3, h))) is not None


def test_page_number_options():
    t = tool("pdf.page-numbers")
    pos = t.option("position")
    assert [c.value for c in pos.choices] == [0, 1, 2, 6, 7, 8]
    assert t.defaults()["position"] == 7
    assert [c.title for c in t.option("format").choices] == [
        "1",
        "第 1 頁",
        "1 / N",
        "第 1 頁，共 N 頁",
        "Page 1",
    ]
    assert t.defaults()["size"] == 11 and t.defaults()["margin"] == 30


def test_placement_unit_with_userunit(tmp_path):
    src = make_pdf(tmp_path / "u.pdf", pages=1, size=(300, 400))
    with pikepdf.open(src, allow_overwriting_input=True) as pdf:
        pdf.pages[0].obj.UserUnit = 2
        pdf.save(src)
    run = run_tool("pdf.page-numbers", [src])
    with pikepdf.open(run.outputs[0]) as pdf:
        xobjs = pdf.pages[0].obj.Resources.XObject
        forms = [x for k, x in xobjs.items() if k.startswith("/QSOverlay")]
        assert forms and float(forms[0].Matrix[0]) == pytest.approx(0.5)
