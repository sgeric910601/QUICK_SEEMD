"""進度面板每一列要顯示什麼（純邏輯；對應原版 QuickActionRow）。"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

from ..core import registry
from ..core.model import RGB
from .queue import QueueItem, Status


HUD_WIDTH = 360
HUD_MARGIN_RIGHT = 14
HUD_MARGIN_TOP = 10
HIDE_SECONDS = 7
MAX_OPEN_OUTPUTS = 3

BUTTON_LABELS: dict[str, str] = {
    "reveal": "在資料夾中顯示",
    "open": "開啟",
    "cancel": "取消",
    "adjust": "在 QUICK_SEEMD 中調整選項…",
}


def file_summary(files: Sequence[Path]) -> str:
    return files[0].name if len(files) == 1 else f"{len(files)} 個檔案"


def state_line(item: QueueItem) -> str:
    """標題下面那一行字。"""
    if item.status is Status.WAITING:
        return f"等待中：{file_summary(item.files)}"
    if item.status is Status.RUNNING:
        return item.message or file_summary(item.files)
    if item.status is Status.DONE:
        if not item.outputs:
            return item.message or "完成"
        if len(item.outputs) == 1:
            return item.outputs[0].name
        return f"完成 {len(item.outputs)} 個檔案"
    return item.reason


def line_style(item: QueueItem) -> str:
    return {
        Status.WAITING: "secondary",
        Status.RUNNING: "progress",
        Status.DONE: "success",
        Status.FAILED: "warning",
    }[item.status]


def adjust_tool(item: QueueItem) -> str | None:
    """失敗時「在 QUICK_SEEMD 中調整選項…」要打開的工具（必須是真的工具）。"""
    if item.status is not Status.FAILED or not item.tool_id:
        return None
    return item.tool_id if registry.tool(item.tool_id) is not None else None


def buttons(item: QueueItem) -> tuple[str, ...]:
    if item.status is Status.RUNNING:
        return ("cancel",)
    if item.status is Status.DONE and item.outputs:
        if len(item.outputs) <= MAX_OPEN_OUTPUTS:
            return ("reveal", "open")
        return ("reveal",)
    if adjust_tool(item):
        return ("adjust",)
    return ()


def hud_origin(workarea: tuple[int, int, int, int], width: int) -> tuple[int, int]:
    """面板左上角：主螢幕可用範圍的右上角（x＝右緣－寬－14，y＝上緣＋10）。"""
    x, y, w, _h = workarea
    return x + w - width - HUD_MARGIN_RIGHT, y + HUD_MARGIN_TOP


def rgb_hex(rgb: RGB) -> str:
    return "#" + "".join(f"{int(round(min(max(c, 0.0), 1.0) * 255)):02x}" for c in rgb)
