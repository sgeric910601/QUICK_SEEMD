"""主視窗側欄（對應原版 MainView 的 List）：工具／轉換／預覽與整合，底部是程式名稱。"""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, Gtk  # noqa: E402

from ..core import registry  # noqa: E402
from ..core.model import TINTS  # noqa: E402
from . import css  # noqa: E402
from .widgets import hbox, icon, label, sf_icon, vbox  # noqa: E402

ACCENT = (0.21, 0.52, 0.89)
APP_ICON = "dev.quickseemd.App"


def sidebar_items() -> list[tuple[str | None, str, str, tuple]]:
    """（項目代碼或 None＝段落標題, 文字, SF Symbol, 顏色）。"""
    items: list[tuple[str | None, str, str, tuple]] = [(None, "工具", "", ACCENT)]
    items.append(("all", "所有工具", "square.grid.2x2.fill", ACCENT))
    for c in registry.categories_with_tools():
        items.append((f"cat:{c.id}", c.title, c.icon, c.tint))
    items.append((None, "轉換", "", ACCENT))
    items.append(("converter", "萬用格式轉換", "arrow.triangle.2.circlepath", TINTS["teal"]))
    items.append((None, "預覽與整合", "", ACCENT))
    items.append(("preview", "預覽測試", "doc.viewfinder", TINTS["slate"]))
    items.append(("integration", "右鍵、拖曳與空白鍵預覽", "cursorarrow.click.2", TINTS["slate"]))
    return items


def app_menu() -> Gio.Menu:
    menu = Gio.Menu()
    first = Gio.Menu()
    first.append("選擇檔案…", "app.open-files")
    first.append("設定…", "app.preferences")
    menu.append_section(None, first)
    second = Gio.Menu()
    second.append("關於 QUICK_SEEMD", "app.about")
    second.append("結束 QUICK_SEEMD", "app.quit")
    menu.append_section(None, second)
    return menu


def app_icon_name() -> str:
    """程式圖示（安裝程式放的 dev.quickseemd.App；還沒安裝時用系統圖示）。"""
    display = Gdk.Display.get_default()
    if display is not None and Gtk.IconTheme.get_for_display(display).has_icon(APP_ICON):
        return APP_ICON
    return "applications-system-symbolic"


class Sidebar(Gtk.Box):
    def __init__(self, on_select: Callable[[str], None]) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, vexpand=True)
        self._on_select = on_select
        self._rows: dict[str, Gtk.ListBoxRow] = {}
        self._updating = False
        toolbar = Adw.ToolbarView()
        bar = Adw.HeaderBar()
        bar.set_title_widget(label("QUICK_SEEMD", "heading"))
        menu = Gtk.MenuButton(icon_name="open-menu-symbolic", menu_model=app_menu())
        menu.set_tooltip_text("主選單")
        bar.pack_end(menu)
        toolbar.add_top_bar(bar)
        self.list = Gtk.ListBox(selection_mode=Gtk.SelectionMode.SINGLE)
        self.list.add_css_class("navigation-sidebar")
        for key, text, symbol, tint in sidebar_items():
            self.list.append(self._row(key, text, symbol, tint))
        self.list.connect("row-selected", self._selected)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        scroll.set_child(self.list)
        content = vbox(0, scroll, self._footer())
        toolbar.set_content(content)
        self.append(toolbar)

    def _row(self, key: str | None, text: str, symbol: str, tint: tuple) -> Gtk.ListBoxRow:
        row = Gtk.ListBoxRow()
        if key is None:
            row.set_selectable(False)
            row.set_activatable(False)
            row.set_child(label(text, "qs-sidebar-heading"))
            return row
        img = sf_icon(symbol, 16, "qs-tinted")
        box = hbox(10, img, label(text, ellipsize="end"))
        box.add_css_class(css.tint_class(tint))
        row.set_child(box)
        row.qs_key = key  # type: ignore[attr-defined]
        self._rows[key] = row
        return row

    def _footer(self) -> Gtk.Widget:
        app_icon = icon(app_icon_name(), 30)
        text = vbox(
            1,
            label("QUICK_SEEMD", "heading"),
            label("萬用預覽・轉檔・工具箱", "qs-small", "qs-dim"),
        )
        box = hbox(
            10, app_icon, text, margin_top=14, margin_bottom=14, margin_start=14, margin_end=14
        )
        return box

    def _selected(self, _lb: Gtk.ListBox, row: Gtk.ListBoxRow | None) -> None:
        if self._updating or row is None:
            return
        key = getattr(row, "qs_key", None)
        if key:
            self._on_select(key)

    def show(self, key: str | None) -> None:
        """依目前頁面更新選取（打開工具時不選任何一列）。"""
        self._updating = True
        try:
            row = self._rows.get(key or "")
            if row is None:
                self.list.unselect_all()
            else:
                self.list.select_row(row)
        finally:
            self._updating = False
