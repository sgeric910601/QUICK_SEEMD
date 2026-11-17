"""影片尺寸計算（對應原版 MTGeometry）。所有輸出尺寸都是偶數。"""

from __future__ import annotations

import math

Size = tuple[int, int]


def round_half_away(value: float) -> int:
    """Swift 的 .rounded()（.5 遠離 0）。"""
    return int(math.floor(value + 0.5)) if value >= 0 else -int(math.floor(-value + 0.5))


def even(value: float) -> int:
    """四捨五入後取偶數（至少 2）。"""
    i = max(2, round_half_away(value))
    return i - i % 2


def even_size(width: float, height: float) -> Size:
    return even(width), even(height)


def capped(width: float, height: float, cap: int) -> Size | None:
    """短邊上限（不放大）；不需要縮小時回傳 None。"""
    short = min(width, height)
    if cap <= 0 or short <= cap + 1:
        return None
    scale = cap / short
    return even_size(width * scale, height * scale)


def fit_inside(width: float, height: float, box_w: float, box_h: float) -> Size:
    """等比例放進 box_w × box_h（只縮小、不放大），四捨五入成整數（至少 1）。"""
    if width <= 0 or height <= 0:
        return max(1, int(width)), max(1, int(height))
    scale = min(1.0, box_w / width, box_h / height)
    return max(1, round_half_away(width * scale)), max(1, round_half_away(height * scale))
