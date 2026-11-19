"""萬用轉換用的 LibreOffice 篩選器（對應原版 CVExternalTools 的 soffice 部分）。

實際執行（全新且封鎖外部連結的設定檔、檔案鎖、逾時）在共用模組 core/soffice.py。
"""

from __future__ import annotations

from pathlib import Path

from .. import soffice

CSV_FILTER = "csv:Text - txt - csv (StarCalc):44,34,76,1"
HTML_IMPORT = "HTML (StarWriter)"

# Writer 文件（含匯入成 Writer 的 HTML／純文字）用的匯出篩選器
WRITER_FILTERS = {
    "pdf": "pdf:writer_pdf_Export",
    "docx": "docx:MS Word 2007 XML",
    "doc": "doc:MS Word 97",
    "odt": "odt:writer8",
    "rtf": "rtf:Rich Text Format",
    "txt": "txt:Text (encoded):UTF8",
    "html": "html:XHTML Writer File:UTF8",
}
WRITER_EXTS = (".doc", ".docx", ".docm", ".odt", ".rtf", ".wpd")


def filter_for(fid: str, writer: bool) -> str:
    if fid == "csv":
        return CSV_FILTER
    if writer and fid in WRITER_FILTERS:
        return WRITER_FILTERS[fid]
    return fid


def run_soffice(
    source: Path, spec: str, scratch_parent: Path, ctx, infilter: str | None = None  # noqa: ANN001
) -> Path:
    """轉檔並回傳產生的檔案（在 scratch_parent 裡的全新暫存資料夾）。"""
    return soffice.convert(source, spec, scratch_parent, cancel=ctx.cancel_event, infilter=infilter)


def convert_file(source: Path, fid: str, scratch_parent: Path, ctx) -> Path:  # noqa: ANN001
    """一般 Office 檔（Word／Excel／PowerPoint／iWork）→ 目標格式。"""
    writer = source.suffix.lower() in WRITER_EXTS
    return run_soffice(source, filter_for(fid, writer), scratch_parent, ctx)


def strip_text_output(path: Path) -> None:
    """LibreOffice 匯出的純文字：拿掉 BOM 與物件替代字元 U+FFFC。"""
    text = path.read_text("utf-8-sig", errors="replace")
    path.write_text(text.replace("\N{OBJECT REPLACEMENT CHARACTER}", ""), "utf-8")
