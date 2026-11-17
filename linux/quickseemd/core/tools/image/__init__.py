"""圖片類工具（對應原版 ImageToolCatalog）；共用的讀寫流程在 loader／writer／pipeline。"""

from __future__ import annotations

from ...model import ToolDefinition
from . import compress, convert, make_icon, strip_metadata

TOOLS: list[ToolDefinition] = [
    convert.TOOL,
    compress.TOOL,
    strip_metadata.TOOL,
    make_icon.TOOL,
]
