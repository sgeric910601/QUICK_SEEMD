"""工具箱的輸出位置（對應原版 OutputLocationPicker／ToolOutputSettings）。

設定鍵 tools.output_same_folder（預設 true）與 tools.output_folder（預設空字串）。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from ..core import settings  # noqa: E402
from .widgets import hbox, icon, label, vbox  # noqa: E402

LOG = logging.getLogger(__name__)
SAME_FOLDER = "與原始檔案相同的資料夾"


def picker_label(values: dict) -> str:
    folder = str(values.get("tools.output_folder") or "")
    if values.get("tools.output_same_folder", True) or not folder:
        return SAME_FOLDER
    return Path(folder).name or folder


def tilde(path: str) -> str:
    home = os.path.expanduser("~")
    return "~" + path[len(home) :] if path == home or path.startswith(home + os.sep) else path


class OutputPicker(Gtk.MenuButton):
    """「儲存到：…」選單：相同資料夾／上次選的資料夾／選擇其他資料夾…"""

    def __init__(self) -> None:
        super().__init__()
        self.add_css_class("flat")
        self._label = label("", "qs-dim", ellipsize="middle")
        self.set_child(hbox(6, icon("folder-symbolic", 16, "qs-dim"), self._label))
        self._pop = Gtk.Popover()
        self.set_popover(self._pop)
        self._pop.connect("show", lambda _p: self._fill())
        self.refresh()

    def refresh(self) -> None:
        self._label.set_label(f"儲存到：{picker_label(settings.load())}")

    def _row(self, text: str, checked: bool, on_click) -> Gtk.Button:  # noqa: ANN001
        btn = Gtk.Button()
        btn.add_css_class("flat")
        mark = icon("object-select-symbolic" if checked else "folder-symbolic", 16)
        btn.set_child(hbox(8, mark, label(text, ellipsize="middle")))
        btn.connect("clicked", lambda _b: (self._pop.popdown(), on_click()))
        return btn

    def _fill(self) -> None:
        s = settings.load()
        same = bool(s["tools.output_same_folder"]) or not s["tools.output_folder"]
        box = vbox(2, margin_top=4, margin_bottom=4)
        box.append(self._row(SAME_FOLDER, same, lambda: self._save(True, None)))
        if s["tools.output_folder"]:
            folder = str(s["tools.output_folder"])
            box.append(self._row(tilde(folder), not same, lambda: self._save(False, None)))
        box.append(Gtk.Separator())
        box.append(self._row("選擇其他資料夾…", False, self._choose))
        self._pop.set_child(box)

    def _save(self, same: bool, folder: str | None) -> None:
        changes: dict = {"tools.output_same_folder": same}
        if folder is not None:
            changes["tools.output_folder"] = folder
        try:
            settings.save(changes)
        except (OSError, KeyError, TypeError):
            LOG.exception("無法儲存輸出位置")
        self.refresh()

    def _choose(self) -> None:
        dlg = Gtk.FileDialog(title="轉換後的檔案要存到哪裡？", accept_label="選擇", modal=True)
        dlg.select_folder(self.get_root(), None, self._picked)

    def _picked(self, dlg: Gtk.FileDialog, res: Gio.AsyncResult) -> None:
        try:
            f = dlg.select_folder_finish(res)
        except GLib.Error:
            return
        if f is not None and f.get_path():
            self._save(False, f.get_path())
