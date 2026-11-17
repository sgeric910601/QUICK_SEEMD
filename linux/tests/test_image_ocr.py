"""圖片文字辨識：tesseract TSV 的行與段落、TXT／Word／RTF 輸出、合併。"""

from __future__ import annotations

from pathlib import Path

import pytest
from PIL import Image, ImageDraw, ImageFont
from test_image_kit import run_tool, tool

from quickseemd.core import backends, errors
from quickseemd.core.tools.image import ocr, ocr_docs

FONT = "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc"


def _text_image(path: Path, lines: list[tuple[int, str]], size=(900, 420)) -> Path:
    font = ImageFont.truetype(FONT, 36, index=3)
    im = Image.new("RGB", size, "white")
    draw = ImageDraw.Draw(im)
    for y, text in lines:
        draw.text((40, y), text, font=font, fill="black")
    im.save(path)
    return path


@pytest.fixture
def text_png(tmp_path):
    return _text_image(
        tmp_path / "掃描.png",
        [(30, "第一行 中文測試"), (90, "第二行 資料整理 2026"), (300, "新段落 Paragraph")],
    )


@pytest.fixture
def blank_png(tmp_path):
    p = tmp_path / "空白.png"
    Image.new("RGB", (300, 200), "white").save(p)
    return p


# ------------------------------------------------------------------ 行的排列


def W(text, left, top, width=30, height=30):  # noqa: N802 — 測試用的簡短建構函式
    return ocr.Word(text, left, top, width, height)


def test_join_words_spacing():
    assert ocr.join_words([W("第", 0, 0), W("一", 32, 0)]) == "第一"
    assert ocr.join_words([W("第", 0, 0), W("一", 50, 0)]) == "第一"  # 中日韓字之間不空格
    assert ocr.join_words([W("資料", 0, 0, 60), W("2026", 72, 0, 60)]) == "資料 2026"
    assert ocr.join_words([W("測試", 0, 0, 60), W("OK", 61, 0, 30)]) == "測試OK"
    assert ocr.join_words([W("Hello", 0, 0, 60), W("World", 62, 0, 60)]) == "Hello World"


def test_lines_layout_rules():
    boxes = [
        ocr.Line("右邊", 400, 10, 100, 30),
        ocr.Line("左邊", 10, 12, 100, 30),
        ocr.Line("第二行", 10, 50, 100, 30),
        ocr.Line("遠處段落", 10, 200, 100, 30),
    ]
    assert ocr.layout(boxes) == "左邊 右邊\n第二行\n\n遠處段落"
    assert ocr.layout([]) == ""


def test_parse_tsv_groups_lines():
    tsv = (
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
        "5\t1\t1\t1\t1\t1\t10\t10\t30\t30\t95\t第\n"
        "5\t1\t1\t1\t1\t2\t42\t10\t30\t30\t95\t一\n"
        "5\t1\t1\t1\t2\t1\t10\t60\t30\t30\t95\tok\n"
        "5\t1\t1\t1\t2\t2\t50\t60\t30\t30\t5\tjunk\n"
        "4\t1\t1\t1\t3\t0\t10\t90\t30\t30\t-1\t\n"
    )
    lines = ocr.parse_tsv(tsv)
    assert [ln.text for ln in lines] == ["第一", "ok"]


def test_tesseract_arguments():
    assert ocr.languages("traditional") == "chi_tra+eng"
    assert ocr.languages("simplified") == "chi_sim+eng"
    assert ocr.languages("japanese") == "jpn+eng"
    assert ocr.languages("korean") == "kor+eng"
    assert ocr.languages("english") == "eng"
    assert ocr.languages("automatic") == "chi_tra+chi_sim+eng+jpn+kor"
    assert ocr.accurate_mode({"accurate": False, "language": "traditional"}) is True
    assert ocr.accurate_mode({"accurate": False, "language": "english"}) is False


# ------------------------------------------------------------------ 執行


def test_txt_output(text_png, outdir):
    outs, ctx = run_tool("image.ocr", [text_png], outdir)
    assert outs[0].name == "掃描-文字.txt"
    text = outs[0].read_text("utf-8")
    assert text.endswith("\n")
    lines = text.rstrip("\n").split("\n")
    assert "第一行" in lines[0] and "第二行" in lines[1]
    assert lines[2] == "" and "新段落" in lines[3]
    assert ctx.last_message.startswith("辨識出 ")


def test_docx_and_rtf(text_png, outdir):
    import docx

    outs, _ = run_tool("image.ocr", [text_png], outdir, output="docx")
    assert outs[0].name == "掃描-文字.docx"
    d = docx.Document(str(outs[0]))
    texts = [p.text for p in d.paragraphs]
    assert any("第一行" in t for t in texts)
    assert d.styles["Normal"].font.name == "Noto Sans CJK TC"
    outs, _ = run_tool("image.ocr", [text_png], outdir, output="rtf")
    raw = outs[0].read_text("ascii")
    assert raw.startswith("{\\rtf1") and "\\u31532?" in raw  # 「第」


def test_rtf_escaping():
    rtf = ocr_docs.rtf_document([("名稱 {a}", "第一行\\x\n😀")], headings=True)
    assert "\\{a\\}" in rtf and "\\\\x" in rtf
    assert "\\u-10179?\\u-8704?" in rtf  # 😀 的 UTF-16 代理對
    assert "\\b" in rtf and "\\fs30" in rtf


def test_combine(text_png, blank_png, outdir):
    outs, _ = run_tool("image.ocr", [text_png, blank_png], outdir, combine=True)
    assert [o.name for o in outs] == ["掃描-文字辨識（2 張）.txt"]
    body = outs[0].read_text("utf-8")
    assert body.startswith("＝＝ 掃描.png ＝＝\n")
    assert "＝＝ 空白.png ＝＝\n（沒有辨識到文字）" in body


def test_empty_results(blank_png, text_png, outdir, tmp_path):
    with pytest.raises(errors.QSError, match="在圖片中找不到可辨識的文字。"):
        run_tool("image.ocr", [blank_png], outdir)
    outs, ctx = run_tool("image.ocr", [text_png, blank_png], outdir)
    assert len(outs) == 1
    assert ctx.last_message == "1 張圖片沒有辨識到文字"
    other = tmp_path / "空白2.png"
    other.write_bytes(blank_png.read_bytes())
    with pytest.raises(errors.QSError, match="在這些圖片中找不到可辨識的文字。"):
        run_tool("image.ocr", [blank_png, other], outdir, combine=True)


def test_tesseract_failure(text_png, outdir, monkeypatch, tmp_path):
    fake = tmp_path / "tesseract"
    fake.write_text("#!/bin/sh\necho 'bad data' >&2\nexit 1\n")
    fake.chmod(0o755)
    monkeypatch.setattr(backends, "find", lambda key: str(fake) if key == "tesseract" else None)
    with pytest.raises(errors.QSError, match="文字辨識失敗：bad data"):
        run_tool("image.ocr", [text_png], outdir)


def test_ocr_ui():
    t = tool("image.ocr")
    d = t.defaults()
    assert t.option("accurate").note({**d, "accurate": False}, {}) == (
        "所選語言需要精確模式，會自動改用精確辨識。",
        True,
    )
    assert t.option("accurate").note({**d, "accurate": False, "language": "english"}, {}) is None
    assert t.option("output").note({**d, "output": "docx"}, {}) == ("Word DOCX", False)
    assert not t.option("combine").is_visible(d, {"count": 1})
    assert t.option("combine").is_visible(d, {"count": 3})
    assert t.requires == ("tesseract",)
