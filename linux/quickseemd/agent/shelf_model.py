"""浮動暫存的清單與尺寸（純邏輯；對應原版 FloatShelf）。不會搬動或複製原檔，也不存檔。"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path

SHELF_WIDTH = 250
ROW_HEIGHT = 46
HEADER_HEIGHT = 58
MAX_VISIBLE_ROWS = 7
RIGHT_OFFSET = 270
SHELF_TITLE = "浮動暫存"
CLEAR_TOOLTIP = "清空"
REMOVE_TOOLTIP = "移除"
ROW_TOOLTIP = "拖到任何地方使用；雙按打開"


@dataclass(frozen=True)
class ShelfModel:
    items: tuple[Path, ...] = ()

    def add(self, paths: Iterable[Path]) -> ShelfModel:
        out = list(self.items)
        for p in paths:
            if p not in out:
                out.append(p)
        return ShelfModel(tuple(out))

    def remove(self, path: Path) -> ShelfModel:
        return ShelfModel(tuple(p for p in self.items if p != path))

    def clear(self) -> ShelfModel:
        return ShelfModel()


def shelf_height(count: int) -> int:
    return min(count, MAX_VISIBLE_ROWS) * ROW_HEIGHT + HEADER_HEIGHT


def shelf_origin(workarea: tuple[int, int, int, int]) -> tuple[int, int]:
    """第一次出現的位置：螢幕右緣往內 270、垂直置中（原版 maxX−270, midY）。"""
    x, y, w, h = workarea
    return x + w - RIGHT_OFFSET, y + h // 2
