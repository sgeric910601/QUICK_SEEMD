"""設定（對應原版 UserDefaults）。三個程序共用 ~/.config/quickseemd/settings.json。

寫入時加檔案鎖、先寫暫存檔再改名；讀取時缺的鍵用預設值。
"""

from __future__ import annotations

import fcntl
import json
import os
import tempfile
from pathlib import Path
from typing import Any

DEFAULTS: dict[str, Any] = {
    "wheel.enabled": True,
    "wheel.key.convert": "shift",
    "wheel.key.tools": "alt",
    "wheel.key.actions": "control",
    "tray.show": True,
    "background.enabled": True,
    "tools.output_same_folder": True,
    "tools.output_folder": "",
    "converter.output_mode": "same",  # same | folder
    "converter.output_folder": "",
    "update.auto_check": True,
}

# 輪盤可用的按鍵（值, 顯示名稱）
WHEEL_KEYS: tuple[tuple[str, str], ...] = (
    ("shift", "⇧ Shift"),
    ("control", "⌃ Ctrl"),
    ("alt", "⌥ Alt"),
    ("super", "❖ Super"),
)
WHEEL_MODES: tuple[tuple[str, str], ...] = (
    ("convert", "轉換"),
    ("tools", "工具"),
    ("actions", "動作"),
)


def config_dir() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return Path(base) / "quickseemd"


def settings_path() -> Path:
    return config_dir() / "settings.json"


def _lock_path() -> Path:
    return config_dir() / ".settings.lock"


def load() -> dict[str, Any]:
    values = dict(DEFAULTS)
    try:
        raw = json.loads(settings_path().read_text("utf-8"))
    except (OSError, ValueError):
        return values
    if isinstance(raw, dict):
        for k, v in raw.items():
            if k in DEFAULTS and isinstance(v, type(DEFAULTS[k])):
                values[k] = v
    return values


def get(key: str) -> Any:
    return load()[key]


def save(changes: dict[str, Any]) -> dict[str, Any]:
    """合併寫入（只接受已知的鍵與正確型別）；回傳寫入後的完整設定。"""
    for k, v in changes.items():
        if k not in DEFAULTS:
            raise KeyError(f"未知的設定：{k}")
        if not isinstance(v, type(DEFAULTS[k])):
            raise TypeError(f"設定 {k} 的型別不對")
    d = config_dir()
    d.mkdir(parents=True, exist_ok=True)
    with open(_lock_path(), "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        try:
            current = load()
            current.update(changes)
            stored = {k: v for k, v in current.items() if v != DEFAULTS[k]}
            fd, tmp = tempfile.mkstemp(prefix=".settings-", suffix=".json", dir=d)
            try:
                with os.fdopen(fd, "w", encoding="utf-8") as fh:
                    json.dump(stored, fh, ensure_ascii=False, indent=2, sort_keys=True)
                    fh.write("\n")
                os.replace(tmp, settings_path())  # 這是我們自己的設定檔，可以整份換新
            except BaseException:
                try:
                    os.unlink(tmp)
                except OSError:
                    pass
                raise
            return current
        finally:
            fcntl.flock(lock, fcntl.LOCK_UN)


def output_folder_for_tools() -> Path | None:
    """工具頁的輸出位置；None＝與原始檔案相同的資料夾。"""
    s = load()
    if s["tools.output_same_folder"] or not s["tools.output_folder"]:
        return None
    p = Path(s["tools.output_folder"]).expanduser()
    return p if p.is_dir() else None


def autostart_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(os.path.expanduser("~"), ".config")
    return Path(base) / "autostart" / "dev.quickseemd.Agent.desktop"
