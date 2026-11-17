"""所有預覽共用的樣式表（對應原版 QSStyle；內容逐字照搬，只補上 Linux 字型）。"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

_CSS_FILE = Path(__file__).with_name("style.css")


@lru_cache(maxsize=1)
def css() -> str:
    """樣式表全文（結尾不含換行，與原版字串相同）。"""
    return _CSS_FILE.read_text("utf-8").removesuffix("\n")
