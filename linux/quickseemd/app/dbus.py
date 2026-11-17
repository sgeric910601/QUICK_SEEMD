"""主程式呼叫其他程序的 D-Bus（非同步，失敗只記錄、不擋住介面）。

- 背景程式 dev.quickseemd.Agent：啟動時 Ping（可自動啟動，讓系統匣與輪盤活起來）、
  不在背景執行時 Quit（不自動啟動）。
- 預覽 org.nemo.Preview.ShowFile(uri, 視窗代碼, False)。
測試可以傳入自己的連線（私有匯流排）。
"""

from __future__ import annotations

import logging
from collections.abc import Callable

from gi.repository import Gio, GLib

from ..core import ipc

LOG = logging.getLogger(__name__)

PING_TIMEOUT_MS = 5000
QUIT_TIMEOUT_MS = 1000
PREVIEW_TIMEOUT_MS = 10000


def _with_bus(
    connection: Gio.DBusConnection | None,
    use: Callable[[Gio.DBusConnection], None],
    fail: Callable[[str], None],
) -> None:
    if connection is not None:
        use(connection)
        return

    def on_bus(_src: object, res: Gio.AsyncResult) -> None:
        try:
            conn = Gio.bus_get_finish(res)
        except GLib.Error as exc:
            fail(exc.message)
            return
        use(conn)

    Gio.bus_get(Gio.BusType.SESSION, None, on_bus)


def ping_agent(
    callback: Callable[[bool], None] | None = None,
    *,
    connection: Gio.DBusConnection | None = None,
    autostart: bool = True,
) -> None:
    """Ping 背景程式；callback(是否有回應)。"""
    done = callback or (lambda _ok: None)
    flags = Gio.DBusCallFlags.NONE if autostart else Gio.DBusCallFlags.NO_AUTO_START

    def on_reply(conn: Gio.DBusConnection, res: Gio.AsyncResult) -> None:
        try:
            conn.call_finish(res)
        except GLib.Error as exc:
            LOG.info("背景程式沒有回應：%s", exc.message)
            done(False)
            return
        done(True)

    def use(conn: Gio.DBusConnection) -> None:
        conn.call(
            ipc.AGENT_BUS_NAME,
            ipc.AGENT_OBJECT_PATH,
            ipc.AGENT_INTERFACE,
            "Ping",
            None,
            GLib.VariantType.new("(s)"),
            flags,
            PING_TIMEOUT_MS,
            None,
            on_reply,
        )

    def fail(message: str) -> None:
        LOG.info("連不上工作階段匯流排：%s", message)
        done(False)

    _with_bus(connection, use, fail)


def quit_agent(
    callback: Callable[[bool], None] | None = None,
    *,
    connection: Gio.DBusConnection | None = None,
) -> None:
    """請背景程式結束（不自動啟動）；callback(是否送達)。"""
    done = callback or (lambda _ok: None)

    def on_reply(conn: Gio.DBusConnection, res: Gio.AsyncResult) -> None:
        try:
            conn.call_finish(res)
        except GLib.Error as exc:
            LOG.info("無法通知背景程式結束：%s", exc.message)
            done(False)
            return
        done(True)

    def use(conn: Gio.DBusConnection) -> None:
        conn.call(
            ipc.AGENT_BUS_NAME,
            ipc.AGENT_OBJECT_PATH,
            ipc.AGENT_INTERFACE,
            "Quit",
            None,
            None,
            Gio.DBusCallFlags.NO_AUTO_START,
            QUIT_TIMEOUT_MS,
            None,
            on_reply,
        )

    _with_bus(connection, use, lambda _msg: done(False))


def preview_show_file(
    uri: str,
    xid: int,
    callback: Callable[[str | None], None],
    *,
    connection: Gio.DBusConnection | None = None,
) -> None:
    """叫預覽視窗顯示檔案；callback(錯誤訊息或 None)。"""

    def on_reply(conn: Gio.DBusConnection, res: Gio.AsyncResult) -> None:
        try:
            conn.call_finish(res)
        except GLib.Error as exc:
            callback(exc.message)
            return
        callback(None)

    def use(conn: Gio.DBusConnection) -> None:
        conn.call(
            ipc.PREVIEW_BUS_NAME,
            ipc.PREVIEW_OBJECT_PATH,
            ipc.PREVIEW_INTERFACE,
            "ShowFile",
            GLib.Variant("(sib)", (uri, int(xid), False)),
            None,
            Gio.DBusCallFlags.NONE,
            PREVIEW_TIMEOUT_MS,
            None,
            on_reply,
        )

    _with_bus(connection, use, callback)
