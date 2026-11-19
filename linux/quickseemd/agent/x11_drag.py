"""偵測任何程式的檔案拖曳（只限 X11；Wayland 上做不到，直接不啟用）。

- 事件執行緒（自己的 X 連線）：XFixes 監看 XdndSelection 換主人＝有程式開始拖曳；
  另外用 XInput 2.2 聽根視窗的原始按鍵事件（來源程式搶走鍵盤時也收得到）。
- 追蹤執行緒（另一條 X 連線）：左鍵按著時每 40 ms 讀一次游標與修飾鍵；
  「點一下」修飾鍵就通知輪盤；拖曳中途用 XConvertSelection 向來源要 text/uri-list（最多等 400 ms）。
- 我們自己視窗發起的拖曳（例如從浮動暫存拖出去）不理會。

python-xlib 的連線不能跨執行緒共用，所以每個執行緒各開一條。
"""

from __future__ import annotations

import logging
import queue
import struct
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from .taps import TapDetector, key_for_keysym_name, mask_to_keys
from .wheel_model import owner_is_ours, parse_uri_list

log = logging.getLogger(__name__)

POLL_SECONDS = 0.04
FETCH_TIMEOUT = 0.4
BUTTON1_MASK = 1 << 8
XI_RAW_KEY_PRESS = 13
XI_RAW_KEY_RELEASE = 14
_RAW_HEADER = struct.Struct("=HII")  # deviceid、time、detail（＝keycode）

FetchFiles = Callable[[], "list[Path] | None"]


@dataclass(frozen=True)
class PointerSample:
    mask: int
    x: int
    y: int
    t: float


class DragListener(Protocol):
    def drag_began(self) -> None: ...

    def key_tapped(self, key: str, x: int, y: int, fetch: FetchFiles) -> None: ...

    def escape_pressed(self) -> None: ...

    def drag_ended(self) -> None: ...


@dataclass(frozen=True)
class RawKey:
    key: str  # shift、control、alt、super、escape
    down: bool
    t: float


class Tracker:
    """一次拖曳的追蹤迴圈。輸入都可以換成假的（測試用）。"""

    def __init__(
        self,
        listener: DragListener,
        query: Callable[[], PointerSample],
        fetch: FetchFiles,
        escape_down: Callable[[], bool],
        inbox: queue.Queue[RawKey],
        active: Callable[[], bool] = lambda: True,
        poll: float = POLL_SECONDS,
    ) -> None:
        self._listener = listener
        self._query = query
        self._fetch = fetch
        self._escape_down = escape_down
        self._inbox = inbox
        self._active = active
        self._poll = poll
        self._detector = TapDetector()
        self._last = PointerSample(0, 0, 0, 0.0)
        self._escape_was_down = False

    def run(self) -> bool:
        """追蹤到放開滑鼠為止；回傳是否真的是一次拖曳（開始時左鍵按著）。"""
        _drain(self._inbox)
        first = self._query()
        if not first.mask & BUTTON1_MASK:
            return False
        self._last = first
        self._detector.reset(mask_to_keys(first.mask), first.t)
        self._listener.drag_began()
        while True:
            self._handle_raw()
            sample = self._query()
            self._last = sample
            if not sample.mask & BUTTON1_MASK or not self._active():
                self._listener.drag_ended()
                return True
            for key in self._detector.sample(mask_to_keys(sample.mask), sample.t):
                self._listener.key_tapped(key, sample.x, sample.y, self._fetch)
            self._check_escape(self._escape_down())
            self._wait()

    def _wait(self) -> None:
        try:
            ev = self._inbox.get(timeout=self._poll)
        except queue.Empty:
            return
        self._apply(ev)

    def _handle_raw(self) -> None:
        for ev in _drain(self._inbox):
            self._apply(ev)

    def _apply(self, ev: RawKey) -> None:
        if ev.key == "escape":
            if ev.down:
                self._listener.escape_pressed()
            return
        if ev.down:
            self._detector.press(ev.key, ev.t)
            return
        tapped = self._detector.release(ev.key, ev.t)
        if tapped:
            self._listener.key_tapped(tapped, self._last.x, self._last.y, self._fetch)

    def _check_escape(self, down: bool) -> None:
        if down and not self._escape_was_down:
            self._listener.escape_pressed()
        self._escape_was_down = down


def _drain(inbox: queue.Queue[RawKey]) -> list[RawKey]:
    out: list[RawKey] = []
    while True:
        try:
            out.append(inbox.get_nowait())
        except queue.Empty:
            return out


def parse_raw_key(data: bytes) -> int | None:
    """XI 原始按鍵事件的資料 → keycode。"""
    if len(data) < _RAW_HEADER.size:
        return None
    _device, _time, detail = _RAW_HEADER.unpack_from(data)
    return detail


class XDragWatcher:
    """在背景執行緒監看拖曳；listener 的方法在追蹤執行緒裡呼叫。"""

    def __init__(self, listener: DragListener, own_xid: int | None) -> None:
        self._listener = listener
        self._own_xid = own_xid
        self._enabled = threading.Event()
        self._begin = threading.Event()
        self._tracking = threading.Event()
        self._inbox: queue.Queue[RawKey] = queue.Queue()
        self._started = False

    @property
    def enabled(self) -> bool:
        return self._enabled.is_set()

    def set_enabled(self, enabled: bool) -> None:
        if enabled:
            self._enabled.set()
        else:
            self._enabled.clear()

    def start(self) -> bool:
        """開兩條 X 連線與執行緒；X 不能用就回傳 False（輪盤停用）。"""
        if self._started:
            return True
        try:
            from Xlib import display as xdisplay  # noqa: PLC0415

            events = xdisplay.Display()
            tracker = xdisplay.Display()
        except Exception as exc:  # noqa: BLE001 — 沒有 X（例如 Wayland）就安靜地停用輪盤
            log.info("無法連上 X，拖曳輪盤停用：%s", exc)
            return False
        from .x11_conn import EventConnection, TrackerConnection  # noqa: PLC0415

        ev = EventConnection(events)
        if not ev.setup():
            return False
        tr = TrackerConnection(tracker)
        self._resource_mask = ev.resource_mask
        threading.Thread(target=self._event_loop, args=(ev,), name="qs-xdnd", daemon=True).start()
        threading.Thread(target=self._track_loop, args=(tr,), name="qs-drag", daemon=True).start()
        self._started = True
        return True

    def _event_loop(self, ev) -> None:  # noqa: ANN001
        while True:
            try:
                kind, value = ev.next()
            except Exception as exc:  # noqa: BLE001 — X 連線斷了：停止監看
                log.error("拖曳監看停止：%s", exc)
                return
            if kind == "owner":
                self._owner_changed(value)
            elif kind == "key" and self._tracking.is_set():
                self._inbox.put(value)

    def _owner_changed(self, owner: int) -> None:
        if not owner or not self._enabled.is_set() or self._tracking.is_set():
            return
        if owner_is_ours(owner, self._own_xid, self._resource_mask):
            log.debug("忽略自己發起的拖曳")
            return
        log.debug("XdndSelection 換主人：%#x", owner)
        self._begin.set()

    def _track_loop(self, tr) -> None:  # noqa: ANN001
        while True:
            self._begin.wait()
            self._begin.clear()
            self._tracking.set()
            try:
                Tracker(
                    self._listener,
                    tr.query,
                    tr.fetch_files,
                    tr.escape_down,
                    self._inbox,
                    active=self._enabled.is_set,
                ).run()
            except Exception:  # noqa: BLE001 — 單次追蹤出錯不能讓整個監看停掉
                log.exception("拖曳追蹤出錯")
            finally:
                self._tracking.clear()


def _now() -> float:
    return time.monotonic()


__all__ = [
    "DragListener",
    "PointerSample",
    "RawKey",
    "Tracker",
    "XDragWatcher",
    "key_for_keysym_name",
    "parse_raw_key",
    "parse_uri_list",
]
