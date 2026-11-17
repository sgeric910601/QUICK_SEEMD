"""影音時間的顯示與解析（對應原版 MTTime）。"""

from __future__ import annotations

import math
import re

# 原版 CharacterSet(",，、;；\n ")：時間點清單的分隔字元
_LIST_SEPARATORS = re.compile(r"[,，、;；\n ]+")
# 只接受一般十進位數字（原版 Double(...) 不吃全形數字、底線、正負號以外的寫法）
_NUMBER = re.compile(r"[0-9]+(\.[0-9]*)?|\.[0-9]+")


def _round_half_away(value: float) -> float:
    """Swift 的 .rounded()：四捨五入，.5 遠離 0（Python 的 round 是銀行家捨入）。"""
    return math.floor(value + 0.5) if value >= 0 else -math.floor(-value + 0.5)


def format_time(seconds: float, decimals: int = 1) -> str:
    """65.25 → "1:05.3"；3725 → "1:02:05.0"（decimals=0 時不含小數）。"""
    s = max(0.0, seconds if math.isfinite(seconds) else 0.0)
    factor = 10**decimals
    rounded = _round_half_away(s * factor) / factor
    whole = int(rounded)
    h, m = whole // 3600, (whole % 3600) // 60
    sec = rounded - (h * 3600 + m * 60)
    if decimals > 0:
        sec_text = f"%0{decimals + 3}.{decimals}f" % sec
    else:
        sec_text = "%02d" % int(sec)
    return f"{h}:{m:02d}:{sec_text}" if h > 0 else f"{m}:{sec_text}"


def short(seconds: float) -> str:
    """短格式：0:15、1:02:05。"""
    return format_time(seconds, 0)


def parse_time(text: str) -> float | None:
    """解析「75」「75.5」「1:15」「1:15.5」「0:01:15」；不正確時回傳 None。"""
    t = text.strip(" \t").replace("：", ":").replace("。", ".")
    t = t.replace("s", "").replace("秒", "")
    if not t:
        return None
    parts = t.split(":")
    if len(parts) > 3:
        return None
    total = 0.0
    for i, part in enumerate(parts):
        if not _NUMBER.fullmatch(part):
            return None
        value = float(part)
        is_last = i == len(parts) - 1
        if not is_last and value != int(value):
            return None
        if i > 0 and value >= 60:
            return None
        total = total * 60 + value
    return total


def parse_times(text: str) -> list[float] | None:
    """解析「0:05, 1:30 2:00」；格式錯誤或沒有任何時間時回傳 None。"""
    parts = [p for p in _LIST_SEPARATORS.split(text) if p]
    if not parts:
        return None
    out: list[float] = []
    for p in parts:
        t = parse_time(p)
        if t is None:
            return None
        out.append(t)
    return out


def file_label(seconds: float) -> str:
    """檔名用：65.5 → "01m05.5s"、5 → "00m05.0s"、3725.25 → "1h02m05.2s"。"""
    s = max(0.0, seconds)
    h, m = int(s) // 3600, (int(s) % 3600) // 60
    sec = s - (h * 3600 + m * 60)
    sec_text = "%04.1f" % (math.floor(sec * 10) / 10)
    return (f"{h}h" if h > 0 else "") + f"{m:02d}m" + sec_text + "s"
