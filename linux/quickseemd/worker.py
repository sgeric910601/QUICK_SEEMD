"""工作程序：從標準輸入讀 JobSpec，執行，進度與結果寫到 QS_PROGRESS_FD。"""

from __future__ import annotations

import json
import os
import signal
import sys
import threading

from .core import process
from .core.jobs import PROGRESS_FD_ENV
from .core.runner import JobSpec, run_spec


MEM_LIMIT_ENV = "QS_WORKER_MEM_GB"
DEFAULT_MEM_LIMIT_GB = 8.0


def cap_memory() -> int:
    """限制工作程序（與它啟動的外部程式）的資料區大小，惡意檔案最多吃掉這麼多記憶體。"""
    import resource  # noqa: PLC0415

    try:
        gb = float(os.environ.get(MEM_LIMIT_ENV, DEFAULT_MEM_LIMIT_GB))
    except ValueError:
        gb = DEFAULT_MEM_LIMIT_GB
    limit = int(gb * 1024**3)
    soft, hard = resource.getrlimit(resource.RLIMIT_DATA)
    if hard != resource.RLIM_INFINITY:
        limit = min(limit, hard)
    if soft == resource.RLIM_INFINITY or soft > limit:
        resource.setrlimit(resource.RLIMIT_DATA, (limit, hard))
    return limit


def main() -> int:
    fd_raw = os.environ.get(PROGRESS_FD_ENV)
    if not fd_raw or not fd_raw.isdigit():
        print("quickseemd.worker 必須由 QUICK_SEEMD 啟動", file=sys.stderr)
        return 2
    cap_memory()
    # 載入圖片像素上限（Pillow 的 MAX_IMAGE_PIXELS 與「解壓縮炸彈」警告當錯誤）：
    # 讓每個工具、萬用轉換裡直接 Image.open 的地方也一起受保護
    from .core.tools.image import limits as _image_limits  # noqa: F401, PLC0415

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
        # 只設旗標並結束外部程式：不在主執行緒任意位置丟例外（寫檔寫到一半被打斷會留下殘檔）。
        # 工具在下一次檢查取消時結束；檢查不到的長運算會被介面在 3 秒後強制結束，
        # 留下的暫存資料夾由下次啟動時的清理處理
        cancel.set()
        process.terminate_all()

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
