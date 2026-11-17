"""進度面板每一列要顯示什麼（純邏輯；對應原版 QuickActionRow）。"""

from __future__ import annotations

import logging
from collections.abc import Sequence
from pathlib import Path

from ..core import registry
from ..core.model import RGB
from .queue import QueueItem, Status

log = logging.getLogger(__name__)

HUD_WIDTH = 360
HUD_MARGIN_RIGHT = 14
HUD_MARGIN_TOP = 10
HIDE_SECONDS = 7
MAX_OPEN_OUTPUTS = 3
QR_ACTION_ID = "other.read-qr"
QR_MAX_BYTES = 1_000_000

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


def qr_payload(text: str) -> str:
    """QR 結果檔 → 要放進剪貼簿的內容（去掉「# 檔名」標題與它前面的空行）。"""
    lines = text.splitlines()
    out: list[str] = []
    for i, line in enumerate(lines):
        if line.startswith("# "):
            continue
        if not line.strip() and i + 1 < len(lines) and lines[i + 1].startswith("# "):
            continue
        out.append(line)
    return "\n".join(out).strip("\n")


def qr_clipboard_text(item: QueueItem) -> str | None:
    """「讀取 QR Code」成功後要複製的文字；不是這個動作或讀不到就回傳 None。"""
    if item.action_id != QR_ACTION_ID or item.status is not Status.DONE or not item.outputs:
        return None
    out = item.outputs[0]
    try:
        if out.stat().st_size > QR_MAX_BYTES:
            return None
        text = out.read_text("utf-8", errors="replace")
    except OSError as exc:
        log.warning("讀不到 QR 結果檔：%s", exc)
        return None
    payload = qr_payload(text)
    return payload or None
