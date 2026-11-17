"""所有工具的清單（對應原版 ToolRegistry）。

每個分類的套件（quickseemd.core.tools.pdf 等）提供 TOOLS: list[ToolDefinition]。
顯示順序以這裡的 ORDER 為準（照原版），各分類的套件不必管順序。
"""

from __future__ import annotations

import importlib
import os
from collections.abc import Sequence
from functools import lru_cache

from .filetypes import FileRef
from .model import CATEGORIES, Category, ToolDefinition
from .quickactions import BY_ID as ACTIONS_BY_ID

ORDER: dict[str, tuple[str, ...]] = {
    "pdf": (
        "pdf.merge",
        "pdf.split",
        "pdf.compress",
        "pdf.to-images",
        "pdf.from-images",
        "pdf.organize",
        "pdf.delete-pages",
        "pdf.rotate",
        "pdf.watermark",
        "pdf.page-numbers",
        "pdf.protect",
        "pdf.unlock",
        "pdf.ocr",
        "pdf.to-text",
        "pdf.from-html",
        "pdf.metadata",
    ),
    "office": (
        "office.word-to-pdf",
        "office.excel-to-pdf",
        "office.ppt-to-pdf",
        "office.pdf-to-word",
        "office.pdf-to-excel",
        "office.pdf-to-ppt",
        "office.word-convert",
        "office.excel-convert",
        "office.ppt-convert",
    ),
    "image": (
        "image.convert",
        "image.compress",
        "image.resize",
        "image.crop",
        "image.rotate",
        "image.remove-bg",
        "image.watermark",
        "image.blur-faces",
        "image.ocr",
        "image.strip-metadata",
        "image.upscale",
        "image.make-icon",
    ),
    "media": (
        "video.convert",
        "video.compress",
        "video.trim",
        "video.to-gif",
        "video.extract-audio",
        "video.mute",
        "video.rotate",
        "video.merge",
        "video.frames",
        "video.speed",
        "video.resize",
        "audio.convert",
        "audio.trim",
    ),
    "other": (
        "other.convert",
        "other.compress",
        "other.extract",
        "other.text-encoding",
        "other.subtitles",
        "other.read-qr",
        "other.checksum",
    ),
}

# 原版選單列「常用工具」
FAVORITES: tuple[str, ...] = (
    "office.word-to-pdf",
    "office.pdf-to-word",
    "pdf.merge",
    "pdf.compress",
    "image.compress",
    "image.remove-bg",
    "video.compress",
    "video.to-gif",
)

_MODULES = {
    "pdf": "quickseemd.core.tools.pdf",
    "office": "quickseemd.core.tools.office",
    "image": "quickseemd.core.tools.image",
    "media": "quickseemd.core.tools.media",
    "other": "quickseemd.core.tools.other",
}


def _load_category(cat_id: str) -> list[ToolDefinition]:
    try:
        mod = importlib.import_module(_MODULES[cat_id])
    except ModuleNotFoundError as exc:
        if exc.name and exc.name.startswith("quickseemd.core.tools"):
            return []
        raise
    tools: list[ToolDefinition] = list(getattr(mod, "TOOLS", []))
    order = ORDER[cat_id]
    rank = {tid: i for i, tid in enumerate(order)}
    return sorted(tools, key=lambda t: rank.get(t.id, len(order)))


def _extra_tools() -> list[ToolDefinition]:
    """開發與測試用：QUICKSEEMD_EXTRA_TOOLS（逗號分隔的模組名稱）裡的 TOOLS 也一起載入。"""
    out: list[ToolDefinition] = []
    for name in filter(None, os.environ.get("QUICKSEEMD_EXTRA_TOOLS", "").split(",")):
        mod = importlib.import_module(name.strip())
        out.extend(getattr(mod, "TOOLS", []))
    return out


@lru_cache(maxsize=1)
def all_tools() -> tuple[ToolDefinition, ...]:
    out: list[ToolDefinition] = []
    for cat in CATEGORIES:
        out.extend(_load_category(cat.id))
    out.extend(_extra_tools())
    return tuple(out)


def by_id() -> dict[str, ToolDefinition]:
    return {t.id: t for t in all_tools()}


def tool(tool_id: str) -> ToolDefinition | None:
    return by_id().get(tool_id)


def tools_in(category: Category) -> list[ToolDefinition]:
    return [t for t in all_tools() if t.category.id == category.id]


def categories_with_tools() -> list[Category]:
    return [c for c in CATEGORIES if tools_in(c)]


def tools_accepting(refs: Sequence[FileRef]) -> list[ToolDefinition]:
    """原版 tools(accepting:)：至少 1 個檔案、多檔時要允許多檔、每個檔案都接受（不看 minimum_files）。"""
    if not refs:
        return []
    return [t for t in all_tools() if t.accepts_all(refs)]


def minimum_files() -> dict[str, int]:
    return {t.id: t.minimum_files for t in all_tools()}


def resolve(identifier: str) -> tuple[ToolDefinition, str | None] | None:
    """工具代碼或動作代碼 → (工具, 動作代碼或 None)。"""
    t = tool(identifier)
    if t is not None:
        return t, None
    act = ACTIONS_BY_ID.get(identifier)
    if act is not None:
        t = tool(act.tool_id)
        if t is not None:
            return t, act.id
    return None


def reload() -> None:
    """測試或開發時重新載入。"""
    all_tools.cache_clear()
