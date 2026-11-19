"""「右鍵、拖曳與空白鍵預覽」頁與設定視窗用到的系統狀態（不需要畫面）。

- Nemo 右鍵擴充：~/.local/share/nemo-python/extensions/quickseemd_nemo.py 是否存在
- 空白鍵預覽：~/.local/share/dbus-1/services/org.nemo.Preview.service 是否存在
- 登入時自動啟動：settings.autostart_path() 的 .desktop 檔；只會刪我們自己寫的那一個
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from ..core import autostart, errors, settings

LOG = logging.getLogger(__name__)

MARKER = autostart.MARKER
LAUNCHER_NAME = "quickseemd-agent"
WHEEL_CONFLICT_TEXT = "有兩個輪盤用了同一個按鍵，只會開啟排在前面的那個"
# （模式, 標題, 原版 SF Symbol）
WHEEL_ROWS: tuple[tuple[str, str, str], ...] = (
    ("convert", "轉換輪盤", "arrow.triangle.2.circlepath"),
    ("tools", "工具輪盤", "wrench.and.screwdriver"),
    ("actions", "動作輪盤", "bolt"),
)
WHEEL_CAPTION = "拖著檔案時點一下按鍵叫出輪盤；放開按鍵輪盤仍會留著，點另一個鍵切換，Esc 取消"


def data_home() -> Path:
    base = os.environ.get("XDG_DATA_HOME") or os.path.join(
        os.path.expanduser("~"), ".local", "share"
    )
    return Path(base)


def nemo_extension_path() -> Path:
    return data_home() / "nemo-python" / "extensions" / "quickseemd_nemo.py"


def preview_service_path() -> Path:
    return data_home() / "dbus-1" / "services" / "org.nemo.Preview.service"


def nemo_extension_installed() -> bool:
    return nemo_extension_path().is_file()


def preview_service_installed() -> bool:
    return preview_service_path().is_file()


def nemo_status(installed: bool) -> tuple[str, bool]:
    if installed:
        return "Nemo 右鍵選單已啟用", True
    return "Nemo 右鍵擴充尚未安裝（請重新執行安裝程式）", False


def preview_status(installed: bool) -> tuple[str, bool]:
    if installed:
        return "預覽擴充已啟用", True
    return "預覽擴充尚未啟用（請重新執行安裝程式）", False


def agent_status(alive: bool | None) -> tuple[str, bool | None]:
    if alive is None:
        return "檢查中…", None
    return ("背景程式執行中", True) if alive else ("背景程式沒有回應", False)


def wheel_conflict(values: dict[str, Any]) -> bool:
    keys = [values.get(f"wheel.key.{mode}") for mode, _t, _i in WHEEL_ROWS]
    return len(set(keys)) < len(keys)


# ---------------------------------------------------------------- 自動啟動


def agent_launcher() -> Path:
    """安裝程式放的背景程式啟動檔（~/.local/bin/quickseemd-agent）。"""
    home = Path(os.path.expanduser("~"))
    return home / ".local" / "bin" / LAUNCHER_NAME


def desktop_quote(arg: str) -> str:
    """依 Desktop Entry 規格引用 Exec 參數。"""
    if all(c.isalnum() or c in "/._-+" for c in arg):
        return arg
    escaped = "".join("\\" + c if c in '"`$\\' else c for c in arg)
    return f'"{escaped}"'


def autostart_entry(launcher: Path) -> str:
    return autostart.entry([str(launcher)])


def autostart_enabled() -> bool:
    return autostart.enabled()


def enable_autostart(launcher: Path | None = None) -> Path:
    """建立自動啟動檔（規則見 core.autostart）；我們的檔案已經開著就不動。"""
    target = settings.autostart_path()
    if autostart.is_ours(target) and autostart.enabled():
        return target
    exe = launcher or agent_launcher()
    if not exe.is_file():
        raise errors.QSError(f"找不到背景程式「{exe}」，請重新執行安裝程式。")
    return autostart.enable([str(exe)])


def is_our_autostart(path: Path) -> bool:
    return autostart.is_ours(path)


def disable_autostart() -> bool:
    """只刪 settings.autostart_path() 這一個檔，而且內容要是我們的。"""
    return autostart.disable()
