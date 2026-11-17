"""合併、拆分、刪除頁面。"""

from __future__ import annotations

from pathlib import Path

import cairo
import pikepdf
import pytest

from conftest import make_pdf
from quickseemd.core import errors
from test_pdf_helpers import (
    digest,
    make_rich_pdf,
    outline_titles,
    page_count,
    pdftotext,
    run_tool,
    tool,
)

# ------------------------------------------------------------------ 合併


def test_merge_with_bookmarks(tmp_path):
    a = make_rich_pdf(tmp_path / "甲.pdf", pages=6)
    b = make_pdf(tmp_path / "乙.pdf", pages=3)
    before = digest(a), digest(b)
    run = run_tool("pdf.merge", [a, b])
    assert [p.name for p in run.outputs] == ["甲-已合併.pdf"]
    out = run.outputs[0]
    assert out.parent == tmp_path
    assert page_count(out) == 9
    text = pdftotext(out)
    assert text.count("第 1 頁") == 2 and "第 6 頁" in text
    titles = outline_titles(out)
    assert titles[0] == (0, "甲", 0)
    assert (1, "第一章", 0) in titles and (2, "1.1", 1) in titles
    assert (1, "第二章", 5) in titles
    assert titles[-1] == (0, "乙", 6)
    with pikepdf.open(out) as pdf:
        assert str(pdf.docinfo.Title) == "甲-已合併"
    assert (digest(a), digest(b)) == before


def test_merge_without_bookmarks_and_duplex(tmp_path):
    a = make_pdf(tmp_path / "a.pdf", pages=3, size=(300, 400))
    b = make_rich_pdf(tmp_path / "b.pdf", pages=3)
    run = run_tool("pdf.merge", [a, b], {"bookmarks": False, "duplex": True, "name": " 報告/最終 "})
    out = run.outputs[0]
    assert out.name == "報告-最終.pdf"
    assert page_count(out) == 7  # 3 頁＋空白頁＋3 頁
    with pikepdf.open(out) as pdf:
        assert [float(v) for v in pdf.pages[3].mediabox] == [0, 0, 300, 400]
        assert str(pdf.docinfo.Title) == "報告-最終"
    assert outline_titles(out) == [(0, "第一章", 4), (1, "1.1", 5), (0, "第二章", 6)]
    assert pdftotext(out, 4).strip() == ""


def test_merge_custom_name_keeps_pdf_suffix(tmp_path):
    a = make_pdf(tmp_path / "a.pdf", pages=1)
    b = make_pdf(tmp_path / "b.pdf", pages=1)
    run = run_tool("pdf.merge", [a, b], {"name": "結果.PDF"})
    assert run.outputs[0].name == "結果.PDF"


def test_merge_needs_two_files(pdf_file):
    with pytest.raises(errors.QSError, match="合併需要至少 2 個 PDF 檔案。"):
        run_tool("pdf.merge", [pdf_file])


def test_merge_preset_and_definition(tmp_path):
    t = tool("pdf.merge")
    assert t.minimum_files == 2 and t.reorderable
    assert t.presets["pdf.merge-pdfs"] == {"bookmarks": True, "duplex": False, "name": ""}
    note = t.option("_merge_note").note
    assert note({}, {"total_pages": 9}) == (
        "合併後共 9 頁。在左側拖曳檔案卡片即可調整順序。",
        False,
    )
    assert note({}, {"total_pages": 0}) is None


def test_merge_never_overwrites(tmp_path):
    a = make_pdf(tmp_path / "a.pdf", pages=1)
    b = make_pdf(tmp_path / "b.pdf", pages=1)
    first = run_tool("pdf.merge", [a, b]).outputs[0]
    second = run_tool("pdf.merge", [a, b]).outputs[0]
    assert first.name == "a-已合併.pdf" and second.name == "a-已合併 (1).pdf"


# ------------------------------------------------------------------ 拆分


def test_split_prefill_ranges():
    t = tool("pdf.split")
    assert t.prefill([], {"page_count": 7}) == {"ranges": "1-4, 5-7"}
    assert t.prefill([], {"page_count": 2}) == {"ranges": "1, 2"}
    assert t.prefill([], {"page_count": 3}) == {"ranges": "1-2, 3"}
    assert t.prefill([], {}) == {}


def test_split_ranges_into_folder(tmp_path):
    src = make_rich_pdf(tmp_path / "書.pdf", pages=6)
    before = digest(src)
    run = run_tool("pdf.split", [src], {"ranges": "1-3, 4、6"})
    assert [p.name for p in run.outputs] == ["書-已拆分"]
    folder = run.outputs[0]
    names = sorted(p.name for p in folder.iterdir())
    assert names == ["書-第01-03頁.pdf", "書-第04頁.pdf", "書-第06頁.pdf"]
    assert "第 2 頁" in pdftotext(folder / "書-第01-03頁.pdf")
    assert [t for _d, t, _p in outline_titles(folder / "書-第01-03頁.pdf")] == ["第一章", "1.1"]
    assert run.summary == "已拆成 3 個檔案"
    assert digest(src) == before


def test_split_every_n_without_folder(tmp_path):
    src = make_pdf(tmp_path / "s.pdf", pages=5)
    run = run_tool("pdf.split", [src], {"mode": "every_n", "chunk": 2, "folder": False})
    assert sorted(p.name for p in run.outputs) == [
        "s-第01-02頁.pdf",
        "s-第03-04頁.pdf",
        "s-第05頁.pdf",
    ]
    assert all(p.parent == tmp_path for p in run.outputs)


def test_split_every_page_preset(tmp_path):
    src = make_pdf(tmp_path / "s.pdf", pages=3)
    t = tool("pdf.split")
    values = t.values_for_preset("pdf.split-pages")
    assert values["mode"] == "every_page" and values["folder"] is True
    run = run_tool("pdf.split", [src], values)
    folder = run.outputs[0]
    assert sorted(p.name for p in folder.iterdir()) == [
        "s-第01頁.pdf",
        "s-第02頁.pdf",
        "s-第03頁.pdf",
    ]
    assert "第 3 頁" in pdftotext(folder / "s-第03頁.pdf")


def test_split_extract_merged_and_separate(tmp_path):
    src = make_pdf(tmp_path / "s.pdf", pages=5)
    run = run_tool("pdf.split", [src], {"mode": "extract", "extract": "2, 4-5"})
    assert [p.name for p in run.outputs] == ["s-已擷取.pdf"]
    assert page_count(run.outputs[0]) == 3
    run2 = run_tool("pdf.split", [src], {"mode": "extract", "extract": "1-3", "separate": True})
    assert sorted(p.name for p in run2.outputs[0].iterdir()) == [
        "s-第01頁.pdf",
        "s-第02頁.pdf",
        "s-第03頁.pdf",
    ]
    # 整份擷取（不分開）是允許的
    whole = run_tool("pdf.split", [src], {"mode": "extract", "extract": ""})
    assert page_count(whole.outputs[0]) == 5


def test_split_non_contiguous_label(tmp_path):
    src = make_pdf(tmp_path / "s.pdf", pages=4)
    run = run_tool("pdf.split", [src], {"ranges": "2, 4"})
    names = sorted(p.name for p in run.outputs[0].iterdir())
    assert names == ["s-第02頁.pdf", "s-第04頁.pdf"]
    from quickseemd.core.tools.pdf.pages import split_label

    assert split_label([2, 3, 5], 9) == "第02頁等3頁"
    assert split_label([1, 2, 3], 120) == "第001-003頁"


def test_split_errors(tmp_path):
    src = make_pdf(tmp_path / "s.pdf", pages=3)
    with pytest.raises(errors.QSError, match="拆分範圍「1-9」不正確：「s.pdf」共 3 頁。"):
        run_tool("pdf.split", [src], {"ranges": "1-9"})
    with pytest.raises(errors.QSError, match="「s.pdf」只有 3 頁，拆分後還是同一份檔案。"):
        run_tool("pdf.split", [src], {"ranges": "1-3"})
    single = run_tool("pdf.split", [src], {"ranges": "2"})
    assert [p.name for p in single.outputs] == ["s-第02頁.pdf"]


def test_split_validation_and_notes():
    t = tool("pdf.split")
    v = t.validate
    info = {"page_count": 6, "locked": []}
    assert v([], {**t.defaults(), "ranges": " "}, info) == "請輸入要拆分的範圍，例如 1-3, 4-6"
    assert v([], {**t.defaults(), "ranges": "1-9"}, info) == "範圍不正確（共 6 頁）"
    assert v([], {**t.defaults(), "mode": "extract", "extract": ""}, info) == "請輸入要擷取的頁面"
    assert (
        v([], {**t.defaults(), "mode": "extract", "extract": "8"}, info)
        == "頁碼範圍不正確（共 6 頁）"
    )
    assert v([], {**t.defaults(), "mode": "every_n", "chunk": 0}, info) == "每個檔案至少 1 頁"
    assert v([], {**t.defaults(), "mode": "every_page"}, info) is None
    note = t.option("_split_preview").note
    assert note({**t.defaults(), "ranges": "1-2, 3"}, info) == ("會產生 2 個 PDF", False)
    assert note({**t.defaults(), "mode": "every_n", "chunk": 4}, info) == (
        "共 6 頁，會產生 2 個 PDF",
        False,
    )
    assert note({**t.defaults(), "mode": "every_page"}, info) == ("共 6 頁，會產生 6 個 PDF", False)
    ext = {**t.defaults(), "mode": "extract", "extract": "1-3"}
    assert note(ext, info) == ("擷取 3 頁成一個 PDF", False)
    assert note({**ext, "separate": True}, info) == ("會產生 3 個 PDF", False)
    assert note(t.defaults(), {}) is None
    folder = t.option("folder")
    assert not folder.is_visible({**t.defaults(), "mode": "extract"}, info)
    assert folder.is_visible({**t.defaults(), "mode": "extract", "separate": True}, info)


# ------------------------------------------------------------------ 刪除頁面


def _with_blank(path: Path) -> Path:
    """第 2 頁是空白頁（只有白底）的 4 頁 PDF。"""
    surface = cairo.PDFSurface(str(path), 300, 400)
    cr = cairo.Context(surface)
    for i in range(4):
        cr.set_source_rgb(1, 1, 1)
        cr.paint()
        if i != 1:
            cr.set_source_rgb(0, 0, 0)
            cr.rectangle(40, 40, 200, 120)
            cr.fill()
        cr.show_page()
    surface.finish()
    return path


def test_delete_custom_pages(tmp_path):
    src = make_rich_pdf(tmp_path / "d.pdf", pages=6)
    before = digest(src)
    run = run_tool("pdf.delete-pages", [src], {"pages": "2, 5-6"})
    out = run.outputs[0]
    assert out.name == "d-已刪頁.pdf"
    text = pdftotext(out)
    assert "第 2 頁" not in text and "第 1 頁" in text and "第 4 頁" in text
    assert run.summary == "已刪除 3 頁，剩下 3 頁"
    assert [t for _d, t, _p in outline_titles(out)] == ["第一章"]
    assert digest(src) == before


def test_delete_even_odd(tmp_path):
    src = make_pdf(tmp_path / "d.pdf", pages=5)
    even = run_tool("pdf.delete-pages", [src], {"mode": "even"})
    assert page_count(even.outputs[0]) == 3
    odd = run_tool("pdf.delete-pages", [src], {"mode": "odd"})
    assert page_count(odd.outputs[0]) == 2
    assert "第 2 頁" in pdftotext(odd.outputs[0])


@pytest.mark.slow
def test_delete_blank_pages(tmp_path):
    src = _with_blank(tmp_path / "b.pdf")
    run = run_tool("pdf.delete-pages", [src], {"mode": "blank"})
    assert page_count(run.outputs[0]) == 3
    assert run.summary == "已刪除 1 頁，剩下 3 頁"
    plain = make_pdf(tmp_path / "p.pdf", pages=2)
    with pytest.raises(errors.QSError, match="「p.pdf」沒有需要刪除的頁面（沒有偵測到空白頁）。"):
        run_tool("pdf.delete-pages", [plain], {"mode": "blank"})
    multi = run_tool("pdf.delete-pages", [plain, src], {"mode": "blank"})
    assert len(multi.outputs) == 1
    assert any("「p.pdf」沒有需要刪除的頁面，略過" in m for m in multi.messages)


def test_delete_all_pages_rejected(tmp_path):
    src = make_pdf(tmp_path / "d.pdf", pages=1)
    with pytest.raises(errors.QSError, match="「d.pdf」共 1 頁，不能把全部頁面都刪除。"):
        run_tool("pdf.delete-pages", [src], {"mode": "odd"})
    with pytest.raises(errors.QSError, match="請輸入要刪除的頁碼。"):
        run_tool("pdf.delete-pages", [src], {"pages": " "})


def test_delete_validation():
    t = tool("pdf.delete-pages")
    info = {"page_count": 3, "locked": []}
    assert t.validate([], t.defaults(), info) == "請輸入要刪除的頁碼"
    assert t.validate([], {**t.defaults(), "pages": "4"}, info) == "頁碼範圍不正確（共 3 頁）"
    assert t.validate([], {**t.defaults(), "pages": "1-3"}, info) == "不能刪除全部的頁面"
    assert t.validate([], {**t.defaults(), "pages": "1"}, info) is None
    assert t.validate([], {**t.defaults(), "mode": "blank"}, info) is None
    note = t.option("pages").note
    assert note({"pages": "9"}, info) == ("範圍不正確（共 3 頁）", True)
    assert note({"pages": "1"}, info) == ("共 3 頁", False)
    assert note({"pages": ""}, {}) is None


def test_split_chunk_range_follows_page_count():
    opt = tool("pdf.split").option("chunk")
    assert opt.current_range({}, {"page_count": 7}) == (1, 7)
    assert opt.current_range({}, {}) == (1, 9999)
