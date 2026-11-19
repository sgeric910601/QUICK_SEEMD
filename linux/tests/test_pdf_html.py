"""HTML 轉 PDF（WeasyPrint）。"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pikepdf
import pytest
from PIL import Image

from quickseemd.core import errors
from quickseemd.core.tools.pdf import html as html_tool
from test_pdf_helpers import digest, page_count, pdftotext, run_tool, tool

PAGE = """<!doctype html><html><head><meta charset="utf-8"><title>測試頁</title>
<link rel="stylesheet" href="style.css"></head>
<body><h1>網頁標題</h1><p class="lead">第一段中文內容，Hello World。</p>
<img src="pic.png" alt="圖">{extra}</body></html>"""


def _site(tmp_path: Path, extra: str = "", css: str = "h1 { color: navy; }") -> Path:
    site = tmp_path / "site"
    site.mkdir()
    (site / "style.css").write_text(css, "utf-8")
    Image.new("RGB", (60, 40), (200, 30, 30)).save(site / "pic.png")
    page = site / "首頁.html"
    page.write_text(PAGE.format(extra=extra), "utf-8")
    return page


def _xobject_count(path: Path) -> int:
    with pikepdf.open(path) as pdf:
        total = 0
        for p in pdf.pages:
            res = p.obj.get("/Resources")
            xo = res.get("/XObject") if res is not None else None
            total += len(list(xo.keys())) if xo is not None else 0
        return total


def _render_center(path: Path) -> tuple[int, int, int]:
    base = path.with_name("center")
    subprocess.run(
        ["/usr/bin/pdftocairo", "-png", "-r", "20", "-singlefile", str(path), str(base)], check=True
    )
    with Image.open(str(base) + ".png") as im:
        im = im.convert("RGB")
        return im.getpixel((im.width // 2, im.height // 2))


def test_html_to_pdf_basic(tmp_path):
    page = _site(tmp_path)
    before = digest(page)
    run = run_tool("pdf.from-html", [page])
    out = run.outputs[0]
    assert out.name == "首頁.pdf"
    assert "第一段中文內容" in pdftotext(out)
    assert run.summary == "已轉成 1 頁 PDF"
    with pikepdf.open(out) as pdf:
        assert [round(float(v), 2) for v in pdf.pages[0].mediabox] == [0, 0, 595.28, 841.89]
        assert str(pdf.docinfo.Title) == "首頁"
    assert _xobject_count(out) >= 1  # 同資料夾的圖片有載入
    assert digest(page) == before


@pytest.mark.slow
def test_html_paper_orientation_and_pages(tmp_path):
    long_text = "".join(f"<p>第 {i} 段文字，內容很長很長。</p>" for i in range(200))
    page = _site(tmp_path, extra=long_text)
    run = run_tool("pdf.from-html", [page], {"paper": "letter", "landscape": True, "margin": 0})
    out = run.outputs[0]
    n = page_count(out)
    assert n > 3 and run.summary == f"已轉成 {n} 頁 PDF"
    with pikepdf.open(out) as pdf:
        assert [float(v) for v in pdf.pages[0].mediabox] == [0, 0, 792, 612]


@pytest.mark.slow
def test_html_margins(tmp_path):
    page = _site(tmp_path)
    narrow = run_tool("pdf.from-html", [page], {"margin": 0}).outputs[0]
    wide = run_tool("pdf.from-html", [page], {"margin": 54}).outputs[0]

    def x_min(path: Path) -> float:
        out = subprocess.run(
            ["/usr/bin/pdftotext", "-bbox", str(path), "-"], capture_output=True, check=True
        ).stdout.decode()
        xs = [
            float(line.split('xMin="')[1].split('"')[0])
            for line in out.splitlines()
            if "<word" in line
        ]
        return min(xs)

    assert x_min(narrow) < 20
    assert 54 <= x_min(wide) < 80


@pytest.mark.slow
def test_html_backgrounds_toggle(tmp_path):
    page = _site(tmp_path, css="html, body { background: rgb(0, 160, 0); min-height: 100%; }")
    on = run_tool("pdf.from-html", [page]).outputs[0]
    off = run_tool("pdf.from-html", [page], {"backgrounds": False}).outputs[0]
    assert _render_center(on)[1] > 120 and _render_center(on)[0] < 60
    assert _render_center(off) == (255, 255, 255)


def test_html_cannot_read_outside_folder(tmp_path):
    Image.new("RGB", (50, 50), (0, 0, 255)).save(tmp_path / "secret.png")
    extra = '<img src="../secret.png"><img src="file:///etc/hostname"><link rel="stylesheet" href="file:///etc/passwd">'
    page = _site(tmp_path, extra=extra)
    (page.parent / "pic.png").unlink()  # 只剩外面的圖
    out = run_tool("pdf.from-html", [page]).outputs[0]
    assert _xobject_count(out) == 0


def test_url_fetcher_rules(tmp_path):
    fetch = html_tool.make_fetcher(tmp_path)
    (tmp_path / "a.css").write_text("p{}", "utf-8")
    result = fetch((tmp_path / "a.css").as_uri())
    assert result.get("string") is not None or result.get("file_obj") is not None
    if result.get("file_obj") is not None:
        result["file_obj"].close()
    with pytest.raises(ValueError):
        fetch("file:///etc/passwd")
    with pytest.raises(ValueError):
        fetch("ftp://example.com/x.css")
    with pytest.raises(ValueError):
        fetch("file://" + str(tmp_path.parent / "x.css"))


def test_html_unreadable(tmp_path):
    page = tmp_path / "鎖.html"
    page.write_text("<p>x</p>", "utf-8")
    os.chmod(page, 0)
    try:
        with pytest.raises(errors.QSError, match="無法讀取檔案內容：網頁載入失敗："):
            run_tool("pdf.from-html", [page])
    finally:
        os.chmod(page, 0o644)


def test_html_definition():
    t = tool("pdf.from-html")
    assert t.input_types == ("text/html",)
    d = t.defaults()
    assert d == {"paper": "a4", "landscape": False, "margin": 36, "backgrounds": True}
    assert [c.title for c in t.option("margin").choices] == ["無", "窄", "標準", "寬"]
    assert html_tool.page_size("letter", True) == (792, 612)
    assert html_tool.page_size("a4", False) == (595.28, 841.89)
