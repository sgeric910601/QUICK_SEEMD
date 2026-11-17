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
import threading
from collections.abc import Sequence
from pathlib import Path

from ..core import autostart, errors
from ..core.jobs import package_root

log = logging.getLogger(__name__)

APP_LAUNCHER = "quickseemd"
AGENT_LAUNCHER = "quickseemd-agent"
PYTHON = "/usr/bin/python3"
APP_ICON = autostart.APP_ICON


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
    # -P：不把目前資料夾放進模組搜尋路徑（避免被同名模組頂替）
    return [_python(), "-P", "-m", "quickseemd", *args], {"PYTHONPATH": str(package_root())}


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
    return [
        "/usr/bin/env",
        f"PYTHONPATH={package_root()}",
        _python(),
        "-P",
        "-m",
        "quickseemd.agent",
    ]


def _default_cwd() -> str:
    """沒指定工作資料夾時用家目錄（不存在就用 /），不沿用背景程式自己的目前資料夾。"""
    home = Path.home()
    return str(home) if home.is_dir() else "/"


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
            cwd=str(cwd) if cwd else _default_cwd(),
            start_new_session=True,
            close_fds=True,
        )
    except OSError as exc:
        log.error("無法啟動 %s：%s", args[0], exc)
        return False
    threading.Thread(target=proc.wait, name="qs-reap", daemon=True).start()
    return True


desktop_exec = autostart.desktop_exec


def autostart_desktop(argv: Sequence[str]) -> str:
    return autostart.entry(argv)


def autostart_enabled() -> bool:
    return autostart.enabled()


def set_autostart(enabled: bool) -> None:
    """建立或刪除自動啟動檔（規則見 core.autostart：只動我們自己的這一個檔案）。"""
    if enabled:
        try:
            autostart.enable(agent_command())
        except errors.QSError:
            log.exception("無法建立自動啟動檔")
    else:
        autostart.disable()
