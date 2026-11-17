"""預覽視窗的大小與位置（純計算，不碰 GTK）。

- 大小：內容想要的大小，最多螢幕可用區域的 85%，最少 MIN_WIDTH × MIN_HEIGHT。
- 圖片與影片：照原始比例縮小到放得下（不放大），再加上標題列的高度。
- 位置：在 Nemo 視窗上方置中；沒有父視窗時在螢幕置中；一律留在螢幕可用區域內。
"""

from __future__ import annotations

from dataclasses import dataclass

SCREEN_RATIO = 0.85
MIN_WIDTH = 360
MIN_HEIGHT = 240
HEADER_ALLOWANCE = 46  # 標題列（HeaderBar）大約的高度


@dataclass(frozen=True)
class Rect:
    x: int
    y: int
    width: int
    height: int


def max_size(workarea: Rect, ratio: float = SCREEN_RATIO) -> tuple[int, int]:
    return int(workarea.width * ratio), int(workarea.height * ratio)


def _bounded(value: int, minimum: int, maximum: int) -> int:
    """先套最小值再套最大值：螢幕太小時以最大值為準。"""
    return min(max(value, minimum), maximum)


def clamp_size(size: tuple[int, int], workarea: Rect) -> tuple[int, int]:
    max_w, max_h = max_size(workarea)
    return _bounded(int(size[0]), MIN_WIDTH, max_w), _bounded(int(size[1]), MIN_HEIGHT, max_h)


def fit_media(
    natural: tuple[float, float], workarea: Rect, extra_height: int = HEADER_ALLOWANCE
) -> tuple[int, int]:
    """圖片／影片的視窗大小：等比例縮小（不放大），加上標題列。"""
    nat_w, nat_h = natural
    if nat_w <= 0 or nat_h <= 0:
        return clamp_size((MIN_WIDTH, MIN_HEIGHT), workarea)
    max_w, max_h = max_size(workarea)
    scale = min(1.0, max_w / nat_w, max(max_h - extra_height, 1) / nat_h)
    width = round(nat_w * scale)
    height = round(nat_h * scale) + extra_height
    return clamp_size((width, height), workarea)


def center_on(size: tuple[int, int], parent: Rect | None, workarea: Rect) -> tuple[int, int]:
    """視窗左上角的位置：在父視窗（或螢幕）上置中，並留在可用區域內。"""
    base = parent or workarea
    width, height = size
    x = base.x + (base.width - width) // 2
    y = base.y + (base.height - height) // 2
    x = max(workarea.x, min(x, workarea.x + workarea.width - width))
    y = max(workarea.y, min(y, workarea.y + workarea.height - height))
    return x, y
