"""PDF 類工具（對應原版 App/Tools/PDF）。

每個模組定義一到數個工具；這裡收集成 TOOLS（顯示順序由 registry.ORDER 決定）。
"""

from __future__ import annotations

from ...model import ToolDefinition
from .organize import ORGANIZE, ROTATE
from .pages import DELETE_PAGES, MERGE, SPLIT

TOOLS: list[ToolDefinition] = [MERGE, SPLIT, ORGANIZE, DELETE_PAGES, ROTATE]
