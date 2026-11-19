"""顯示用的格式化（對應原版 QSFormat）。

- 位元組：1024 以下寫「N 位元組」，以上照 macOS 檔案大小格式：每 KB 1000 位元組，
  KB 不帶小數、MB 一位、GB 以上兩位，小數尾端的 0 省略，四捨五入。
- 日期：短格式 yyyy/MM/dd HH:mm；長格式「2026年11月17日 下午1:26:00」（zh_TW 長日期＋中等時間）。
- 權限：rwxr-xr-x（755），括號是全形。
"""

from __future__ import annotations

from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal

NONE_MARK = "—"
_UNITS: tuple[tuple[str, int], ...] = (("KB", 0), ("MB", 1), ("GB", 2), ("TB", 2), ("PB", 2))
_BYTES_THRESHOLD = 1024
_STEP = 1000


def _round_label(value: Decimal, decimals: int) -> str:
    quantum = Decimal(1).scaleb(-decimals)
    rounded = value.quantize(quantum, rounding=ROUND_HALF_UP)
    text = f"{rounded:,.{decimals}f}"
    if "." in text:
        text = text.rstrip("0").rstrip(".")
    return text


def bytes_label(n: int) -> str:
    """檔案大小（例如「512 位元組」「8 KB」「2.1 MB」）。"""
    if n < _BYTES_THRESHOLD:
        return f"{n} 位元組"
    value = Decimal(n) / _STEP
    for index, (unit, decimals) in enumerate(_UNITS):
        quantum = Decimal(1).scaleb(-decimals)
        rounded = value.quantize(quantum, rounding=ROUND_HALF_UP)
        last = index == len(_UNITS) - 1
        if rounded < _STEP or last:
            return f"{_round_label(value, decimals)} {unit}"
        value = value / _STEP
    return f"{n} 位元組"  # 不會走到這裡；保留給型別檢查


def number(n: int) -> str:
    """千分位逗號。"""
    return f"{n:,}"


def date(d: datetime | None) -> str:
    """短日期 yyyy/MM/dd HH:mm（本地時間）。"""
    if d is None:
        return NONE_MARK
    return f"{d.year:04d}/{d.month:02d}/{d.day:02d} {d.hour:02d}:{d.minute:02d}"


def date_long(d: datetime | None) -> str:
    """長日期「2026年11月17日 下午1:26:00」。"""
    if d is None:
        return NONE_MARK
    period = "上午" if d.hour < 12 else "下午"
    hour = d.hour % 12 or 12
    return f"{d.year}年{d.month}月{d.day}日 {period}{hour}:{d.minute:02d}:{d.second:02d}"


def permissions(mode: int | None) -> str:
    """rwxr-xr-x（755）；只看最低 9 個位元。"""
    if mode is None:
        return NONE_MARK
    chars = "rwxrwxrwx"
    bits = "".join(chars[i] if (mode >> (8 - i)) & 1 else "-" for i in range(9))
    return f"{bits}（{mode & 0o777:o}）"
