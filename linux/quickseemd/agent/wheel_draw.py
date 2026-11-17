"""輪盤的 cairo 繪圖（對應原版 WheelView）：圓盤、扇形、標籤與中心。

座標都是輪盤視窗的邏輯像素（440×440，圓心 220,220）。
"""

from __future__ import annotations

import math
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import cairo
import gi

gi.require_version("Pango", "1.0")
gi.require_version("PangoCairo", "1.0")
from gi.repository import Pango, PangoCairo  # noqa: E402

from ..core import wheel as qs_wheel  # noqa: E402
from ..core.wheel import GREY, WheelItem  # noqa: E402
from . import wheel_model  # noqa: E402
from .queue import ACCENT  # noqa: E402
from .wheel_controller import WheelState  # noqa: E402

RGBA = tuple[float, float, float, float]
IconLoader = Callable[[str, int, RGBA], "cairo.Surface | None"]
FileIconLoader = Callable[[Path, int], "cairo.Surface | None"]

C = wheel_model.CENTER
INNER = qs_wheel.INNER_RADIUS
OUTER = qs_wheel.OUTER_RADIUS
CENTER_RADIUS = qs_wheel.CENTER_DIAMETER / 2
LABEL_WIDTH = 92
LABEL_ICON = 17
HOVER_SCALE = 1.12
FONT = "Noto Sans CJK TC, Sans"
WHITE: RGBA = (1.0, 1.0, 1.0, 1.0)


@dataclass(frozen=True)
class Palette:
    bg: RGBA
    fg: RGBA
    secondary: RGBA
    composited: bool


def _sector_path(cr: cairo.Context, index: int, count: int) -> None:
    start, end = qs_wheel.sector_angles(index, count)
    a0, a1 = math.radians(start - 90), math.radians(end - 90)
    cr.new_path()
    cr.arc(C, C, OUTER, a0, a1)
    cr.arc_negative(C, C, INNER, a1, a0)
    cr.close_path()


def _layout(cr: cairo.Context, text: str, px: float, bold: bool, width: int, lines: int):
    layout = PangoCairo.create_layout(cr)
    desc = Pango.FontDescription(FONT)
    desc.set_absolute_size(px * Pango.SCALE)
    desc.set_weight(Pango.Weight.BOLD if bold else Pango.Weight.NORMAL)
    layout.set_font_description(desc)
    layout.set_width(width * Pango.SCALE)
    layout.set_alignment(Pango.Alignment.CENTER)
    layout.set_wrap(Pango.WrapMode.WORD_CHAR)
    layout.set_ellipsize(Pango.EllipsizeMode.END)
    layout.set_height(-lines)
    layout.set_text(text, -1)
    return layout


def _disk(cr: cairo.Context, pal: Palette) -> None:
    if pal.composited:
        shadow = cairo.RadialGradient(C, C + 6, OUTER - 6, C, C + 6, OUTER + 18)
        shadow.add_color_stop_rgba(0, 0, 0, 0, 0.28)
        shadow.add_color_stop_rgba(1, 0, 0, 0, 0)
        cr.set_source(shadow)
        cr.arc(C, C + 6, OUTER + 18, 0, 2 * math.pi)
        cr.fill()
    cr.set_source_rgba(*pal.bg[:3], 0.94 if pal.composited else 1.0)
    cr.new_path()
    cr.arc(C, C, OUTER, 0, 2 * math.pi)
    cr.fill()


def _label_color(item: WheelItem, hovered: bool, pal: Palette) -> RGBA:
    if hovered:
        return WHITE
    if item.tint == GREY:
        return pal.secondary
    return (*item.tint, 1.0)


def _draw_label(
    cr: cairo.Context,
    item: WheelItem,
    index: int,
    count: int,
    hovered: bool,
    pal: Palette,
    icon: IconLoader,
) -> None:
    lx, ly = wheel_model.label_center(index, count)
    color = _label_color(item, hovered, pal)
    layout = _layout(cr, item.title, 12, True, LABEL_WIDTH, 2)
    _w, text_h = layout.get_pixel_size()
    total = LABEL_ICON + 3 + text_h
    cr.save()
    cr.translate(lx, ly)
    if hovered:
        cr.scale(HOVER_SCALE, HOVER_SCALE)
    surface = icon(item.icon, LABEL_ICON, color)
    if surface is not None:
        cr.set_source_surface(surface, -LABEL_ICON / 2, -total / 2)
        cr.paint()
    cr.set_source_rgba(*color)
    cr.move_to(-LABEL_WIDTH / 2, -total / 2 + LABEL_ICON + 3)
    PangoCairo.show_layout(cr, layout)
    cr.restore()


def _draw_sectors(
    cr: cairo.Context, items: tuple[WheelItem, ...], hover: object, pal: Palette, icon: IconLoader
) -> None:
    n = len(items)
    for i, item in enumerate(items):
        _sector_path(cr, i, n)
        if hover == i:
            cr.set_source_rgba(*item.tint, 1.0)
            cr.fill_preserve()
        cr.set_source_rgba(*pal.fg[:3], 0.08)
        cr.set_line_width(1)
        cr.stroke()
    for i, item in enumerate(items):
        _draw_label(cr, item, i, n, hover == i, pal, icon)


def _fit_hint(cr: cairo.Context, text: str, width: int):
    """提示字太長就縮小（最小 70%，原版 minimumScaleFactor）。"""
    size = 8.5
    layout = _layout(cr, text, size, False, 10_000, 1)
    natural, _h = layout.get_pixel_size()
    if natural > width:
        size = max(size * width / natural, size * 0.7)
    return _layout(cr, text, size, False, width, 1)


def _draw_center(
    cr: cairo.Context, state: WheelState, hovered: bool, pal: Palette, file_icon: FileIconLoader
) -> None:
    cr.new_path()  # 清掉上一個標籤留下的目前點，不然會多畫一條線
    cr.arc(C, C, CENTER_RADIUS, 0, 2 * math.pi)
    cr.set_source_rgba(*pal.bg[:3], 1.0)
    cr.fill_preserve()
    if hovered:
        cr.set_source_rgba(*ACCENT, 0.25)
        cr.fill_preserve()
    cr.set_source_rgba(*pal.fg[:3], 0.12)
    cr.set_line_width(1)
    cr.stroke()
    title = _layout(cr, state.center_title, 12, True, int(CENTER_RADIUS * 2 - 16), 1)
    hint = _fit_hint(cr, state.hint, int(CENTER_RADIUS * 2 - 22))
    icon = file_icon(state.files[0], 34) if state.files else None
    th, hh = title.get_pixel_size()[1], hint.get_pixel_size()[1]
    total = (34 + 2 if icon is not None else 0) + th + 2 + hh
    y = C - total / 2
    if icon is not None:
        cr.set_source_surface(icon, C - 17, y)
        cr.paint()
        y += 36
    cr.set_source_rgba(*pal.fg)
    cr.move_to(C - (CENTER_RADIUS * 2 - 16) / 2, y)
    PangoCairo.show_layout(cr, title)
    cr.set_source_rgba(*pal.secondary)
    cr.move_to(C - (CENTER_RADIUS * 2 - 22) / 2, y + th + 2)
    PangoCairo.show_layout(cr, hint)


def draw_wheel(
    cr: cairo.Context,
    state: WheelState,
    hover: object,
    pal: Palette,
    icon: IconLoader,
    file_icon: FileIconLoader,
) -> None:
    """畫整個輪盤；hover 是格子編號、"center" 或 None。"""
    cr.save()
    cr.set_operator(cairo.OPERATOR_CLEAR)
    cr.paint()
    cr.restore()
    _disk(cr, pal)
    _draw_sectors(cr, state.items, hover, pal, icon)
    _draw_center(cr, state, hover == "center", pal, file_icon)


def circle_mask(size: int, radius: float) -> cairo.ImageSurface:
    """圓形遮罩（給輸入範圍與無合成器時的視窗外形用）。"""
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, size, size)
    cr = cairo.Context(surface)
    cr.arc(size / 2, size / 2, radius, 0, 2 * math.pi)
    cr.set_source_rgba(0, 0, 0, 1)
    cr.fill()
    return surface
