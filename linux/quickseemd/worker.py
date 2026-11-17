"""工作程序：從標準輸入讀 JobSpec，執行，進度與結果寫到 QS_PROGRESS_FD。"""

from __future__ import annotations

import json
import os
import signal
import sys
import threading

from .core import errors, process
from .core.jobs import PROGRESS_FD_ENV
from .core.runner import JobSpec, run_spec


def main() -> int:
    fd_raw = os.environ.get(PROGRESS_FD_ENV)
    if not fd_raw or not fd_raw.isdigit():
        print("quickseemd.worker 必須由 QUICK_SEEMD 啟動", file=sys.stderr)
        return 2
    out = os.fdopen(int(fd_raw), "w", buffering=1, encoding="utf-8")
    write_lock = threading.Lock()

    def emit(event: dict) -> None:
        with write_lock:
            try:
                out.write(json.dumps(event, ensure_ascii=False) + "\n")
            except (BrokenPipeError, ValueError):
                pass

    cancel = threading.Event()

    def on_term(_signum, _frame) -> None:  # noqa: ANN001
        # 設旗標、結束外部程式，並在主執行緒丟出「已取消」：
        # 就算工具正在做不檢查取消的長運算，回到 Python 時也會中斷，執行器的 finally 會清掉暫存資料夾
        first = not cancel.is_set()
        cancel.set()
        process.terminate_all()
        if first:
            raise errors.Cancelled()

    signal.signal(signal.SIGTERM, on_term)

    try:
        spec = JobSpec.from_dict(json.loads(sys.stdin.read() or "{}"))
    except ValueError as exc:
        emit({"type": "result", "action": "", "error": f"工作描述不正確：{exc}"})
        return 2

    last = [-1.0]

    def report(fraction: float, message: str | None) -> None:
        # 進度變化太小就不送，避免塞爆管道（原版門檻 0.005）
        if fraction - last[0] < 0.005 and fraction < 1.0 and message is None:
            return
        last[0] = fraction
        emit({"type": "progress", "fraction": round(fraction, 4), "message": message})

    result = run_spec(spec, report, cancel)
    payload = {"type": "result", **result.to_json(), "skipped": result.skipped}
    emit(payload)
    try:
        out.close()
    except OSError:  # 讀取端（介面程序）已經離開，結果無處可送，照常結束
        pass
    return 0 if result.error is None else 1


if __name__ == "__main__":
    sys.exit(main())
