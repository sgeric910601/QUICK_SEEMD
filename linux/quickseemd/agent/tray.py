"""系統匣圖示（AyatanaAppIndicator3；對應原版選單列圖示）。

選單內容由 tray_model 決定；設定改變時整份重建。沒有安裝指示器函式庫時就不顯示圖示。
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from . import tray_model as tm  # noqa: E402

log = logging.getLogger(__name__)

INDICATOR_ID = "dev.quickseemd.Agent"
ICON_NAME = "quickseemd-symbolic"
FALLBACK_ICON = "view-app-grid-symbolic"

ActionFn = Callable[[str], None]


def _indicator_module():  # noqa: ANN202
    try:
        gi.require_version("AyatanaAppIndicator3", "0.1")
        from gi.repository import AyatanaAppIndicator3  # noqa: PLC0415

        return AyatanaAppIndicator3
    except (ImportError, ValueError) as exc:
        log.warning("沒有 AyatanaAppIndicator3，系統匣圖示停用：%s", exc)
        return None


def build_gtk_menu(entries: tuple[tm.MenuEntry, ...], on_action: ActionFn) -> Gtk.Menu:
    menu = Gtk.Menu()
    for entry in entries:
        menu.append(_menu_item(entry, on_action))
    menu.show_all()
    return menu


def _menu_item(entry: tm.MenuEntry, on_action: ActionFn) -> Gtk.MenuItem:
    if entry.kind == "separator":
        return Gtk.SeparatorMenuItem()
    label = tm.escape_mnemonic(entry.label)
    if entry.kind == "check":
        item = Gtk.CheckMenuItem.new_with_mnemonic(label)
        item.set_active(entry.active)  # 先設狀態再接訊號，避免建立時就觸發
        item.connect("activate", lambda _i, a=entry.action: on_action(a))
        return item
    item = Gtk.MenuItem.new_with_mnemonic(label)
    if entry.kind == "header":
        item.set_sensitive(False)
    elif entry.kind == "submenu":
        sub = Gtk.Menu()
        for child in entry.children:
            sub.append(_menu_item(child, on_action))
        item.set_submenu(sub)
        item.set_sensitive(bool(entry.children))
    else:
        item.connect("activate", lambda _i, a=entry.action: on_action(a))
    return item


class Tray:
    def __init__(self, on_action: ActionFn) -> None:
        self._on_action = on_action
        self._indicator = None
        module = _indicator_module()
        if module is None:
            return
        theme = Gtk.IconTheme.get_default()
        icon = ICON_NAME if theme.has_icon(ICON_NAME) else FALLBACK_ICON
        self._module = module
        self._indicator = module.Indicator.new(
            INDICATOR_ID, icon, module.IndicatorCategory.APPLICATION_STATUS
        )
        self._indicator.set_title(tm.TOOLTIP)

    @property
    def available(self) -> bool:
        return self._indicator is not None

    def update(self, *, visible: bool, wheel_enabled: bool, autostart_on: bool) -> None:
        if self._indicator is None:
            return
        entries = tm.build_menu(wheel_enabled=wheel_enabled, autostart_on=autostart_on)
        self._indicator.set_menu(build_gtk_menu(entries, self._on_action))
        status = self._module.IndicatorStatus
        self._indicator.set_status(status.ACTIVE if visible else status.PASSIVE)
