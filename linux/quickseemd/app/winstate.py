"""記住主視窗大小（對應原版 frame 存成 "AppMain"）。

存在 ~/.config/quickseemd/window.json（主程式自己的狀態檔，先寫暫存檔再改名）。
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
from pathlib import Path

from ..core import settings

LOG = logging.getLogger(__name__)
DEFAULT_SIZE = (1160, 760)
MIN_SIZE = (980, 640)
MAX_SIZE = (10000, 10000)


def state_path() -> Path:
    return settings.config_dir() / "window.json"


def _clamp(w: int, h: int) -> tuple[int, int]:
    return (min(max(int(w), MIN_SIZE[0]), MAX_SIZE[0]), min(max(int(h), MIN_SIZE[1]), MAX_SIZE[1]))


def load_size() -> tuple[int, int]:
    try:
        raw = json.loads(state_path().read_text("utf-8"))
        return _clamp(int(raw["width"]), int(raw["height"]))
    except (OSError, ValueError, KeyError, TypeError):
        return DEFAULT_SIZE


def save_size(width: int, height: int) -> None:
    w, h = _clamp(width, height)
    folder = state_path().parent
    try:
        folder.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(prefix=".window-", suffix=".json", dir=folder)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump({"width": w, "height": h}, fh)
        os.replace(tmp, state_path())
    except OSError:
        LOG.exception("無法記住視窗大小")
