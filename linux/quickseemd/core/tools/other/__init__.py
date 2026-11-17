"""其他類工具（對應原版 OtherToolCatalog）：萬用轉換、壓縮、解壓縮、文字編碼、字幕、QR Code、校驗碼。"""

from __future__ import annotations

from ...model import ToolDefinition
from . import checksum, compress, convert, encoding, extract, readqr, subtitles

TOOLS: list[ToolDefinition] = [
    convert.TOOL,
    compress.TOOL,
    extract.TOOL,
    encoding.TOOL,
    subtitles.TOOL,
    readqr.TOOL,
    checksum.TOOL,
]
