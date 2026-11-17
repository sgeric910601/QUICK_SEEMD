"""圖片類工具（對應原版 ImageToolCatalog）；共用的讀寫流程在 loader／writer／pipeline。"""

from __future__ import annotations

from ...model import ToolDefinition
from . import (
    blur_faces,
    compress,
    convert,
    crop,
    make_icon,
    resize,
    rotate,
    strip_metadata,
    upscale,
)

TOOLS: list[ToolDefinition] = [
    convert.TOOL,
    compress.TOOL,
    resize.TOOL,
    crop.TOOL,
    rotate.TOOL,
    blur_faces.TOOL,
    strip_metadata.TOOL,
    upscale.TOOL,
    make_icon.TOOL,
]
