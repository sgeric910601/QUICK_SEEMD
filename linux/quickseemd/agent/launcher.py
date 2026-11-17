"""啟動主程式（quickseemd open …）與「登入時自動啟動」檔案。

找啟動檔的順序：和自己的啟動檔放在一起的 quickseemd、~/.local/bin/quickseemd；
都沒有（例如開發時直接 python3 -m）就用 python3 -m quickseemd，並帶上 PYTHONPATH。
自動啟動只會建立或刪除 settings.autostart_path() 這一個檔案。
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import tempfile
import threading
from collections.abc import Sequence
from pathlib import Path

from ..core import settings
from ..core.jobs import package_root

log = logging.getLogger(__name__)

APP_LAUNCHER = "quickseemd"
AGENT_LAUNCHER = "quickseemd-agent"
PYTHON = "/usr/bin/python3"
APP_ICON = "dev.quickseemd.App"
_RESERVED = frozenset(" \t\n\"'\\><~|&;$*?#()`")
_ESCAPE_IN_QUOTES = frozenset('"`$\\')


def _python() -> str:
    exe = sys.executable
    return exe if exe and os.path.isabs(exe) else PYTHON


def find_launcher(name: str) -> Path | None:
    """已安裝的啟動檔（必須是可執行的一般檔案）。"""
    candidates = []
    if sys.argv and sys.argv[0]:
        candidates.append(Path(sys.argv[0]).resolve().parent / name)
    candidates.append(Path.home() / ".local" / "bin" / name)
    for cand in candidates:
        if cand.is_file() and os.access(cand, os.X_OK):
            return cand
    return None


def app_command(*args: str) -> tuple[list[str], dict[str, str]]:
    """執行主程式的指令與要加的環境變數。"""
    found = find_launcher(APP_LAUNCHER)
    if found is not None:
        return [str(found), *args], {}
    return [_python(), "-m", "quickseemd", *args], {"PYTHONPATH": str(package_root())}


def open_files_command(
    files: Sequence[Path], tool_id: str | None = None
) -> tuple[list[str], dict[str, str]]:
    """quickseemd open [--tool 工具] 檔案…（檔案一律是絕對路徑，不會被當成選項）。"""
    args = ["open"]
    if tool_id:
        args += ["--tool", tool_id]
    args += [str(Path(f).absolute()) for f in files]
    return app_command(*args)


def agent_command() -> list[str]:
    """背景程式自己的啟動指令（寫進自動啟動檔）。"""
    found = find_launcher(AGENT_LAUNCHER)
    if found is not None:
        return [str(found)]
    return ["/usr/bin/env", f"PYTHONPATH={package_root()}", _python(), "-m", "quickseemd.agent"]


def spawn(argv: Sequence[str], env: dict[str, str] | None = None, cwd: Path | None = None) -> bool:
    """在背景啟動另一個程式（不等它結束，也不會留下殭屍程序）；失敗時記錄並回傳 False。"""
    args = [os.fspath(a) for a in argv]
    if not args or not os.path.isabs(args[0]):
        log.error("拒絕執行非絕對路徑的程式：%r", args[:1])
        return False
    full_env = os.environ.copy()
    if env:
        for key, value in env.items():
            old = full_env.get(key)
            full_env[key] = value + (os.pathsep + old if key == "PYTHONPATH" and old else "")
    try:
        proc = subprocess.Popen(  # noqa: S603 — 參數清單、絕對路徑、不經過 shell
            args,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            env=full_env,
            cwd=str(cwd) if cwd else None,
            start_new_session=True,
            close_fds=True,
        )
    except OSError as exc:
        log.error("無法啟動 %s：%s", args[0], exc)
        return False
    threading.Thread(target=proc.wait, name="qs-reap", daemon=True).start()
    return True


def _quote_arg(arg: str) -> str:
    value = arg.replace("%", "%%")
    if any(c in _RESERVED for c in value):
        inner = "".join("\\" + c if c in _ESCAPE_IN_QUOTES else c for c in value)
        value = f'"{inner}"'
    # .desktop 的字串層級跳脫：反斜線與控制字元
    value = value.replace("\\", "\\\\")
    return value.replace("\n", "\\n").replace("\t", "\\t").replace("\r", "\\r")


def desktop_exec(argv: Sequence[str]) -> str:
    """依 Desktop Entry 規格把參數清單寫成 Exec= 的值。"""
    return " ".join(_quote_arg(a) for a in argv)


def autostart_desktop(argv: Sequence[str]) -> str:
    return (
        "[Desktop Entry]\n"
        "Type=Application\n"
        "Name=QUICK_SEEMD\n"
        "Comment=萬用預覽・轉檔・工具箱\n"
        f"Exec={desktop_exec(argv)}\n"
        f"Icon={APP_ICON}\n"
        "Terminal=false\n"
        "NoDisplay=true\n"
        "X-GNOME-Autostart-enabled=true\n"
    )


def autostart_enabled() -> bool:
    path = settings.autostart_path()
    try:
        text = path.read_text("utf-8")
    except OSError:
        return False
    lines = {line.strip() for line in text.splitlines()}
    return "Hidden=true" not in lines and "X-GNOME-Autostart-enabled=false" not in lines


def set_autostart(enabled: bool) -> None:
    """建立或刪除自動啟動檔（只動我們自己的這一個檔案）。"""
    path = settings.autostart_path()
    if not enabled:
        path.unlink(missing_ok=True)
        return
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=".quickseemd-", suffix=".desktop", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(autostart_desktop(agent_command()))
        os.replace(tmp, path)  # 我們自己的檔案，可以整份換新
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
