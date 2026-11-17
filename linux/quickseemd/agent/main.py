"""背景程式的進入點：python3 -m quickseemd.agent。

Gtk.Application（IS_SERVICE），D-Bus 自動啟動與登入時自動啟動都跑同一個指令；
已經有一份在執行時，新的這份直接結束。程式一直保持執行（系統匣與輪盤要常駐），
收到 Quit 或選「結束 QUICK_SEEMD」才結束。
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Callable, Sequence
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from ..core import ipc, settings  # noqa: E402
from ..core.wheel import WheelItem  # noqa: E402
from . import launcher  # noqa: E402
from .clipboard import ClipboardOwner  # noqa: E402
from .file_actions_ui import FileActionsUI  # noqa: E402
from .hud import HudController, HudWindow  # noqa: E402
from .queue import JobQueue, QueueItem, Status  # noqa: E402
from .service import AgentService  # noqa: E402
from .settings_watch import FileWatcher, changed_keys  # noqa: E402
from .shelf import ShelfWindow  # noqa: E402
from .tray import Tray  # noqa: E402
from .wheel_setup import Wheel  # noqa: E402

log = logging.getLogger("quickseemd.agent")

FILE_MANAGER = ("org.freedesktop.FileManager1", "/org/freedesktop/FileManager1")
PICK_TITLE = "選擇檔案，接著挑選要使用的工具"


def call_soon(fn: Callable[[], None]) -> None:
    """排到主迴圈執行（其他執行緒的回呼一律經過這裡）。"""

    def run() -> bool:
        try:
            fn()
        except Exception:  # noqa: BLE001 — 主迴圈裡的錯誤只記錄，不讓程式掛掉
            log.exception("主迴圈工作出錯")
        return False

    GLib.idle_add(run)


class AgentApp(Gtk.Application):
    def __init__(self) -> None:
        super().__init__(application_id=ipc.AGENT_BUS_NAME, flags=Gio.ApplicationFlags.IS_SERVICE)
        self._service = AgentService(self, dispatch=call_soon)
        self._values = settings.load()
        self._ready = False

    # ------------------------------------------------------------ Gtk.Application

    def do_dbus_register(self, connection: Gio.DBusConnection, object_path: str) -> bool:
        self._service.register(connection)
        return Gtk.Application.do_dbus_register(self, connection, object_path)

    def do_dbus_unregister(self, connection: Gio.DBusConnection, object_path: str) -> None:
        self._service.unregister(connection)
        Gtk.Application.do_dbus_unregister(self, connection, object_path)

    def do_startup(self) -> None:
        Gtk.Application.do_startup(self)
        self.hold()
        self._build()
        self._ready = True
        log.info("背景程式已啟動")

    def do_activate(self) -> None:
        return None

    def _build(self) -> None:
        self.clipboard = ClipboardOwner()
        self.hud = HudWindow(self)
        hud_ctl = HudController(self.hud, on_item_finished=self._item_finished)
        self.queue = JobQueue(hud_ctl, dispatch=call_soon)
        hud_ctl.attach(self.queue)
        self.shelf = ShelfWindow()
        self.file_actions = FileActionsUI(self.queue, self.shelf, self.clipboard)
        self.tray = Tray(self._tray_action)
        self._update_tray()
        self.wheel = Wheel(self._wheel_chosen, lambda: self._values, call_soon)
        self.wheel.start(self.clipboard.widget, enabled=bool(self._values["wheel.enabled"]))
        self._watcher = FileWatcher(
            [settings.settings_path(), settings.autostart_path()], self._settings_changed
        )
        settings.config_dir().mkdir(parents=True, exist_ok=True)
        self._watcher.start()

    # ------------------------------------------------------------ D-Bus（AgentHandler）

    def run_action(self, action: str, paths: list[Path]) -> None:
        self.queue.run_action(action, paths)

    def convert(self, fmt: str, paths: list[Path]) -> None:
        self.queue.convert(fmt, paths)

    def notify(self, title: str, icon: str, message: str) -> None:
        self.queue.notify(title, icon or "info.circle", message)

    def add_to_shelf(self, paths: list[Path]) -> None:
        self.shelf.add_files(paths)

    def quit(self) -> None:  # noqa: D102 — 覆寫 Gio.Application.quit：先放掉 hold
        log.info("背景程式結束")
        if hasattr(self, "queue"):
            self.queue.shutdown()
        if self._ready:
            self._ready = False
            self.release()
        Gio.Application.quit(self)

    # ------------------------------------------------------------ 進度面板（HudActions）

    def cancel(self, item_id: int) -> None:
        self.queue.cancel(item_id)

    def dismiss(self, item_id: int) -> None:
        self.queue.dismiss(item_id)

    def reveal(self, paths: Sequence[Path]) -> None:
        uris = [Path(p).as_uri() for p in paths]
        conn = self.get_dbus_connection()
        if conn is None:
            self._open_folder(paths)
            return
        conn.call(
            FILE_MANAGER[0],
            FILE_MANAGER[1],
            FILE_MANAGER[0],
            "ShowItems",
            GLib.Variant("(ass)", (uris, "")),
            None,
            Gio.DBusCallFlags.NONE,
            5000,
            None,
            self._on_reveal_done,
            list(paths),
        )

    def _on_reveal_done(
        self, conn: Gio.DBusConnection, result: Gio.AsyncResult, paths
    ) -> None:  # noqa: ANN001
        try:
            conn.call_finish(result)
        except GLib.Error as exc:
            log.warning("檔案管理員無法顯示項目，改開資料夾：%s", exc.message)
            self._open_folder(paths)

    def _open_folder(self, paths: Sequence[Path]) -> None:
        if paths:
            self.open_files([Path(paths[0]).parent])

    def open_files(self, paths: Sequence[Path]) -> None:
        for p in paths:
            try:
                Gio.AppInfo.launch_default_for_uri(Path(p).as_uri(), None)
            except GLib.Error as exc:
                log.error("無法開啟 %s：%s", Path(p).name, exc.message)

    def adjust(self, tool_id: str, files: Sequence[Path]) -> None:
        argv, env = launcher.open_files_command(files, tool_id=tool_id)
        launcher.spawn(argv, env)

    def _item_finished(self, item: QueueItem) -> None:
        """工具要求拷貝的文字（例如 QR Code 內容）：背景程式一直在，剪貼簿就一直有效。"""
        if item.status is Status.DONE and item.clipboard:
            self.clipboard.set_text(item.clipboard)

    # ------------------------------------------------------------ 輪盤

    def _wheel_chosen(self, item: WheelItem | None, paths: list[Path]) -> None:
        if item is None or item.kind == "more":
            argv, env = launcher.open_files_command(paths)
            launcher.spawn(argv, env)
        elif item.kind == "action":
            self.queue.run_action(item.payload, paths)
        elif item.kind == "format":
            self.queue.convert(item.payload, paths)
        elif item.kind == "file":
            self.file_actions.perform(item.payload, paths)

    # ------------------------------------------------------------ 系統匣

    def _update_tray(self) -> None:
        self.tray.update(
            visible=bool(self._values["tray.show"]),
            wheel_enabled=bool(self._values["wheel.enabled"]),
            autostart_on=launcher.autostart_enabled(),
        )

    def _tray_action(self, action: str) -> None:
        if action in ("open-main", "settings"):
            launcher.spawn(*launcher.app_command())
        elif action == "pick-files":
            self._pick_files()
        elif action.startswith("tool:"):
            argv, env = launcher.open_files_command([], tool_id=action.split(":", 1)[1])
            launcher.spawn(argv, env)
        elif action == "toggle-wheel":
            settings.save({"wheel.enabled": not self._values["wheel.enabled"]})
            self._settings_changed()
        elif action == "toggle-autostart":
            launcher.set_autostart(not launcher.autostart_enabled())
            self._update_tray()
        elif action == "quit":
            self.quit()

    def _pick_files(self) -> None:
        dialog = Gtk.FileChooserDialog(title=PICK_TITLE, action=Gtk.FileChooserAction.OPEN)
        dialog.add_button("取消", Gtk.ResponseType.CANCEL)
        dialog.add_button("選擇", Gtk.ResponseType.ACCEPT)
        dialog.set_default_response(Gtk.ResponseType.ACCEPT)
        dialog.set_select_multiple(True)
        dialog.set_keep_above(True)
        dialog.connect("response", self._on_pick_response)
        dialog.present()

    def _on_pick_response(self, dialog: Gtk.Dialog, response: int) -> None:
        names = dialog.get_filenames() if response == Gtk.ResponseType.ACCEPT else []
        dialog.destroy()
        if names:
            argv, env = launcher.open_files_command([Path(n) for n in names])
            launcher.spawn(argv, env)

    # ------------------------------------------------------------ 設定

    def _settings_changed(self) -> None:
        new = settings.load()
        changed = changed_keys(self._values, new)
        self._values = new
        if "wheel.enabled" in changed:
            self.wheel.set_enabled(bool(new["wheel.enabled"]))
        self._update_tray()
        if changed:
            log.info("設定已更新：%s", ", ".join(sorted(changed)))


def _setup_logging() -> None:
    level = os.environ.get("QUICKSEEMD_LOG_LEVEL", "WARNING").upper()
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    log_file = os.environ.get("QUICKSEEMD_LOG_FILE")
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding="utf-8"))
        level = "DEBUG"
    logging.basicConfig(
        level=getattr(logging, level, logging.WARNING),
        format="%(asctime)s %(name)s %(levelname)s %(message)s",
        handlers=handlers,
    )


def main(argv: list[str] | None = None) -> int:
    _setup_logging()
    args = list(sys.argv if argv is None else argv)
    ok, _rest = Gtk.init_check(args)
    if not ok or Gdk.Display.get_default() is None:
        log.error("沒有圖形畫面，背景程式無法啟動")
        return 1
    app = AgentApp()
    try:
        app.register(None)
    except GLib.Error as exc:
        if "acquire bus name" in (exc.message or ""):
            # 已經有一份在執行（名稱被佔用）：這份直接結束
            log.info("背景程式已在執行")
            return 0
        log.error("無法登記 D-Bus 名稱：%s", exc.message)
        return 1
    return app.run(args[:1])
