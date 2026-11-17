"""預覽視窗用的文字格式（大小、日期、播放時間）。"""

from __future__ import annotations

import time

from gi.repository import GLib


def format_size(size: int) -> str:
    """未滿 1024 顯示「N 位元組」，其餘用 1000 進位（與原版相同的規則）。"""
    if size < 1024:
        return f"{size} 位元組"
    return GLib.format_size(size)


def format_time(epoch: float) -> str:
    return time.strftime("%Y/%m/%d %H:%M", time.localtime(epoch))


def format_duration(seconds: float) -> str:
    """播放時間：未滿一小時「m:ss」，否則「h:mm:ss」；負數或無效值當成 0。"""
    if not seconds or seconds < 0 or seconds != seconds:
        seconds = 0
    total = int(seconds)
    hours, rest = divmod(total, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"
