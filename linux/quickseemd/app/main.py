"""主程式入口：命令列 `quickseemd [open …]` 呼叫 main(argv)。

argv 從 "open" 開始（或是空的），例如 ["open", "--tool", "pdf.merge", "a.pdf"]。
"""

from __future__ import annotations

import logging
import os
import sys


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    level = logging.DEBUG if os.environ.get("QS_DEBUG") else logging.WARNING
    logging.basicConfig(level=level, format="%(name)s: %(message)s")
    from .application import QuickSeemdApp  # noqa: PLC0415 — 用到才載入 GTK

    app = QuickSeemdApp()
    return int(app.run(["quickseemd", *args]))


if __name__ == "__main__":
    sys.exit(main())
