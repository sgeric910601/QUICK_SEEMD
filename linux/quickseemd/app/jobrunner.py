"""在主執行緒使用的工作程序包裝：WorkerJob 的回呼改由 GLib.idle_add 送回主迴圈。

進度只往前（原版 max(old, new)），結束時回傳工作程序的結果 dict
（成功有 outputs、失敗有 error、取消有 cancelled=True）。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from gi.repository import GLib

from ..core import jobs
from ..core.runner import JobSpec

LOG = logging.getLogger(__name__)

ProgressFn = Callable[[float, "str | None"], None]
DoneFn = Callable[[dict[str, Any]], None]


class JobRunner:
    """一次執行一個工作；回呼一律在主執行緒。"""

    def __init__(
        self,
        spec: JobSpec,
        on_progress: ProgressFn,
        on_done: DoneFn,
        *,
        factory: Callable[..., jobs.WorkerJob] = jobs.WorkerJob,
    ) -> None:
        self.spec = spec
        self._on_progress = on_progress
        self._on_done = on_done
        self._factory = factory
        self._job: jobs.WorkerJob | None = None
        self._fraction = 0.0
        self.active = False

    def start(self) -> None:
        self.active = True
        self._job = self._factory(self.spec, self._progress_thread, self._done_thread)
        try:
            self._job.start()
        except OSError as exc:
            LOG.exception("無法啟動工作程序")
            self._done_main({"error": f"無法啟動處理程序：{exc}"})

    def cancel(self) -> None:
        if self._job is not None and self.active:
            self._job.cancel()

    # 工作程序的讀取執行緒呼叫這兩個，轉回主執行緒
    def _progress_thread(self, fraction: float, message: str | None) -> None:
        GLib.idle_add(self._progress_main, fraction, message)

    def _done_thread(self, result: dict[str, Any]) -> None:
        GLib.idle_add(self._done_main, result)

    def _progress_main(self, fraction: float, message: str | None) -> bool:
        if self.active:
            self._fraction = max(self._fraction, fraction)
            self._on_progress(self._fraction, message)
        return False

    def _done_main(self, result: dict[str, Any]) -> bool:
        if not self.active:
            return False
        self.active = False
        self._on_done(result)
        return False
