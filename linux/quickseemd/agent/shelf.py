"""浮動暫存（GTK3；對應原版 FloatShelf）：先把檔案放在螢幕邊，之後再拖到別的地方。

- 永遠在最上層、出現在所有工作區，不搶焦點。視窗類型用 DOCK（Mutter 上 UTILITY 視窗會一直
  「要求注意」），沒有標題列，所以拖標題那一列自己移動視窗。
- 每一列：檔案圖示與名稱；滑鼠移上去出現移除鈕；雙按開啟；可以拖出去（只當成拷貝，不搬動原檔）。
- 可以把檔案拖進來加入；清空或移除最後一個就隱藏；不存檔。
"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk, Pango  # noqa: E402

from . import file_actions as fa  # noqa: E402
from . import shelf_model as sm  # noqa: E402
from .hud import install_css  # noqa: E402
from .wheel_model import parse_uri_list  # noqa: E402

log = logging.getLogger(__name__)

URI_LIST = "text/uri-list"
CSS = b"""
.qs-shelf {
  background-color: @theme_bg_color;
  border-radius: 14px;
  border: 1px solid alpha(@theme_fg_color, 0.12);
}
.qs-shelf.qs-drop-target { border: 2px solid @theme_selected_bg_color; }
.qs-shelf-title { font-weight: bold; }
.qs-shelf-count { font-weight: bold; font-size: 0.85em; color: alpha(@theme_fg_color, 0.6); }
.qs-shelf-row { border-radius: 8px; padding: 4px 6px; }
.qs-shelf-row.qs-hover { background-color: alpha(@theme_fg_color, 0.07); }
button.qs-shelf-button { padding: 0; min-height: 0; min-width: 0; border: none; background: none; }
"""


def _install_shelf_css(screen: Gdk.Screen) -> None:
    install_css(screen)
    provider = Gtk.CssProvider()
    provider.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_screen(
        screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )


def _flat_button(icon: str, tooltip: str) -> Gtk.Button:
    button = Gtk.Button.new_from_icon_name(icon, Gtk.IconSize.MENU)
    button.set_relief(Gtk.ReliefStyle.NONE)
    button.set_tooltip_text(tooltip)
    button.get_style_context().add_class("qs-shelf-button")
    button.set_valign(Gtk.Align.CENTER)
    return button


def _open(path: Path) -> None:
    try:
        Gio.AppInfo.launch_default_for_uri(path.as_uri(), None)
    except GLib.Error as exc:
        log.error("無法開啟 %s：%s", path.name, exc.message)


class ShelfWindow(Gtk.Window):
    def __init__(self) -> None:
        super().__init__(type=Gtk.WindowType.TOPLEVEL)
        self._model = sm.ShelfModel()
        self._placed = False
        self._move_from: tuple[float, float] | None = None
        _install_shelf_css(self.get_screen())
        self.set_title(sm.SHELF_TITLE)
        self.set_decorated(False)
        self.set_resizable(False)
        self.set_type_hint(Gdk.WindowTypeHint.DOCK)
        self.set_keep_above(True)
        self.stick()
        self.set_skip_taskbar_hint(True)
        self.set_skip_pager_hint(True)
        self.set_accept_focus(False)
        self.connect("delete-event", lambda *_a: self.hide() or True)
        self._frame = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._frame.get_style_context().add_class("qs-shelf")
        self._frame.pack_start(self._build_header(), False, False, 0)
        self._frame.pack_start(Gtk.Separator(), False, False, 0)
        self._list = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._list.set_margin_start(6)
        self._list.set_margin_end(6)
        self._list.set_margin_top(6)
        self._list.set_margin_bottom(6)
        scroller = Gtk.ScrolledWindow()
        scroller.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroller.add(self._list)
        self._frame.pack_start(scroller, True, True, 0)
        self.add(self._frame)
        self._setup_drop()

    @property
    def items(self) -> tuple[Path, ...]:
        return self._model.items

    # ------------------------------------------------------------ 對外

    def add_files(self, paths: Sequence[Path]) -> None:
        self._set_model(self._model.add(fa.existing_paths(paths)))

    def remove_item(self, path: Path) -> None:
        self._set_model(self._model.remove(path))

    def clear(self) -> None:
        self._set_model(self._model.clear())

    def _set_model(self, model: sm.ShelfModel) -> None:
        self._model = model
        self._rebuild()
        if not model.items:
            self.hide()
            return
        height = sm.shelf_height(len(model.items))
        self.set_size_request(sm.SHELF_WIDTH, height)
        self.resize(sm.SHELF_WIDTH, height)
        if not self._placed:
            self._place()
        self.show_all()
        self._refresh_hover()

    def _place(self) -> None:
        display = self.get_display()
        monitor = display.get_primary_monitor() or display.get_monitor(0)
        if monitor is None:
            return
        wa = monitor.get_workarea()
        self.move(*sm.shelf_origin((wa.x, wa.y, wa.width, wa.height)))
        self._placed = True

    # ------------------------------------------------------------ 標題列（可拖動視窗）

    def _build_header(self) -> Gtk.EventBox:
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_top(9)
        box.set_margin_bottom(9)
        box.pack_start(
            Gtk.Image.new_from_icon_name("folder-download-symbolic", Gtk.IconSize.MENU),
            False,
            False,
            0,
        )
        title = Gtk.Label(label=sm.SHELF_TITLE)
        title.get_style_context().add_class("qs-shelf-title")
        box.pack_start(title, False, False, 0)
        self._count = Gtk.Label(label="0")
        self._count.get_style_context().add_class("qs-shelf-count")
        box.pack_start(self._count, False, False, 0)
        clear = _flat_button("window-close-symbolic", sm.CLEAR_TOOLTIP)
        clear.connect("clicked", lambda _b: self.clear())
        box.pack_end(clear, False, False, 0)
        header = Gtk.EventBox()
        header.add(box)
        header.add_events(Gdk.EventMask.BUTTON_PRESS_MASK | Gdk.EventMask.BUTTON1_MOTION_MASK)
        header.connect("button-press-event", self._on_header_press)
        header.connect("motion-notify-event", self._on_header_motion)
        return header

    def _on_header_press(self, _w: Gtk.Widget, event: Gdk.EventButton) -> bool:
        if event.button != 1:
            return False
        x, y = self.get_position()
        self._move_from = (event.x_root - x, event.y_root - y)
        return True

    def _on_header_motion(self, _w: Gtk.Widget, event: Gdk.EventMotion) -> bool:
        if self._move_from is None or not event.state & Gdk.ModifierType.BUTTON1_MASK:
            return False
        dx, dy = self._move_from
        self.move(int(event.x_root - dx), int(event.y_root - dy))
        return True

    # ------------------------------------------------------------ 列

    def _rebuild(self) -> None:
        for child in self._list.get_children():
            child.destroy()
        for path in self._model.items:
            self._list.pack_start(self._build_row(path), False, False, 0)
        self._count.set_text(str(len(self._model.items)))

    def _build_row(self, path: Path) -> Gtk.EventBox:
        row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        row.get_style_context().add_class("qs-shelf-row")
        image = Gtk.Image.new_from_gicon(_file_icon(path), Gtk.IconSize.DND)
        image.set_pixel_size(30)
        row.pack_start(image, False, False, 0)
        name = Gtk.Label(label=path.name or str(path), xalign=0.0)
        name.set_ellipsize(Pango.EllipsizeMode.MIDDLE)
        name.set_max_width_chars(1)
        name.set_hexpand(True)
        row.pack_start(name, True, True, 0)
        remove = _flat_button("list-remove-symbolic", sm.REMOVE_TOOLTIP)
        remove.connect("clicked", lambda _b: self.remove_item(path))
        remove.set_no_show_all(True)
        row.pack_end(remove, False, False, 0)
        box = Gtk.EventBox()
        box.add(row)
        box.set_tooltip_text(sm.ROW_TOOLTIP)
        box.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        box.connect("enter-notify-event", self._on_row_crossing, row, remove, True)
        box.connect("leave-notify-event", self._on_row_crossing, row, remove, False)
        box.connect("button-press-event", self._on_row_press, path)
        target = Gtk.TargetEntry.new(URI_LIST, 0, 0)
        box.drag_source_set(Gdk.ModifierType.BUTTON1_MASK, [target], Gdk.DragAction.COPY)
        box.drag_source_set_icon_gicon(_file_icon(path))
        box.connect("drag-data-get", lambda _w, _c, data, _i, _t: data.set_uris([path.as_uri()]))
        return box

    def _on_row_crossing(self, _w, event, row, remove, inside: bool) -> bool:  # noqa: ANN001
        if event.detail == Gdk.NotifyType.INFERIOR:
            return False
        ctx = row.get_style_context()
        if inside:
            ctx.add_class("qs-hover")
            remove.show()
        else:
            ctx.remove_class("qs-hover")
            remove.hide()
        return False

    def _on_row_press(self, _w: Gtk.Widget, event: Gdk.EventButton, path: Path) -> bool:
        if event.button == 1 and event.type == Gdk.EventType._2BUTTON_PRESS:
            _open(path)
            return True
        return False

    def _refresh_hover(self) -> None:
        for box in self._list.get_children():
            remove = box.get_child().get_children()[-1]
            remove.hide()

    # ------------------------------------------------------------ 接收拖放

    def _setup_drop(self) -> None:
        target = Gtk.TargetEntry.new(URI_LIST, 0, 0)
        self.drag_dest_set(Gtk.DestDefaults.ALL, [target], Gdk.DragAction.COPY)
        self.connect("drag-data-received", self._on_drop)
        self.connect("drag-motion", lambda *_a: self._highlight(True) or False)
        self.connect("drag-leave", lambda *_a: self._highlight(False))

    def _highlight(self, on: bool) -> None:
        ctx = self._frame.get_style_context()
        if on:
            ctx.add_class("qs-drop-target")
        else:
            ctx.remove_class("qs-drop-target")

    def _on_drop(self, _w, _ctx, _x, _y, data, _info, _t) -> None:  # noqa: ANN001
        self._highlight(False)
        raw = data.get_data() if data is not None else b""
        paths = parse_uri_list(bytes(raw or b""))
        if paths:
            self.add_files(paths)


def _file_icon(path: Path) -> Gio.Icon:
    try:
        info = Gio.File.new_for_path(str(path)).query_info(
            "standard::icon", Gio.FileQueryInfoFlags.NONE, None
        )
        icon = info.get_icon()
        if icon is not None:
            return icon
    except GLib.Error as exc:
        log.debug("讀不到檔案圖示：%s", exc.message)
    return Gio.ThemedIcon.new("text-x-generic")
