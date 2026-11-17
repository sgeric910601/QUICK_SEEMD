"""共用的小元件（對應原版 ToolUIComponents）：工具圖示、按鈕、拖放、縮圖、提示。"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Gdk", "4.0")
from gi.repository import Gdk, Gio, GLib, GObject, Gtk, Pango  # noqa: E402

from ..core import filetypes, icons  # noqa: E402
from ..core.result import format_size, size_of  # noqa: E402
from . import css, thumbnails  # noqa: E402

LOG = logging.getLogger(__name__)
RGB = tuple[float, float, float]


def label(
    text: str,
    *classes: str,
    wrap: bool = False,
    xalign: float = 0.0,
    lines: int = 0,
    ellipsize: str | None = None,
    selectable: bool = False,
) -> Gtk.Label:
    lbl = Gtk.Label(label=text, xalign=xalign, wrap=wrap, selectable=selectable)
    if wrap:
        lbl.set_wrap_mode(Pango.WrapMode.WORD_CHAR)
    if lines:
        lbl.set_lines(lines)
    if ellipsize:
        modes = {
            "end": Pango.EllipsizeMode.END,
            "middle": Pango.EllipsizeMode.MIDDLE,
            "start": Pango.EllipsizeMode.START,
        }
        lbl.set_ellipsize(modes[ellipsize])
    for c in classes:
        lbl.add_css_class(c)
    return lbl


def vbox(spacing: int = 0, *children: Gtk.Widget, **kw) -> Gtk.Box:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=spacing, **kw)
    for c in children:
        box.append(c)
    return box


def hbox(spacing: int = 0, *children: Gtk.Widget, **kw) -> Gtk.Box:
    box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=spacing, **kw)
    for c in children:
        box.append(c)
    return box


def icon(name: str, size: int = 16, *classes: str) -> Gtk.Image:
    img = Gtk.Image.new_from_icon_name(name)
    img.set_pixel_size(size)
    for c in classes:
        img.add_css_class(c)
    return img


def sf_icon(symbol: str | None, size: int = 16, *classes: str) -> Gtk.Image:
    """原版 SF Symbol 名稱 → 系統圖示。"""
    return icon(icons.icon_name(symbol), size, *classes)


def tool_tile(symbol: str, rgb: RGB, size: int, badge: str | None = None) -> Gtk.Widget:
    """彩色圓角方塊＋白色符號；size ≥ 34 時右下角顯示類型標籤（原版 ToolIcon）。"""
    box = Gtk.Box(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
    for c in ("qs-tile", css.tint_class(rgb), css.tile_size_class(size)):
        box.add_css_class(c)
    img = sf_icon(symbol, max(int(size * 0.44), 8))
    img.set_hexpand(True)
    img.set_halign(Gtk.Align.CENTER)
    box.append(img)
    box.set_size_request(size, size)
    box.set_hexpand(False)
    if not badge or size < 34:
        return box
    overlay = Gtk.Overlay(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER)
    overlay.set_child(box)
    tag = label(badge, "qs-badge", css.tint_class(rgb))
    tag.set_halign(Gtk.Align.END)
    tag.set_valign(Gtk.Align.END)
    overlay.add_overlay(tag)
    overlay.set_overflow(Gtk.Overflow.VISIBLE)
    overlay.set_hexpand(False)
    return overlay


def button(
    text: str,
    icon_name: str | None = None,
    *classes: str,
    icon_end: bool = False,
    on_click: Callable[[], None] | None = None,
) -> Gtk.Button:
    btn = Gtk.Button()
    content = hbox(8)
    lbl = label(text, xalign=0.5)
    if icon_name and not icon_end:
        content.append(icon(icon_name))
    content.append(lbl)
    if icon_name and icon_end:
        lbl.set_hexpand(True)
        lbl.set_xalign(0)
        content.append(icon(icon_name))
    btn.set_child(content)
    btn.qs_label = lbl  # type: ignore[attr-defined]
    for c in classes:
        btn.add_css_class(c)
    if on_click is not None:
        btn.connect("clicked", lambda _b: on_click())
    return btn


def primary(text: str, icon_name: str | None = None, **kw) -> Gtk.Button:
    return button(text, icon_name, "qs-primary", **kw)


def secondary(text: str, icon_name: str | None = None, **kw) -> Gtk.Button:
    return button(text, icon_name, "qs-secondary", **kw)


def link(text: str, icon_name: str | None = None, **kw) -> Gtk.Button:
    return button(text, icon_name, "qs-link", "flat", **kw)


def warning_row(text: str = "") -> Gtk.Box:
    """橘色警告（三角形＋文字）；用 set_warning 更新。"""
    row = hbox(6, icon("dialog-warning-symbolic", 16, "qs-warning"))
    lbl = label(text, "qs-warning", wrap=True)
    lbl.set_hexpand(True)
    row.append(lbl)
    row.qs_label = lbl  # type: ignore[attr-defined]
    row.set_visible(bool(text))
    return row


def set_warning(row: Gtk.Box, text: str | None) -> None:
    row.qs_label.set_label(text or "")  # type: ignore[attr-defined]
    row.set_visible(bool(text))


def clear(box: Gtk.Widget) -> None:
    """移除容器（Box／FlowBox／ListBox）裡所有子元件。"""
    child = box.get_first_child()
    while child is not None:
        nxt = child.get_next_sibling()
        box.remove(child)
        child = nxt


# ---------------------------------------------------------------- 拖放


def files_from_value(value: object) -> list[Path]:
    files: list[Path] = []
    if isinstance(value, Gdk.FileList):
        for f in value.get_files():
            p = f.get_path()
            if p:
                files.append(Path(p))
    return files


def add_file_drop(
    widget: Gtk.Widget,
    on_drop: Callable[[list[Path]], None],
    on_hover: Callable[[bool], None] | None = None,
) -> Gtk.DropTarget:
    """讓元件接受從檔案管理員拖進來的檔案（一律當成拷貝，不會移動原檔）。"""
    target = Gtk.DropTarget.new(Gdk.FileList, Gdk.DragAction.COPY)

    def dropped(_t: Gtk.DropTarget, value: object, _x: float, _y: float) -> bool:
        if on_hover:
            on_hover(False)
        files = files_from_value(value)
        if files:
            on_drop(files)
        return bool(files)

    target.connect("drop", dropped)
    if on_hover is not None:
        target.connect("enter", lambda *_a: on_hover(True) or Gdk.DragAction.COPY)
        target.connect("leave", lambda *_a: on_hover(False))
    widget.add_controller(target)
    return target


def drop_overlay(text: str | None = None) -> Gtk.Widget:
    """拖檔案進來時蓋在上面的虛線框。"""
    box = vbox(0, hexpand=True, vexpand=True, can_target=False)
    box.add_css_class("qs-drop")
    if text:
        inner = hbox(10, icon("folder-download-symbolic", 26), label(text, "qs-drop-label"))
        inner.set_halign(Gtk.Align.CENTER)
        inner.set_valign(Gtk.Align.CENTER)
        inner.set_vexpand(True)
        box.append(inner)
    box.set_visible(False)
    return box


# ---------------------------------------------------------------- 檔案縮圖與說明


def mime_icon(path: Path, size: int) -> Gtk.Image:
    mime = filetypes.FOLDER_MIME if path.is_dir() else filetypes.mime_of(path)
    gicon = Gio.content_type_get_icon(mime)
    img = Gtk.Image.new_from_gicon(gicon)
    img.set_pixel_size(size)
    return img


def device_scale() -> int:
    """螢幕縮放倍率（高解析度螢幕的縮圖多畫幾倍，顯示時再縮回來）。"""
    display = Gdk.Display.get_default()
    if display is None:
        return 1
    monitors = display.get_monitors()
    scales = [monitors.get_item(i).get_scale_factor() for i in range(monitors.get_n_items())]
    return max(scales or [1])


class ScaledPaintable(GObject.Object, Gdk.Paintable):
    """把高解析度的縮圖以 1/scale 的大小顯示（不讓卡片被撐大）。"""

    def __init__(self, texture: Gdk.Texture, scale: int) -> None:
        super().__init__()
        self._texture = texture
        self._scale = max(scale, 1)

    def do_get_intrinsic_width(self) -> int:
        return self._texture.get_width() // self._scale

    def do_get_intrinsic_height(self) -> int:
        return self._texture.get_height() // self._scale

    def do_snapshot(self, snapshot: Gdk.Snapshot, width: float, height: float) -> None:
        self._texture.snapshot(snapshot, width, height)


class FileThumb(Gtk.Box):
    """檔案縮圖：先顯示類型圖示，背景產生縮圖後換掉。"""

    def __init__(self, path: Path, width: int, height: int) -> None:
        super().__init__(halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, hexpand=False)
        self.set_size_request(width, height)
        self._path = path
        icon_img = mime_icon(path, int(min(width, height) * 0.6))
        icon_img.set_hexpand(True)
        self.append(icon_img)
        self._scale = device_scale()
        thumbnails.loader().request(path, width * self._scale, height * self._scale, self._loaded)

    def _loaded(self, pixbuf: object) -> None:
        if pixbuf is None:
            return
        texture = Gdk.Texture.new_for_pixbuf(pixbuf)
        paintable = texture if self._scale == 1 else ScaledPaintable(texture, self._scale)
        pic = Gtk.Picture.new_for_paintable(paintable)
        pic.set_content_fit(Gtk.ContentFit.CONTAIN)
        pic.set_can_shrink(True)
        pic.set_hexpand(True)
        pic.add_css_class("qs-thumb")
        clear(self)
        self.append(pic)


def file_meta(path: Path) -> str:
    """「大小 · 類型」。"""
    kind = "資料夾" if path.is_dir() else filetypes.describe(filetypes.mime_of(path))
    return f"{format_size(size_of(path))} · {kind}"


def file_dialog_filter(types: Sequence[str], title: str) -> Gtk.FileFilter | None:
    """工具的輸入類型 → 選檔視窗的篩選（包含 item 時不篩選）。"""
    if not types or "item" in types:
        return None
    flt = Gtk.FileFilter(name=title)
    for t in types:
        _add_type(flt, t)
    return flt


def _add_type(flt: Gtk.FileFilter, t: str) -> None:
    groups = {
        "archive": filetypes.ARCHIVE_MIMES,
        "word": filetypes.WORD_MIMES,
        "excel": filetypes.EXCEL_MIMES,
        "powerpoint": filetypes.POWERPOINT_MIMES,
    }
    if t == "folder":
        return
    if t.startswith("."):
        flt.add_suffix(t[1:])
    elif t in groups:
        for m in sorted(groups[t]):
            flt.add_mime_type(m)
    elif t == "text/plain":
        flt.add_mime_type("text/*")
        flt.add_mime_type("application/json")
        flt.add_mime_type("application/xml")
    else:
        flt.add_mime_type(t)


def idle(func: Callable[[], None]) -> None:
    """在主執行緒稍後執行一次。"""
    GLib.idle_add(lambda: func() and False)
