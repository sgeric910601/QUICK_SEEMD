"""整合頁與設定視窗共用的設定列（對應原版 WheelKeySettings 與各個 Toggle）。

全部直接讀寫 ~/.config/quickseemd/settings.json（背景程式監看這個檔案，改了立刻套用）。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from ..core import errors, settings  # noqa: E402
from . import integration  # noqa: E402
from .widgets import sf_icon  # noqa: E402

LOG = logging.getLogger(__name__)
Toast = Callable[[str], None]


def save(changes: dict[str, Any], toast: Toast | None = None) -> bool:
    try:
        settings.save(changes)
    except (OSError, KeyError, TypeError) as exc:
        LOG.exception("無法儲存設定")
        if toast:
            toast(f"無法儲存設定：{exc}")
        return False
    return True


def switch_row(
    title: str,
    key: str,
    subtitle: str = "",
    toast: Toast | None = None,
    on_change: Callable[[bool], None] | None = None,
) -> Adw.SwitchRow:
    row = Adw.SwitchRow(title=title, subtitle=subtitle)
    row.set_active(bool(settings.load()[key]))

    def changed(r: Adw.SwitchRow, _p: object) -> None:
        on = bool(r.get_active())
        if save({key: on}, toast) and on_change:
            on_change(on)

    row.connect("notify::active", changed)
    return row


def autostart_row(toast: Toast | None = None) -> Adw.SwitchRow:
    """「登入時自動啟動」：建立或移除 ~/.config/autostart 裡我們自己的那個檔案。"""
    row = Adw.SwitchRow(title="登入時自動啟動")
    row.set_active(integration.autostart_enabled())
    guard = {"busy": False}

    def changed(r: Adw.SwitchRow, _p: object) -> None:
        if guard["busy"]:
            return
        try:
            if r.get_active():
                integration.enable_autostart()
            else:
                integration.disable_autostart()
        except (errors.QSError, OSError) as exc:
            if toast:
                toast(str(exc))
        guard["busy"] = True
        r.set_active(integration.autostart_enabled())
        guard["busy"] = False

    row.connect("notify::active", changed)
    return row


class WheelKeys:
    """三個輪盤的按鍵選擇＋重複按鍵的警告。"""

    def __init__(self, toast: Toast | None = None) -> None:
        self.toast = toast
        self.rows: list[Adw.ComboRow] = []
        values = [v for v, _t in settings.WHEEL_KEYS]
        titles = [t for _v, t in settings.WHEEL_KEYS]
        current = settings.load()
        for mode, title, symbol in integration.WHEEL_ROWS:
            row = Adw.ComboRow(title=title, model=Gtk.StringList.new(titles))
            row.add_prefix(sf_icon(symbol, 16))
            key = f"wheel.key.{mode}"
            if current[key] in values:
                row.set_selected(values.index(current[key]))
            row.connect(
                "notify::selected", lambda r, _p, k=key: self._picked(k, values[r.get_selected()])
            )
            self.rows.append(row)
        self.warning = Adw.ActionRow(title=integration.WHEEL_CONFLICT_TEXT)
        self.warning.add_css_class("qs-warning")
        self.warning.add_prefix(Gtk.Image.new_from_icon_name("dialog-warning-symbolic"))
        self._update_warning()

    def _picked(self, key: str, value: str) -> None:
        save({key: value}, self.toast)
        self._update_warning()

    def _update_warning(self) -> None:
        self.warning.set_visible(integration.wheel_conflict(settings.load()))

    def set_visible(self, on: bool) -> None:
        for r in self.rows:
            r.set_visible(on)
        self.warning.set_visible(on and integration.wheel_conflict(settings.load()))

    def all_rows(self) -> list[Gtk.Widget]:
        return [*self.rows, self.warning]
