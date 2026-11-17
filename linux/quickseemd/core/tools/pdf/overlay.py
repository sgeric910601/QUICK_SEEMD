"""在每頁疊加新內容（對應原版 PTRedraw＋PTPDFWriter 的 under／over）。

原頁面的物件原封不動（文字、字型、連結、書籤、表單都保留），新內容畫成另一份 PDF 的頁面，
再包成 Form XObject 加進原頁面：
    [q /QSUnderlay Do Q] q <原本的內容> Q [q /QSOverlay Do Q]

畫布座標是「看起來的頁面」：裁切框、已套用 /Rotate 與 /UserUnit，單位是實際的 pt。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import cairo
import gi
import pikepdf

gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Pango, PangoCairo  # noqa: E402

from ... import errors  # noqa: E402
from ...model import ToolContext  # noqa: E402
from .common import (  # noqa: E402
    crop_box,
    read_info,
    rotation,
    save_pdf,
    user_unit,
    verify_pages,
    visual_size,
    write_info,
)

log = logging.getLogger(__name__)

Matrix = tuple[float, float, float, float, float, float]

# 黑體／明體（原版 PingFang TC／宋體-繁）
FONT_FAMILIES = {"sans": "Noto Sans CJK TC", "serif": "Noto Serif CJK TC"}
# 粗體：黑體用 Semibold（原版 PingFangTC-Semibold），明體用 Bold
BOLD_WEIGHTS = {"sans": Pango.Weight.SEMIBOLD, "serif": Pango.Weight.BOLD}


@dataclass(frozen=True)
class Canvas:
    """疊加時一頁的資訊（座標：看起來的頁面，左下角為原點）。"""

    index: int
    count: int
    width: float
    height: float


Draw = Callable[[cairo.Context, Canvas], bool]


# ---------------------------------------------------------------- 座標轉換


def canvas_matrix(page: pikepdf.Page) -> Matrix:
    """畫布（看起來的頁面，pt）→ 頁面使用者座標的矩陣。"""
    x0, y0, x1, y1 = crop_box(page)
    w, h = x1 - x0, y1 - y0
    s = 1.0 / user_unit(page)
    r = rotation(page)
    if r == 90:
        a, b, c, d, e, f = 0.0, 1.0, -1.0, 0.0, x0 + w, y0
    elif r == 180:
        a, b, c, d, e, f = -1.0, 0.0, 0.0, -1.0, x0 + w, y0 + h
    elif r == 270:
        a, b, c, d, e, f = 0.0, -1.0, 1.0, 0.0, x0, y0 + h
    else:
        a, b, c, d, e, f = 1.0, 0.0, 0.0, 1.0, x0, y0
    return (a * s, b * s, c * s, d * s, e, f)


def concat(first: Matrix, then: Matrix) -> Matrix:
    """先套 first 再套 then 的矩陣（PDF 列向量慣例：first × then）。"""
    a1, b1, c1, d1, e1, f1 = first
    a2, b2, c2, d2, e2, f2 = then
    return (
        a1 * a2 + b1 * c2,
        a1 * b2 + b1 * d2,
        c1 * a2 + d1 * c2,
        c1 * b2 + d1 * d2,
        e1 * a2 + f1 * c2 + e2,
        e1 * b2 + f1 * d2 + f2,
    )


# ---------------------------------------------------------------- 文字


@lru_cache(maxsize=64)
def _font(style: str, bold: bool, size: float) -> Pango.FontDescription:
    desc = Pango.FontDescription()
    desc.set_family(FONT_FAMILIES.get(style, FONT_FAMILIES["sans"]))
    desc.set_weight(BOLD_WEIGHTS.get(style, Pango.Weight.BOLD) if bold else Pango.Weight.NORMAL)
    desc.set_absolute_size(max(size, 0.5) * Pango.SCALE)
    return desc


_MEASURE_SURFACE = cairo.ImageSurface(cairo.FORMAT_A8, 1, 1)


def _layout(cr: cairo.Context, text: str, desc: Pango.FontDescription) -> Pango.Layout:
    layout = PangoCairo.create_layout(cr)
    options = cairo.FontOptions()
    options.set_hint_metrics(cairo.HINT_METRICS_OFF)
    options.set_hint_style(cairo.HINT_STYLE_NONE)
    PangoCairo.context_set_font_options(layout.get_context(), options)
    layout.context_changed()
    layout.set_font_description(desc)
    layout.set_text(text, -1)
    return layout


class TextLine:
    """一行文字（Pango），寬度與上下高度用來排版；顏色取自繪圖環境。"""

    def __init__(self, text: str, style: str, bold: bool, size: float) -> None:
        self.text = text
        self.desc = _font(style, bold, float(size))
        layout = _layout(cairo.Context(_MEASURE_SURFACE), text, self.desc)
        _ink, logical = layout.get_extents()
        self.width = logical.width / Pango.SCALE
        self.ascent = layout.get_baseline() / Pango.SCALE
        self.descent = max(logical.height / Pango.SCALE - self.ascent, 0.0)

    @property
    def height(self) -> float:
        return self.ascent + self.descent

    def draw(self, cr: cairo.Context, x: float, baseline_y: float) -> None:
        """以 (x, 基線) 為左側起點畫出（cairo 座標，y 向下）。"""
        layout = _layout(cr, self.text, self.desc)
        cr.move_to(x, baseline_y - self.ascent)
        PangoCairo.show_layout(cr, layout)


# ---------------------------------------------------------------- 圖層與疊加


def draw_layer(
    pdf: pikepdf.Pdf,
    path: Path,
    draw: Draw,
    progress: Callable[[int, int], None],
    check: Callable[[], None],
) -> list[bool]:
    """每頁畫一張與「看起來的頁面」同大小的圖層頁；回傳每頁是否有畫東西。"""
    count = len(pdf.pages)
    surface = cairo.PDFSurface(str(path), 10, 10)
    drew: list[bool] = []
    try:
        for i, page in enumerate(pdf.pages):
            check()
            progress(i, count)
            w, h = visual_size(page)
            surface.set_size(max(w, 1.0), max(h, 1.0))
            cr = cairo.Context(surface)
            cr.save()
            drew.append(bool(draw(cr, Canvas(i, count, w, h))))
            cr.restore()
            cr.show_page()
    finally:
        surface.finish()
    return drew


def _own_resources(page: pikepdf.Page) -> pikepdf.Dictionary:
    """讓這頁有自己的資源字典（避免改到多頁共用的那一份）。"""
    res = page.obj.get("/Resources")
    fresh = (
        pikepdf.Dictionary({k: v for k, v in res.items()})
        if isinstance(res, pikepdf.Dictionary)
        else pikepdf.Dictionary()
    )
    xobjs = fresh.get("/XObject")
    fresh.XObject = pikepdf.Dictionary(
        {k: v for k, v in xobjs.items()} if isinstance(xobjs, pikepdf.Dictionary) else {}
    )
    page.obj.Resources = fresh
    return fresh


def _unique_name(xobjs: pikepdf.Dictionary, prefix: str) -> str:
    n = 0
    while f"/{prefix}{n or ''}" in xobjs:
        n += 1
    return f"/{prefix}{n or ''}"


def place_form(
    pdf: pikepdf.Pdf,
    page: pikepdf.Page,
    layer_page: pikepdf.Page,
    under: bool = False,
    size: tuple[float, float] | None = None,
) -> None:
    """把另一份 PDF 的一頁包成 Form XObject，疊在這頁上方（或放在下方）。

    size：圖層頁要對應到的畫布大小（預設＝看起來的頁面大小；OCR 文字層用它縮放）。
    """
    form = pdf.copy_foreign(layer_page.as_form_xobject(False))
    lx0, ly0, lx1, ly1 = (float(v) for v in layer_page.mediabox)
    cw, ch = size or visual_size(page)
    sx = cw / max(lx1 - lx0, 1e-6)
    sy = ch / max(ly1 - ly0, 1e-6)
    fit: Matrix = (sx, 0.0, 0.0, sy, -lx0 * sx, -ly0 * sy)
    form.Matrix = pikepdf.Array(list(concat(fit, canvas_matrix(page))))
    xobjs = _own_resources(page).XObject
    name = _unique_name(xobjs, "QSUnderlay" if under else "QSOverlay")
    xobjs[name] = form
    invoke = f"q {name} Do Q\n".encode()
    if under:
        page.contents_add(pdf.make_stream(invoke + b"q\n"), prepend=True)
        page.contents_add(pdf.make_stream(b"\nQ\n"))
    else:
        page.contents_add(pdf.make_stream(b"q\n"), prepend=True)
        page.contents_add(pdf.make_stream(b"\nQ\n" + invoke))


def stamp(
    pdf: pikepdf.Pdf,
    out: Path,
    ctx: ToolContext,
    draw: Draw,
    *,
    under: bool = False,
    message: Callable[[int, int], str] = lambda i, n: f"處理第 {i} / {n} 頁…",
    span: tuple[float, float] = (0.05, 0.85),
) -> None:
    """畫圖層、疊到 pdf 的每一頁，並寫出到 out（對應原版 PTRedraw.run）。

    文件資訊照原版改寫成 5 個欄位＋Producer＋日期；書籤、目錄項目維持原樣。
    """
    lo, hi = span
    layer_path = out.with_name("layer.pdf")

    def progress(i: int, n: int) -> None:
        ctx.progress(lo + (hi - lo) * i / max(n, 1), message(i + 1, n))

    drew = draw_layer(pdf, layer_path, draw, progress, ctx.check_cancelled)
    try:
        layer = pikepdf.open(layer_path)
    except pikepdf.PdfError as exc:
        raise errors.write_failed("無法建立疊加內容。") from exc
    # 複製過來的串流在寫出時才讀取，所以圖層檔要開到寫完為止
    with layer:
        if len(layer.pages) != len(pdf.pages):
            raise errors.write_failed("無法建立疊加內容。")
        ctx.progress(hi, "寫出 PDF…")
        for i, page in enumerate(pdf.pages):
            if drew[i]:
                place_form(pdf, page, layer.pages[i], under)
        write_info(pdf, read_info(pdf))
        save_pdf(pdf, out)
    verify_pages(out, len(drew))


def fill_color(cr: cairo.Context, rgb: tuple[float, float, float], alpha: float = 1.0) -> None:
    cr.set_source_rgba(rgb[0], rgb[1], rgb[2], alpha)
