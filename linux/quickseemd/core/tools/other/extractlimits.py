"""解壓縮的大小上限（防壓縮炸彈與塞滿磁碟）。

上限（DEFAULT_LIMITS）：
- 開始前量一次暫存資料夾所在磁碟的可用空間；可用空間不到 1 GB（reserve_bytes）就不解。
- 寫進磁碟的總量（含 bsdtar 轉存的中間 tar）不能超過 min(可用空間 − 1 GB, 20 GB)。
- 解出來的內容超過 10 MiB（ratio_floor_bytes）後，內容大小不能超過壓縮檔大小的 1000 倍
  （max_ratio）。一般壓縮檔的壓縮比遠低於 100 倍；deflate 理論上限約 1032 倍。
超過時丟出 TooLarge，由 unpack.extract 轉成中文錯誤並清掉已寫出的暫存檔。

外部程式（bsdtar、7z）寫檔時用 SizeWatch 在背景每 0.1 秒量一次大小，超過就請 process.run 結束它。
"""

from __future__ import annotations

import os
import shutil
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from ... import errors

KiB = 1 << 10
MiB = 1 << 20
GiB = 1 << 30
WATCH_INTERVAL = 0.1


@dataclass(frozen=True)
class ExtractLimits:
    reserve_bytes: int = 1 * GiB
    max_total_bytes: int = 20 * GiB
    max_ratio: int = 1000
    ratio_floor_bytes: int = 10 * MiB


DEFAULT_LIMITS = ExtractLimits()


def format_bytes(n: int) -> str:
    """1 GB、20 GB、1 MB 這種簡短寫法（以 1024 為單位）。"""
    for unit, size in (("GB", GiB), ("MB", MiB)):
        if n >= size:
            return f"{n / size:.3g} {unit}"
    return f"{n / KiB:.3g} KB"


class TooLarge(Exception):
    """超過上限。reason：nospace（開始前空間就不夠）、space、total、ratio。"""

    def __init__(self, reason: str, limits: ExtractLimits) -> None:
        super().__init__(reason)
        self.reason = reason
        self.limits = limits

    def error(self, archive: Path) -> errors.QSError:
        name = Path(archive).name
        lim = self.limits
        messages = {
            "nospace": f"磁碟可用空間不足 {format_bytes(lim.reserve_bytes)}，無法解壓縮「{name}」。",
            "space": f"「{name}」解開後會用掉太多磁碟空間"
            f"（要保留 {format_bytes(lim.reserve_bytes)} 可用空間），已停止解壓縮。",
            "total": f"「{name}」解開後超過 {format_bytes(lim.max_total_bytes)}，"
            "為了避免塞滿磁碟，已停止解壓縮。",
            "ratio": f"「{name}」的壓縮比異常（超過 {lim.max_ratio} 倍），"
            "可能是惡意的壓縮炸彈，已停止解壓縮。",
        }
        return errors.QSError(messages[self.reason])


class Budget:
    """一次解壓縮的額度：disk＝寫進磁碟的位元組（含中間檔），content＝解出來的內容。"""

    def __init__(self, archive_size: int, free_bytes: int, limits: ExtractLimits) -> None:
        self.limits = limits
        self.archive_size = max(archive_size, 1)
        room = free_bytes - limits.reserve_bytes
        if room <= 0:
            raise TooLarge("nospace", limits)
        self.space_bound = room < limits.max_total_bytes
        self.limit = min(room, limits.max_total_bytes)
        self.disk = 0
        self.content = 0
        self.planned = 0
        self._lock = threading.Lock()

    @classmethod
    def for_target(cls, archive: Path, work: Path, limits: ExtractLimits) -> Budget:
        return cls(Path(archive).stat().st_size, shutil.disk_usage(work).free, limits)

    def check(self, extra_disk: int = 0, extra_content: int = 0) -> None:
        """目前用量加上 extra 是否還在額度內；超過就丟 TooLarge（不記帳）。"""
        lim = self.limits
        content = self.content + extra_content
        if content > lim.ratio_floor_bytes and content > lim.max_ratio * self.archive_size:
            raise TooLarge("ratio", lim)
        if self.disk + extra_disk > self.limit:
            raise TooLarge("space" if self.space_bound else "total", lim)

    def plan(self, declared: int) -> None:
        """接下來會寫出 declared 位元組：先檢查（超過就在寫之前停），並記下來給進度用。"""
        self.check(extra_disk=declared, extra_content=declared)
        self.planned = self.content + declared

    def charge(self, n: int, *, content: bool = True) -> None:
        """記帳（寫出 n 位元組）並檢查。"""
        with self._lock:
            self.disk += n
            if content:
                self.content += n
            self.check()

    def release(self, n: int) -> None:
        """刪掉自己的中間檔後退回磁碟額度。"""
        with self._lock:
            self.disk = max(0, self.disk - n)


def file_size(path: Path) -> int:
    try:
        return os.lstat(path).st_size
    except OSError:
        return 0


def tree_size(root: Path) -> int:
    total = 0
    for dirpath, _dirs, files in os.walk(root):
        for name in files:
            total += file_size(Path(dirpath) / name)
    return total


class SizeWatch:
    """外部程式執行時在背景量大小；超過額度或使用者取消時設定 stop（交給 process.run 的 cancel）。"""

    def __init__(self, measure: Callable[[], int], budget: Budget, cancel: threading.Event):
        self._measure = measure
        self._budget = budget
        self._cancel = cancel
        self._done = threading.Event()
        self.stop = threading.Event()
        self.exceeded: TooLarge | None = None
        self._thread = threading.Thread(target=self._loop, name="qs-size-watch", daemon=True)

    def _loop(self) -> None:
        while not self._done.wait(WATCH_INTERVAL):
            if self._cancel.is_set():
                self.stop.set()
                return
            size = self._measure()
            try:
                self._budget.check(extra_disk=size, extra_content=size)
            except TooLarge as exc:
                self.exceeded = exc
                self.stop.set()
                return

    def __enter__(self) -> SizeWatch:
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._done.set()
        self._thread.join()

    def raise_if_exceeded(self) -> None:
        if self.exceeded is not None:
            raise self.exceeded
