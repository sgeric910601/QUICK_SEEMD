"""把背景執行緒的結果送回主執行緒。

用一般優先權（PRIORITY_DEFAULT）而不是預設的閒置優先權：播放影片時主迴圈一直有
較高優先權的事件，閒置優先權的回呼會一直排不到。
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from gi.repository import GLib


def _call(func: Callable[..., Any], args: tuple[Any, ...]) -> bool:
    func(*args)
    return False


def to_main(func: Callable[..., Any], *args: Any) -> None:
    """稍後在主執行緒呼叫 func(*args) 一次。"""
    GLib.idle_add(_call, func, args, priority=GLib.PRIORITY_DEFAULT)
