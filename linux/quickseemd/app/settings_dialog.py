"""設定視窗「QUICK_SEEMD 設定」（對應原版 SettingsView，Ctrl+,）。"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from .. import __version__  # noqa: E402
from ..core import settings  # noqa: E402
from . import integration  # noqa: E402
from .output_picker import OutputPicker  # noqa: E402
from .prefs_rows import WheelKeys, autostart_row, switch_row  # noqa: E402
from .widgets import label  # noqa: E402

OUTPUT_NOTE = "右鍵與拖曳轉換的結果一律存在原始檔案旁邊，不會覆蓋任何檔案。"
NO_UPDATE = "這一版沒有記錄更新來源，無法自動更新。"


class SettingsDialog(Adw.PreferencesDialog):
    def __init__(self) -> None:
        super().__init__(title="QUICK_SEEMD 設定")
        self.set_content_width(520)
        page = Adw.PreferencesPage()
        page.add(self._quick())
        page.add(self._run())
        page.add(self._update())
        page.add(self._output())
        self.add(page)

    def _say(self, text: str) -> None:
        self.add_toast(Adw.Toast(title=text))

    def _quick(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="快速轉換")
        self.keys = WheelKeys(self._say)
        wheel = switch_row(
            "拖曳轉換輪盤",
            "wheel.enabled",
            subtitle=integration.WHEEL_CAPTION,
            toast=self._say,
            on_change=self.keys.set_visible,
        )
        group.add(wheel)
        for row in self.keys.all_rows():
            group.add(row)
        self.keys.set_visible(bool(settings.load()["wheel.enabled"]))
        status, ok = integration.nemo_status(integration.nemo_extension_installed())
        nemo = Adw.ActionRow(title="右鍵一鍵轉換", subtitle=status)
        nemo.add_suffix(
            Gtk.Image.new_from_icon_name("emblem-ok-symbolic" if ok else "dialog-warning-symbolic")
        )
        group.add(nemo)
        return group

    def _run(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="執行")
        group.add(switch_row("關閉視窗後繼續在背景執行", "background.enabled", toast=self._say))
        group.add(switch_row("在系統匣顯示圖示", "tray.show", toast=self._say))
        group.add(autostart_row(self._say))
        return group

    def _update(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="更新", description=NO_UPDATE)
        row = Adw.ActionRow(title="目前版本")
        row.add_suffix(label(__version__, "qs-mono"))
        group.add(row)
        return group

    def _output(self) -> Adw.PreferencesGroup:
        group = Adw.PreferencesGroup(title="輸出", description=OUTPUT_NOTE)
        row = Adw.ActionRow(title="工具箱的輸出位置")
        picker = OutputPicker()
        picker.set_valign(Gtk.Align.CENTER)
        row.add_suffix(picker)
        group.add(row)
        return group
