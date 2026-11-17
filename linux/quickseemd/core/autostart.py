"""登入時自動啟動背景程式（主程式、背景程式、安裝程式共用同一套規則）。

只管 settings.autostart_path() 這一個檔案，而且只動「我們的」檔案：
內容有 MARKER，或 Exec 指向 quickseemd-agent／quickseemd.agent。
別人放在同一個位置的檔案一律不碰（不覆蓋、不刪除）。
"""

from __future__ import annotations

import logging
import os
import tempfile
from collections.abc import Sequence
from pathlib import Path

from . import errors, settings

LOG = logging.getLogger(__name__)
MARKER = "X-QuickSeemd-Autostart=true"
APP_ICON = "dev.quickseemd.App"
_OUR_HINTS = (MARKER, "quickseemd-agent", "quickseemd.agent")
_RESERVED = set(" \t\n\"'\\><~|&;$*?#()`")
_ESCAPE_IN_QUOTES = set('"`$\\')


def path() -> Path:
    return settings.autostart_path()


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


def entry(argv: Sequence[str]) -> str:
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
        f"{MARKER}\n"
    )


def _read(p: Path) -> str | None:
    try:
        return p.read_text("utf-8", errors="replace")
    except OSError:
        return None


def is_ours(p: Path | None = None) -> bool:
    text = _read(p or path())
    return text is not None and any(h in text for h in _OUR_HINTS)


def enabled() -> bool:
    """檔案存在、而且沒有被設成隱藏或停用。"""
    text = _read(path())
    if text is None:
        return False
    lines = {line.strip() for line in text.splitlines()}
    return "Hidden=true" not in lines and "X-GNOME-Autostart-enabled=false" not in lines


def enable(argv: Sequence[str]) -> Path:
    """建立（或更新我們自己的）自動啟動檔；位置上已有別人的檔案時丟出錯誤，不覆蓋。"""
    target = path()
    if target.exists() and not is_ours(target):
        raise errors.QSError(f"「{target}」已經是別的程式的自動啟動設定，沒有變更它。")
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        try:
            with open(target, "x", encoding="utf-8") as fh:
                fh.write(entry(argv))
            return target
        except FileExistsError:
            if not is_ours(target):
                raise errors.QSError(f"「{target}」已經是別的程式的自動啟動設定，沒有變更它。")
    fd, tmp = tempfile.mkstemp(prefix=".quickseemd-", suffix=".desktop", dir=target.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write(entry(argv))
        os.replace(tmp, target)  # 確認過是我們自己的檔案，整份換新
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    return target


def disable() -> bool:
    """只刪我們自己的自動啟動檔；回傳是否真的刪了。"""
    target = path()
    if not target.is_file() or not is_ours(target):
        return False
    try:
        target.unlink()
    except OSError:
        LOG.exception("無法移除自動啟動檔")
        return False
    return True
