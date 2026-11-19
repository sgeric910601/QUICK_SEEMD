"""剪貼簿（GTK3）：同時提供多種格式（檔案管理員能貼上的檔案、純文字）。

PyGObject 的 Gtk.Clipboard 沒有 set_with_data，所以用一個看不見的元件擁有 CLIPBOARD 選取區，
別人要資料時再依格式回傳。背景程式一直在執行，剪貼簿內容就一直有效。
"""

from __future__ import annotations

import logging

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk  # noqa: E402

from .file_actions import TEXT_TARGETS  # noqa: E402

log = logging.getLogger(__name__)


class ClipboardOwner:
    def __init__(self) -> None:
        self._widget = Gtk.Invisible()
        self._widget.realize()
        self._payloads: dict[str, bytes] = {}
        self._widget.connect("selection-get", self._on_get)

    @property
    def widget(self) -> Gtk.Widget:
        return self._widget

    def set_payloads(self, payloads: dict[str, bytes]) -> bool:
        """放上多種格式的內容；回傳是否成功取得剪貼簿。"""
        selection = Gdk.SELECTION_CLIPBOARD
        Gtk.selection_clear_targets(self._widget, selection)
        for info, target in enumerate(payloads):
            Gtk.selection_add_target(self._widget, selection, Gdk.Atom.intern(target, False), info)
        self._payloads = dict(payloads)
        ok = Gtk.selection_owner_set(self._widget, selection, Gdk.CURRENT_TIME)
        if not ok:
            log.warning("無法取得剪貼簿")
        return bool(ok)

    def set_text(self, text: str) -> bool:
        data = text.encode("utf-8")
        return self.set_payloads({t: data for t in TEXT_TARGETS})

    def _on_get(self, _w: Gtk.Widget, data: Gtk.SelectionData, _info: int, _time: int) -> None:
        target = data.get_target()
        payload = self._payloads.get(target.name())
        if payload is not None:
            data.set(target, 8, payload)
