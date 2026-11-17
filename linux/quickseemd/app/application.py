"""QUICK_SEEMD 主程式（Adw.Application，單一實例）。

- `quickseemd open [--tool ID] [檔案…]` 由 D-Bus 轉給已經在跑的那一份（HANDLES_COMMAND_LINE），
  依原版規則打開工具或回到大廳選工具。
- 第一次開視窗時 Ping 背景程式（需要時自動啟動），讓系統匣與輪盤活起來。
- 關閉視窗就結束主程式（背景工作在背景程式裡）；設定為不在背景執行時順便請背景程式結束。
"""

from __future__ import annotations

import logging
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from .. import __version__  # noqa: E402
from ..core import ipc, registry, settings  # noqa: E402
from . import dbus, routing  # noqa: E402
from .cmdline import parse_open_args  # noqa: E402
from .state import AppState  # noqa: E402

LOG = logging.getLogger(__name__)

ACCELS: dict[str, list[str]] = {
    "app.open-files": ["<Control>o"],
    "app.preferences": ["<Control>comma"],
    "app.quit": ["<Control>q"],
    "win.close": ["<Control>w"],
}


class QuickSeemdApp(Adw.Application):
    def __init__(self) -> None:
        super().__init__(application_id=ipc.APP_ID, flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE)
        self.state = AppState()
        self.window = None
        self.agent_alive: bool | None = None

    # ------------------------------------------------------------ 啟動與命令列

    def do_startup(self) -> None:
        Adw.Application.do_startup(self)
        GLib.set_application_name("QUICK_SEEMD")
        for name, handler in (
            ("open-files", self.choose_files),
            ("preferences", self.show_preferences),
            ("about", self.show_about),
            ("quit", self.quit_app),
        ):
            action = Gio.SimpleAction.new(name, None)
            action.connect("activate", lambda _a, _p, h=handler: h())
            self.add_action(action)
        for action, accels in ACCELS.items():
            self.set_accels_for_action(action, accels)

    def do_command_line(self, cmdline: Gio.ApplicationCommandLine) -> int:
        args = list(cmdline.get_arguments() or [])[1:]
        req = parse_open_args(args, cmdline.get_cwd())
        win = self.ensure_window()
        route = routing.route_open(req, registry.all_tools())
        LOG.info(
            "開啟請求：%s %s（%d 個檔案）",
            route.kind,
            route.tool.id if route.tool else "-",
            len(route.files),
        )
        self.state.apply_route(route)
        win.present()
        return 0

    def do_activate(self) -> None:
        self.ensure_window().present()

    def ensure_window(self):  # noqa: ANN201
        if self.window is None:
            from .window import MainWindow  # noqa: PLC0415 — 用到才載入介面

            self.window = MainWindow(self, self.state)
            self.window.connect("close-request", self._on_close)
            close = Gio.SimpleAction.new("close", None)
            close.connect("activate", lambda *_a: self.window and self.window.close())
            self.window.add_action(close)
            dbus.ping_agent(self._on_ping)
        return self.window

    def _on_ping(self, ok: bool) -> None:
        self.agent_alive = ok

    def _on_close(self, _win: Gtk.Window) -> bool:
        self.window = None
        if not settings.load()["background.enabled"]:
            self.hold()
            dbus.quit_agent(lambda _ok: self.release())
        return False

    # ------------------------------------------------------------ 選單動作

    def choose_files(self) -> None:
        """「選擇檔案…」：選好後回到大廳，只列出能處理它們的工具。"""
        win = self.ensure_window()
        dlg = Gtk.FileDialog(
            title="選擇檔案，接著挑選要使用的工具", accept_label="選擇", modal=True
        )
        dlg.open_multiple(win, None, self._files_chosen)

    def _files_chosen(self, dlg: Gtk.FileDialog, res: Gio.AsyncResult) -> None:
        try:
            model = dlg.open_multiple_finish(res)
        except GLib.Error:
            return
        paths = [Path(model.get_item(i).get_path()) for i in range(model.get_n_items())]
        self.state.receive([p for p in paths if p.exists()])

    def show_preferences(self) -> None:
        from .settings_dialog import SettingsDialog  # noqa: PLC0415

        SettingsDialog().present(self.ensure_window())

    def show_about(self) -> None:
        about = Adw.AboutDialog(
            application_name="QUICK_SEEMD",
            application_icon=ipc.APP_ID,
            version=__version__,
            comments="萬用預覽・轉檔・工具箱",
        )
        about.present(self.ensure_window())

    def quit_app(self) -> None:
        if self.window is not None:
            self.window.close()
        self.quit()
