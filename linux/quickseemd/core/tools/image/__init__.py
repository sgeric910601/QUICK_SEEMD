"""圖片類工具（對應原版 ImageToolCatalog）；共用的讀寫流程在 loader／writer／pipeline。"""

from __future__ import annotations

from ...model import ToolDefinition
from . import convert, make_icon

TOOLS: list[ToolDefinition] = [
    convert.TOOL,
    make_icon.TOOL,
]
