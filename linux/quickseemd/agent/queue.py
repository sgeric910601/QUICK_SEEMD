"""背景工作佇列（對應原版 QuickActionRunner）：右鍵與輪盤的工作一次跑一個。

- 每個工作在獨立的工作程序執行（jobs.WorkerJob），輸出一律存在原始檔案旁邊。
- 只保留最新的 6 筆；佇列有變化時通知觀察者（右上角進度面板）。
- 回呼可能來自讀取執行緒，一律經過 dispatch 轉回主執行緒再改狀態。
"""

from __future__ import annotations

import itertools
import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass, replace
from enum import Enum
from pathlib import Path
from typing import Any, Protocol

from ..core import quickactions, registry
from ..core.converter import formats
from ..core.model import RGB, TINTS
from ..core.runner import JobSpec

log = logging.getLogger(__name__)

MAX_ITEMS = 6
GREY: RGB = (0.56, 0.56, 0.58)
ACCENT: RGB = (0.21, 0.52, 0.89)  # 系統強調色（原版 .accentColor）
UNKNOWN_TITLE = "找不到動作"
CANCELLED_REASON = "已取消"
CONVERT_TOOL_ID = "other.convert"

ProgressFn = Callable[[float, "str | None"], None]
DoneFn = Callable[[dict[str, Any]], None]


class Job(Protocol):
    def start(self) -> None: ...

    def cancel(self) -> None: ...


JobStarter = Callable[[ProgressFn, DoneFn], Job]
WorkerFactory = Callable[[JobSpec, ProgressFn, DoneFn], Job]
Dispatch = Callable[[Callable[[], None]], None]


class Status(Enum):
    WAITING = "waiting"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"


@dataclass(frozen=True)
class QueueItem:
    """面板上的一列。狀態改變時整個換新（不可變）。"""

    id: int
    title: str
    icon: str  # 原版 SF Symbol 名稱（或圖示主題名稱）
    tint: RGB
    files: tuple[Path, ...]
    tool_id: str | None
    action_id: str | None = None
    status: Status = Status.WAITING
    progress: float = 0.0
    message: str = ""
    outputs: tuple[Path, ...] = ()
    reason: str = ""


class QueueObserver(Protocol):
    def queue_changed(self, items: tuple[QueueItem, ...]) -> None: ...

    def queue_shown(self) -> None: ...  # 有新項目：顯示面板、取消待執行的隱藏

    def queue_idle(self) -> None: ...  # 沒有工作在等：排定 7 秒後隱藏

    def queue_empty(self) -> None: ...  # 最後一列被關掉：立刻隱藏

    def item_finished(self, item: QueueItem) -> None: ...


@dataclass(frozen=True)
class ActionInfo:
    title: str
    icon: str
    tint: RGB
    tool_id: str


def describe_action(action_id: str) -> ActionInfo | None:
    """動作代碼 → 面板列的標題、圖示與顏色；這個版本做不到的動作回傳 None。"""
    act = quickactions.action(action_id)
    if act is None or not act.enabled:
        return None
    tool = registry.tool(act.tool_id)
    if tool is None:
        return None
    icon = act.icon or tool.icon or "wand.and.stars"
    return ActionInfo(act.title, icon, tool.tint_rgb, tool.id)


def auto_hide_decision(items: Sequence[QueueItem], hovering: bool) -> str:
    """7 秒到了要怎麼辦：wait＝滑鼠在上面再等一輪；keep＝留著；hide＝隱藏並清空。"""
    if hovering:
        return "wait"
    if any(i.status in (Status.RUNNING, Status.WAITING, Status.FAILED) for i in items):
        return "keep"
    return "hide"


def _call_now(fn: Callable[[], None]) -> None:
    fn()


def default_worker_factory(spec: JobSpec, progress: ProgressFn, done: DoneFn) -> Job:
    from ..core.jobs import WorkerJob  # noqa: PLC0415

    return WorkerJob(spec, on_progress=progress, on_done=done)


class _FailJob:
    """一開始就失敗的工作（找不到動作）。"""

    def __init__(self, reason: str, done: DoneFn) -> None:
        self._reason = reason
        self._done = done

    def start(self) -> None:
        self._done({"error": self._reason})

    def cancel(self) -> None:
        return None


class JobQueue:
    def __init__(
        self,
        observer: QueueObserver,
        *,
        worker_factory: WorkerFactory = default_worker_factory,
        dispatch: Dispatch = _call_now,
    ) -> None:
        self._observer = observer
        self._worker_factory = worker_factory
        self._dispatch = dispatch
        self._items: list[QueueItem] = []
        self._starters: dict[int, JobStarter] = {}
        self._jobs: dict[int, Job] = {}
        self._current: int | None = None
        self._ids = itertools.count(1)

    @property
    def items(self) -> tuple[QueueItem, ...]:
        return tuple(self._items)

    # ------------------------------------------------------------ 加入工作

    def run_action(self, action_id: str, files: Sequence[str | Path]) -> int | None:
        paths = [Path(f) for f in files]
        if not paths:
            return None
        info = describe_action(action_id)
        if info is None:
            reason = f"這個版本的 QUICK_SEEMD 不認得動作「{action_id}」。"
            return self.enqueue(
                UNKNOWN_TITLE,
                "questionmark",
                GREY,
                paths,
                None,
                lambda _p, done: _FailJob(reason, done),
            )
        spec = JobSpec("action", action_id, [str(p) for p in paths])
        return self.enqueue(
            info.title,
            info.icon,
            info.tint,
            paths,
            info.tool_id,
            lambda p, d: self._worker_factory(spec, p, d),
            action_id=action_id,
        )

    def convert(self, format_id: str, files: Sequence[str | Path]) -> int | None:
        fmt = formats.BY_ID.get(format_id)
        if fmt is None:
            raise ValueError(f"未知的格式：{format_id}")
        paths = [Path(f) for f in files]
        if not paths:
            return None
        spec = JobSpec("convert", format_id, [str(p) for p in paths])
        return self.enqueue(
            f"轉成 {fmt.display_name}",
            fmt.category.icon,
            TINTS["teal"],
            paths,
            CONVERT_TOOL_ID,
            lambda p, d: self._worker_factory(spec, p, d),
        )

    def enqueue(
        self,
        title: str,
        icon: str,
        tint: RGB,
        files: Sequence[Path],
        tool_id: str | None,
        starter: JobStarter,
        *,
        action_id: str | None = None,
    ) -> int:
        item = QueueItem(next(self._ids), title, icon, tint, tuple(files), tool_id, action_id)
        self._starters[item.id] = starter
        self._append(item)
        self._observer.queue_shown()
        self._start_next()
        return item.id

    def notify(self, title: str, icon: str, message: str) -> int:
        """只顯示一則已完成的訊息（拷貝、拷貝路徑這類瞬間完成的動作）。"""
        item = QueueItem(
            next(self._ids),
            title,
            icon,
            TINTS["slate"],
            (),
            None,
            status=Status.DONE,
            progress=1.0,
            message=message,
        )
        self._append(item)
        self._observer.queue_shown()
        self._observer.queue_idle()
        return item.id

    # ------------------------------------------------------------ 取消、關閉

    def cancel(self, item_id: int) -> None:
        if self._current == item_id and item_id in self._jobs:
            self._jobs[item_id].cancel()
            return
        item = self._find(item_id)
        if item is not None and item.status is Status.WAITING:
            self._starters.pop(item_id, None)
            self._replace(replace(item, status=Status.FAILED, reason=CANCELLED_REASON))

    def dismiss(self, item_id: int) -> None:
        self._items = [i for i in self._items if i.id != item_id]
        self._starters.pop(item_id, None)
        self._observer.queue_changed(self.items)
        if not self._items:
            self._observer.queue_empty()

    def shutdown(self) -> None:
        """程式要結束了：取消執行中的工作（工作程序的整個程序群組都會結束）。"""
        for job in list(self._jobs.values()):
            job.cancel()

    def clear(self) -> None:
        """面板自動隱藏時清空清單（工作中的項目不會在這時候被清）。"""
        self._items = [i for i in self._items if i.status in (Status.RUNNING, Status.WAITING)]
        self._observer.queue_changed(self.items)

    # ------------------------------------------------------------ 內部

    def _find(self, item_id: int) -> QueueItem | None:
        return next((i for i in self._items if i.id == item_id), None)

    def _append(self, item: QueueItem) -> None:
        self._items.append(item)
        if len(self._items) > MAX_ITEMS:
            dropped = self._items[: len(self._items) - MAX_ITEMS]
            self._items = self._items[-MAX_ITEMS:]
            for d in dropped:
                self._starters.pop(d.id, None)
        self._observer.queue_changed(self.items)

    def _replace(self, item: QueueItem) -> None:
        self._items = [item if i.id == item.id else i for i in self._items]
        self._observer.queue_changed(self.items)

    def _start_next(self) -> None:
        if self._current is not None:
            return
        item = next((i for i in self._items if i.status is Status.WAITING), None)
        if item is None:
            return
        starter = self._starters.pop(item.id, None)
        if starter is None:
            return
        self._current = item.id
        self._replace(replace(item, status=Status.RUNNING))
        item_id = item.id

        def progress(fraction: float, message: str | None) -> None:
            self._dispatch(lambda: self._update(item_id, fraction, message))

        def done(result: dict[str, Any]) -> None:
            self._dispatch(lambda: self._finish(item_id, result))

        try:
            job = starter(progress, done)
            self._jobs[item_id] = job
            job.start()
        except (OSError, ValueError, RuntimeError) as exc:
            log.error("工作無法啟動：%s", exc)
            self._finish(item_id, {"error": f"無法啟動處理程序：{exc}"})

    def _update(self, item_id: int, fraction: float, message: str | None) -> None:
        item = self._find(item_id)
        if item is None or item.status is not Status.RUNNING:
            return
        new = replace(
            item,
            progress=max(item.progress, min(max(fraction, 0.0), 1.0)),
            message=message if message is not None else item.message,
        )
        self._replace(new)

    def _finish(self, item_id: int, result: dict[str, Any]) -> None:
        if self._current != item_id:
            return
        self._jobs.pop(item_id, None)
        self._current = None
        item = self._find(item_id)
        if item is not None:
            finished = _apply_result(item, result)
            _log_result(finished)
            self._replace(finished)
            self._observer.item_finished(finished)
        if any(i.status is Status.WAITING for i in self._items):
            self._start_next()
        else:
            self._observer.queue_idle()


def _apply_result(item: QueueItem, result: dict[str, Any]) -> QueueItem:
    error = result.get("error")
    if error is not None:
        reason = CANCELLED_REASON if result.get("cancelled") else str(error)
        return replace(item, status=Status.FAILED, reason=reason, progress=1.0)
    outputs = tuple(Path(p) for p in result.get("outputs") or [] if isinstance(p, str))
    return replace(item, status=Status.DONE, outputs=outputs, progress=1.0)


def _log_result(item: QueueItem) -> None:
    if item.status is Status.DONE:
        log.info("完成：%s", ", ".join(p.name for p in item.outputs))
    else:
        log.warning("失敗：%s", item.reason)
