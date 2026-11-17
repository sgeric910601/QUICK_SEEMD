"""裁剪框的幾何（對應原版 ITCropSession／ITCropEditor），不需要畫面。

框用正規化座標（0…1，左上為原點，和原版一樣）；交給工具的值是原圖像素：
選項 `crop` = JSON 字串 `{"x":…, "y":…, "w":…, "h":…}`（已依 EXIF 轉正後的像素）。
比例來自選項 `ratio`（見 options_model.parse_ratio）。
"""

from __future__ import annotations

import json
from dataclasses import dataclass

HANDLES = ("tl", "tr", "bl", "br", "t", "b", "l", "r")
CORNERS = ("tl", "tr", "bl", "br")


@dataclass(frozen=True)
class Rect:
    x: float
    y: float
    w: float
    h: float


FULL = Rect(0.0, 0.0, 1.0, 1.0)


def centered(ratio: float, image_aspect: float) -> Rect:
    """依比例選出最大、置中的範圍（原版 centered(ratio:imageAspect:)）。"""
    if ratio > image_aspect:
        h = image_aspect / ratio
        return Rect(0.0, (1 - h) / 2, 1.0, h)
    w = ratio / image_aspect
    return Rect((1 - w) / 2, 0.0, w, 1.0)


def default_rect(ratio: float | None, width: int, height: int) -> Rect:
    if ratio is None or width <= 0 or height <= 0:
        return FULL
    return centered(ratio, width / height)


def pixel_rect(r: Rect, width: int, height: int) -> tuple[int, int, int, int]:
    x = min(max(int(round(r.x * width)), 0), max(width - 1, 0))
    y = min(max(int(round(r.y * height)), 0), max(height - 1, 0))
    w = min(max(int(round(r.w * width)), 1), width - x)
    h = min(max(int(round(r.h * height)), 1), height - y)
    return x, y, w, h


def to_value(r: Rect, width: int, height: int) -> str:
    x, y, w, h = pixel_rect(r, width, height)
    return json.dumps({"x": x, "y": y, "w": w, "h": h})


def from_value(text: str, width: int, height: int) -> Rect | None:
    try:
        data = json.loads(text)
        x, y, w, h = (float(data[k]) for k in ("x", "y", "w", "h"))
    except (ValueError, TypeError, KeyError):
        return None
    if width <= 0 or height <= 0:
        return None
    return Rect(x / width, y / height, w / width, h / height)


def size_text(r: Rect, width: int, height: int) -> str:
    _x, _y, w, h = pixel_rect(r, width, height)
    return f"{w} × {h} px"


def edge_handles_allowed(ratio: float | None) -> bool:
    """只有「自由」比例可以拖邊（原版）。"""
    return ratio is None


# ---------------------------------------------------------------- 拖曳


def move(r: Rect, dx: float, dy: float) -> Rect:
    x = min(max(r.x + dx, 0.0), 1.0 - r.w)
    y = min(max(r.y + dy, 0.0), 1.0 - r.h)
    return Rect(x, y, r.w, r.h)


def _resize_free(r: Rect, handle: str, dx: float, dy: float, min_size: float) -> Rect:
    left, top, right, bottom = r.x, r.y, r.x + r.w, r.y + r.h
    if "l" in handle:
        left = min(max(left + dx, 0.0), right - min_size)
    if "r" in handle:
        right = max(min(right + dx, 1.0), left + min_size)
    if "t" in handle:
        top = min(max(top + dy, 0.0), bottom - min_size)
    if "b" in handle:
        bottom = max(min(bottom + dy, 1.0), top + min_size)
    return Rect(left, top, right - left, bottom - top)


def _resize_ratio(
    r: Rect, handle: str, dx: float, dy: float, ratio: float, image_aspect: float, min_size: float
) -> Rect:
    k = image_aspect / ratio  # 正規化高度 = 寬 × k
    sx = -1.0 if "l" in handle else 1.0
    sy = -1.0 if "t" in handle else 1.0
    ax = r.x + r.w if sx < 0 else r.x  # 對角固定點
    ay = r.y + r.h if sy < 0 else r.y
    from_x = r.w + sx * dx
    from_y = (r.h + sy * dy) / k
    w = from_x if abs(from_x - r.w) >= abs(from_y - r.w) else from_y
    max_w = min(ax if sx < 0 else 1.0 - ax, (ay if sy < 0 else 1.0 - ay) / k)
    min_w = max(min_size, min_size / k)
    w = min(max(w, min(min_w, max_w)), max_w)
    h = w * k
    x = ax - w if sx < 0 else ax
    y = ay - h if sy < 0 else ay
    return Rect(x, y, w, h)


def resize(
    r: Rect,
    handle: str,
    dx: float,
    dy: float,
    ratio: float | None,
    image_aspect: float,
    min_size: float,
) -> Rect:
    """拖曳把手：自由比例四邊四角都能拖；固定比例只拖四角並維持比例。"""
    if handle not in HANDLES:
        raise ValueError(f"未知的把手：{handle}")
    if ratio is None or handle not in CORNERS:
        return _resize_free(r, handle, dx, dy, min_size)
    return _resize_ratio(r, handle, dx, dy, ratio, image_aspect, min_size)


def hit_test(r: Rect, px: float, py: float, tol: float, edges: bool = True) -> str | None:
    """正規化座標 (px, py) 落在哪個把手；框內是 "move"，框外是 None。"""
    xs = {"l": r.x, "r": r.x + r.w}
    ys = {"t": r.y, "b": r.y + r.h}
    for vy, yv in ys.items():
        for hx, xv in xs.items():
            if abs(px - xv) <= tol and abs(py - yv) <= tol:
                return vy + hx
    inside_x = r.x - tol <= px <= r.x + r.w + tol
    inside_y = r.y - tol <= py <= r.y + r.h + tol
    if edges:
        for name, yv in ys.items():
            if abs(py - yv) <= tol and inside_x:
                return name
        for name, xv in xs.items():
            if abs(px - xv) <= tol and inside_y:
                return name
    if r.x <= px <= r.x + r.w and r.y <= py <= r.y + r.h:
        return "move"
    return None
