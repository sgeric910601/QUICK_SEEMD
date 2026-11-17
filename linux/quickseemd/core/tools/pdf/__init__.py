"""PDF 類工具（對應原版 App/Tools/PDF）。

每個模組定義一到數個工具；這裡收集成 TOOLS（顯示順序由 registry.ORDER 決定）。
"""

from __future__ import annotations

from ...model import ToolDefinition
from .compress import COMPRESS
from .organize import ORGANIZE, ROTATE
from .pages import DELETE_PAGES, MERGE, SPLIT
from .security import METADATA, PROTECT, UNLOCK
from .stamp import PAGE_NUMBERS, WATERMARK

TOOLS: list[ToolDefinition] = [
    MERGE,
    SPLIT,
    COMPRESS,
    ORGANIZE,
    DELETE_PAGES,
    ROTATE,
    WATERMARK,
    PAGE_NUMBERS,
    PROTECT,
    UNLOCK,
    METADATA,
]
