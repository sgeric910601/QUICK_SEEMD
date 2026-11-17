"""OCR PDF（加入看不見的文字層）與 PDF 轉文字。"""

from __future__ import annotations

import subprocess
import zlib
from pathlib import Path

import pikepdf
import pytest
from pikepdf import Name
from PIL import Image

from conftest import make_pdf
from quickseemd.core import errors
from quickseemd.core.tools.pdf import ocr
from test_pdf_helpers import digest, pdftotext, run_tool, tool


def make_scan_pdf(path: Path, pages: int = 1, blank: bool = False, rotate: int = 0) -> Path:
    """掃描檔：每頁只有一張圖（文字畫在圖裡，沒有文字層）。"""
    text_pdf = make_pdf(path.with_name(path.stem + "-src.pdf"), pages=pages, text=not blank)
    pdf = pikepdf.new()
    for n in range(1, pages + 1):
        base = path.with_name(f"{path.stem}-{n}")
        subprocess.run(
            [
                "/usr/bin/pdftocairo",
                "-png",
                "-gray",
                "-r",
                "150",
                "-singlefile",
                "-f",
                str(n),
                "-l",
                str(n),
                str(text_pdf),
                str(base),
            ],
            check=True,
        )
        with Image.open(str(base) + ".png") as im:
            gray = im.convert("L")
        img = pikepdf.Stream(pdf, zlib.compress(gray.tobytes()))
        img.Type, img.Subtype, img.Width, img.Height = (
            Name.XObject,
            Name.Image,
            gray.width,
            gray.height,
        )
        img.ColorSpace, img.BitsPerComponent, img.Filter = Name.DeviceGray, 8, Name.FlateDecode
        page = pikepdf.Dictionary(
            Type=Name.Page,
            MediaBox=[0, 0, 595.28, 841.89],
            Rotate=rotate,
            Resources=pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im0=img)),
            Contents=pdf.make_stream(b"q 595.28 0 0 841.89 0 0 cm /Im0 Do Q"),
        )
        pdf.pages.append(pikepdf.Page(pdf.make_indirect(page)))
    pdf.save(path)
    return path


def _bbox(path: Path, word: str) -> tuple[float, float]:
    out = subprocess.run(
        ["/usr/bin/pdftotext", "-bbox", str(path), "-"], capture_output=True, check=True
    ).stdout.decode()
    for line in out.splitlines():
        if f">{word}" in line and "<word" in line:
            parts = dict(p.split("=") for p in line.split()[1:5])
            return float(parts["xMin"].strip('"')), float(parts["yMin"].strip('"'))
    raise AssertionError(f"找不到 {word}")


# ------------------------------------------------------------------ 純函式


def test_languages_and_modes():
    assert ocr.tesseract_langs("traditional") == "chi_tra+eng"
    assert ocr.tesseract_langs("simplified") == "chi_sim+eng"
    assert ocr.tesseract_langs("japanese") == "jpn+eng"
    assert ocr.tesseract_langs("korean") == "kor+eng"
    assert ocr.tesseract_langs("english") == "eng"
    assert ocr.effective_accurate({"accurate": False, "language": "traditional"})
    assert not ocr.effective_accurate({"accurate": False, "language": "english"})
    assert ocr.ocr_dpi((595.28, 841.89), True) == 300
    assert ocr.ocr_dpi((595.28, 841.89), False) == 200
    assert ocr.ocr_dpi((5000, 5000), True) == 72
    assert ocr.ocr_dpi((1200, 1000), True) == pytest.approx(4200 / 1200 * 72)


def test_parse_tsv_and_join():
    tsv = "\n".join(
        [
            "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext",
            "5\t1\t1\t1\t1\t1\t100\t10\t40\t20\t90\t第",
            "5\t1\t1\t1\t1\t2\t145\t10\t40\t20\t90\t1",
            "5\t1\t1\t1\t1\t3\t190\t10\t40\t20\t90\t頁",
            "5\t1\t1\t1\t1\t4\t240\t10\t60\t20\t90\tPage",
            "5\t1\t1\t1\t1\t5\t305\t10\t30\t20\t90\t測試",
            "5\t1\t1\t1\t1\t6\t340\t10\t30\t20\t90\t文字",
            "5\t1\t2\t1\t1\t1\t10\t12\t50\t20\t90\tLeft",
            "5\t1\t3\t1\t1\t1\t10\t60\t50\t20\t90\tNext",
            "5\t1\t3\t1\t1\t2\t70\t60\t50\t20\t-1\t",
        ]
    )
    lines = ocr.parse_tsv(tsv)
    assert [ln.text for ln in lines] == ["第 1 頁 Page 測試文字", "Left", "Next"]
    assert ocr.join_lines(lines) == "Left 第 1 頁 Page 測試文字\nNext"


# ------------------------------------------------------------------ OCR PDF


@pytest.mark.slow
def test_ocr_scanned_pdf(tmp_path):
    src = make_scan_pdf(tmp_path / "掃描.pdf")
    before = digest(src)
    t = tool("pdf.ocr")
    assert t.presets["pdf.ocr-searchable"] == {}
    run = run_tool("pdf.ocr", [src], t.values_for_preset("pdf.ocr-searchable"))
    out = run.outputs[0]
    assert out.name == "掃描-可搜尋.pdf"
    text = pdftotext(out)
    assert "測試" in text and "Page" in text
    assert run.summary.startswith("已辨識 1 頁、") and run.summary.endswith("行文字")
    # 文字層的位置和圖上的字對齊（與原本有文字層的 PDF 比較）
    x, y = _bbox(out, "Page")
    ox, oy = _bbox(tmp_path / "掃描-src.pdf", "Page")
    assert abs(x - ox) < 8 and abs(y - oy) < 8
    assert digest(src) == before


@pytest.mark.slow
def test_ocr_rotated_and_mixed_pages(tmp_path):
    scan = make_scan_pdf(tmp_path / "s.pdf", rotate=90)
    text = make_pdf(tmp_path / "t.pdf", pages=1)
    with pikepdf.open(text) as a, pikepdf.open(scan) as b:
        a.pages.extend(b.pages)
        a.save(tmp_path / "mix.pdf")
    run = run_tool("pdf.ocr", [tmp_path / "mix.pdf"], {"accurate": False})
    assert "，略過 1 頁已有文字的頁面" in run.summary
    assert run.summary.endswith("（快速模式不支援此語言，已改用精確模式）")
    assert "測試" in pdftotext(run.outputs[0], 2)


@pytest.mark.slow
def test_ocr_english_fast_and_page_range(tmp_path):
    src = make_scan_pdf(tmp_path / "e.pdf", pages=2)
    run = run_tool("pdf.ocr", [src], {"language": "english", "accurate": False, "pages": "2"})
    assert "（快速" not in run.summary
    assert "Page" in pdftotext(run.outputs[0], 2)
    assert pdftotext(run.outputs[0], 1).strip() == ""
    assert any(m.endswith("（快速）") for m in run.messages)


def test_ocr_errors(tmp_path, pdf_file):
    with pytest.raises(
        errors.QSError,
        match="「文件.pdf」的頁面已經有文字層，不需要 OCR。若要強制辨識，請關閉「略過已有文字的頁面」。",
    ):
        run_tool("pdf.ocr", [pdf_file])
    blank = make_scan_pdf(tmp_path / "空.pdf", blank=True)
    with pytest.raises(
        errors.QSError, match="檔案沒有可轉換的內容：在「空.pdf」中辨識不到任何文字。"
    ):
        run_tool("pdf.ocr", [blank], {"language": "english"})


def test_ocr_definition(pdf_file):
    t = tool("pdf.ocr")
    info = t.inspect([pdf_file])
    assert t.validate([pdf_file], {**t.defaults(), "pages": "9"}, info) == "頁碼範圍不正確"
    cap = t.option("accurate").note
    assert cap({"accurate": True}, {}) == ("準確度最高，適合正式文件。", False)
    assert cap({"accurate": False}, {}) == ("速度較快；中日韓文會自動改用精確模式。", False)
    assert [c.value for c in t.option("language").choices] == [
        "traditional",
        "simplified",
        "japanese",
        "korean",
        "english",
    ]


# ------------------------------------------------------------------ PDF 轉文字


def test_to_text_with_separators(tmp_path, pdf_file):
    run = run_tool("pdf.to-text", [pdf_file])
    out = run.outputs[0]
    assert out.name == "文件.txt"
    body = out.read_text("utf-8")
    assert body.startswith("──── 第 1 頁 ────\n第 1 頁 Page 1 測試文字")
    assert "\n\n──── 第 2 頁 ────\n" in body and body.endswith("\n")


def test_to_text_plain_and_range(tmp_path, pdf_file):
    out = run_tool("pdf.to-text", [pdf_file], {"separators": False, "pages": "3, 1"}).outputs[0]
    body = out.read_text("utf-8")
    assert body == "第 3 頁 Page 3 測試文字\n\f\n第 1 頁 Page 1 測試文字\n"


@pytest.mark.slow
def test_to_text_ocr_fallback(tmp_path):
    src = make_scan_pdf(tmp_path / "掃.pdf")
    run = run_tool("pdf.to-text", [src])
    assert "測試" in run.outputs[0].read_text("utf-8")
    assert run.summary == "有 1 頁以 OCR 辨識"


def test_to_text_errors(tmp_path):
    blank = make_scan_pdf(tmp_path / "空.pdf", blank=True)
    with pytest.raises(errors.QSError, match="「空.pdf」沒有文字層，可開啟「自動 OCR」再試。"):
        run_tool("pdf.to-text", [blank], {"auto_ocr": False})
    with pytest.raises(
        errors.QSError, match="檔案沒有可轉換的內容：「空.pdf」中沒有文字，OCR 也辨識不到內容。"
    ):
        run_tool("pdf.to-text", [blank], {"language": "english"})


def test_to_text_definition(pdf_file):
    t = tool("pdf.to-text")
    assert t.badge == "TXT"
    assert not t.option("language").is_visible({"auto_ocr": False}, {})
    info = t.inspect([pdf_file])
    assert t.validate([pdf_file], {**t.defaults(), "pages": "0"}, info) == "頁碼範圍不正確"
