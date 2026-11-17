"""影音類工具（對應原版 MediaToolCatalog）：全部用 ffmpeg／ffprobe 處理。

顯示順序由 registry.ORDER 決定，這裡的順序照原版。
"""

from __future__ import annotations

from ...model import ToolDefinition
from . import audio_tools, compress, convert, edit, frames, gif, merge, resize, speed, trim

TOOLS: list[ToolDefinition] = [
    convert.TOOL,
    compress.TOOL,
    trim.TOOL,
    gif.TOOL,
    audio_tools.EXTRACT_TOOL,
    edit.MUTE_TOOL,
    edit.ROTATE_TOOL,
    merge.TOOL,
    frames.TOOL,
    speed.TOOL,
    resize.TOOL,
    audio_tools.CONVERT_TOOL,
    audio_tools.TRIM_TOOL,
]
