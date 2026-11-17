"""工具頁的自訂畫面（整理 PDF、裁剪圖片、剪輯影音），取代檔案縮圖區。

*_model.py 是不需要畫面的純邏輯；同名的介面模組才載入 GTK（用到時才載入）。
工具以 ToolDefinition.custom_view 指定："pdf-organize"、"image-crop"、"media-trim"、"audio-trim"。
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from ...core.model import ToolDefinition
from ..options_model import OptionsState

LOG = logging.getLogger(__name__)

# 自訂畫面代碼 → (模組, 類別)
REGISTRY: dict[str, tuple[str, str]] = {
    "pdf-organize": ("organize", "OrganizeView"),
    "image-crop": ("crop", "CropView"),
    "media-trim": ("trim", "TrimView"),
    "audio-trim": ("trim", "AudioTrimView"),
}


@dataclass
class ViewContext:
    tool: ToolDefinition
    options: OptionsState
    remove_file: Callable[[], None]
    changed: Callable[[], None]  # 自訂畫面的檢查結果可能變了（例如讀完 PDF）


class CustomView(Protocol):
    widget: Any  # 取代檔案縮圖區的元件
    panel: Any  # 放在選項面板最上方的元件（可以是 None）

    def set_file(self, path: Path) -> None: ...

    def validate(self) -> str | None: ...

    def destroy(self) -> None: ...


def create(kind: str, ctx: ViewContext) -> CustomView | None:
    entry = REGISTRY.get(kind)
    if entry is None:
        LOG.warning("未知的自訂畫面：%s", kind)
        return None
    module = importlib.import_module(f"{__name__}.{entry[0]}")
    return getattr(module, entry[1])(ctx)
