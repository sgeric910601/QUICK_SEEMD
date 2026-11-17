"""圖片類工具（對應原版 ImageToolCatalog）；共用的讀寫流程在 loader／writer／pipeline。"""

from __future__ import annotations

from ...model import ToolDefinition
from . import (
    blur_faces,
    compress,
    convert,
    crop,
    make_icon,
    remove_bg,
    resize,
    rotate,
    strip_metadata,
    upscale,
    watermark,
)

TOOLS: list[ToolDefinition] = [
    convert.TOOL,
    compress.TOOL,
    resize.TOOL,
    crop.TOOL,
    rotate.TOOL,
    remove_bg.TOOL,
    watermark.TOOL,
    blur_faces.TOOL,
    strip_metadata.TOOL,
    upscale.TOOL,
    make_icon.TOOL,
]
