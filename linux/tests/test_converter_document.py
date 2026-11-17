"""萬用轉換：文件路線（Markdown、純文字、HTML、Word 類、PDF 轉文件）與外部引擎。

LibreOffice 與 pandoc 的測試比較慢，標成 slow。
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any

import pytest
from conftest import make_pdf

from quickseemd.core import errors
from quickseemd.core.converter import convert, document
from quickseemd.core.model import ToolContext


def run(path: Path, fid: str, out: Path, **opts: Any) -> list[Path]:
    ctx = ToolContext(out)
    try:
        return convert(path, fid, ctx, opts)
    finally:
        ctx.cleanup()


def pdf_text(path: Path) -> str:
    from quickseemd.core import backends, process

    args = [backends.require("pdftotext"), "-enc", "UTF-8", str(path), "-"]
    return process.run_checked(args, "pdftotext").stdout


def docx_text(path: Path) -> str:
    with zipfile.ZipFile(path) as z:
        return z.read("word/document.xml").decode("utf-8")


# ------------------------------------------------------------------ 快速（不需 LibreOffice）


def test_markdown_to_html_uses_screen_css(samples: Path, outdir: Path) -> None:
    [html] = run(samples / "README.md", "html", outdir)
    assert html.name == "README.html"
    page = html.read_text("utf-8")
    assert '<article class="markdown-body">' in page
    assert "prefers-color-scheme: dark" in page


def test_markdown_to_pdf(tmp_path: Path, outdir: Path) -> None:
    md = tmp_path / "筆記.md"
    md.write_text(
        "# 標題\n\n中文段落 **粗體**。\n\n![遠端](https://example.invalid/x.png)\n", "utf-8"
    )
    [pdf] = run(md, "pdf", outdir)
    assert pdf.name == "筆記.pdf"
    text = pdf_text(pdf)
    assert "標題" in text and "中文段落" in text


def test_plain_text_to_html_and_pdf(samples: Path, outdir: Path) -> None:
    [html] = run(samples / "app.py", "html", outdir)
    assert '<pre class="code">' in html.read_text("utf-8")
    [pdf] = run(samples / "big5.txt", "pdf", outdir)
    assert "繁體中文" in pdf_text(pdf)


def test_html_to_pdf_blocks_network_and_outside_files(tmp_path: Path, outdir: Path) -> None:
    secret = tmp_path / "secret.txt"
    secret.write_text("SECRET-CONTENT")
    site = tmp_path / "site"
    site.mkdir()
    page = site / "page.html"
    page.write_text(
        '<html><head><meta charset="utf-8">'
        '<link rel="stylesheet" href="http://127.0.0.1:9/x.css"></head><body>'
        "<p>頁面內容</p>"
        f'<object data="file://{secret}" type="text/plain"></object>'
        f'<img src="file://{secret}"></body></html>',
        "utf-8",
    )
    [pdf] = run(page, "pdf", outdir)
    text = pdf_text(pdf)
    assert "頁面內容" in text and "SECRET-CONTENT" not in text


def test_plain_html_escapes_and_paginates() -> None:
    page = document.plain_html("a<b>&\fsecond", "t<", code=False)
    assert "a&lt;b&gt;&amp;" in page
    assert 'page-break-before: always">second</pre>' in page
    assert "<title>t&lt;</title>" in page


def test_block_remote_references() -> None:
    markup = (
        '<p><img src="https://x/y.png" alt="a"> <img src="local.png"></p><a href="http://x">l</a>'
    )
    blocked = document.block_remote(markup)
    assert 'data-blocked-src="https://x/y.png"' in blocked
    assert '<img src="local.png">' in blocked
    assert '<a href="http://x">' in blocked


def test_pdf_to_html_text_only(pdf_file: Path, outdir: Path) -> None:
    [html] = run(pdf_file, "html", outdir)
    page = html.read_text("utf-8")
    assert "第 1 頁" in page and 'class="text page"' in page


def test_htmlpdf_usage_error() -> None:
    res = document.run_htmlpdf_cli([])
    assert res.returncode == 2 and "用法" in res.stderr


# ------------------------------------------------------------------ 需要 LibreOffice／pandoc


@pytest.mark.slow
def test_markdown_to_docx_and_odt(samples: Path, outdir: Path) -> None:
    [docx] = run(samples / "README.md", "docx", outdir)
    assert docx.name == "README.docx"
    assert "<w:t" in docx_text(docx)
    [odt] = run(samples / "README.md", "odt", outdir)
    assert zipfile.ZipFile(odt).read("mimetype") == b"application/vnd.oasis.opendocument.text"


@pytest.mark.slow
def test_plain_text_to_docx_and_rtf(samples: Path, outdir: Path) -> None:
    [docx] = run(samples / "big5.txt", "docx", outdir)
    assert "繁體中文" in docx_text(docx)
    [rtf] = run(samples / "app.py", "rtf", outdir)
    assert rtf.read_bytes().startswith(b"{\\rtf1")


@pytest.mark.slow
def test_docx_roundtrip_through_libreoffice(tmp_path: Path, outdir: Path) -> None:
    import docx

    d = docx.Document()
    d.add_heading("報告", 1)
    d.add_paragraph("內容第一段\N{OBJECT REPLACEMENT CHARACTER}")
    src = tmp_path / "報告.docx"
    d.save(src)
    [pdf] = run(src, "pdf", outdir)
    assert "內容第一段" in pdf_text(pdf)
    [txt] = run(src, "txt", outdir)
    data = txt.read_bytes()
    assert not data.startswith(b"\xef\xbb\xbf")
    assert "內容第一段" in data.decode(
        "utf-8"
    ) and "\N{OBJECT REPLACEMENT CHARACTER}" not in data.decode("utf-8")
    [html] = run(src, "html", outdir)
    assert "內容第一段" in html.read_text("utf-8")
    [odt] = run(src, "odt", outdir)
    assert odt.name == "報告.odt"
    assert not list(tmp_path.glob(".~lock*"))


@pytest.mark.slow
def test_pdf_to_docx_keeps_text(pdf_file: Path, outdir: Path) -> None:
    [docx] = run(pdf_file, "docx", outdir)
    body = docx_text(docx)
    assert "第 1 頁" in body or "第" in body
    assert 'w:type="page"' in body or "w:pageBreakBefore" in body


@pytest.mark.slow
def test_html_to_docx(tmp_path: Path, outdir: Path) -> None:
    page = tmp_path / "p.html"
    page.write_text("<html><body><h1>標題</h1><p>段落</p></body></html>", "utf-8")
    [docx] = run(page, "docx", outdir)
    assert "段落" in docx_text(docx)


@pytest.mark.slow
def test_spreadsheet_and_csv_through_libreoffice(
    samples: Path, tmp_path: Path, outdir: Path
) -> None:
    [ods] = run(samples / "data.csv", "ods", outdir)
    assert (
        zipfile.ZipFile(ods)
        .read("mimetype")
        .startswith(b"application/vnd.oasis.opendocument.spreadsheet")
    )
    [pdf] = run(samples / "data.csv", "pdf", outdir)
    assert "王小明" in pdf_text(pdf)
    from quickseemd.core.converter import tables, xlsx

    book = tmp_path / "b.xlsx"
    xlsx.write_xlsx([tables.Table("S", (("名稱", "數量"), ("蘋果", "3")))], book)
    [ods2] = run(book, "ods", outdir)
    assert ods2.name == "b.ods"


@pytest.mark.slow
def test_pandoc_outputs(samples: Path, outdir: Path) -> None:
    [epub] = run(samples / "README.md", "epub", outdir)
    assert zipfile.ZipFile(epub).read("mimetype") == b"application/epub+zip"
    [tex] = run(samples / "README.md", "tex", outdir)
    assert "\\begin{document}" in tex.read_text("utf-8")
    [md] = run(samples / "analysis.ipynb", "md", outdir)
    assert md.name == "analysis.md"


@pytest.mark.slow
def test_libreoffice_failure_is_reported(tmp_path: Path, outdir: Path) -> None:
    from quickseemd.core.converter import office

    bad = tmp_path / "broken.docx"
    bad.write_bytes(b"PK\x03\x04 not really a document")
    ctx = ToolContext(outdir)
    try:
        with pytest.raises(errors.QSError) as exc:
            office.convert_file(bad, "pdf", tmp_path, ctx)
    finally:
        ctx.cleanup()
    assert "LibreOffice" in str(exc.value)


def test_soffice_lock_is_exclusive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    import fcntl
    import os
    import threading

    from quickseemd.core import soffice

    monkeypatch.setattr(soffice, "lock_path", lambda: tmp_path / "soffice.lock")
    monkeypatch.setattr(soffice, "LOCK_POLL_SECONDS", 0.01)
    fd = os.open(tmp_path / "soffice.lock", os.O_RDWR | os.O_CREAT)
    fcntl.flock(fd, fcntl.LOCK_EX)
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(errors.Cancelled):
        with soffice.lock(cancel):
            pass
    os.close(fd)
    with soffice.lock(None):
        pass


def test_soffice_profile_blocks_external_links(tmp_path: Path) -> None:
    from xml.parsers import expat

    from quickseemd.core import soffice

    profile = soffice.make_profile(tmp_path)
    xcu = (profile / "user" / "registrymodifications.xcu").read_text("utf-8")
    expat.ParserCreate().Parse(xcu.encode(), True)
    assert (
        '<item oor:path="/org.openoffice.Office.Common/Security/Scripting">'
        '<prop oor:name="BlockUntrustedRefererLinks" oor:op="fuse"><value>true</value>'
    ) in xcu
    assert '"DisableMacrosExecution"' in xcu
    args = soffice.arguments(tmp_path / "a.docx", "pdf", tmp_path / "o", profile, None)
    assert args[1] == f"-env:UserInstallation={profile.resolve().as_uri()}"
    assert args[-1] == str((tmp_path / "a.docx").resolve())


def test_scanned_pdf_to_docx_without_tesseract(
    tmp_path: Path, outdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from quickseemd.core import backends

    src = make_pdf(tmp_path / "scan.pdf", pages=1, text=False)
    real = backends.available
    monkeypatch.setattr(
        backends, "available", lambda key: False if key == "tesseract" else real(key)
    )
    with pytest.raises(errors.QSError) as exc:
        run(src, "html", outdir)
    assert "OCR 也辨識不到內容" in str(exc.value)


def test_pandoc_ast_sanitizer(tmp_path: Path) -> None:
    from quickseemd.core.converter import pandocrun

    base = tmp_path / "doc"
    base.mkdir()
    (base / "ok.png").write_bytes(b"x")

    def image(url: str) -> dict:
        return {"t": "Image", "c": [["", [], []], [{"t": "Str", "c": "alt"}], [url, ""]]}

    doc = {
        "pandoc-api-version": [1, 23, 1],
        "meta": {
            "cover-image": {"t": "MetaString", "c": "/etc/passwd"},
            "title": {"t": "MetaString", "c": "T"},
        },
        "blocks": [
            {
                "t": "Para",
                "c": [
                    image("ok.png"),
                    image("https://example.invalid/x.png"),
                    image("/etc/hosts"),
                    image("../outside.png"),
                    image("data:image/png;base64,AAAA"),
                    {"t": "RawInline", "c": ["html", '<img src="http://x/y">']},
                    {"t": "RawInline", "c": ["html", "<b>"]},
                ],
            },
            {"t": "RawBlock", "c": ["html", '<iframe src="file:///etc/passwd"></iframe>']},
        ],
    }
    clean = pandocrun.clean_document(doc, [str(base)], str(base))
    kinds = [n["t"] for n in clean["blocks"][0]["c"]]
    assert kinds == ["Image", "Span", "Span", "Span", "Image", "Str", "RawInline"]
    assert clean["blocks"][1] == {"t": "Plain", "c": []}
    assert "cover-image" not in clean["meta"] and "title" in clean["meta"]
    assert doc["blocks"][0]["c"][1]["t"] == "Image"  # 原物件不變


@pytest.mark.slow
def test_docx_to_markdown_with_pandoc(tmp_path: Path, outdir: Path) -> None:
    import docx

    d = docx.Document()
    d.add_heading("標題", 1)
    d.add_paragraph("段落文字")
    src = tmp_path / "w.docx"
    d.save(src)
    [md] = run(src, "md", outdir)
    text = md.read_text("utf-8")
    assert "# 標題" in text and "段落文字" in text
