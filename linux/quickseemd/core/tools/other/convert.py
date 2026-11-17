"""萬用格式轉換的工具卡片（對應原版 OtherToolCatalog.converter）。

這個工具在主程式裡整頁換成萬用轉換畫面（custom_page="converter"），轉換本身在 core/converter；
直接執行（例如命令列 quickseemd run other.convert）時只提示要到主程式選格式。
"""

from __future__ import annotations

from pathlib import Path

from ... import errors
from ...model import OTHER, TINTS, ToolContext, ToolDefinition
from ...options import Values

CONVERTER_ID = "other.convert"
RUN_MESSAGE = "請在主程式的「萬用格式轉換」選擇要轉成的格式。"


def run(files: list[Path], values: Values, ctx: ToolContext) -> list[Path]:  # noqa: ARG001
    raise errors.QSError(RUN_MESSAGE)


TOOL = ToolDefinition(
    id=CONVERTER_ID,
    category=OTHER,
    title="萬用格式轉換",
    subtitle="任何檔案丟進來，列出這台電腦能轉的所有格式：圖片、文件、影音、資料、編碼、壓縮檔。",
    icon="arrow.triangle.2.circlepath",
    run=run,
    input_types=("item",),
    input_label="檔案",
    action_title="轉換",
    keywords=("convert", "any", "格式", "轉檔", "萬用"),
    tint=TINTS["teal"],
    badge="ALL",
    custom_page="converter",
)
