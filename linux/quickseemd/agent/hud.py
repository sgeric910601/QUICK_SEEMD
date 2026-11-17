"""右上角的浮動進度面板（GTK3 彈出視窗；對應原版 QuickActionHUD）。

- 不搶焦點、蓋在所有視窗上，放在主螢幕可用範圍的右上角。
- 每一列：上色的圖示方塊、標題、關閉鈕，以及依狀態不同的內容（hud_model 決定文字與按鈕）。
- 7 秒後自動收起；滑鼠停在上面就再等一輪；有失敗的項目就一直留著。
"""

from __future__ import annotations

import logging
import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk, Pango  # noqa: E402

from . import hud_model as hm  # noqa: E402
from . import icons_gtk  # noqa: E402
from .queue import QueueItem, Status, auto_hide_decision  # noqa: E402

log = logging.getLogger(__name__)

CSS = b"""
.qs-hud { background-color: transparent; }
.qs-hud.qs-opaque { background-color: @theme_bg_color; }
.qs-hud-row {
  background-color: alpha(@theme_base_color, 0.97);
  border-radius: 14px;
  border: 1px solid alpha(@theme_fg_color, 0.10);
  box-shadow: 0 4px 12px alpha(black, 0.18);
  padding: 12px;
}
.qs-hud.qs-opaque .qs-hud-row { box-shadow: none; }
.qs-hud-title { font-weight: 600; }
.qs-hud-caption { font-size: 0.85em; }
.qs-hud-secondary { color: alpha(@theme_fg_color, 0.6); }
.qs-hud-success { color: #26a269; }
.qs-hud-warning { color: #e66100; }
button.qs-link, button.qs-dismiss {
  padding: 0 2px; min-height: 0; min-width: 0;
  border: none; background: none; box-shadow: none;
}
button.qs-link label { font-weight: 600; font-size: 0.85em; }
progressbar.qs-hud-progress trough, progressbar.qs-hud-progress progress { min-height: 4px; }
"""

ROW_SPACING = 8
WINDOW_PADDING = 10
TEXT_WIDTH = hm.HUD_WIDTH - 2 * WINDOW_PADDING - 2 * 12 - 34 - 12 - 2


class HudActions(Protocol):
    def cancel(self, item_id: int) -> None: ...

    def dismiss(self, item_id: int) -> None: ...

    def reveal(self, paths: Sequence[Path]) -> None: ...

    def open_files(self, paths: Sequence[Path]) -> None: ...

    def adjust(self, tool_id: str, files: Sequence[Path]) -> None: ...


_css_installed = False
_tint_classes: set[str] = set()


def install_css(screen: Gdk.Screen) -> None:
    global _css_installed
    if _css_installed:
        return
    provider = Gtk.CssProvider()
    provider.load_from_data(CSS)
    Gtk.StyleContext.add_provider_for_screen(
        screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
    )
    _css_installed = True


def tint_class(screen: Gdk.Screen, rgb: tuple[float, float, float]) -> str:
    """每種顏色一個樣式類別（進度條與連結按鈕用工具的顏色）。"""
    color = hm.rgb_hex(rgb)
    name = f"qs-tint-{color[1:]}"
    if name not in _tint_classes:
        css = (
            f".{name} progress {{ background-color: {color}; border-color: {color}; }}\n"
            f".{name} label {{ color: {color}; }}\n"
        )
        provider = Gtk.CssProvider()
        provider.load_from_data(css.encode())
        Gtk.StyleContext.add_provider_for_screen(
            screen, provider, Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1
        )
        _tint_classes.add(name)
    return name


def _label(text: str, *classes: str, lines: int = 1) -> Gtk.Label:
    label = Gtk.Label(label=text, xalign=0.0)
    label.set_ellipsize(Pango.EllipsizeMode.END)
    label.set_max_width_chars(1)
    label.set_hexpand(True)
    if lines > 1:
        label.set_line_wrap(True)
        label.set_line_wrap_mode(Pango.WrapMode.WORD_CHAR)
        label.set_lines(lines)
    for cls in classes:
        label.get_style_context().add_class(cls)
    return label


def _tile(item: QueueItem) -> Gtk.DrawingArea:
    """34×34 上色圓角方塊＋白色圖示。"""
    area = Gtk.DrawingArea()
    area.set_size_request(34, 34)
    area.set_valign(Gtk.Align.START)

    def draw(widget: Gtk.Widget, cr) -> bool:  # noqa: ANN001
        r = 9
        cr.new_sub_path()
        cr.arc(34 - r, r, r, -math.pi / 2, 0)
        cr.arc(34 - r, 34 - r, r, 0, math.pi / 2)
        cr.arc(r, 34 - r, r, math.pi / 2, math.pi)
        cr.arc(r, r, r, math.pi, 3 * math.pi / 2)
        cr.close_path()
        cr.set_source_rgb(*item.tint)
        cr.fill()
        surface = icons_gtk.symbolic_surface(item.icon, 16, (1, 1, 1, 1), widget)
        if surface is not None:
            cr.set_source_surface(surface, 9, 9)
            cr.paint()
        return True

    area.connect("draw", draw)
    return area


@dataclass
class _RowView:
    status: Status
    outputs: int
    widget: Gtk.Widget
    progress: Gtk.ProgressBar | None = None
    message: Gtk.Label | None = None


class HudWindow(Gtk.Window):
    """面板本身（只負責畫，不管排程）。"""

    def __init__(self, actions: HudActions) -> None:
        super().__init__(type=Gtk.WindowType.POPUP)
        self._actions = actions
        self._rows: dict[int, _RowView] = {}
        self.hovering = False
        screen = self.get_screen()
        install_css(screen)
        composited = screen.is_composited()
        visual = screen.get_rgba_visual()
        if composited and visual is not None:
            self.set_visual(visual)
            self.set_app_paintable(True)
        ctx = self.get_style_context()
        ctx.add_class("qs-hud")
        if not composited:
            ctx.add_class("qs-opaque")
        self.set_type_hint(Gdk.WindowTypeHint.NOTIFICATION)
        self.set_size_request(hm.HUD_WIDTH, -1)
        self._box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=ROW_SPACING)
        self._box.set_margin_start(WINDOW_PADDING)
        self._box.set_margin_end(WINDOW_PADDING)
        self._box.set_margin_top(WINDOW_PADDING)
        self._box.set_margin_bottom(WINDOW_PADDING)
        self.add(self._box)
        self._box.show()
        self.add_events(Gdk.EventMask.ENTER_NOTIFY_MASK | Gdk.EventMask.LEAVE_NOTIFY_MASK)
        self.connect("enter-notify-event", self._on_crossing, True)
        self.connect("leave-notify-event", self._on_crossing, False)

    def _on_crossing(self, _w: Gtk.Widget, event: Gdk.EventCrossing, inside: bool) -> bool:
        if event.detail != Gdk.NotifyType.INFERIOR:
            self.hovering = inside
        return False

    # ------------------------------------------------------------ 顯示

    def show_panel(self) -> None:
        display = self.get_display()
        monitor = display.get_primary_monitor() or display.get_monitor(0)
        if monitor is not None:
            wa = monitor.get_workarea()
            x, y = hm.hud_origin((wa.x, wa.y, wa.width, wa.height), hm.HUD_WIDTH)
            self.move(x, y)
        if not self.get_visible():
            self.show()

    def hide_panel(self) -> None:
        self.hovering = False
        self.hide()

    def set_items(self, items: Sequence[QueueItem]) -> None:
        wanted = {i.id for i in items}
        for item_id in [k for k in self._rows if k not in wanted]:
            self._rows.pop(item_id).widget.destroy()
        for index, item in enumerate(items):
            view = self._rows.get(item.id)
            if (
                view is not None
                and view.status is item.status
                and view.outputs == len(item.outputs)
            ):
                self._refresh(view, item)
            else:
                if view is not None:
                    view.widget.destroy()
                view = self._build_row(item)
                self._rows[item.id] = view
                self._box.pack_start(view.widget, False, False, 0)
            self._box.reorder_child(view.widget, index)
        self.resize(hm.HUD_WIDTH, 1)

    def _refresh(self, view: _RowView, item: QueueItem) -> None:
        if view.progress is not None:
            view.progress.set_fraction(item.progress)
        if view.message is not None:
            view.message.set_text(hm.state_line(item))

    # ------------------------------------------------------------ 每一列

    def _build_row(self, item: QueueItem) -> _RowView:
        frame = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        frame.get_style_context().add_class("qs-hud-row")
        frame.get_style_context().add_class(tint_class(self.get_screen(), item.tint))
        frame.pack_start(_tile(item), False, False, 0)
        column = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        column.set_size_request(TEXT_WIDTH, -1)
        column.pack_start(self._header(item), False, False, 0)
        view = _RowView(item.status, len(item.outputs), frame)
        self._fill_state(column, item, view)
        frame.pack_start(column, True, True, 0)
        frame.show_all()
        return view

    def _header(self, item: QueueItem) -> Gtk.Box:
        header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        header.pack_start(_label(item.title, "qs-hud-title"), True, True, 0)
        close = Gtk.Button.new_from_icon_name("window-close-symbolic", Gtk.IconSize.MENU)
        close.set_relief(Gtk.ReliefStyle.NONE)
        close.get_style_context().add_class("qs-dismiss")
        close.set_valign(Gtk.Align.START)
        close.connect("clicked", lambda _b: self._actions.dismiss(item.id))
        header.pack_end(close, False, False, 0)
        return header

    def _fill_state(self, column: Gtk.Box, item: QueueItem, view: _RowView) -> None:
        text = hm.state_line(item)
        style = hm.line_style(item)
        if style == "progress":
            bar = Gtk.ProgressBar(fraction=item.progress)
            bar.get_style_context().add_class("qs-hud-progress")
            column.pack_start(bar, False, False, 0)
            view.progress = bar
            line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            view.message = _label(text, "qs-hud-caption", "qs-hud-secondary")
            line.pack_start(view.message, True, True, 0)
            line.pack_end(self._button(item, "cancel"), False, False, 0)
            column.pack_start(line, False, False, 0)
            return
        if style == "secondary":
            view.message = _label(text, "qs-hud-caption", "qs-hud-secondary")
            column.pack_start(view.message, False, False, 0)
            return
        column.pack_start(self._status_line(item, text, style), False, False, 0)
        names = hm.buttons(item)
        if names:
            links = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
            for name in names:
                links.pack_start(self._button(item, name), False, False, 0)
            column.pack_start(links, False, False, 0)

    def _status_line(self, item: QueueItem, text: str, style: str) -> Gtk.Box:
        line = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        failed = style == "warning"
        icon = "dialog-warning-symbolic" if failed else "emblem-ok-symbolic"
        image = Gtk.Image.new_from_icon_name(icon, Gtk.IconSize.MENU)
        image.set_valign(Gtk.Align.START)
        cls = "qs-hud-warning" if failed else "qs-hud-success"
        image.get_style_context().add_class(cls)
        lines = 3 if failed else (2 if not item.outputs else 1)
        line.pack_start(image, False, False, 0)
        line.pack_start(_label(text, "qs-hud-caption", cls, lines=lines), True, True, 0)
        return line

    def _button(self, item: QueueItem, name: str) -> Gtk.Button:
        button = Gtk.Button(label=hm.BUTTON_LABELS[name])
        button.set_relief(Gtk.ReliefStyle.NONE)
        button.get_style_context().add_class("qs-link")
        button.set_valign(Gtk.Align.CENTER)
        button.connect("clicked", lambda _b: self._clicked(item, name))
        return button

    def _clicked(self, item: QueueItem, name: str) -> None:
        if name == "cancel":
            self._actions.cancel(item.id)
        elif name == "reveal":
            self._actions.reveal(item.outputs)
        elif name == "open":
            self._actions.open_files(item.outputs)
        elif name == "adjust":
            tool_id = hm.adjust_tool(item)
            if tool_id:
                self._actions.adjust(tool_id, item.files)


class HudController:
    """佇列的觀察者：顯示、更新、7 秒後收起（對應原版 scheduleHide）。"""

    def __init__(self, window: HudWindow, on_item_finished=None) -> None:  # noqa: ANN001
        self._window = window
        self._timer = 0
        self._queue = None
        self._on_item_finished = on_item_finished

    def attach(self, queue) -> None:  # noqa: ANN001
        self._queue = queue

    def queue_changed(self, items: tuple[QueueItem, ...]) -> None:
        self._window.set_items(items)

    def queue_shown(self) -> None:
        self._cancel_timer()
        self._window.show_panel()

    def queue_idle(self) -> None:
        self._cancel_timer()
        self._timer = GLib.timeout_add_seconds(hm.HIDE_SECONDS, self._on_timer)

    def queue_empty(self) -> None:
        self._cancel_timer()
        self._window.hide_panel()

    def item_finished(self, item: QueueItem) -> None:
        if self._on_item_finished is not None:
            self._on_item_finished(item)

    def _cancel_timer(self) -> None:
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0

    def _on_timer(self) -> bool:
        self._timer = 0
        items = self._queue.items if self._queue is not None else ()
        decision = auto_hide_decision(items, self._window.hovering)
        if decision == "wait":
            self.queue_idle()
        elif decision == "hide":
            self._window.hide_panel()
            if self._queue is not None:
                self._queue.clear()
        return False
