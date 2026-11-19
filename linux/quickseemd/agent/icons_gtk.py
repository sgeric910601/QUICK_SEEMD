"""從圖示主題取圖示並畫成 cairo 圖面（GTK3；處理高解析度縮放，結果有快取）。

工具與動作寫的是原版 SF Symbol 名稱，經 core.icons 轉成圖示主題名稱；
也接受直接給圖示主題名稱（例如 window-close-symbolic）。
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from ..core import icons  # noqa: E402

log = logging.getLogger(__name__)

RGBA = tuple[float, float, float, float]
FILE_FALLBACK = "text-x-generic"


def theme_name(icon: str | None) -> str:
    """SF Symbol 或圖示主題名稱 → 圖示主題名稱。"""
    if not icon:
        return icons.FALLBACK
    if icon in icons.SF_TO_FREEDESKTOP:
        return icons.icon_name(icon)
    if Gtk.IconTheme.get_default().has_icon(icon):
        return icon
    return icons.FALLBACK


@lru_cache(maxsize=256)
def _symbolic_pixbuf(name: str, size: int, scale: int, rgba: RGBA):  # noqa: ANN202
    theme = Gtk.IconTheme.get_default()
    flags = Gtk.IconLookupFlags.FORCE_SYMBOLIC | Gtk.IconLookupFlags.FORCE_SIZE
    info = theme.lookup_icon_for_scale(name, size, scale, flags)
    if info is None:
        info = theme.lookup_icon_for_scale(icons.FALLBACK, size, scale, flags)
    if info is None:
        return None
    color = Gdk.RGBA(*rgba)
    try:
        pixbuf, _was_symbolic = info.load_symbolic(color, None, None, None)
    except GLib.Error as exc:
        log.warning("圖示 %s 載入失敗：%s", name, exc.message)
        return None
    return pixbuf


def symbolic_surface(icon: str | None, size: int, rgba: RGBA, widget: Gtk.Widget):  # noqa: ANN201
    """單色圖示（著上指定顏色）→ cairo 圖面；失敗回傳 None。"""
    scale = max(widget.get_scale_factor(), 1)
    pixbuf = _symbolic_pixbuf(theme_name(icon), size, scale, rgba)
    if pixbuf is None:
        return None
    return Gdk.cairo_surface_create_from_pixbuf(pixbuf, scale, widget.get_window())


@lru_cache(maxsize=256)
def _file_pixbuf(path: str, size: int, scale: int):  # noqa: ANN202
    theme = Gtk.IconTheme.get_default()
    gicon = None
    try:
        info = Gio.File.new_for_path(path).query_info(
            "standard::icon", Gio.FileQueryInfoFlags.NONE, None
        )
        gicon = info.get_icon()
    except GLib.Error as exc:
        log.debug("讀不到檔案圖示 %s：%s", path, exc.message)
    flags = Gtk.IconLookupFlags.FORCE_SIZE
    found = theme.lookup_by_gicon_for_scale(gicon, size, scale, flags) if gicon else None
    if found is None:
        found = theme.lookup_icon_for_scale(FILE_FALLBACK, size, scale, flags)
    if found is None:
        return None
    try:
        return found.load_icon()
    except GLib.Error as exc:
        log.warning("檔案圖示載入失敗：%s", exc.message)
        return None


def file_surface(path: Path, size: int, widget: Gtk.Widget):  # noqa: ANN201
    """檔案類型圖示 → cairo 圖面；失敗回傳 None。"""
    scale = max(widget.get_scale_factor(), 1)
    pixbuf = _file_pixbuf(str(path), size, scale)
    if pixbuf is None:
        return None
    return Gdk.cairo_surface_create_from_pixbuf(pixbuf, scale, widget.get_window())


def theme_colors(widget: Gtk.Widget) -> dict[str, RGBA]:
    """目前主題的背景、文字、次要文字顏色。"""
    ctx = widget.get_style_context()
    found, bg = ctx.lookup_color("theme_bg_color")
    if not found:
        bg = Gdk.RGBA(0.96, 0.96, 0.96, 1.0)
    fg = ctx.get_color(Gtk.StateFlags.NORMAL)
    return {
        "bg": (bg.red, bg.green, bg.blue, 1.0),
        "fg": (fg.red, fg.green, fg.blue, 1.0),
        "secondary": (fg.red, fg.green, fg.blue, 0.55),
    }
