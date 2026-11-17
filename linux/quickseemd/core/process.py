"""執行外部程式（對應原版 CVProcess）。

- 參數一律是清單，不經過 shell。
- 每個子程序在自己的程序群組；取消時先 SIGTERM 整個群組，3 秒後還在就 SIGKILL。
- 只會對自己啟動的子程序群組送訊號（SPAWNED 記錄，測試防護也靠它）。
"""

from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, BinaryIO

from . import errors

KILL_GRACE_SECONDS = 3.0
_POLL_SECONDS = 0.1

# 這個程序啟動過、還可能活著的子程序 pid（＝程序群組 id）
SPAWNED: set[int] = set()
_SPAWNED_LOCK = threading.Lock()


@dataclass(frozen=True)
class Result:
    returncode: int
    stdout: str
    stderr: str


def _register(pid: int) -> None:
    with _SPAWNED_LOCK:
        SPAWNED.add(pid)


def _unregister(pid: int) -> None:
    with _SPAWNED_LOCK:
        SPAWNED.discard(pid)


def terminate_group(proc: subprocess.Popen[bytes]) -> None:
    """結束我們自己啟動的子程序整個群組。

    不在 SPAWNED 裡的 pid 一律不送訊號（刻意的防護：絕不碰不是我們啟動的程序）。
    """
    if proc.poll() is not None:
        return
    pid = proc.pid
    with _SPAWNED_LOCK:
        if pid not in SPAWNED:
            return
    try:
        os.killpg(pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        proc.wait(timeout=KILL_GRACE_SECONDS)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        proc.wait()


def terminate_all() -> None:
    """結束所有還在跑的子程序群組（工作程序收到取消時用）。

    會在訊號處理函式裡呼叫：不拿鎖（主執行緒可能正握著鎖，拿鎖會卡死），
    直接複製一份清單；複製時剛好被改動就重試。
    """
    for _ in range(5):
        try:
            pids = tuple(SPAWNED)
            break
        except RuntimeError:  # 複製途中集合被改動
            continue
    else:
        pids = ()
    for pid in pids:
        try:
            os.killpg(pid, signal.SIGTERM)
        except ProcessLookupError:
            pass


def _check_args(args: Sequence[str]) -> list[str]:
    out = [os.fspath(a) for a in args]
    if not out or not os.path.isabs(out[0]):
        raise ValueError("外部程式必須用絕對路徑（從 backends 取得）")
    if any("\0" in a for a in out):
        raise ValueError("參數不能包含 NUL 字元")
    return out


def run(
    args: Sequence[str | os.PathLike[str]],
    *,
    cancel: threading.Event | None = None,
    on_line: Callable[[str], None] | None = None,
    stdout_path: Path | None = None,
    stdin_bytes: bytes | None = None,
    env: dict[str, str] | None = None,
    cwd: Path | None = None,
    timeout: float | None = None,
) -> Result:
    """執行並等待結束。on_line 會逐行收到標準輸出（例如 ffmpeg -progress）。

    stdout_path 的檔案以獨佔方式建立；取消或逾時時留下的半成品由呼叫端的暫存資料夾一起清掉。
    """
    argv = _check_args([os.fspath(a) for a in args])
    tool = os.path.basename(argv[0])
    proc, out_file = _spawn(argv, stdout_path, stdin_bytes is not None, env, cwd)
    pump = _Pump(proc, on_line, stdin_bytes)
    try:
        outcome = _wait(proc, cancel, timeout)
    except BaseException:
        terminate_group(proc)
        raise
    finally:
        pump.join()
        _unregister(proc.pid)
        if out_file is not None:
            out_file.close()
    # 工作程序收到取消時會先結束子程序群組，wait 可能先回來：以取消旗標為準
    if outcome == "cancelled" or (cancel is not None and cancel.is_set()):
        raise errors.Cancelled()
    if outcome == "timeout":
        raise errors.tool_failed(tool, "執行逾時。")
    if pump.callback_error is not None:
        raise pump.callback_error
    return Result(proc.returncode, pump.stdout_text(), pump.stderr_text())


def _spawn(
    argv: list[str],
    stdout_path: Path | None,
    has_stdin: bool,
    env: dict[str, str] | None,
    cwd: Path | None,
) -> tuple[subprocess.Popen[bytes], BinaryIO | None]:
    """啟動子程序（自己的程序群組）；失敗時關掉已開的輸出檔並丟出中文錯誤。"""
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    out_file = open(stdout_path, "xb") if stdout_path is not None else None  # noqa: SIM115
    try:
        proc = subprocess.Popen(  # noqa: S603 — argv 已檢查且不經過 shell
            argv,
            stdin=subprocess.PIPE if has_stdin else subprocess.DEVNULL,
            stdout=out_file if out_file is not None else subprocess.PIPE,
            stderr=subprocess.PIPE,
            env=full_env,
            cwd=str(cwd) if cwd is not None else None,
            start_new_session=True,
        )
    except OSError as exc:
        if out_file is not None:
            out_file.close()
        tool = os.path.basename(argv[0])
        if isinstance(exc, FileNotFoundError):
            raise errors.tool_missing(tool) from exc
        raise errors.tool_failed(tool, exc.strerror or str(exc)) from exc
    _register(proc.pid)
    return proc, out_file


class _Pump:
    """背景執行緒讀標準輸出／錯誤、寫標準輸入。

    回呼出錯時記下來繼續把輸出讀完（不然子程序會塞住），等程序結束後再丟出。
    """

    def __init__(
        self,
        proc: subprocess.Popen[bytes],
        on_line: Callable[[str], None] | None,
        stdin_bytes: bytes | None,
    ) -> None:
        self.proc = proc
        self.on_line = on_line
        self.callback_error: BaseException | None = None
        self._stdout: list[str] = []
        self._stderr: list[bytes] = []
        targets: list[tuple[Callable[..., None], tuple]] = [(self._read_stderr, ())]
        if proc.stdout is not None:
            targets.append((self._read_stdout, ()))
        if stdin_bytes is not None:
            targets.append((self._write_stdin, (stdin_bytes,)))
        self._threads = [threading.Thread(target=f, args=a, daemon=True) for f, a in targets]
        for t in self._threads:
            t.start()

    def _read_stdout(self) -> None:
        assert self.proc.stdout is not None
        for raw in iter(self.proc.stdout.readline, b""):
            line = raw.decode("utf-8", "replace")
            if self.on_line is None:
                self._stdout.append(line)
            elif self.callback_error is None:
                try:
                    self.on_line(line.rstrip("\n"))
                except BaseException as exc:  # noqa: BLE001 — 讀完再由主執行緒丟出
                    self.callback_error = exc

    def _read_stderr(self) -> None:
        assert self.proc.stderr is not None
        for raw in iter(lambda: self.proc.stderr.read(65536), b""):  # type: ignore[union-attr]
            self._stderr.append(raw)

    def _write_stdin(self, data: bytes) -> None:
        assert self.proc.stdin is not None
        try:
            self.proc.stdin.write(data)
        except OSError:  # 子程序不讀標準輸入就結束了，照常等它的結果
            pass
        finally:
            try:
                self.proc.stdin.close()
            except OSError:
                pass

    def join(self) -> None:
        for t in self._threads:
            t.join(timeout=5)

    def stdout_text(self) -> str:
        return "".join(self._stdout)

    def stderr_text(self) -> str:
        return b"".join(self._stderr).decode("utf-8", "replace")


def _wait(
    proc: subprocess.Popen[bytes], cancel: threading.Event | None, timeout: float | None
) -> str:
    """等子程序結束；回傳 "done"、"cancelled" 或 "timeout"（後兩者已結束整個群組）。"""
    deadline = None if timeout is None else time.monotonic() + timeout
    while True:
        try:
            proc.wait(timeout=_POLL_SECONDS)
            return "done"
        except subprocess.TimeoutExpired:
            pass
        if cancel is not None and cancel.is_set():
            terminate_group(proc)
            return "cancelled"
        if deadline is not None and time.monotonic() >= deadline:
            terminate_group(proc)
            return "timeout"


def last_lines(text: str, n: int = 3) -> str:
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    return "\n".join(lines[-n:])


def run_checked(args: Sequence[str | os.PathLike[str]], tool_name: str, **kwargs: Any) -> Result:
    """非 0 結束時丟出「{工具} 轉換失敗：最後 3 行」。"""
    res = run(args, **kwargs)
    if res.returncode != 0:
        detail = last_lines(res.stderr) or last_lines(res.stdout)
        raise errors.tool_failed(tool_name, detail)
    return res
