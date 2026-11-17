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
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from . import errors

KILL_GRACE_SECONDS = 3.0
_POLL_SECONDS = 0.1

# 這個程序啟動過、還可能活著的子程序 pid（＝程序群組 id）
SPAWNED: set[int] = set()
_SPAWNED_LOCK = threading.Lock()


@dataclass
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
    """結束我們自己啟動的子程序整個群組。"""
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
    """結束所有還在跑的子程序群組（工作程序收到取消時用）。"""
    with _SPAWNED_LOCK:
        pids = list(SPAWNED)
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
    """執行並等待結束。on_line 會逐行收到標準輸出（例如 ffmpeg -progress）。"""
    argv = _check_args([os.fspath(a) for a in args])
    full_env = os.environ.copy()
    if env:
        full_env.update(env)
    stdout_target: int | object
    out_file = None
    if stdout_path is not None:
        out_file = open(stdout_path, "xb")  # noqa: SIM115 — 輸出檔必須是新檔
        stdout_target = out_file
    else:
        stdout_target = subprocess.PIPE
    try:
        proc = subprocess.Popen(  # noqa: S603 — argv 已檢查且不經過 shell
            argv,
            stdin=subprocess.PIPE if stdin_bytes is not None else subprocess.DEVNULL,
            stdout=stdout_target,
            stderr=subprocess.PIPE,
            env=full_env,
            cwd=str(cwd) if cwd else None,
            start_new_session=True,
        )
    except FileNotFoundError as exc:
        if out_file:
            out_file.close()
        raise errors.tool_missing(os.path.basename(argv[0])) from exc
    _register(proc.pid)
    stdout_chunks: list[str] = []
    stderr_chunks: list[bytes] = []
    cancelled = threading.Event()

    def read_stdout() -> None:
        assert proc.stdout is not None
        for raw in iter(proc.stdout.readline, b""):
            line = raw.decode("utf-8", "replace")
            if on_line is not None:
                on_line(line.rstrip("\n"))
            else:
                stdout_chunks.append(line)

    def read_stderr() -> None:
        assert proc.stderr is not None
        for raw in iter(lambda: proc.stderr.read(65536), b""):  # type: ignore[union-attr]
            stderr_chunks.append(raw)

    readers = [threading.Thread(target=read_stderr, daemon=True)]
    if stdout_path is None:
        readers.append(threading.Thread(target=read_stdout, daemon=True))
    for t in readers:
        t.start()
    if stdin_bytes is not None and proc.stdin is not None:
        try:
            proc.stdin.write(stdin_bytes)
            proc.stdin.close()
        except BrokenPipeError:
            pass

    waited = 0.0
    try:
        while True:
            try:
                proc.wait(timeout=_POLL_SECONDS)
                break
            except subprocess.TimeoutExpired:
                waited += _POLL_SECONDS
            if cancel is not None and cancel.is_set():
                cancelled.set()
                terminate_group(proc)
                break
            if timeout is not None and waited >= timeout:
                terminate_group(proc)
                break
    finally:
        for t in readers:
            t.join(timeout=5)
        _unregister(proc.pid)
        if out_file:
            out_file.close()
    if cancelled.is_set():
        raise errors.Cancelled()
    stderr = b"".join(stderr_chunks).decode("utf-8", "replace")
    if timeout is not None and waited >= timeout and proc.returncode != 0:
        raise errors.tool_failed(os.path.basename(argv[0]), "執行逾時。")
    return Result(proc.returncode, "".join(stdout_chunks), stderr)


def last_lines(text: str, n: int = 3) -> str:
    lines = [ln for ln in text.strip().splitlines() if ln.strip()]
    return "\n".join(lines[-n:])


def run_checked(args: Sequence[str | os.PathLike[str]], tool_name: str, **kwargs) -> Result:
    """非 0 結束時丟出「{工具} 轉換失敗：最後 3 行」。"""
    res = run(args, **kwargs)
    if res.returncode != 0:
        detail = last_lines(res.stderr) or last_lines(res.stdout)
        raise errors.tool_failed(tool_name, detail)
    return res
