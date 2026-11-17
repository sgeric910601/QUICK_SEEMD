"""空白鍵預覽程式本體（Gtk.Application，D-Bus 名稱 org.nemo.Preview）。

- 以 IS_SERVICE 模式執行，由 D-Bus 自動啟動；同一時間只有一份。
- 在 do_dbus_register 登記 org.nemo.Preview 介面（ShowFile、Close）。
- 收到 ShowFile 先立刻回覆，再交給 Controller 在背景準備內容。
- 沒有視窗時閒置 10 分鐘自動結束；收到 SIGTERM／SIGINT 正常結束。
"""

from __future__ import annotations

import logging
import os
import signal
import sys
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from typing import Any

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import Gio, GLib, Gtk  # noqa: E402

from ..core import ipc  # noqa: E402
from .content import Content  # noqa: E402
from .controller import Controller  # noqa: E402

log = logging.getLogger(__name__)

INACTIVITY_MS = 10 * 60 * 1000
WORKERS = 2


class GtkUi:
    """Controller 的視窗端：建立、更新、關閉 PreviewWindow。"""

    def __init__(self, app: Gtk.Application, on_close: Callable[[], None]) -> None:
        self._app = app
        self._on_close = on_close
        self.window: Any = None

    def prepare(self, xid: int) -> None:
        from .window import PreviewWindow  # noqa: PLC0415

        if self.window is None:
            self.window = PreviewWindow(self._app, self._on_close)
        self.window.attach_to(xid)

    def show_loading(self, title: str, text: str) -> None:
        if self.window is not None:
            self.window.show_loading(title, text)

    def show_content(self, c: Content) -> None:
        if self.window is not None:
            self.window.show_content(c)

    def present(self) -> None:
        if self.window is not None:
            self.window.present_now()

    def close_window(self) -> None:
        if self.window is not None:
            window, self.window = self.window, None
            window.destroy()


class PreviewApp(Gtk.Application):
    def __init__(self) -> None:
        super().__init__(application_id=ipc.PREVIEW_BUS_NAME, flags=Gio.ApplicationFlags.IS_SERVICE)
        self.set_inactivity_timeout(INACTIVITY_MS)
        self._pool = ThreadPoolExecutor(max_workers=WORKERS, thread_name_prefix="preview")
        self._registration = 0
        self.ui = GtkUi(self, self._close_requested)
        self.controller = Controller(self.ui, self._background, self._later)

    # ------------------------------------------------------------ 生命週期

    def do_startup(self) -> None:
        Gtk.Application.do_startup(self)
        for signum in (signal.SIGTERM, signal.SIGINT):
            GLib.unix_signal_add(GLib.PRIORITY_DEFAULT, signum, self._on_signal)

    def do_activate(self) -> None:
        """以服務模式執行，沒有主視窗；視窗只在 ShowFile 時出現。"""

    def do_shutdown(self) -> None:
        self.ui.close_window()
        self._pool.shutdown(wait=False, cancel_futures=True)
        Gtk.Application.do_shutdown(self)

    def _on_signal(self) -> bool:
        log.info("收到結束訊號，關閉預覽")
        self.quit()
        return GLib.SOURCE_REMOVE

    # ------------------------------------------------------------ D-Bus

    def do_dbus_register(self, connection: Gio.DBusConnection, object_path: str) -> bool:
        node = Gio.DBusNodeInfo.new_for_xml(ipc.PREVIEW_XML)
        self._registration = connection.register_object(
            ipc.PREVIEW_OBJECT_PATH, node.interfaces[0], self._on_call, None, None
        )
        return Gtk.Application.do_dbus_register(self, connection, object_path)

    def do_dbus_unregister(self, connection: Gio.DBusConnection, object_path: str) -> None:
        if self._registration:
            connection.unregister_object(self._registration)
            self._registration = 0
        Gtk.Application.do_dbus_unregister(self, connection, object_path)

    def _on_call(
        self,
        _connection: Gio.DBusConnection,
        _sender: str,
        _path: str,
        _interface: str,
        method: str,
        params: GLib.Variant,
        invocation: Gio.DBusMethodInvocation,
    ) -> None:
        if method == "ShowFile":
            uri, xid, close_if_shown = params.unpack()
            invocation.return_value(None)  # 先回覆，Nemo 不用等
            self.controller.show_file(uri, xid, close_if_shown)
        elif method == "Close":
            invocation.return_value(None)
            self.controller.close()
        else:
            invocation.return_dbus_error(
                "org.freedesktop.DBus.Error.UnknownMethod", f"不支援的方法：{method}"
            )

    def _close_requested(self) -> None:
        self.controller.close()

    # ------------------------------------------------------------ 排程

    def _background(self, job: Callable[[], Any], done: Callable[[Any], None]) -> None:
        future = self._pool.submit(job)
        future.add_done_callback(lambda f: GLib.idle_add(self._deliver, f, done))

    @staticmethod
    def _deliver(future: Future, done: Callable[[Any], None]) -> bool:
        if future.cancelled():
            return GLib.SOURCE_REMOVE
        exc = future.exception()
        if exc is not None:  # Controller 的工作本身會攔錯，這裡只是保險
            log.error("背景工作失敗：%s", exc)
            return GLib.SOURCE_REMOVE
        done(future.result())
        return GLib.SOURCE_REMOVE

    @staticmethod
    def _later(ms: int, fn: Callable[[], None]) -> None:
        def once() -> bool:
            fn()
            return GLib.SOURCE_REMOVE

        GLib.timeout_add(ms, once)


def _setup_logging() -> None:
    level = logging.DEBUG if os.environ.get("QUICKSEEMD_DEBUG") else logging.WARNING
    logging.basicConfig(
        level=level, format="quickseemd-preview %(levelname)s %(name)s: %(message)s"
    )


def main(argv: list[str] | None = None) -> int:
    _setup_logging()
    args = list(sys.argv if argv is None else argv)
    app = PreviewApp()
    # 只傳程式名稱：參數不交給 GApplication 解析
    return app.run(args[:1])
