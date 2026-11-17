"""桌面動作：在資料夾中顯示、用預設程式開啟。

- 「在資料夾中顯示」走 D-Bus org.freedesktop.FileManager1.ShowItems（這台導向 Nemo）；
  失敗時改成打開所在的資料夾。
- 測試防護（環境變數 QS_TEST_GUARD=1）下絕不真的開程式，只記在 TEST_LOG。
"""

from __future__ import annotations

import logging
import os
from collections.abc import Sequence
from pathlib import Path

from gi.repository import Gio, GLib

LOG = logging.getLogger(__name__)

FILEMANAGER_NAME = "org.freedesktop.FileManager1"
FILEMANAGER_PATH = "/org/freedesktop/FileManager1"
DBUS_TIMEOUT_MS = 5000
TEST_LOG: list[tuple[str, list[str]]] = []


def _guarded(action: str, paths: Sequence[Path]) -> bool:
    if os.environ.get("QS_TEST_GUARD") == "1":
        TEST_LOG.append((action, [str(p) for p in paths]))
        return True
    return False


def _existing(paths: Sequence[Path]) -> list[Path]:
    return [Path(p) for p in paths if Path(p).exists()]


def _launch_uri(uri: str) -> bool:
    try:
        return bool(Gio.AppInfo.launch_default_for_uri(uri, None))
    except GLib.Error as exc:
        LOG.warning("無法開啟 %s：%s", uri, exc.message)
        return False


def _reveal_fallback(paths: list[Path]) -> None:
    for folder in dict.fromkeys(p.parent for p in paths):
        _launch_uri(folder.as_uri())


def reveal(paths: Sequence[Path]) -> None:
    """在檔案管理員中顯示並選取這些檔案。"""
    items = _existing(paths)
    if not items or _guarded("reveal", items):
        return
    uris = [p.as_uri() for p in items]

    def on_reply(conn: Gio.DBusConnection, res: Gio.AsyncResult) -> None:
        try:
            conn.call_finish(res)
        except GLib.Error as exc:
            LOG.info("檔案管理員沒有回應，改開資料夾：%s", exc.message)
            _reveal_fallback(items)

    def on_bus(_src: object, res: Gio.AsyncResult) -> None:
        try:
            conn = Gio.bus_get_finish(res)
        except GLib.Error:
            _reveal_fallback(items)
            return
        conn.call(
            FILEMANAGER_NAME,
            FILEMANAGER_PATH,
            FILEMANAGER_NAME,
            "ShowItems",
            GLib.Variant("(ass)", (uris, "")),
            None,
            Gio.DBusCallFlags.NONE,
            DBUS_TIMEOUT_MS,
            None,
            on_reply,
        )

    Gio.bus_get(Gio.BusType.SESSION, None, on_bus)


def open_paths(paths: Sequence[Path]) -> None:
    """用預設程式開啟。"""
    items = _existing(paths)
    if not items or _guarded("open", items):
        return
    for p in items:
        _launch_uri(p.as_uri())
