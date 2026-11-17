"""文件互轉（對應原版 CVDocumentConverter）。

原版用系統的富文字引擎（與 textutil 相同）；Linux 版改成：
- Markdown → HTML：自己的渲染器（螢幕樣式、支援深色模式）。
- Markdown／純文字／HTML → PDF：WeasyPrint（A4、邊界 64 pt；Markdown 用匯入樣式）。
- Word 類格式（DOCX、DOC、ODT、RTF）與純文字輸出：LibreOffice。
- PDF → DOCX／RTF／HTML／ODT：pdftotext 取文字（掃描檔改用 OCR）→ 簡單 HTML →（LibreOffice）。
"""

from __future__ import annotations

import html
import os
import sys
import tempfile
from pathlib import Path

from .. import errors, naming, process
from . import htmlsafe, mdrender, office, pdf, textcodec
from .formats import Format
from .profile import Profile

HTMLPDF_TIMEOUT = 300.0
_HTMLPDF = Path(__file__).with_name("htmlpdf.py")
PLAIN_CSS = """body { margin: 0; color: #000; }
pre { margin: 0; white-space: pre-wrap; overflow-wrap: anywhere; }
pre.text { font-family: "Helvetica Neue", "Noto Sans CJK TC", sans-serif; font-size: 12pt; line-height: 1.2; }
pre.code { font-family: Menlo, "Noto Sans Mono CJK TC", monospace; font-size: 10.5pt; line-height: 1.1; }
.page { break-before: page; page-break-before: always; }"""  # noqa: E501
TEXT_FONT = "Noto Sans CJK TC"
CODE_FONT = "Noto Sans Mono CJK TC"


def plain_html(text: str, title: str, code: bool) -> str:
    """純文字 → HTML（每個換頁字元 \\f 之後換新頁）。"""
    cls = "code" if code else "text"
    pages = text.split("\f")
    blocks = []
    for i, page in enumerate(pages):
        # 換頁也寫成行內樣式：LibreOffice 匯入 HTML 時只認行內的 page-break-before
        extra = ' page" style="page-break-before: always' if i else ""
        blocks.append(f'<pre class="{cls}{extra}">{html.escape(page, quote=False)}</pre>')
    return (
        '<!DOCTYPE html>\n<html lang="zh-Hant">\n<head>\n<meta charset="utf-8">\n'
        f"<title>{html.escape(title)}</title>\n<style>\n{PLAIN_CSS}\n</style>\n</head>\n<body>\n"
        + "\n".join(blocks)
        + "\n</body>\n</html>\n"
    )


def html_to_pdf(html_file: Path, out: Path, base_dir: Path, ctx) -> None:  # noqa: ANN001
    """在子程序裡用 WeasyPrint 排版成 PDF。"""
    args = [sys.executable, "-I", str(_HTMLPDF), str(html_file), str(out), str(base_dir)]
    ctx.run_process(args, "WeasyPrint", timeout=HTMLPDF_TIMEOUT)
    if not out.exists():
        raise errors.write_failed("無法建立 PDF。")


def _scratch(work: Path) -> Path:
    return Path(tempfile.mkdtemp(prefix=".doc-", dir=work))


def _write_html(work: Path, base: str, markup: str) -> Path:
    path = _scratch(work) / naming.join_name(naming.sanitize(base), "html")
    path.write_text(markup, "utf-8")
    return path


def _via_office(
    source: Path, fid: str, out: Path, work: Path, ctx, infilter: str | None
) -> None:  # noqa: ANN001, E501
    produced = office.run_soffice(source, office.filter_for(fid, True), work, ctx, infilter)
    if fid == "txt":
        office.strip_text_output(produced)
    os.rename(produced, out)


def _from_html(
    markup: str, fid: str, out: Path, work: Path, base_dir: Path, ctx
) -> None:  # noqa: ANN001, E501
    """自己產生的 HTML → html／pdf／Word 類格式。"""
    if fid == "html":
        out.write_text(markup, "utf-8")
        return
    if fid == "pdf":
        html_to_pdf(_write_html(work, out.stem, markup), out, base_dir, ctx)
        return
    source = _write_html(work, out.stem, htmlsafe.sanitize(markup, base_dir))
    _via_office(source, fid, out, work, ctx, office.HTML_IMPORT)


def _user_html_to_office(path: Path, fid: str, out: Path, work: Path, ctx) -> None:  # noqa: ANN001
    """使用者的 HTML 檔：先清掉外部參照（本機圖片改成內嵌），再交給 LibreOffice。"""
    clean = htmlsafe.sanitize_bytes(path.read_bytes(), path.parent)
    source = _scratch(work) / naming.join_name(naming.sanitize(out.stem), "html")
    source.write_bytes(clean)
    _via_office(source, fid, out, work, ctx, office.HTML_IMPORT)


def _decoded(path: Path, options: dict) -> str:
    forced = textcodec.forced_codec(options.get("source_encoding"))
    text = textcodec.decode_file(path, forced).text
    return textcodec.normalize_line_endings(text, textcodec.LineEnding.LF)


def _markdown(
    path: Path, fid: str, out: Path, work: Path, options: dict, ctx
) -> None:  # noqa: ANN001, E501
    text = _decoded(path, options)
    title = mdrender.first_heading(text) or naming.base_name(path)
    markup = mdrender.render_document(text, title, for_import=fid != "html")
    _from_html(markup, fid, out, work, path.parent, ctx)


def _plain(
    path: Path, profile: Profile, fid: str, out: Path, work: Path, options: dict, ctx
) -> None:  # noqa: ANN001, E501
    text = _decoded(path, options)
    code = profile.is_source_code or profile.data_kind is not None
    if fid in ("html", "pdf"):
        markup = plain_html(text, naming.base_name(path), code)
        _from_html(markup, fid, out, work, path.parent, ctx)
        return
    source = _scratch(work) / naming.join_name(naming.sanitize(out.stem), "txt")
    source.write_text(text, "utf-8")
    infilter = f"Text (encoded):UTF8,LF,{CODE_FONT if code else TEXT_FONT},zh-TW,"
    _via_office(source, fid, out, work, ctx, infilter)


def _from_pdf(path: Path, fid: str, out: Path, work: Path, ctx) -> None:  # noqa: ANN001
    pages = pdf.page_texts(path, work, ctx)
    text = "\n\f".join(p.strip("\r\n") for p in pages)
    markup = plain_html(text, naming.base_name(path), False)
    _from_html(markup, fid, out, work, path.parent, ctx)


def convert(
    path: Path, profile: Profile, fmt: Format, options: dict, work: Path, ctx
) -> list[Path]:  # noqa: ANN001, E501
    out = work / naming.join_name(naming.base_name(path), fmt.ext)
    kind = profile.document_kind
    try:
        if profile.is_markdown:
            _markdown(path, fmt.id, out, work, options, ctx)
        elif profile.is_pdf:
            _from_pdf(path, fmt.id, out, work, ctx)
        elif kind == "plain":
            _plain(path, profile, fmt.id, out, work, options, ctx)
        elif kind == "html" and fmt.id == "pdf":
            html_to_pdf(path, out, path.parent, ctx)
        elif kind == "html":
            _user_html_to_office(path, fmt.id, out, work, ctx)
        elif kind in ("rtf", "doc", "docx", "odt"):
            _via_office(path, fmt.id, out, work, ctx, None)
        else:
            raise errors.unsupported_target(path.name, fmt.display_name)
    except OSError as exc:
        raise errors.write_failed(exc.strerror or str(exc)) from exc
    ctx.progress(1)
    return [out]


def run_htmlpdf_cli(args: list[str]) -> process.Result:
    """測試與除錯用：直接呼叫 HTML → PDF 子程序。"""
    return process.run([sys.executable, "-I", str(_HTMLPDF), *args], timeout=HTMLPDF_TIMEOUT)
