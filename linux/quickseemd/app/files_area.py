"""工具頁左側的檔案區（對應原版 filesArea／FileCard／AddMoreCard）。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gio, GLib, GObject, Gtk  # noqa: E402

from ..core.model import ToolDefinition  # noqa: E402
from . import routing  # noqa: E402
from .widgets import (  # noqa: E402
    FileThumb,
    file_meta,
    hbox,
    icon,
    label,
    link,
    set_warning,
    vbox,
    warning_row,
)

CARD_WIDTH = 150
DRAG_PREFIX = "qs-file:"


class FileCard(Gtk.Box):
    """縮圖、名稱（兩行、中間省略）、「大小 · 類型」；可排序時左上角有順序號碼。"""

    def __init__(
        self,
        path: Path,
        order: int | None = None,
        on_remove: Callable[[], None] | None = None,
        width: int = CARD_WIDTH,
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        self.path = path
        self.add_css_class("qs-file-card")
        self.set_size_request(width, -1)
        self.set_halign(Gtk.Align.CENTER)
        self.set_tooltip_text(str(path))
        over = Gtk.Overlay()
        thumb = FileThumb(path, 128, 146)
        thumb.set_size_request(-1, 150)
        over.set_child(thumb)
        if order is not None:
            badge = label(str(order), "qs-order", xalign=0.5)
            badge.set_halign(Gtk.Align.START)
            badge.set_valign(Gtk.Align.START)
            over.add_overlay(badge)
        if on_remove is not None:
            rm = Gtk.Button(icon_name="window-close-symbolic", tooltip_text="移除")
            rm.add_css_class("qs-remove")
            rm.set_halign(Gtk.Align.END)
            rm.set_valign(Gtk.Align.START)
            rm.connect("clicked", lambda _b: on_remove())
            over.add_overlay(rm)
        self.append(over)
        name = label(path.name, "qs-semibold", xalign=0.5, wrap=True, lines=2, ellipsize="middle")
        name.set_justify(Gtk.Justification.CENTER)
        name.set_size_request(-1, 36)
        name.set_valign(Gtk.Align.START)
        self.append(name)
        self.append(label(file_meta(path), "qs-small", "qs-dim", xalign=0.5, ellipsize="end"))


def add_more_card(on_click: Callable[[], None]) -> Gtk.Button:
    btn = Gtk.Button()
    btn.add_css_class("qs-addmore")
    plus = icon("list-add-symbolic", 26)
    circle = Gtk.Box(halign=Gtk.Align.CENTER, hexpand=False)
    circle.add_css_class("qs-plus-circle")
    plus.set_hexpand(True)
    circle.append(plus)
    box = vbox(10, circle, label("加入更多檔案", "qs-dim", xalign=0.5))
    box.set_valign(Gtk.Align.CENTER)
    btn.set_child(box)
    btn.set_size_request(CARD_WIDTH, 236)
    btn.set_halign(Gtk.Align.CENTER)
    btn.connect("clicked", lambda _b: on_click())
    return btn


class FilesArea(Gtk.Box):
    """檔案標題列＋卡片格線（或自訂畫面）。"""

    def __init__(
        self,
        tool: ToolDefinition,
        on_change: Callable[[list[Path]], None],
        on_pick: Callable[[], None],
    ) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, hexpand=True, vexpand=True)
        self.add_css_class("qs-files-bg")
        self.tool = tool
        self._on_change = on_change
        self._on_pick = on_pick
        self.files: list[Path] = []
        self._build_header()
        self.stack = Gtk.Stack(vexpand=True)
        self.grid = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True)
        self.grid.set_column_spacing(16)
        self.grid.set_row_spacing(16)
        self.grid.set_max_children_per_line(12)
        self.grid.set_valign(Gtk.Align.START)
        for side in ("top", "bottom", "start", "end"):
            getattr(self.grid, f"set_margin_{side}")(20)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        scroll.set_child(self.grid)
        self.stack.add_named(scroll, "grid")
        self.append(self.stack)
        self._install_actions()

    def _build_header(self) -> None:
        head = hbox(10, margin_start=20, margin_end=20)
        head.set_size_request(-1, 44)
        self.count = label("", "heading")
        head.append(self.count)
        self.hint = label("拖曳卡片可調整順序", "qs-dim")
        head.append(self.hint)
        menu = Gio.Menu()
        menu.append("依名稱 A → Z", "files.sort::az")
        menu.append("依名稱 Z → A", "files.sort::za")
        menu.append("反轉順序", "files.sort::reverse")
        self.sort_button = Gtk.MenuButton(menu_model=menu)
        self.sort_button.set_child(hbox(6, icon("view-sort-ascending-symbolic"), label("排序")))
        self.sort_button.add_css_class("flat")
        head.append(self.sort_button)
        self.note = warning_row()
        head.append(self.note)
        spacer = Gtk.Box(hexpand=True)
        head.append(spacer)
        self.remove_all = link("全部移除", on_click=lambda: self._on_change([]))
        head.append(self.remove_all)
        self.append(head)

    def _install_actions(self) -> None:
        group = Gio.SimpleActionGroup()
        sort = Gio.SimpleAction.new("sort", GLib.VariantType.new("s"))
        sort.connect("activate", self._sort)
        group.add_action(sort)
        self.insert_action_group("files", group)

    def _sort(self, _action: Gio.SimpleAction, mode: GLib.Variant) -> None:
        self._on_change(routing.sort_files(self.files, mode.get_string()))

    # ------------------------------------------------------------ 內容

    def set_custom(self, widget: Gtk.Widget | None) -> None:
        old = self.stack.get_child_by_name("custom")
        if old is not None:
            self.stack.remove(old)
        if widget is not None:
            self.stack.add_named(widget, "custom")
            self.stack.set_visible_child_name("custom")
        else:
            self.stack.set_visible_child_name("grid")

    def set_files(self, files: Sequence[Path]) -> None:
        self.files = list(files)
        n = len(self.files)
        reorder = self.tool.reorderable and n > 1
        self.count.set_label(routing.files_header(n))
        self.hint.set_visible(reorder)
        self.sort_button.set_visible(reorder)
        self._rebuild(reorder)

    def set_note(self, note: str | None) -> None:
        set_warning(self.note, note)

    def _rebuild(self, reorder: bool) -> None:
        self.grid.remove_all()
        for i, p in enumerate(self.files):
            card = FileCard(p, i + 1 if reorder else None, lambda p=p: self._remove(p))
            if reorder:
                self._make_draggable(card, i)
            self.grid.append(card)
        if self.tool.allows_multiple:
            self.grid.append(add_more_card(self._on_pick))

    def _remove(self, path: Path) -> None:
        self._on_change([f for f in self.files if f != path])

    def _make_draggable(self, card: Gtk.Widget, index: int) -> None:
        src = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
        value = GObject.Value(GObject.TYPE_STRING, f"{DRAG_PREFIX}{index}")
        src.connect("prepare", lambda *_a: Gdk.ContentProvider.new_for_value(value))
        src.connect("drag-begin", lambda *_a: card.set_opacity(0.35))
        src.connect("drag-end", lambda *_a: card.set_opacity(1.0))
        card.add_controller(src)
        dst = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        dst.connect("drop", lambda _t, v, _x, _y: self._dropped(v, index))
        card.add_controller(dst)

    def _dropped(self, value: object, target: int) -> bool:
        text = str(value or "")
        if not text.startswith(DRAG_PREFIX) or not text[len(DRAG_PREFIX) :].isdigit():
            return False
        src = int(text[len(DRAG_PREFIX) :])
        self._on_change(routing.move_item(self.files, src, target))
        return True
