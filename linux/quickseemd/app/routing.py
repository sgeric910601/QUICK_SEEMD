"""主程式的規則（不需要畫面）：外部請求要開哪個工具、大廳要列哪些工具、工具頁的檔案處理。

對應原版 AppState.openFromOutside／inboxFits、ToolHubView 的篩選、ToolPageView 的 add／validation。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TypeVar

from gi.repository import GLib

from ..core.filetypes import FileRef, file_ref
from ..core.model import ALL_SUBTITLE, CATEGORIES, Category, ToolDefinition
from ..core.options import Info, Values
from .cmdline import OpenRequest

CONVERTER_ID = "other.convert"
HUB_TITLE = "你需要的檔案工具，全都在這裡"
SORT_MODES = ("az", "za", "reverse")
T = TypeVar("T")


@dataclass(frozen=True)
class Route:
    """外部請求的處理方式：kind = "tool"（打開工具）、"inbox"（大廳選工具）、"show"（只顯示視窗）。"""

    kind: str
    tool: ToolDefinition | None = None
    files: tuple[Path, ...] = ()


def is_converter(tool: ToolDefinition) -> bool:
    return tool.custom_page == "converter" or tool.id == CONVERTER_ID


# ---------------------------------------------------------------- 外部請求


def _accepting(tools: Sequence[ToolDefinition], refs: Sequence[FileRef]) -> list[ToolDefinition]:
    if not refs:
        return []
    return [t for t in tools if t.accepts_all(refs)]


def route_open(req: OpenRequest, tools: Sequence[ToolDefinition]) -> Route:
    """原版 openFromOutside／receiveFromOutside 的規則。"""
    files = tuple(req.files)
    if req.tool_id is None:
        return Route("inbox", None, files) if files else Route("show")
    refs = [file_ref(p) for p in files]
    tool = next((t for t in tools if t.id == req.tool_id), None)
    if tool is not None and all(tool.accepts_ref(r) for r in refs):
        return Route("tool", tool, files)
    if tool is not None:
        for alt in _accepting(tools, refs):
            if alt.category.id == tool.category.id and alt.custom_page is None:
                return Route("tool", alt, files)
    return Route("inbox", None, files) if files else Route("show")


# ---------------------------------------------------------------- 大廳


def inbox_fits(tool: ToolDefinition, inbox: Sequence[FileRef]) -> bool:
    if not inbox:
        return True
    if is_converter(tool):
        return True
    if len(inbox) > 1 and not tool.allows_multiple:
        return False
    return all(tool.accepts_ref(r) for r in inbox)


def inbox_files_for(tool: ToolDefinition, inbox: Sequence[Path]) -> list[Path]:
    """點卡片時要帶進工具的檔案：工具收得下才帶。"""
    if not inbox:
        return []
    refs = [file_ref(p) for p in inbox]
    return list(inbox) if inbox_fits(tool, refs) else []


def hub_sections(
    category: Category | None,
    tools: Sequence[ToolDefinition],
    query: str,
    inbox: Sequence[FileRef],
) -> list[tuple[Category, list[ToolDefinition]]]:
    """大廳要顯示的段落：搜尋符合 且（沒有待處理檔案 或 工具收得下）。"""
    cats = [category] if category is not None else list(CATEGORIES)
    out: list[tuple[Category, list[ToolDefinition]]] = []
    for cat in cats:
        lst = [
            t
            for t in tools
            if t.category.id == cat.id and t.matches(query) and inbox_fits(t, inbox)
        ]
        if lst:
            out.append((cat, lst))
    return out


def hub_title(category: Category | None) -> str:
    return HUB_TITLE if category is None else category.title


def hub_subtitle(category: Category | None) -> str:
    return ALL_SUBTITLE if category is None else category.subtitle


def empty_message(query: str, has_inbox: bool) -> str:
    return "沒有工具能同時處理這些檔案" if has_inbox else f"找不到符合「{query}」的工具"


def inbox_title(count: int) -> str:
    return f"已選取 {count} 個檔案：要對它們做什麼？"


def inbox_names(files: Sequence[Path]) -> str:
    return "、".join(Path(f).name for f in files)


# ---------------------------------------------------------------- 工具頁檔案


def initial_files(tool: ToolDefinition, paths: Sequence[Path]) -> list[Path]:
    accepted = [Path(p) for p in paths if tool.accepts(p)]
    return accepted[:1] if not tool.allows_multiple else accepted


def add_files(
    tool: ToolDefinition, current: Sequence[Path], incoming: Sequence[Path]
) -> tuple[list[Path], str | None]:
    """加入檔案：不支援的略過（回傳提示）、重複的忽略、單檔工具換成最後一個。"""
    accepted = [Path(p) for p in incoming if tool.accepts(p)]
    skipped = len(incoming) - len(accepted)
    note = f"已略過 {skipped} 個不支援的檔案（需要{tool.input_label}）" if skipped > 0 else None
    merged = list(current)
    for p in accepted:
        if p not in merged:
            merged.append(p)
    if not tool.allows_multiple and accepted:
        merged = [accepted[-1]]
    return merged, note


def _natural_key(p: Path) -> str:
    return GLib.utf8_collate_key_for_filename(p.name, -1)


def sort_files(files: Sequence[Path], mode: str) -> list[Path]:
    """「排序」選單：依名稱 A → Z、Z → A（自然排序）、反轉順序。"""
    if mode == "az":
        return sorted(files, key=_natural_key)
    if mode == "za":
        return sorted(files, key=_natural_key, reverse=True)
    if mode == "reverse":
        return list(reversed(files))
    raise ValueError(f"未知的排序方式：{mode}")


def move_item(items: Sequence[T], src: int, dst: int) -> list[T]:
    """拖曳排序：把 src 的項目移到 dst 的位置。索引不合法時原樣回傳。"""
    out = list(items)
    if not (0 <= src < len(out) and 0 <= dst < len(out)) or src == dst:
        return out
    item = out.pop(src)
    out.insert(dst, item)
    return out


def validation_reason(
    tool: ToolDefinition, files: Sequence[Path], values: Values, info: Info
) -> str | None:
    """顯示在執行按鈕上方的原因（順序照原版：檔案數量 → 工具自己的檢查）。"""
    reason = tool.check_files(list(files))
    if reason is None and tool.validate is not None:
        reason = tool.validate(list(files), values, info)
    return reason


def choose_button_title(tool: ToolDefinition) -> str:
    return f"選擇{tool.input_label}"


def landing_hint(tool: ToolDefinition) -> str:
    return "或把檔案拖到這裡，可以一次選多個" if tool.allows_multiple else "或把檔案拖到這裡"


def picker_message(tool: ToolDefinition) -> str:
    return f"選擇要用「{tool.title}」處理的{tool.input_label}"


def accepts_folders(tool: ToolDefinition) -> bool:
    """選檔視窗要不要讓人選資料夾（原版：輸入類型明確包含資料夾時）。"""
    return "folder" in tool.input_types


def files_header(count: int) -> str:
    return f"{count} 個檔案"
