"""在獨立程序執行工作（主程式與背景程式共用）。

協定（凍結）：
- 啟動：`/usr/bin/python3 -m quickseemd.worker`，工作描述（JobSpec JSON）從標準輸入送進去。
- 進度：工作程序把 JSON 行寫到環境變數 QS_PROGRESS_FD 指定的管道（不是標準輸出）：
    {"type":"progress","fraction":0.42,"message":"處理中：a.pdf"}
    {"type":"result", ...RunResult.to_json()..., "skipped":[...]}
- 取消：對工作程序的整個程序群組送 SIGTERM，3 秒後還在就 SIGKILL。
- 一次只跑一個工作由呼叫端（佇列）負責。
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

from . import process
from .runner import JobSpec

PROGRESS_FD_ENV = "QS_PROGRESS_FD"
CANCEL_GRACE_SECONDS = 3.0

ProgressCallback = Callable[[float, "str | None"], None]
DoneCallback = Callable[[dict[str, Any]], None]


def package_root() -> Path:
    """quickseemd 套件所在的資料夾（工作程序的 PYTHONPATH）。"""
    return Path(__file__).resolve().parent.parent.parent


class WorkerJob:
    """一個在背景程序執行的工作。回呼在讀取執行緒裡呼叫，介面層要自己轉回主執行緒。"""

    def __init__(
        self,
        spec: JobSpec,
        on_progress: ProgressCallback | None = None,
        on_done: DoneCallback | None = None,
        python: str = sys.executable or "/usr/bin/python3",
    ) -> None:
        self.spec = spec
        self.on_progress = on_progress
        self.on_done = on_done
        self.python = python
        self._proc: subprocess.Popen[bytes] | None = None
        self._cancelled = threading.Event()
        self._thread: threading.Thread | None = None
        self.result: dict[str, Any] | None = None

    def start(self) -> None:
        read_fd, write_fd = os.pipe()
        env = os.environ.copy()
        env[PROGRESS_FD_ENV] = str(write_fd)
        root = str(package_root())
        env["PYTHONPATH"] = root + (os.pathsep + env["PYTHONPATH"] if env.get("PYTHONPATH") else "")
        try:
            self._proc = subprocess.Popen(  # noqa: S603
                [self.python, "-m", "quickseemd.worker"],
                stdin=subprocess.PIPE,
                stdout=subprocess.DEVNULL,
                stderr=None,
                env=env,
                pass_fds=(write_fd,),
                start_new_session=True,
            )
        finally:
            os.close(write_fd)
        process._register(self._proc.pid)  # noqa: SLF001 — 讓取消與測試防護認得這個子程序
        assert self._proc.stdin is not None
        self._proc.stdin.write(json.dumps(self.spec.to_dict(), ensure_ascii=False).encode())
        self._proc.stdin.close()
        self._thread = threading.Thread(target=self._read, args=(read_fd,), daemon=True)
        self._thread.start()

    def _read(self, fd: int) -> None:
        result: dict[str, Any] | None = None
        with os.fdopen(fd, "rb") as fh:
            for raw in fh:
                try:
                    event = json.loads(raw)
                except ValueError:
                    continue
                if event.get("type") == "progress" and self.on_progress:
                    msg = event.get("message")
                    self.on_progress(
                        float(event.get("fraction", 0)), msg if isinstance(msg, str) else None
                    )
                elif event.get("type") == "result":
                    result = event
        assert self._proc is not None
        self._proc.wait()
        process._unregister(self._proc.pid)  # noqa: SLF001
        if self._cancelled.is_set():
            result = {
                "type": "result",
                "action": self.spec.id,
                "error": "已取消。",
                "cancelled": True,
            }
        elif result is None:
            result = {
                "type": "result",
                "action": self.spec.id,
                "error": f"處理程序意外結束（代碼 {self._proc.returncode}）。",
            }
        self.result = result
        if self.on_done:
            self.on_done(result)

    def cancel(self) -> None:
        self._cancelled.set()
        proc = self._proc
        if proc is None or proc.poll() is not None:
            return
        try:
            os.killpg(proc.pid, signal.SIGTERM)
        except ProcessLookupError:
            return

        def escalate() -> None:
            try:
                proc.wait(timeout=CANCEL_GRACE_SECONDS)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

        threading.Thread(target=escalate, daemon=True).start()

    def wait(self, timeout: float | None = None) -> dict[str, Any] | None:
        if self._thread is not None:
            self._thread.join(timeout)
        return self.result
