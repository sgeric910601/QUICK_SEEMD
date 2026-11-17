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


def test_html_sanitizer_blocks_external_references(tmp_path: Path) -> None:
    from PIL import Image

    from quickseemd.core.converter import htmlsafe

    base = tmp_path / "site"
    base.mkdir()
    Image.new("RGB", (4, 4), "red").save(base / "ok.png")
    (tmp_path / "secret.png").write_bytes(b"\x89PNG secret")
    markup = (
        '<html><head><base href="http://evil/"><meta http-equiv="refresh" content="0;url=http://x">'
        '<link rel="stylesheet" href="http://evil/a.css">'
        "<style>@import url(http://evil/b.css); p { background: url('http://evil/c.png') }</style>"
        "</head><body>"
        '<img src="ok.png"><img src="../secret.png"><img src="&#104;ttp://evil/d.png">'
        '<img src=" http://evil/e.png" srcset="ok.png 1x, http://evil/f.png 2x">'
        '<video poster="//evil/g.png"></video><table background="/etc/h.png"></table>'
        '<p style="background:url(http://evil/i.png)">src="http://evil/text" 保留</p>'
        '<object data="file:///etc/passwd"></object><embed src="http://evil/j"><iframe src="http://evil/k"></iframe>'
        '<a href="http://example.com/">連結保留</a></body></html>'
    )
    clean = htmlsafe.block_remote(markup, base)
    assert ' href="http://evil/a.css"' not in clean and "data-blocked-href=" in clean
    for needle in ("evil/b.css", "evil/c.png", "evil/i.png"):  # @import 與 url() 整段拿掉
        assert needle not in clean
    assert "<base" not in clean and "refresh" not in clean
    assert 'src="data:image/png;base64,' in clean
    assert (
        "secret" not in clean.split("data-blocked-src")[0]
        or 'data-blocked-src="../secret.png"' in clean
    )
    assert 'data-blocked-src="../secret.png"' in clean
    assert 'data-blocked-src="&#104;ttp://evil/d.png"' in clean
    assert 'data-blocked-src=" http://evil/e.png"' in clean
    assert "data-blocked-srcset=" in clean
    assert "data-blocked-poster=" in clean and "data-blocked-background=" in clean
    assert "data-blocked-data=" in clean
    assert (
        'data-blocked-src="http://evil/j"' in clean and 'data-blocked-src="http://evil/k"' in clean
    )
    assert 'src="http://evil/text" 保留' in clean  # 內文不動
    assert '<a href="http://example.com/">' in clean
    raw = htmlsafe.block_remote_bytes('<img src="http://x/y.png">中'.encode("cp950"), base)
    assert raw.endswith("中".encode("cp950")) and b"data-blocked-src" in raw


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
    (tmp_path / "secret.txt").write_text("SECRET")
    (base / "link.png").symlink_to(tmp_path / "secret.txt")
    resources = tmp_path / "res"
    resources.mkdir()

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
                    image("../secret.txt"),
                    image("link.png"),
                    image("data:image/png;base64,AAAA"),
                    {"t": "RawInline", "c": ["html", '<img src="../secret.txt">']},
                    {"t": "RawInline", "c": ["latex", "\\textbf{x}"]},
                ],
            },
            {"t": "RawBlock", "c": ["html", "<p>plain</p>"]},
        ],
    }
    clean = pandocrun.Cleaner([base], base, resources).document(doc)
    para = clean["blocks"][0]["c"]
    assert [n["t"] for n in para] == [
        "Image",
        "Span",
        "Span",
        "Span",
        "Span",
        "Image",
        "Str",
        "RawInline",
    ]
    assert para[0]["c"][2][0] == "img-1.png" and (resources / "img-1.png").read_bytes() == b"x"
    assert clean["blocks"][1] == {"t": "Plain", "c": []}
    assert "cover-image" not in clean["meta"] and "title" in clean["meta"]
    assert doc["blocks"][0]["c"][1]["t"] == "Image"  # 原物件不變
    keep = pandocrun.Cleaner([base], base, None).document(doc)
    assert keep["blocks"][0]["c"][0]["c"][2][0] == "ok.png"  # 不內嵌的格式保留原路徑


@pytest.mark.slow
def test_epub_does_not_embed_files_outside_the_folder(tmp_path: Path, outdir: Path) -> None:
    from PIL import Image

    (tmp_path / "secret.txt").write_text("TOP-SECRET-CONTENT")
    book = tmp_path / "book"
    book.mkdir()
    Image.new("RGB", (8, 8), "red").save(book / "ok.png")
    (book / "sneaky.png").symlink_to(tmp_path / "secret.txt")
    md = book / "notes.md"
    md.write_text(
        "# 標題\n\n![好](ok.png)\n\n![壞](../secret.txt)\n\n![連結](sneaky.png)\n\n"
        '<img src="../secret.txt">\n\n<object data="../secret.txt"></object>\n',
        "utf-8",
    )
    [epub] = run(md, "epub", outdir)
    with zipfile.ZipFile(epub) as z:
        blobs = [z.read(n) for n in z.namelist()]
        names = z.namelist()
    assert not any(b"TOP-SECRET-CONTENT" in b for b in blobs)
    assert sum(1 for n in names if n.lower().endswith(".png")) == 1


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


def _recording_server():  # noqa: ANN202
    import http.server
    import threading

    hits: list[str] = []

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            hits.append(self.path)
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"1,2\n")

        def log_message(self, *_args: object) -> None:
            pass

    server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server, hits


def _spreadsheet_with_links(path: Path, port: int) -> Path:
    path.write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<office:document xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
        'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
        'xmlns:xlink="http://www.w3.org/1999/xlink" office:version="1.2" '
        'office:mimetype="application/vnd.oasis.opendocument.spreadsheet">'
        '<office:body><office:spreadsheet><table:table table:name="Sheet1"><table:table-row>'
        f'<table:table-cell table:formula="of:=WEBSERVICE(&quot;http://127.0.0.1:{port}/ws&quot;)"'
        ' office:value-type="string"><text:p>cached</text:p></table:table-cell>'
        f"<table:table-cell table:formula=\"of:=['http://127.0.0.1:{port}/ext.ods']#$Sheet1.A1\""
        ' office:value-type="float" office:value="1"><text:p>1</text:p></table:table-cell>'
        '</table:table-row></table:table><table:table table:name="Linked">'
        f'<table:table-source xlink:type="simple" xlink:href="http://127.0.0.1:{port}/linked.csv"'
        ' table:filter-name="Text - txt - csv (StarCalc)" table:mode="copy-all"/>'
        "<table:table-row><table:table-cell><text:p>old</text:p></table:table-cell>"
        "</table:table-row></table:table></office:spreadsheet></office:body></office:document>",
        "utf-8",
    )
    return path


@pytest.mark.slow
@pytest.mark.parametrize("sandbox", [True, False])
def test_libreoffice_makes_no_requests(
    tmp_path: Path, outdir: Path, monkeypatch: pytest.MonkeyPatch, sandbox: bool
) -> None:
    from quickseemd.core import soffice

    if not sandbox:
        monkeypatch.setattr(soffice, "sandbox_prefix", lambda _w: [])  # 只靠設定檔也要擋住
    server, hits = _recording_server()
    try:
        port = server.server_address[1]
        sheet = _spreadsheet_with_links(tmp_path / "links.fods", port)
        soffice.convert(sheet, "pdf", tmp_path)
        page = tmp_path / "page.html"
        page.write_text(
            f'<html><body><p>x</p><img src="http://127.0.0.1:{port}/img.png"></body></html>'
        )
        [docx] = run(page, "docx", outdir)
        assert docx.exists()
    finally:
        server.shutdown()
    assert hits == []


@pytest.mark.slow
def test_html_to_docx_embeds_local_images(tmp_path: Path, outdir: Path) -> None:
    from PIL import Image

    Image.new("RGB", (10, 10), "blue").save(tmp_path / "pic.png")
    page = tmp_path / "p.html"
    page.write_text('<html><body><p>圖</p><img src="pic.png"></body></html>', "utf-8")
    [docx] = run(page, "docx", outdir)
    assert any("media/" in n for n in zipfile.ZipFile(docx).namelist())
