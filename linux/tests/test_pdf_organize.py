"""整理 PDF、旋轉 PDF。"""

from __future__ import annotations

import json

import pikepdf
import pytest

from conftest import make_pdf
from quickseemd.core import errors
from test_pdf_helpers import (
    digest,
    make_rich_pdf,
    outline_titles,
    page_count,
    page_rotations,
    pdftotext,
    run_tool,
    tool,
)

# ------------------------------------------------------------------ 整理


def _plan(*items) -> str:
    return json.dumps([{"src": s, "rotate": r} for s, r in items])


def test_organize_reorder_rotate_blank(tmp_path):
    src = make_rich_pdf(tmp_path / "整.pdf", pages=4)
    before = digest(src)
    plan = _plan((3, 0), (None, 0), (0, 90), (1, 270))
    run = run_tool("pdf.organize", [src], {"pages": plan})
    out = run.outputs[0]
    assert out.name == "整-已整理.pdf"
    assert page_count(out) == 4
    assert page_rotations(out) == [0, 0, 90, 270]
    assert pdftotext(out, 1).count("第 4 頁") == 1
    assert pdftotext(out, 2).strip() == ""
    assert "第 1 頁" in pdftotext(out, 3)
    titles = outline_titles(out)
    assert (0, "第一章", 2) in titles and (1, "1.1", 3) in titles and (0, "第二章", 0) in titles
    with pikepdf.open(out) as pdf:
        # 空白頁沿用前一頁（原第 4 頁）看起來的大小
        assert [round(float(v), 2) for v in pdf.pages[1].mediabox] == [0, 0, 595.28, 841.89]
    assert digest(src) == before


def test_organize_blank_size_and_existing_rotation(tmp_path):
    src = make_pdf(tmp_path / "o.pdf", pages=2, size=(300, 500))
    with pikepdf.open(src, allow_overwriting_input=True) as pdf:
        pdf.pages[0].obj.Rotate = 90
        pdf.save(src)
    run = run_tool("pdf.organize", [src], {"pages": _plan((None, 0), (0, 90), (None, 0))})
    out = run.outputs[0]
    assert page_rotations(out) == [0, 180, 0]
    with pikepdf.open(out) as pdf:
        # 開頭的空白頁用第一頁看起來的大小（已旋轉 → 500×300）；後面的空白頁沿用前一頁
        assert [float(v) for v in pdf.pages[0].mediabox] == [0, 0, 500, 300]
        assert [float(v) for v in pdf.pages[2].mediabox] == [0, 0, 500, 300]


def test_organize_unchanged_when_empty_plan(tmp_path):
    src = make_rich_pdf(tmp_path / "o.pdf", pages=3)
    run = run_tool("pdf.organize", [src], {"pages": ""})
    out = run.outputs[0]
    assert page_count(out) == 3
    with pikepdf.open(out) as pdf:
        assert "/StructTreeRoot" in pdf.Root  # 整份同順序：沿用目錄項目


def test_organize_errors(tmp_path, pdf_file):
    with pytest.raises(errors.QSError, match="至少要保留一頁"):
        run_tool("pdf.organize", [pdf_file], {"pages": "[]"})
    with pytest.raises(
        errors.QSError, match="「文件.pdf」已經變更（找不到第 9 頁），請重新選擇檔案。"
    ):
        run_tool("pdf.organize", [pdf_file], {"pages": _plan((8, 0))})
    with pytest.raises(errors.QSError, match="請選擇一個 PDF 檔案。"):
        run_tool("pdf.organize", [], {"pages": ""})
    with pytest.raises(errors.QSError, match="頁面安排的資料格式不正確"):
        run_tool("pdf.organize", [pdf_file], {"pages": "{壞"})


def test_organize_definition_and_inspect(pdf_file):
    t = tool("pdf.organize")
    assert t.custom_view == "pdf-organize"
    assert not t.allows_multiple
    opt = t.option("pages")
    assert opt.default == "" and not opt.is_visible({}, {})
    info = t.inspect([pdf_file])
    assert info["page_count"] == 3
    assert len(info["page_sizes"]) == 3
    assert info["encrypted"] is False and info["has_text"] is True
    assert "metadata" in info
    assert t.validate([pdf_file], {"pages": "[]"}, info) == "至少要保留一頁"
    assert t.validate([pdf_file], {"pages": ""}, info) is None
    assert t.validate([pdf_file], {"pages": "x"}, info) == "頁面安排的資料格式不正確。"


# ------------------------------------------------------------------ 旋轉


def test_rotate_selected_pages(tmp_path):
    src = make_rich_pdf(tmp_path / "r.pdf", pages=3)
    before = digest(src)
    run = run_tool("pdf.rotate", [src], {"angle": 270, "pages": "2-3"})
    out = run.outputs[0]
    assert out.name == "r-已旋轉.pdf"
    assert page_rotations(out) == [0, 270, 270]
    assert "第 2 頁" in pdftotext(out)
    with pikepdf.open(out) as pdf:
        assert "/StructTreeRoot" in pdf.Root and "/Outlines" in pdf.Root
    assert digest(src) == before


def test_rotate_preset_adds_to_existing(tmp_path):
    src = make_pdf(tmp_path / "r.pdf", pages=2)
    with pikepdf.open(src, allow_overwriting_input=True) as pdf:
        pdf.pages[0].obj.Rotate = 270
        pdf.save(src)
    t = tool("pdf.rotate")
    assert t.presets["pdf.rotate-right"] == {"angle": 90, "pages": ""}
    run = run_tool(
        "pdf.rotate",
        [src, make_pdf(tmp_path / "s.pdf", pages=1)],
        t.values_for_preset("pdf.rotate-right"),
    )
    assert len(run.outputs) == 2
    assert page_rotations(run.outputs[0]) == [0, 90]


def test_rotate_validation_and_errors(pdf_file):
    t = tool("pdf.rotate")
    info = t.inspect([pdf_file])
    assert t.validate([pdf_file], {"angle": 90, "pages": "5"}, info) == "頁碼範圍不正確"
    assert t.validate([pdf_file], {"angle": 90, "pages": "1"}, info) is None
    with pytest.raises(errors.QSError, match="頁碼範圍「5」不正確"):
        run_tool("pdf.rotate", [pdf_file], {"pages": "5"})
