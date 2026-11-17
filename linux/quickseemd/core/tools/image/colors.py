"""顏色（對應原版 ITRGBA 與 ITColorChoice）。

選項裡的顏色值：
- "#rrggbb" 或 "#rrggbbaa"
- "transparent"：透明
- "custom"：自訂色，實際顏色存在選項的 custom_key（"#rrggbb"；沒設定時用工具的預設自訂色）
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from ...options import Choice


@dataclass(frozen=True)
class Rgba:
    """sRGB 顏色，各分量 0–1。"""

    r: float
    g: float
    b: float
    a: float = 1.0

    @property
    def is_opaque(self) -> bool:
        return self.a >= 0.999

    @property
    def is_light(self) -> bool:
        """原版陰影顏色的判斷：r+g+b > 1.5 算淺色。"""
        return self.r + self.g + self.b > 1.5

    def to8(self) -> tuple[int, int, int, int]:
        return tuple(max(0, min(255, round(v * 255))) for v in (self.r, self.g, self.b, self.a))

    def rgb8(self) -> tuple[int, int, int]:
        r, g, b, _a = self.to8()
        return (r, g, b)

    def opacity(self, value: float) -> Rgba:
        return Rgba(self.r, self.g, self.b, self.a * value)


WHITE = Rgba(1, 1, 1)
BLACK = Rgba(0, 0, 0)
CLEAR = Rgba(0, 0, 0, 0)
GRAY = Rgba(0.5, 0.5, 0.5)  # SwiftUI Color.gray


def _from_hex(text: str) -> Rgba | None:
    raw = text.strip().lstrip("#")
    if len(raw) not in (6, 8):
        return None
    try:
        parts = [int(raw[i : i + 2], 16) / 255 for i in range(0, len(raw), 2)]
    except ValueError:
        return None
    return Rgba(*parts)


def _from_any(value: Any) -> Rgba | None:
    if isinstance(value, Rgba):
        return value
    if isinstance(value, str):
        if value == "transparent":
            return CLEAR
        return _from_hex(value)
    if isinstance(value, (list, tuple)) and len(value) in (3, 4):
        try:
            return Rgba(*(float(v) for v in value))
        except (TypeError, ValueError):
            return None
    return None


def parse(value: Any, custom: Any = None, default_custom: Rgba = WHITE) -> Rgba:
    """選項值 → 顏色；看不懂的值當成白色。"""
    if value == "custom":
        return _from_any(custom) or default_custom
    return _from_any(value) or WHITE


def hex_of(color: Rgba) -> str:
    r, g, b = color.rgb8()
    return f"#{r:02x}{g:02x}{b:02x}"


# 原版 ITColorChoice 的四種選擇
TRANSPARENT_CHOICE = Choice("transparent", "透明", icon="checkerboard.rectangle")
WHITE_CHOICE = Choice("#ffffff", "白色", icon="circle")
BLACK_CHOICE = Choice("#000000", "黑色", icon="circle.fill")
CUSTOM_CHOICE = Choice("custom", "自訂", icon="paintpalette")
