"""圖片工具共用的定義方式與選項元件（對應原版 ITViews 與 ImageToolCatalog）。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from ...model import IMAGE, TINTS, ToolDefinition
from ...options import Choice, Info, Kind, Option, Values
from . import colors, pipeline

IMAGE_TYPES: tuple[str, ...] = ("image/*",)
INPUT_LABEL = "圖片"

Predicate = Callable[[Values, Info], bool]
NoteFunc = Callable[[Values, Info], "tuple[str, bool] | None"]


def quality_text(value: Any) -> str:
    """0–1 的比例顯示成百分比（原版 ITOptionFormat.quality）。"""
    return f"{round(float(value) * 100)}%"


def percent_text(value: Any) -> str:
    """已經是百分比的數值（原版 ITOptionFormat.percent）。"""
    return f"{round(float(value))}%"


def count(info: Info) -> int:
    return int(info.get("count", 0) or 0)


def define(
    tool_id: str,
    title: str,
    subtitle: str,
    icon: str,
    run: Callable,
    *,
    badge: str,
    action_title: str | Callable[[Values, Info], str],
    keywords: tuple[str, ...],
    options: tuple[Option, ...],
    tint: str | None = None,
    **extra: Any,
) -> ToolDefinition:
    """圖片類工具的共同設定：接受所有圖片、可多檔、預設讀取檔案資訊。"""
    extra.setdefault("inspect", pipeline.image_info)
    return ToolDefinition(
        id=tool_id,
        category=IMAGE,
        title=title,
        subtitle=subtitle,
        icon=icon,
        run=run,
        input_types=IMAGE_TYPES,
        input_label=INPUT_LABEL,
        action_title=action_title,
        keywords=keywords,
        options=options,
        tint=TINTS[tint] if tint else None,
        badge=badge,
        **extra,
    )


def color_option(
    key: str,
    label: str,
    choices: tuple[Choice, ...],
    default: str,
    *,
    group: str | None = None,
    group_icon: str | None = None,
    visible: Predicate | None = None,
    note: NoteFunc | None = None,
) -> Option:
    """顏色膠囊＋自訂色（自訂色存在「鍵_custom」）。"""
    return Option(
        key=key,
        kind=Kind.COLOR,
        label=label,
        default=default,
        choices=choices,
        custom_key=f"{key}_custom",
        group=group,
        group_icon=group_icon,
        visible=visible,
        note=note,
    )


def color_value(values: Values, key: str, custom_default: colors.Rgba) -> colors.Rgba:
    return colors.parse(values.get(key), values.get(f"{key}_custom"), custom_default)


def dynamic_note(key: str, note: NoteFunc, *, group: str | None = None, **kw: Any) -> Option:
    """內容會變的說明框。"""
    return Option(key=key, kind=Kind.NOTE, group=group, note=note, **kw)
