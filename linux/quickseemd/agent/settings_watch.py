"""監看設定檔（與自動啟動檔）的變動，改了就通知（合併 150 ms 內的連續變動）。"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

import gi

gi.require_version("Gio", "2.0")
from gi.repository import Gio, GLib  # noqa: E402

log = logging.getLogger(__name__)

DEBOUNCE_MS = 150


def changed_keys(old: Mapping[str, Any], new: Mapping[str, Any]) -> set[str]:
    return {k for k in set(old) | set(new) if old.get(k) != new.get(k)}


class FileWatcher:
    """監看幾個檔案；資料夾不存在的就略過（不替使用者建立資料夾）。"""

    def __init__(self, paths: Sequence[Path], on_change: Callable[[], None]) -> None:
        self._paths = list(paths)
        self._on_change = on_change
        self._monitors: list[Gio.FileMonitor] = []
        self._pending = 0

    def start(self) -> None:
        for path in self._paths:
            if not path.parent.is_dir():
                log.debug("不監看 %s（資料夾不存在）", path)
                continue
            try:
                monitor = Gio.File.new_for_path(str(path)).monitor_file(
                    Gio.FileMonitorFlags.WATCH_MOVES, None
                )
            except GLib.Error as exc:
                log.warning("無法監看 %s：%s", path, exc.message)
                continue
            monitor.connect("changed", self._on_changed)
            self._monitors.append(monitor)

    def _on_changed(self, *_args: object) -> None:
        if self._pending:
            GLib.source_remove(self._pending)
        self._pending = GLib.timeout_add(DEBOUNCE_MS, self._fire)

    def _fire(self) -> bool:
        self._pending = 0
        try:
            self._on_change()
        except Exception:  # noqa: BLE001 — 套用設定失敗不能讓監看停掉
            log.exception("套用設定變動時出錯")
        return False
