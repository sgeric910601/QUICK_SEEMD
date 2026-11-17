"""OCR PDF（對應原版 PTOCR.swift 前半）：辨識掃描頁面，加入看不見、可搜尋的文字層。

辨識引擎是 Tesseract：頁面先以 pdftocairo 畫成圖，Tesseract 輸出「只有文字」的 PDF
（textonly_pdf=1，文字是隱形的），再把它縮放、轉正後疊到原頁面上。原頁面內容不動。
"""

from __future__ import annotations

import functools
import io
import logging
import unicodedata
from dataclasses import dataclass
from pathlib import Path

import pikepdf

from ... import backends, errors
from ...model import PDF, ToolContext, ToolDefinition, per_file
from ...options import Choice, Info, Kind, Option, Values, note
from . import render
from .common import (
    work_dir,
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
    read_info,
    save_pdf,
    verify_pages,
    write_info,
)
from .overlay import place_form

log = logging.getLogger(__name__)

# 鍵 → (標題, 說明, Tesseract 語言)
LANGUAGES: dict[str, tuple[str, str | None, str]] = {
    "traditional": ("繁體中文＋英文", "台灣、香港的文件", "chi_tra+eng"),
    "simplified": ("簡體中文＋英文", "中國大陸的文件", "chi_sim+eng"),
    "japanese": ("日文＋英文", None, "jpn+eng"),
    "korean": ("韓文＋英文", None, "kor+eng"),
    "english": ("英文", "以及其他拉丁字母語言", "eng"),
}
LANGUAGE_ICONS = {
    "traditional": "character",
    "simplified": "character",
    "japanese": "character.ja",
    "korean": "character.ko",
    "english": "globe",
}
ACCURATE_DPI = 300.0
FAST_DPI = 200.0
MAX_OCR_PIXELS = 4200.0


def tesseract_langs(language: str) -> str:
    return LANGUAGES.get(language, LANGUAGES["traditional"])[2]


def effective_accurate(values: Values) -> bool:
    """快速模式只用在拉丁語系；中日韓會自動改用精確模式（與原版相同）。"""
    if as_bool(values.get("accurate"), True):
        return True
    return str(values.get("language") or "traditional") != "english"


def ocr_dpi(points: tuple[float, float], accurate: bool) -> float:
    """辨識用的解析度：約 300 dpi（快速模式 200），長邊不超過 4200 px，至少 72。"""
    cap = ACCURATE_DPI if accurate else FAST_DPI
    dpi = min(cap, MAX_OCR_PIXELS / max(points[0], points[1], 1.0) * 72.0)
    return max(dpi, 72.0)


# ---------------------------------------------------------------- Tesseract 結果


@dataclass(frozen=True)
class OCRLine:
    """一行辨識結果（圖片像素座標，左上為原點）。"""

    text: str
    left: float
    top: float
    width: float
    height: float

    @property
    def mid_y(self) -> float:
        return self.top + self.height / 2


def _is_cjk(ch: str) -> bool:
    if not ch:
        return False
    name = unicodedata.name(ch, "")
    return name.startswith(("CJK", "HIRAGANA", "KATAKANA", "HANGUL", "FULLWIDTH", "IDEOGRAPHIC"))


def join_words(words: list[str]) -> str:
    """同一行的字詞相連：兩邊都是中日韓文字時不加空白，其他用空白隔開。"""
    out = ""
    for w in words:
        if out and not (_is_cjk(out[-1]) and _is_cjk(w[0])):
            out += " "
        out += w
    return out


def parse_tsv(text: str) -> list[OCRLine]:
    """Tesseract 的 TSV 輸出 → 每行文字與外框（依區塊、段落、行分組）。"""
    groups: dict[tuple[str, ...], list[tuple[float, float, float, float, str]]] = {}
    for row in text.splitlines()[1:]:
        cols = row.split("\t")
        if len(cols) < 12 or cols[0] != "5" or not cols[11].strip():
            continue
        try:
            box = tuple(float(v) for v in cols[6:10])
        except ValueError:
            continue
        groups.setdefault(tuple(cols[1:5]), []).append((*box, cols[11].strip()))  # type: ignore[arg-type]
    lines: list[OCRLine] = []
    for words in groups.values():
        x0 = min(w[0] for w in words)
        y0 = min(w[1] for w in words)
        x1 = max(w[0] + w[2] for w in words)
        y1 = max(w[1] + w[3] for w in words)
        text_line = join_words([w[4] for w in sorted(words, key=lambda w: w[0])])
        lines.append(OCRLine(text_line, x0, y0, x1 - x0, y1 - y0))
    return lines


def _line_order(a: OCRLine, b: OCRLine) -> int:
    if abs(a.mid_y - b.mid_y) > min(a.height, b.height) * 0.5:
        return -1 if a.mid_y < b.mid_y else 1
    return -1 if a.left < b.left else (1 if a.left > b.left else 0)


def join_lines(lines: list[OCRLine]) -> str:
    """由上而下、由左而右排列；中心高度相差不到半行高的併成同一行（原版 PTToTextSession.join）。"""
    ordered = sorted(lines, key=functools.cmp_to_key(_line_order))
    out: list[str] = []
    last: OCRLine | None = None
    for line in ordered:
        if (
            last is not None
            and out
            and abs(line.mid_y - last.mid_y) <= min(line.height, last.height) * 0.5
        ):
            out[-1] += " " + line.text
        else:
            out.append(line.text)
        last = line
    return "\n".join(out)


def recognize(
    ctx: ToolContext, image: Path, language: str, dpi: float, out_base: Path, want_pdf: bool
) -> tuple[list[OCRLine], Path | None]:
    """辨識一張圖；want_pdf 時另外輸出只有隱形文字的 PDF。"""
    args = [backends.require("tesseract"), str(image), str(out_base)]
    args += ["-l", tesseract_langs(language), "--dpi", str(int(round(dpi)))]
    if want_pdf:
        args += ["-c", "textonly_pdf=1", "pdf"]
    args.append("tsv")
    ctx.run_process(args, "Tesseract")
    tsv = out_base.with_name(out_base.name + ".tsv")
    lines = parse_tsv(tsv.read_text("utf-8", errors="replace")) if tsv.is_file() else []
    pdf = out_base.with_name(out_base.name + ".pdf")
    return lines, (pdf if want_pdf and pdf.is_file() else None)


def render_for_ocr(
    ctx: ToolContext, file: Path, page: pikepdf.Page, n: int, accurate: bool, work: Path
) -> tuple[Path, float]:
    points = render.page_points(page)
    dpi = ocr_dpi(points, accurate)
    return render.render_page(ctx, file, n, dpi, work / f"ocr-{n}", points=points), dpi


# ---------------------------------------------------------------- OCR PDF 工具


@dataclass
class _Counts:
    recognized: int = 0
    skipped: int = 0
    found: int = 0


def _ocr_page(
    ctx: ToolContext, file: Path, doc: pikepdf.Pdf, i: int, values: Values, work: Path
) -> tuple[int, pikepdf.Pdf | None]:
    """辨識第 i 頁（0 起算）並疊上文字層；回傳（行數, 文字層 PDF）。"""
    accurate = effective_accurate(values)
    page = doc.pages[i]
    image, dpi = render_for_ocr(ctx, file, page, i + 1, accurate, work)
    language = str(values.get("language") or "traditional")
    lines, layer_path = recognize(ctx, image, language, dpi, work / f"text-{i + 1}", True)
    if not lines or layer_path is None:
        return len(lines), None
    layer = pikepdf.open(io.BytesIO(layer_path.read_bytes()))
    place_form(doc, page, layer.pages[0])
    return len(lines), layer


def _ocr_document(
    file: Path, doc: pikepdf.Pdf, values: Values, ctx: ToolContext, work: Path
) -> tuple[_Counts, list[pikepdf.Pdf]]:
    n = len(doc.pages)
    targets = sorted({p - 1 for p in page_list(str(values.get("pages") or ""), n, file)})
    has_text: set[int] = set()
    if as_bool(values.get("skip_text"), True):
        texts = render.all_page_texts(ctx, file, n)
        has_text = {i for i in targets if texts[i].strip()}
    counts = _Counts()
    layers: list[pikepdf.Pdf] = []
    suffix = "" if effective_accurate(values) else "（快速）"
    for i in targets:
        ctx.check_cancelled()
        ctx.progress(0.02 + 0.93 * i / n, f"辨識第 {i + 1} / {n} 頁…{suffix}")
        if i in has_text:
            counts.skipped += 1
            continue
        found, layer = _ocr_page(ctx, file, doc, i, values, work)
        counts.recognized += 1
        counts.found += found
        if layer is not None:
            layers.append(layer)
    return counts, layers


def _summary(counts: _Counts, values: Values) -> str:
    text = f"已辨識 {counts.recognized} 頁、{counts.found} 行文字"
    if counts.skipped > 0:
        text += f"，略過 {counts.skipped} 頁已有文字的頁面"
    if not as_bool(values.get("accurate"), True) and effective_accurate(values):
        text += "（快速模式不支援此語言，已改用精確模式）"
    return text


def _ocr_file(file: Path, values: Values, ctx: ToolContext) -> list[Path]:
    work = work_dir(ctx, file)
    tmp = work / "ocr.pdf"
    with open_pdf(file) as doc:
        counts, layers = _ocr_document(file, doc, values, ctx, work)
        try:
            if counts.recognized == 0 and counts.skipped > 0:
                raise errors.QSError(
                    f"「{file.name}」的頁面已經有文字層，不需要 OCR。"
                    "若要強制辨識，請關閉「略過已有文字的頁面」。"
                )
            if counts.found == 0:
                raise errors.empty_content(f"在「{file.name}」中辨識不到任何文字。")
            ctx.progress(0.95, "寫出 PDF…")
            write_info(doc, read_info(doc))
            save_pdf(doc, tmp)
        finally:
            for layer in layers:
                layer.close()
        count = len(doc.pages)
    verify_pages(tmp, count)
    out = ctx.deliver(tmp, ctx.output_name(file, "可搜尋", "pdf"), file)
    ctx.progress(1, _summary(counts, values))
    return [out]


def run_ocr(files: list[Path], values: Values, ctx: ToolContext) -> list[Path]:
    return per_file(files, ctx, lambda f, sub: _ocr_file(f, values, sub), skip_errors=False)


def language_choices() -> tuple[Choice, ...]:
    return tuple(
        Choice(key, title, sub, LANGUAGE_ICONS[key]) for key, (title, sub, _l) in LANGUAGES.items()
    )


def _mode_caption(values: Values, info: Info) -> tuple[str, bool]:
    if as_bool(values.get("accurate"), True):
        return "準確度最高，適合正式文件。", False
    return "速度較快；中日韓文會自動改用精確模式。", False


OCR = ToolDefinition(
    id="pdf.ocr",
    category=PDF,
    title="OCR PDF",
    subtitle="辨識掃描檔中的文字，讓 PDF 可以搜尋、選取與複製，外觀不變。",
    icon="text.viewfinder",
    run=run_ocr,
    input_types=PDF_TYPES,
    input_label=INPUT_LABEL,
    action_title="辨識文字",
    keywords=("ocr", "searchable", "scan", "recognize text", "文字辨識", "可搜尋", "掃描", "辨識"),
    badge="OCR",
    options=(
        Option(
            "language",
            Kind.CARDS,
            "文件語言",
            "traditional",
            choices=language_choices(),
            group="文件語言",
            group_icon="character.book.closed",
        ),
        Option(
            "accurate",
            Kind.SEGMENTED,
            "辨識方式",
            True,
            choices=(Choice(False, "快速"), Choice(True, "精確")),
            group="辨識方式",
            group_icon="speedometer",
            note=_mode_caption,
        ),
        page_range_option("pages", "要辨識的頁面", note=range_caption("pages")),
        Option(
            "skip_text",
            Kind.TOGGLE,
            "略過已有文字的頁面",
            True,
            caption="只辨識掃描或圖片頁面",
            group="頁面",
            group_icon="doc.on.doc",
        ),
        note(
            "頁面外觀完全不變，只加入看不見的文字層，讓你可以搜尋、選取與複製。"
            "全程在這台電腦上處理，不會上傳。"
        ),
    ),
    presets={"pdf.ocr-searchable": {}},
    inspect=inspect_pdfs,
    validate=make_validator(lambda f, v, i: range_reason(str(v.get("pages") or ""), i)),
    requires=("tesseract",),
)
