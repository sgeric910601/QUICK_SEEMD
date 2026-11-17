"""「動作」輪盤的檔案動作：介面部分（對話框、剪貼簿、啟動程式）。"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path
from typing import Protocol

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402

from ..core.model import TINTS  # noqa: E402
from ..core.wheel import FILE_ACTIONS  # noqa: E402
from . import file_actions as fa  # noqa: E402
from . import launcher  # noqa: E402
from .clipboard import ClipboardOwner  # noqa: E402
from .queue import JobQueue  # noqa: E402

log = logging.getLogger(__name__)

ICONS = {item.payload: item.icon for item in FILE_ACTIONS}
OPEN_WITH_TITLE = next(i.title for i in FILE_ACTIONS if i.payload == "open-with")


class ShelfLike(Protocol):
    def add_files(self, paths: Sequence[Path]) -> None: ...


class FileActionsUI:
    def __init__(self, queue: JobQueue, shelf: ShelfLike, clipboard: ClipboardOwner) -> None:
        self._queue = queue
        self._shelf = shelf
        self._clipboard = clipboard

    def perform(self, kind: str, raw_paths: Sequence[Path]) -> None:
        paths = fa.existing_paths(raw_paths)
        if not paths:
            return
        handler = {
            "open-with": self._open_with,
            "shelf": self._shelf.add_files,
            "copy": self._copy,
            "move": self._move,
            "copy-path": self._copy_path,
            "terminal": self._terminal,
        }.get(kind)
        if handler is None:
            log.warning("不認得的檔案動作：%s", kind)
            return
        handler(paths)

    # ------------------------------------------------------------ 剪貼簿

    def _copy(self, paths: list[Path]) -> None:
        if self._clipboard.set_payloads(fa.clipboard_payloads(paths)):
            title, message = fa.copy_notice(paths)
            self._queue.notify(title, ICONS["copy"], message)

    def _copy_path(self, paths: list[Path]) -> None:
        text = fa.copy_path_payload(paths)
        if text is None:
            title, message = fa.UNSAFE_PATH_NOTICE
            self._queue.notify(title, ICONS["copy-path"], message)
            return
        if self._clipboard.set_text(text):
            title, message = fa.copy_path_notice(paths)
            self._queue.notify(title, ICONS["copy-path"], message)

    # ------------------------------------------------------------ 終端機

    def _terminal(self, paths: list[Path]) -> None:
        folder = fa.terminal_folder(paths[0])
        found = fa.terminal_command(folder)
        if found is None:
            log.warning("這台電腦沒有可用的終端機")
            return
        argv, cwd = found
        launcher.spawn(argv, cwd=cwd)

    # ------------------------------------------------------------ 用其他程式開啟

    def _open_with(self, paths: list[Path]) -> None:
        dialog = Gtk.AppChooserDialog.new(None, 0, Gio.File.new_for_path(str(paths[0])))
        dialog.set_title(OPEN_WITH_TITLE)
        dialog.set_keep_above(True)
        dialog.connect("response", self._on_open_with_response, paths)
        dialog.present()

    def _on_open_with_response(self, dialog: Gtk.Dialog, response: int, paths: list[Path]) -> None:
        app = dialog.get_app_info() if response == Gtk.ResponseType.OK else None
        dialog.destroy()
        if app is None:
            return
        files = [Gio.File.new_for_path(str(p)) for p in paths]
        context = Gdk.Display.get_default().get_app_launch_context()
        try:
            app.launch(files, context)
        except GLib.Error as exc:
            log.error("無法用 %s 開啟：%s", app.get_name(), exc.message)

    # ------------------------------------------------------------ 移動到…

    def _move(self, paths: list[Path]) -> None:
        dialog = Gtk.FileChooserDialog(
            title=fa.move_prompt(paths), action=Gtk.FileChooserAction.SELECT_FOLDER
        )
        dialog.add_button("取消", Gtk.ResponseType.CANCEL)
        dialog.add_button("移到這裡", Gtk.ResponseType.ACCEPT)
        dialog.set_default_response(Gtk.ResponseType.ACCEPT)
        dialog.set_create_folders(True)
        dialog.set_current_folder(str(paths[0].parent))
        dialog.set_keep_above(True)
        dialog.connect("response", self._on_move_response, paths)
        dialog.present()

    def _on_move_response(self, dialog: Gtk.Dialog, response: int, paths: list[Path]) -> None:
        chosen = dialog.get_filename() if response == Gtk.ResponseType.ACCEPT else None
        dialog.destroy()
        if not chosen:
            return
        self.enqueue_move(paths, Path(chosen))

    def enqueue_move(self, paths: list[Path], destination: Path) -> int:
        return self._queue.enqueue(
            fa.move_title(destination),
            ICONS["move"],
            TINTS["slate"],
            paths,
            None,
            lambda progress, done: fa.MoveJob(paths, destination, progress, done),
        )
