"""加入浮水印、加入頁碼（對應原版 PTStamp.swift）。

排版數學照原版（座標原點在左下、角度逆時針為正），畫圖時才換成 cairo 的 y 向下座標。
原版選項面板上方的即時預覽在這個版本沒有做。
"""

from __future__ import annotations

import math
from pathlib import Path

import cairo

from ...model import PDF, ToolContext, ToolDefinition, per_file
from ...options import Choice, Info, Kind, Option, Values
from .common import (
    GRID_TITLES,
    INK_BLACK,
    INK_BLUE,
    INK_GRAY,
    INK_GREEN,
    INK_RED,
    INKS,
    INPUT_LABEL,
    PDF_TYPES,
    as_bool,
    as_float,
    as_int,
    ink_rgb,
    inspect_pdfs,
    make_validator,
    open_pdf,
    page_list,
    page_range_option,
    range_caption,
    range_reason,
)
from .overlay import Canvas, TextLine, fill_color, stamp

MARGIN = 36.0

# ---------------------------------------------------------------- 排版（原版座標：左下為原點）


def grid_center(
    position: int, box: tuple[float, float], page: tuple[float, float], margin: float
) -> tuple[float, float]:
    """九宮格位置：在頁面放一個 box 大小的方塊時，方塊中心的位置。"""
    col, row = position % 3, position // 3
    bw, bh = box
    pw, ph = page
    x = (margin + bw / 2, pw / 2, pw - margin - bw / 2)[col]
    y = (ph - margin - bh / 2, ph / 2, margin + bh / 2)[row]
    return x, y


def single_box(
    width: float, height: float, angle: float, page: tuple[float, float], margin: float
) -> tuple[tuple[float, float], float]:
    """旋轉後文字的外框，與「太大時自動縮小」的比例。"""
    rad = math.radians(angle)
    c, s = abs(math.cos(rad)), abs(math.sin(rad))
    box = (width * c + height * s, width * s + height * c)
    fit = min(1.0, (page[0] - margin * 2) / box[0], (page[1] - margin * 2) / box[1])
    return box, fit


def _draw_single(cr: cairo.Context, line: TextLine, canvas: Canvas, angle: float, pos: int) -> None:
    page = (canvas.width, canvas.height)
    box, fit = single_box(line.width, line.height, angle, page, MARGIN)
    cx, cy = grid_center(pos, (box[0] * fit, box[1] * fit), page, MARGIN)
    cr.translate(cx, canvas.height - cy)
    cr.rotate(-math.radians(angle))
    cr.scale(fit, fit)
    line.draw(cr, -line.width / 2, line.height / 2 - line.descent)


def _draw_tiled(
    cr: cairo.Context, line: TextLine, canvas: Canvas, angle: float, size: float
) -> None:
    cr.rectangle(0, 0, canvas.width, canvas.height)
    cr.clip()
    step_x = line.width + size * 2
    step_y = line.height * 3.2
    half = math.hypot(canvas.width, canvas.height) / 2 + step_x
    cr.translate(canvas.width / 2, canvas.height / 2)
    cr.rotate(-math.radians(angle))
    row = 0
    y = -half
    while y <= half:
        x = -half - (0 if row % 2 == 0 else step_x / 2)
        while x <= half:
            line.draw(cr, x, -y)
            x += step_x
        y += step_y
        row += 1


def draw_watermark(cr: cairo.Context, canvas: Canvas, values: Values) -> None:
    text = str(values.get("text") or "").strip()
    if not text:
        return
    size = as_float(values.get("size"), 60)
    line = TextLine(
        text, str(values.get("font") or "sans"), as_bool(values.get("bold"), True), size
    )
    if line.width <= 0:
        return
    fill_color(cr, ink_rgb(values.get("ink"), INK_GRAY), as_float(values.get("opacity"), 30) / 100)
    angle = as_float(values.get("rotation"), 45)
    if values.get("layout") == "tiled":
        _draw_tiled(cr, line, canvas, angle, size)
    else:
        _draw_single(cr, line, canvas, angle, as_int(values.get("position"), 4))


# ---------------------------------------------------------------- 浮水印工具


def _watermark_file(file: Path, values: Values, ctx: ToolContext) -> list[Path]:
    with open_pdf(file) as doc:
        targets = {n - 1 for n in page_list(str(values.get("pages") or ""), len(doc.pages), file)}

        def draw(cr: cairo.Context, canvas: Canvas) -> bool:
            if canvas.index not in targets:
                return False
            draw_watermark(cr, canvas, values)
            return True

        tmp = ctx.make_work_dir(file) / "watermark.pdf"
        stamp(
            doc,
            tmp,
            ctx,
            draw,
            under=as_bool(values.get("behind"), False),
            message=lambda i, n: f"加入浮水印：第 {i} / {n} 頁…",
        )
    return [ctx.deliver(tmp, ctx.output_name(file, "浮水印", "pdf"), file)]


def run_watermark(files: list[Path], values: Values, ctx: ToolContext) -> list[Path]:
    return per_file(files, ctx, lambda f, sub: _watermark_file(f, values, sub), skip_errors=False)


def _watermark_validate(files: list[Path], values: Values, info: Info) -> str | None:
    if not str(values.get("text") or "").strip():
        return "請輸入浮水印文字"
    return range_reason(str(values.get("pages") or ""), info)


def _ink_choices(*keys: str) -> tuple[Choice, ...]:
    return tuple(Choice(k, INKS[k][0]) for k in keys)


def _pt(v: object) -> str:
    return f"{int(as_float(v, 0))} pt"


def _pct(v: object) -> str:
    return f"{int(as_float(v, 0))}%"


_TEXT = {"group": "文字", "group_icon": "pencil.line"}
_COLOR = {"group": "顏色與透明度", "group_icon": "paintpalette"}
_POS = {"group": "位置", "group_icon": "square.grid.3x3"}

WATERMARK = ToolDefinition(
    id="pdf.watermark",
    category=PDF,
    title="加入浮水印",
    subtitle="在頁面上加入文字浮水印，可調整字型、顏色、透明度、角度與位置。",
    icon="seal",
    run=run_watermark,
    input_types=PDF_TYPES,
    input_label=INPUT_LABEL,
    action_title="加入浮水印",
    keywords=("watermark", "stamp", "confidential", "draft", "浮水印", "印章", "機密", "草稿"),
    badge="PDF",
    options=(
        Option(
            "text",
            Kind.TEXT,
            "浮水印文字",
            "機密文件",
            placeholder="例如：機密文件、草稿、CONFIDENTIAL",
            **_TEXT,
        ),
        Option(
            "font",
            Kind.SEGMENTED,
            "字型",
            "sans",
            choices=(Choice("sans", "黑體"), Choice("serif", "明體")),
            **_TEXT,
        ),
        Option("bold", Kind.TOGGLE, "粗體", True, **_TEXT),
        Option(
            "size", Kind.SLIDER, "字級", 60, minimum=12, maximum=160, step=2, display=_pt, **_TEXT
        ),
        Option(
            "ink",
            Kind.COLOR,
            "顏色",
            INK_GRAY,
            choices=_ink_choices(INK_GRAY, INK_RED, INK_BLUE, INK_BLACK, INK_GREEN),
            **_COLOR,
        ),
        Option(
            "opacity",
            Kind.SLIDER,
            "不透明度",
            30,
            minimum=10,
            maximum=100,
            step=5,
            display=_pct,
            **_COLOR,
        ),
        Option(
            "rotation",
            Kind.SEGMENTED,
            "角度",
            45,
            choices=(Choice(0, "0°"), Choice(45, "45°"), Choice(-45, "-45°")),
            group="角度",
            group_icon="angle",
        ),
        Option(
            "layout",
            Kind.SEGMENTED,
            "排列",
            "single",
            choices=(Choice("single", "單一位置"), Choice("tiled", "平鋪整頁")),
            **_POS,
        ),
        Option(
            "position",
            Kind.GRID,
            "位置",
            4,
            choices=tuple(Choice(i, t) for i, t in enumerate(GRID_TITLES)),
            columns=3,
            visible=lambda v, i: v.get("layout", "single") == "single",
            **_POS,
        ),
        Option(
            "behind",
            Kind.SEGMENTED,
            "圖層",
            False,
            choices=(Choice(False, "疊在內容上方"), Choice(True, "放在內容下方")),
            group="圖層",
            group_icon="square.3.layers.3d",
            note=lambda v, i: (
                ("頁面有白色底色時，下方的浮水印會被蓋住。", False)
                if as_bool(v.get("behind"), False)
                else None
            ),
        ),
        page_range_option("pages", "要加浮水印的頁面", note=range_caption("pages")),
    ),
    inspect=inspect_pdfs,
    validate=make_validator(_watermark_validate),
)

# ---------------------------------------------------------------- 頁碼

NUMBER_FORMATS: dict[str, tuple[str, str]] = {
    "plain": ("1", "{n}"),
    "chinese": ("第 1 頁", "第 {n} 頁"),
    "slash": ("1 / N", "{n} / {total}"),
    "chinese_total": ("第 1 頁，共 N 頁", "第 {n} 頁，共 {total} 頁"),
    "english": ("Page 1", "Page {n}"),
}


def number_label(fmt: str, start: int, skip_first: bool, index: int, count: int) -> str | None:
    """第 index 頁（0 起算）要顯示的文字；None＝不加頁碼。"""
    if skip_first and index == 0:
        return None
    numbered = count - 1 if skip_first else count
    n = start + (index - 1 if skip_first else index)
    pattern = NUMBER_FORMATS.get(fmt, NUMBER_FORMATS["plain"])[1]
    return pattern.format(n=n, total=start + numbered - 1)


def draw_number(cr: cairo.Context, canvas: Canvas, text: str, values: Values) -> None:
    line = TextLine(text, "sans", False, as_float(values.get("size"), 11))
    m = as_float(values.get("margin"), 30)
    pos = as_int(values.get("position"), 7)
    col, row = pos % 3, pos // 3
    x = (m, (canvas.width - line.width) / 2, canvas.width - m - line.width)[col]
    baseline = canvas.height - m - line.ascent if row == 0 else m + line.descent
    fill_color(cr, ink_rgb(values.get("ink"), INK_BLACK))
    line.draw(cr, x, canvas.height - baseline)


def _numbers_file(file: Path, values: Values, ctx: ToolContext) -> list[Path]:
    fmt = str(values.get("format") or "plain")
    start = as_int(values.get("start"), 1)
    skip = as_bool(values.get("skip_first"), False)

    def draw(cr: cairo.Context, canvas: Canvas) -> bool:
        text = number_label(fmt, start, skip, canvas.index, canvas.count)
        if text is None:
            return False
        draw_number(cr, canvas, text, values)
        return True

    with open_pdf(file) as doc:
        tmp = ctx.make_work_dir(file) / "numbered.pdf"
        stamp(doc, tmp, ctx, draw, message=lambda i, n: f"加入頁碼：第 {i} / {n} 頁…")
    return [ctx.deliver(tmp, ctx.output_name(file, "已加頁碼", "pdf"), file)]


def run_page_numbers(files: list[Path], values: Values, ctx: ToolContext) -> list[Path]:
    return per_file(files, ctx, lambda f, sub: _numbers_file(f, values, sub), skip_errors=False)


_STYLE = {"group": "文字樣式", "group_icon": "paintbrush.pointed"}

PAGE_NUMBERS = ToolDefinition(
    id="pdf.page-numbers",
    category=PDF,
    title="加入頁碼",
    subtitle="在每頁的頁首或頁尾加上頁碼，可選位置、格式與起始號碼。",
    icon="number.square",
    run=run_page_numbers,
    input_types=PDF_TYPES,
    input_label=INPUT_LABEL,
    action_title="加入頁碼",
    keywords=("page numbers", "numbering", "bates", "footer", "header", "頁碼", "編號", "頁尾"),
    badge="PDF",
    options=(
        Option(
            "position",
            Kind.GRID,
            "位置",
            7,
            choices=tuple(Choice(i, GRID_TITLES[i]) for i in (0, 1, 2, 6, 7, 8)),
            columns=3,
            group="位置",
            group_icon="square.grid.3x2",
        ),
        Option(
            "format",
            Kind.CHIPS,
            "格式",
            "plain",
            choices=tuple(Choice(k, sample) for k, (sample, _p) in NUMBER_FORMATS.items()),
            group="格式",
            group_icon="number",
        ),
        Option(
            "start",
            Kind.NUMBER,
            "起始號碼",
            1,
            minimum=0,
            maximum=99999,
            group="編號",
            group_icon="list.number",
        ),
        Option(
            "skip_first",
            Kind.TOGGLE,
            "第一頁不加頁碼",
            False,
            caption="封面不編號，從第 2 頁開始以起始號碼編號",
            group="編號",
            group_icon="list.number",
        ),
        Option(
            "size", Kind.SLIDER, "字級", 11, minimum=7, maximum=36, step=1, display=_pt, **_STYLE
        ),
        Option(
            "ink",
            Kind.COLOR,
            "顏色",
            INK_BLACK,
            choices=_ink_choices(INK_BLACK, INK_GRAY, INK_BLUE, INK_RED),
            **_STYLE,
        ),
        Option(
            "margin",
            Kind.SEGMENTED,
            "與頁面邊緣的距離",
            30,
            choices=(Choice(18, "窄"), Choice(30, "標準"), Choice(48, "寬")),
            **_STYLE,
        ),
    ),
    presets={"pdf.add-page-numbers": {}},
    inspect=inspect_pdfs,
    validate=make_validator(),
)
