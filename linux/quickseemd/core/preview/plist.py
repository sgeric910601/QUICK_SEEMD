"""二進位 plist → XML（對應原版 QSPreviewEngine.binaryPlistPage 的轉換部分）。

Linux 版補充：二進位 plist 裡同一個物件可以被引用很多次，轉成 XML 時每次都會寫一遍，
小檔案可能展開成好幾 GB。轉換前先估算展開後的大小（走訪有上限），超過 MAX_XML_BYTES 就不轉換。
"""

from __future__ import annotations

import logging
import plistlib
from datetime import datetime

from .page import FileInfo

log = logging.getLogger(__name__)

MAX_PLIST_BYTES = 32 * 1024 * 1024
MAX_XML_BYTES = 4 * 1024 * 1024
MAX_NODES = 500_000
_PER_NODE = 24  # 標籤與縮排的大約位元組數


def _estimate(obj: object, budget: int) -> bool:
    """展開後是否在 budget 位元組以內（走訪的節點數也有上限；循環引用會因此停下）。"""
    total = nodes = 0
    stack = [obj]
    while stack:
        item = stack.pop()
        nodes += 1
        if nodes > MAX_NODES:
            return False
        total += _PER_NODE
        if isinstance(item, dict):
            stack.extend(item.values())
            total += sum(len(str(k)) for k in item)
        elif isinstance(item, list | tuple):
            stack.extend(item)
        elif isinstance(item, str):
            total += len(item) * 2
        elif isinstance(item, bytes | bytearray):
            total += len(item) * 4 // 3 + 4
        elif not isinstance(item, int | float | bool | datetime):
            return False  # UID 等 XML 不支援的型別
        if total > budget:
            return False
    return True


def to_xml(info: FileInfo) -> str | None:
    """轉成 XML 文字；太大、損毀或展開過大時回傳 None。"""
    if info.size >= MAX_PLIST_BYTES or not info.regular:
        return None
    try:
        data = info.path.read_bytes()
        obj = plistlib.loads(data)
    except Exception:  # noqa: BLE001 — 壞檔案會丟出各種例外，一律當成不是 plist
        log.debug("plist 解析器例外：%s", info.path, exc_info=True)
        return None
    if not _estimate(obj, MAX_XML_BYTES):
        return None
    try:
        xml = plistlib.dumps(obj, fmt=plistlib.FMT_XML)
    except (TypeError, ValueError, OverflowError, RecursionError):
        log.debug("無法轉成 XML：%s", info.path, exc_info=True)
        return None
    return xml.decode("utf-8", "replace")
