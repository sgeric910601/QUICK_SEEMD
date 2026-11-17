"""主視窗的導覽狀態（對應原版 AppState），不需要畫面，介面訂閱變動後重畫。

側欄項目代碼："all"、"cat:<分類>"、"converter"、"preview"、"integration"。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from pathlib import Path

from ..core.model import CATEGORIES, ToolDefinition
from .routing import Route, is_converter

LOG = logging.getLogger(__name__)
SIDEBAR_FIXED = ("all", "converter", "preview", "integration")


def valid_sidebar(item: str) -> bool:
    if item in SIDEBAR_FIXED:
        return True
    return item.startswith("cat:") and item[4:] in {c.id for c in CATEGORIES}


class AppState:
    """側欄選擇、開著的工具、帶進工具的檔案、待選工具的檔案（inbox）。"""

    def __init__(self) -> None:
        self.sidebar = "all"
        self.open_tool_id: str | None = None
        self.pending_files: tuple[Path, ...] = ()
        self.inbox: tuple[Path, ...] = ()
        self.page_token = 0
        # 萬用轉換收檔案的地方（轉換頁建立後接上）
        self.converter_sink: Callable[[list[Path]], None] | None = None
        self._listeners: list[Callable[[], None]] = []

    # ------------------------------------------------------------ 訂閱

    def subscribe(self, callback: Callable[[], None]) -> Callable[[], None]:
        self._listeners.append(callback)
        return lambda: self._listeners.remove(callback)

    def _notify(self) -> None:
        for cb in list(self._listeners):
            cb()

    def page_key(self) -> str:
        return f"tool:{self.open_tool_id}" if self.open_tool_id else self.sidebar

    # ------------------------------------------------------------ 動作

    def select(self, item: str) -> None:
        if not valid_sidebar(item):
            raise ValueError(f"未知的側欄項目：{item}")
        self.sidebar = item
        self.open_tool_id = None
        self.pending_files = ()
        self._notify()

    def open(self, tool: ToolDefinition, files: Sequence[Path] = ()) -> None:
        files = tuple(Path(f) for f in files)
        if is_converter(tool) and files and self.converter_sink is not None:
            self.converter_sink(list(files))
        self.pending_files = files
        self.open_tool_id = tool.id
        self.page_token += 1
        if files:
            self.inbox = ()
        self._notify()

    def close_tool(self) -> None:
        self.open_tool_id = None
        self.pending_files = ()
        self._notify()

    def receive(self, files: Sequence[Path]) -> None:
        """收到檔案：回到工具大廳，只列出能處理它們的工具。"""
        if not files:
            return
        self.inbox = tuple(Path(f) for f in files)
        self.open_tool_id = None
        self.pending_files = ()
        self.sidebar = "all"
        self._notify()

    def clear_inbox(self) -> None:
        self.inbox = ()
        self._notify()

    def apply_route(self, route: Route) -> None:
        if route.kind == "tool" and route.tool is not None:
            self.open(route.tool, route.files)
        elif route.kind == "inbox":
            self.receive(route.files)
        else:
            LOG.debug("只顯示視窗")
