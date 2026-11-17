"""PDF 轉文字（對應原版 PTOCR.swift 後半 PTToTextSession）。

文字層用 pdftotext 取出（閱讀順序）；沒有文字的頁面可自動以 Tesseract 辨識。
"""

from __future__ import annotations

from pathlib import Path

from ... import errors
from ...model import PDF, TINTS, ToolContext, ToolDefinition, per_file
from ...options import Choice, Kind, Option, Values, note
from . import render
from .common import (
    INPUT_LABEL,
    PDF_TYPES,
    as_bool,
    inspect_pdfs,
    make_validator,
    open_pdf,
    page_list,
    page_range_option,
    range_caption,
    range_reason,
)
from .ocr import LANGUAGES, join_lines, recognize, render_for_ocr

SEPARATOR = "──── 第 {n} 頁 ────\n{text}"


def _ocr_text(ctx: ToolContext, file: Path, page, n: int, language: str, work: Path) -> str:
    image, dpi = render_for_ocr(ctx, file, page, n, True, work)
    lines, _pdf = recognize(ctx, image, language, dpi, work / f"text-{n}", False)
    return join_lines(lines).strip()


def _page_texts(file: Path, values: Values, ctx: ToolContext, work: Path) -> tuple[list, int]:
    """[(頁碼, 文字)] 與以 OCR 辨識的頁數。"""
    auto_ocr = as_bool(values.get("auto_ocr"), True)
    language = str(values.get("language") or "traditional")
    with open_pdf(file) as doc:
        pages = page_list(str(values.get("pages") or ""), len(doc.pages), file)
        first = min(pages)
        texts = render.page_texts(ctx, file, first, max(pages))
        out: list[tuple[int, str]] = []
        ocr_pages = 0
        for i, n in enumerate(pages):
            ctx.check_cancelled()
            ctx.progress(i / len(pages), f"讀取第 {n} 頁…")
            text = texts[n - first].strip()
            if not text and auto_ocr:
                ctx.progress(i / len(pages), f"OCR 辨識第 {n} 頁…")
                text = _ocr_text(ctx, file, doc.pages[n - 1], n, language, work)
                ocr_pages += 1 if text else 0
            out.append((n, text))
    return out, ocr_pages


def _to_text_file(file: Path, values: Values, ctx: ToolContext) -> list[Path]:
    separators = as_bool(values.get("separators"), True)
    work = ctx.make_work_dir(file)
    pages, ocr_pages = _page_texts(file, values, ctx, work)
    if not any(text for _n, text in pages):
        if as_bool(values.get("auto_ocr"), True):
            raise errors.empty_content(f"「{file.name}」中沒有文字，OCR 也辨識不到內容。")
        raise errors.QSError(f"「{file.name}」沒有文字層，可開啟「自動 OCR」再試。")
    parts = [SEPARATOR.format(n=n, text=t) if separators else t for n, t in pages]
    body = ("\n\n" if separators else "\n\f\n").join(parts) + "\n"
    tmp = work / "text.txt"
    tmp.write_text(body, "utf-8")
    if ocr_pages > 0:
        ctx.progress(1, f"有 {ocr_pages} 頁以 OCR 辨識")
    return [ctx.deliver(tmp, ctx.output_name(file, None, "txt"), file)]


def run_to_text(files: list[Path], values: Values, ctx: ToolContext) -> list[Path]:
    return per_file(files, ctx, lambda f, sub: _to_text_file(f, values, sub), skip_errors=False)


TO_TEXT = ToolDefinition(
    id="pdf.to-text",
    category=PDF,
    title="PDF 轉文字",
    subtitle="取出 PDF 中的文字存成 .txt；掃描檔會自動以 OCR 辨識。",
    icon="doc.plaintext",
    run=run_to_text,
    input_types=PDF_TYPES,
    input_label=INPUT_LABEL,
    action_title="轉成文字",
    keywords=("pdf to text", "txt", "extract text", "copy text", "轉文字", "純文字", "擷取文字"),
    tint=TINTS["slate"],
    badge="TXT",
    options=(
        page_range_option("pages", "要轉換的頁面", note=range_caption("pages")),
        Option(
            "separators",
            Kind.TOGGLE,
            "標示頁碼分隔線",
            True,
            caption="每頁開頭加上「第 N 頁」",
            group="格式",
            group_icon="text.alignleft",
        ),
        Option(
            "auto_ocr",
            Kind.TOGGLE,
            "沒有文字時自動 OCR",
            True,
            caption="以 Tesseract 辨識掃描或圖片頁面",
            group="掃描檔",
            group_icon="text.viewfinder",
        ),
        Option(
            "language",
            Kind.CHIPS,
            "辨識語言",
            "traditional",
            choices=tuple(Choice(k, title) for k, (title, _s, _l) in LANGUAGES.items()),
            group="掃描檔",
            group_icon="text.viewfinder",
            visible=lambda v, i: as_bool(v.get("auto_ocr"), True),
        ),
        note("輸出 UTF-8 純文字檔（.txt）。"),
    ),
    inspect=inspect_pdfs,
    validate=make_validator(lambda f, v, i: range_reason(str(v.get("pages") or ""), i)),
)
