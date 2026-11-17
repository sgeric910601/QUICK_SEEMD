"""PDF 工具的共用核心：開檔、頁面大小、文件資訊、書籤、組合頁面。"""

from __future__ import annotations

from pathlib import Path

import pikepdf
import pytest
from pikepdf import Name

from conftest import make_pdf
from quickseemd.core import errors
from quickseemd.core.tools.pdf import assemble, common, outline
from test_pdf_helpers import encrypt, make_rich_pdf, outline_titles, pdftotext

# ------------------------------------------------------------------ 開檔


def test_open_pdf_errors(tmp_path):
    with pytest.raises(errors.QSError, match="找不到檔案「沒有.pdf」"):
        common.open_pdf(tmp_path / "沒有.pdf")
    bad = tmp_path / "壞.pdf"
    bad.write_bytes(b"not a pdf at all")
    with pytest.raises(
        errors.QSError, match="無法讀取檔案內容：「壞.pdf」不是有效的 PDF，或檔案已損毀。"
    ):
        common.open_pdf(bad)


def test_open_pdf_locked_and_restricted(tmp_path, pdf_file):
    locked = encrypt(pdf_file, tmp_path / "鎖.pdf", user="u")
    with pytest.raises(
        errors.QSError, match="「鎖.pdf」受密碼保護，請先用「解鎖 PDF」工具移除密碼後再試。"
    ):
        common.open_pdf(locked)
    assert common.is_locked(locked)
    assert not common.is_locked(pdf_file)
    with common.open_pdf(locked, password="u") as pdf:
        assert len(pdf.pages) == 3
    limited = encrypt(pdf_file, tmp_path / "限.pdf", extract=False)
    with pytest.raises(errors.QSError, match="設有權限限制（禁止修改或複製）"):
        common.open_pdf(limited)
    full = encrypt(pdf_file, tmp_path / "全.pdf")
    with common.open_pdf(full) as pdf:
        assert pdf.is_encrypted


def test_open_pdf_without_pages(tmp_path):
    empty = tmp_path / "空.pdf"
    pikepdf.new().save(empty)
    with pytest.raises(errors.QSError, match="「空.pdf」沒有任何頁面。"):
        common.open_pdf(empty)


def test_page_list_and_padding(tmp_path):
    assert common.page_list("", 3, tmp_path / "a.pdf") == [1, 2, 3]
    assert common.page_list("3, 1", 3, tmp_path / "a.pdf") == [3, 1]
    with pytest.raises(errors.QSError, match="頁碼範圍「9」不正確：「a.pdf」共 3 頁。"):
        common.page_list("9", 3, tmp_path / "a.pdf")
    assert common.padded(7, 9) == "07"
    assert common.padded(7, 120) == "007"


# ------------------------------------------------------------------ 頁面大小


def test_visual_size_rotation_crop_userunit():
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(400, 300))
    page = pdf.pages[0]
    assert common.visual_size(page) == (400, 300)
    page.obj.CropBox = [10, 20, 210, 120]
    page.obj.Rotate = 90
    assert common.crop_box(page) == (10, 20, 210, 120)
    assert common.visual_size(page) == (100, 200)
    page.obj.UserUnit = 2
    assert common.visual_size(page) == (200, 400)
    page.obj.Rotate = -90
    assert common.rotation(page) == 270
    page.obj.CropBox = [-500, -500, 9000, 9000]  # 超出媒體框：取交集
    assert common.crop_box(page) == (0, 0, 400, 300)


def test_info_roundtrip(tmp_path, pdf_file):
    with pikepdf.open(pdf_file) as pdf:
        common.write_info(pdf, {"Title": "標題", "Author": "", "Keywords": "a, b"})
        info = common.read_info(pdf)
        assert info == {"Title": "標題", "Keywords": "a, b"}
        assert str(pdf.docinfo.Producer) == "QUICK_SEEMD"
        assert str(pdf.docinfo.ModDate).startswith("D:") and str(pdf.docinfo.ModDate).endswith("Z")


# ------------------------------------------------------------------ 檔案資訊與檢查


def test_inspect_single_and_multi(tmp_path, pdf_file):
    info = common.inspect_pdfs([pdf_file])
    assert info["page_count"] == 3
    assert info["page_sizes"][0] == pytest.approx([595.28, 841.89])
    assert info["encrypted"] is False
    assert info["has_text"] is True
    assert info["locked"] == []
    assert info["first_base"] == "文件"
    locked = encrypt(pdf_file, tmp_path / "鎖.pdf", user="u")
    multi = common.inspect_pdfs([pdf_file, locked])
    assert "page_count" not in multi
    assert multi["locked"] == ["鎖.pdf"]
    assert multi["encrypted_names"] == ["鎖.pdf"]
    assert multi["total_pages"] == 3
    assert common.locked_reason(multi) == "「鎖.pdf」受密碼保護，請先用「解鎖 PDF」移除密碼"
    v = common.make_validator(lambda f, v, i: "其他原因")
    assert v([pdf_file], {}, multi).startswith("「鎖.pdf」")
    assert v([pdf_file], {}, info) == "其他原因"


def test_inspect_handles_broken(tmp_path):
    bad = tmp_path / "壞.pdf"
    bad.write_bytes(b"%PDF-1.4 broken")
    info = common.inspect_pdfs([bad])
    assert info["locked"] == []
    assert "page_count" not in info


def test_range_reason_and_caption():
    assert common.range_reason("1-9", {"page_count": 3}) == "頁碼範圍不正確"
    assert common.range_reason("1-2", {"page_count": 3}) is None
    assert common.range_reason("1-99", {}) is None
    note = common.range_caption("pages")
    assert note({"pages": "1-9"}, {"page_count": 3}) == ("範圍不正確（共 3 頁）", True)


def test_value_coercion_and_ink():
    assert common.as_int("12", 1) == 12 and common.as_int(None, 5) == 5
    assert common.as_float("x", 2.5) == 2.5
    assert common.as_bool("true", False) and not common.as_bool("0", True)
    assert common.as_bool(None, True)
    assert common.ink_rgb(common.INK_RED) == (0.86, 0.16, 0.14)
    assert common.ink_rgb("#ff0000") == (1.0, 0.0, 0.0)
    assert common.ink_rgb("garbage") == (0.0, 0.0, 0.0)


# ------------------------------------------------------------------ 書籤


def test_read_outline_with_named_destinations(tmp_path):
    src = make_rich_pdf(tmp_path / "書.pdf")
    with pikepdf.open(src) as pdf:
        nodes = outline.read_outline(pdf, lambda i: i)
    assert [n.title for n in nodes] == ["第一章", "第二章"]
    assert nodes[0].page == 0 and nodes[0].point == (0, 800) and nodes[0].open
    assert nodes[0].children[0].title == "1.1"
    assert nodes[0].children[0].point == (0, pytest.approx(841.89))
    assert nodes[1].page == 5  # 具名目的地解析成最後一頁
    # 只保留前兩頁：第二章的目的地不在新文件中 → 略過
    with pikepdf.open(src) as pdf:
        subset = outline.read_outline(pdf, lambda i: i if i < 2 else None)
    assert [n.title for n in subset] == ["第一章"]


def test_outline_survives_loops(tmp_path, pdf_file):
    with pikepdf.open(pdf_file) as pdf:
        root = pdf.make_indirect(pikepdf.Dictionary(Type=Name.Outlines))
        a = pdf.make_indirect(pikepdf.Dictionary(Title="A", Dest=[pdf.pages[0].obj, Name.Fit]))
        a.Next = a  # 自己指向自己
        root.First = a
        pdf.Root.Outlines = root
        nodes = outline.read_outline(pdf, lambda i: i)
    assert [n.title for n in nodes] == ["A"]


def test_write_outline_counts(tmp_path, pdf_file):
    nodes = (
        outline.OutlineNode("甲", 0, (0, 800), True, (outline.OutlineNode("甲一", 1),)),
        outline.OutlineNode("乙", 2, None, False, (outline.OutlineNode("乙一", 2),)),
    )
    with pikepdf.open(pdf_file) as pdf:
        outline.write_outline(pdf, nodes)
        root = pdf.Root.Outlines
        assert int(root.Count) == 3  # 甲、甲一、乙（乙收合）
        assert int(root.Last.Count) == -1
        assert pdf.Root.PageMode == Name.UseOutlines
        pdf.save(tmp_path / "o.pdf")
    assert outline_titles(tmp_path / "o.pdf") == [
        (0, "甲", 0),
        (1, "甲一", 1),
        (0, "乙", 2),
        (1, "乙一", 2),
    ]


# ------------------------------------------------------------------ 組合頁面


def test_assemble_subset_drops_links_and_catalog(tmp_path):
    src = make_rich_pdf(tmp_path / "書.pdf")
    out = tmp_path / "out.pdf"
    with pikepdf.open(src) as pdf:
        entries = [assemble.Entry(pdf, 1), assemble.Entry(pdf, 2), assemble.Entry(pdf, 0)]
        mapping = {1: 0, 2: 1, 0: 2}
        nodes = outline.read_outline(pdf, mapping.get)
        assemble.write(entries, nodes, common.read_info(pdf), out)
    text = pdftotext(out)
    assert text.index("第 2 頁") < text.index("第 3 頁") < text.index("第 1 頁")
    with pikepdf.open(out) as res:
        assert "/StructTreeRoot" not in res.Root and "/PageLabels" not in res.Root
        assert str(res.Root.Lang) == "zh-TW"
        # 第 2 頁 → 第 3 頁的連結保留，且指向新文件的頁面
        link = res.pages[0].obj.Annots[0]
        assert link.A.D[0].objgen == res.pages[1].obj.objgen
        # 原第 1 頁的具名連結指向最後一頁（不在新文件）→ 移除
        assert "/Annots" not in res.pages[2].obj
        assert str(res.docinfo.Title) == "原始標題"
        assert str(res.docinfo.Producer) == "QUICK_SEEMD"
    assert [t for _d, t, _p in outline_titles(out)] == ["第一章", "1.1"]


def test_assemble_whole_document_keeps_catalog(tmp_path):
    src = make_rich_pdf(tmp_path / "書.pdf")
    out = tmp_path / "out.pdf"
    with pikepdf.open(src) as pdf:
        entries = [assemble.Entry(pdf, i) for i in range(len(pdf.pages))]
        assemble.write(entries, outline.read_outline(pdf, lambda i: i), {}, out)
    with pikepdf.open(out) as res:
        assert "/StructTreeRoot" in res.Root and "/PageLabels" in res.Root
        pg = res.Root.StructTreeRoot.K[0].Pg
        assert pg.objgen == res.pages[0].obj.objgen
        link = res.pages[0].obj.Annots[0]
        assert link.Dest[0].objgen == res.pages[5].obj.objgen  # 具名連結已解析


def test_assemble_blank_rotation_and_duplicates(tmp_path, pdf_file):
    out = tmp_path / "out.pdf"
    with pikepdf.open(pdf_file) as pdf:
        entries = [
            assemble.Entry(pdf, 0, rotation=90),
            assemble.Entry(pdf, None, blank_size=(300, 200)),
            assemble.Entry(pdf, 0),
        ]
        assemble.write(entries, (), {}, out)
    with pikepdf.open(out) as res:
        assert len(res.pages) == 3
        assert int(res.pages[0].obj.Rotate) == 90
        assert int(res.pages[2].obj.Rotate) == 0
        assert [float(v) for v in res.pages[1].mediabox] == [0, 0, 300, 200]
    assert pdftotext(out, 3).count("第 1 頁") == 1


def test_assemble_rejects_empty(tmp_path):
    with pytest.raises(errors.QSError, match="至少要保留一頁"):
        assemble.write([], (), {}, tmp_path / "x.pdf")


def test_assemble_from_two_sources(tmp_path):
    a = make_pdf(tmp_path / "a.pdf", pages=1)
    b = make_pdf(tmp_path / "b.pdf", pages=2)
    out = tmp_path / "out.pdf"
    with pikepdf.open(a) as pa, pikepdf.open(b) as pb:
        entries = [assemble.Entry(pa, 0), assemble.Entry(pb, 0), assemble.Entry(pb, 1)]
        assemble.write(entries, (), {"Title": "合併"}, out)
    with pikepdf.open(out) as res:
        assert len(res.pages) == 3
        assert "/Lang" not in res.Root


def test_assemble_progress_and_cancel(tmp_path, pdf_file):
    seen: list[float] = []
    with pikepdf.open(pdf_file) as pdf:
        entries = [assemble.Entry(pdf, i) for i in range(3)]
        assemble.write(entries, (), {}, tmp_path / "p.pdf", progress=seen.append)

        def cancel() -> None:
            raise errors.Cancelled()

        with pytest.raises(errors.Cancelled):
            assemble.write(entries, (), {}, tmp_path / "c.pdf", check=cancel)
    assert seen[-1] == 1.0
    assert not (tmp_path / "c.pdf").exists()


def test_verify_pages_mismatch(tmp_path, pdf_file):
    with pytest.raises(errors.QSError, match="頁數不符"):
        common.verify_pages(pdf_file, 5)
    junk = tmp_path / "junk.pdf"
    junk.write_bytes(b"x")
    with pytest.raises(errors.QSError, match="寫入輸出檔失敗"):
        common.verify_pages(junk, 1)


# ------------------------------------------------------------------ 惡意結構（不可信的檔案）


def _hostile_name_tree(pdf: pikepdf.Pdf) -> None:
    """自己參照自己、又層層重複參照同一批節點的名稱樹（沒有防護時會指數爆量）。"""
    leaf = pdf.make_indirect(
        pikepdf.Dictionary(Names=[pikepdf.String("x"), [pdf.pages[0].obj, Name.Fit]])
    )
    node = leaf
    for _ in range(25):  # 3^25 條路徑；名稱樹深度上限是 32
        node = pdf.make_indirect(pikepdf.Dictionary(Kids=[node, node, node]))
    loop = pdf.make_indirect(pikepdf.Dictionary())
    loop.Kids = pikepdf.Array([loop, node, loop])
    pdf.Root.Names = pikepdf.Dictionary(Dests=loop)


def test_name_tree_cycles_and_dags_finish_quickly(pdf_file):
    import time

    with pikepdf.open(pdf_file) as pdf:
        _hostile_name_tree(pdf)
        started = time.monotonic()
        dests = outline.Destinations.of(pdf)
        assert dests.resolve(pikepdf.String("x")) is not None
        assert dests.resolve(pikepdf.String("沒有")) is None
        assert dests.resolve(Name("/x")) is not None
        assert dests.resolve(42) is None
    assert time.monotonic() - started < 2


def test_looping_outline_and_links_finish_quickly(tmp_path, pdf_file):
    import time

    with pikepdf.open(pdf_file) as pdf:
        _hostile_name_tree(pdf)
        root = pdf.make_indirect(pikepdf.Dictionary(Type=Name.Outlines))
        a = pdf.make_indirect(pikepdf.Dictionary(Title="A", Dest=pikepdf.String("x")))
        b = pdf.make_indirect(pikepdf.Dictionary(Title="B", Dest=[pdf.pages[1].obj, Name.Fit]))
        a.Next, b.Next = b, a  # 兄弟互相指向
        a.First = root  # 子書籤指回根
        root.First = a
        pdf.Root.Outlines = root
        links = [
            pdf.make_indirect(
                pikepdf.Dictionary(
                    Type=Name.Annot, Subtype=Name.Link, Rect=[0, 0, 9, 9], Dest=pikepdf.String("x")
                )
            )
            for _ in range(200)
        ]
        pdf.pages[1].obj.Annots = pikepdf.Array(links)
        started = time.monotonic()
        nodes = outline.read_outline(pdf, lambda i: i)
        assemble.write([assemble.Entry(pdf, 1)], (), {}, tmp_path / "o.pdf")
    assert time.monotonic() - started < 3
    assert [n.title for n in nodes] == ["A", "B"]
    with pikepdf.open(tmp_path / "o.pdf") as res:
        assert "/Annots" not in res.pages[0].obj  # 指向第 1 頁（不在新文件）的連結都移除


def test_parent_loop_does_not_hang():
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(100, 100))
    page = pdf.pages[0].obj
    a = pdf.make_indirect(pikepdf.Dictionary())
    a.Parent = a
    del page["/MediaBox"]
    page.Parent = a
    assert common.crop_box(page) == (0.0, 0.0, *common.A4)
    assert common.rotation(page) == 0
