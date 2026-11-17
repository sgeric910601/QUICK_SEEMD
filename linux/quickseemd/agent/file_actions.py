"""「動作」輪盤的檔案動作：純邏輯部分（文字、剪貼簿內容、終端機、移動）。

對應原版 WheelFileActions.swift。介面部分（對話框、剪貼簿擁有者）在 file_actions_ui.py。
AirDrop 在 Linux 沒有對應，改成「用其他程式開啟」。
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Callable, Iterable, Sequence
from pathlib import Path
from typing import Any

from ..core import errors, naming

log = logging.getLogger(__name__)

GNOME_TERMINAL = "/usr/bin/gnome-terminal"
FALLBACK_TERMINAL = "/usr/bin/x-terminal-emulator"

# 剪貼簿提供的格式（順序＝偏好順序；Nemo 讀 x-special/gnome-copied-files）
COPIED_FILES_TARGET = "x-special/gnome-copied-files"
URI_LIST_TARGET = "text/uri-list"
TEXT_TARGETS: tuple[str, ...] = ("UTF8_STRING", "text/plain;charset=utf-8", "text/plain")

ProgressFn = Callable[[float, "str | None"], None]
DoneFn = Callable[[dict[str, Any]], None]


def existing_paths(raw: Iterable[str | Path]) -> list[Path]:
    """只留下存在的絕對路徑（拖放與 D-Bus 送來的清單都要先過這關）。"""
    out: list[Path] = []
    for item in raw:
        s = os.fspath(item)
        if "\0" in s or not os.path.isabs(s):
            continue
        p = Path(os.path.normpath(s))
        if os.path.lexists(p):
            out.append(p)
    return out


def count_label(paths: Sequence[Path]) -> str:
    return f"「{paths[0].name}」" if len(paths) == 1 else f"{len(paths)} 個項目"


def copy_notice(paths: Sequence[Path]) -> tuple[str, str]:
    return "已拷貝", f"{count_label(paths)}，可到檔案管理員貼上"


UNSAFE_PATH_NOTICE = ("拷貝路徑", "有檔名包含控制字元，沒有拷貝。")
_KEEP_IN_CLIPBOARD = frozenset("\n\t")


def _is_control(ch: str) -> bool:
    """C0（含換行、Tab）、DEL 與 C1 控制字元。"""
    code = ord(ch)
    return code < 0x20 or 0x7F <= code <= 0x9F


def has_control_chars(text: str) -> bool:
    return any(_is_control(ch) for ch in text)


def clean_clipboard_text(text: str) -> str:
    """工具要求拷貝的文字：去掉控制字元（只留換行與 Tab），貼到終端機時不會夾帶跳脫序列。"""
    return "".join(ch for ch in text if ch in _KEEP_IN_CLIPBOARD or not _is_control(ch))


def copy_path_text(paths: Sequence[Path]) -> str:
    return "\n".join(str(p) for p in paths)


def copy_path_payload(paths: Sequence[Path]) -> str | None:
    """「拷貝路徑」的內容；有檔名含控制字元（例如換行）就不拷貝，免得貼到終端機變成好幾行指令。"""
    if any(has_control_chars(str(p)) for p in paths):
        return None
    return copy_path_text(paths)


def copy_path_notice(paths: Sequence[Path]) -> tuple[str, str]:
    return "已拷貝路徑", str(paths[0]) if len(paths) == 1 else f"{len(paths)} 個路徑"


def clipboard_payloads(paths: Sequence[Path]) -> dict[str, bytes]:
    """「拷貝」放進剪貼簿的內容：檔案管理員能貼上的檔案，以及純文字路徑。"""
    uris = [Path(p).as_uri() for p in paths]
    payloads = {
        COPIED_FILES_TARGET: ("copy\n" + "\n".join(uris)).encode("utf-8"),
        URI_LIST_TARGET: ("\r\n".join(uris) + "\r\n").encode("utf-8"),
    }
    # 檔案網址已經百分比編碼；純文字路徑只在沒有控制字元時才提供（和「拷貝路徑」同一規則）
    text = copy_path_payload(paths)
    if text is not None:
        payloads.update({t: text.encode("utf-8") for t in TEXT_TARGETS})
    return payloads


def move_prompt(paths: Sequence[Path]) -> str:
    return f"要把{count_label(paths)}移到哪裡？"


def move_title(destination: Path) -> str:
    return f"移動到「{destination.name or str(destination)}」"


def terminal_folder(path: Path) -> Path:
    """終端機要開在哪：資料夾本身，否則它所在的資料夾。"""
    return path if path.is_dir() else path.parent


def terminal_command(
    folder: Path, exists: Callable[[str], bool] = os.path.exists
) -> tuple[list[str], Path] | None:
    """開終端機的指令（絕對路徑，不經過 shell）；這台沒有終端機就回傳 None。"""
    if exists(GNOME_TERMINAL):
        return [GNOME_TERMINAL, f"--working-directory={folder}"], folder
    if exists(FALLBACK_TERMINAL):
        return [FALLBACK_TERMINAL], folder
    return None


def _is_inside(child: Path, parent: Path) -> bool:
    try:
        child.resolve().relative_to(parent.resolve())
    except ValueError:
        return False
    return True


def move_files(
    files: Sequence[Path], destination: Path, progress: ProgressFn, cancel: threading.Event
) -> list[Path]:
    """「移動到…」：已經在目的地的略過，其餘不覆蓋地搬過去（同名加編號）。"""
    moved: list[Path] = []
    n = len(files)
    for i, f in enumerate(files):
        if cancel.is_set():
            raise errors.Cancelled()
        if f.parent.resolve() == destination.resolve():
            moved.append(f)
            continue
        if f.is_dir() and not f.is_symlink() and _is_inside(destination, f):
            raise errors.QSError(f"無法把「{f.name}」移到它自己裡面。")
        if not os.path.lexists(f):
            raise errors.file_not_found(f.name)
        moved.append(naming.move_no_overwrite(f, destination))
        progress((i + 1) / n, f.name)
    return moved


class MoveJob:
    """在背景執行緒搬檔案（佇列的工作介面：start／cancel）。"""

    def __init__(
        self, files: Sequence[Path], destination: Path, progress: ProgressFn, done: DoneFn
    ) -> None:
        self._files = list(files)
        self._destination = destination
        self._progress = progress
        self._done = done
        self._cancel = threading.Event()

    def start(self) -> None:
        threading.Thread(target=self._run, name="qs-move", daemon=True).start()

    def cancel(self) -> None:
        self._cancel.set()

    def _run(self) -> None:
        try:
            moved = move_files(self._files, self._destination, self._progress, self._cancel)
        except errors.Cancelled:
            self._done({"error": "已取消。", "cancelled": True})
        except errors.QSError as exc:
            self._done({"error": str(exc)})
        except OSError as exc:
            log.error("移動失敗：%s", exc)
            self._done({"error": str(errors.write_failed(exc.strerror or str(exc)))})
        else:
            self._done({"outputs": [str(p) for p in moved]})
