"""拖曳轉換輪盤的內容與幾何（對應原版 DragWheel.swift 的 WheelCatalog 與命中判斷）。

純邏輯、不碰介面，背景程式的輪盤視窗只負責畫與接收放下。
"""

from __future__ import annotations

import math
from collections.abc import Callable, Sequence
from dataclasses import dataclass

from . import quickactions
from .converter.formats import WHEEL_SORT
from .filetypes import FileRef, conforms
from .model import RGB, TINTS

MODES: tuple[str, ...] = ("convert", "tools", "actions")
MODE_TITLES = {"convert": "轉換", "tools": "工具", "actions": "動作"}
MODE_ICONS = {
    "convert": "arrow.triangle.2.circlepath",
    "tools": "wrench.and.screwdriver",
    "actions": "bolt",
}
MAX_SECTORS = 10

# 幾何（像素，邏輯座標）
WINDOW_SIZE = 440
INNER_RADIUS = 68
OUTER_RADIUS = 196
HIT_OUTER_RADIUS = 220
LABEL_RADIUS = 138
CENTER_DIAMETER = 128

GREY: RGB = (0.56, 0.56, 0.58)

# 一鍵動作與萬用轉換格式的對應（避免輪盤上出現兩個「JPG」）
ACTION_FORMAT_EQUIV: dict[str, str] = {
    "image.to-jpg": "jpeg",
    "image.to-png": "png",
    "image.to-heic": "heic",
    "pdf.to-jpg": "jpeg",
    "video.convert-mp4": "mp4",
    "video.gif-default": "anim-gif",
    "audio.convert-m4a": "m4a",
    "audio.convert-wav": "wav",
    "office.to-pdf": "pdf",
    "office.pdf-to-docx": "docx",
    "office.pdf-to-xlsx": "xlsx",
    "office.pdf-to-pptx": "pptx",
    "office.excel-to-csv": "csv",
    "other.to-utf8": "utf8",
}


@dataclass(frozen=True)
class WheelItem:
    kind: (
        str  # "action"（一鍵動作）、"format"（萬用轉換）、"more"（打開主程式）、"file"（動作輪盤）
    )
    payload: str  # 動作代碼、格式代碼、或檔案動作名稱
    title: str
    icon: str  # 原版 SF Symbol 名稱
    tint: RGB


FILE_ACTIONS: tuple[WheelItem, ...] = (
    WheelItem("file", "open-with", "用其他程式開啟", "square.and.arrow.up", TINTS["slate"]),
    WheelItem("file", "shelf", "浮動暫存", "tray.and.arrow.down", TINTS["slate"]),
    WheelItem("file", "copy", "拷貝", "doc.on.doc", TINTS["slate"]),
    WheelItem("file", "move", "移動到…", "folder", TINTS["slate"]),
    WheelItem("file", "copy-path", "拷貝路徑", "link", TINTS["slate"]),
    WheelItem("file", "terminal", "終端機", "terminal", TINTS["slate"]),
)


def short_title(title: str) -> str:
    """「轉成 Word（.docx）」→「Word」。"""
    t = title
    for prefix in ("轉成 ", "轉成", "轉換成 "):
        if t.startswith(prefix):
            t = t[len(prefix) :]
            break
    for mark in ("（", "("):
        pos = t.find(mark)
        if pos > 0:
            t = t[:pos]
    return t.strip()


def _is_convert_title(title: str) -> bool:
    return title.startswith("轉成")


@dataclass(frozen=True)
class TargetLike:
    """converter.Target 的最小介面（測試可以自己組）。"""

    id: str
    label: str
    category_id: str
    category_order: int
    icon: str


def converter_targets(paths: Sequence[str]) -> list[TargetLike]:
    """前 5 個檔案共同可轉的萬用轉換格式（轉換器還沒實作或出錯時回傳空清單）。"""
    from pathlib import Path  # noqa: PLC0415

    from . import converter  # noqa: PLC0415

    try:
        found = converter.common_targets([Path(p) for p in paths[:5]])
    except (NotImplementedError, OSError):
        return []
    return [
        TargetLike(
            t.format.id,
            t.format.label,
            t.format.category.id,
            t.format.category.order,
            t.format.category.icon,
        )
        for t in found
    ]


def _format_rank(t: TargetLike) -> int:
    try:
        return WHEEL_SORT.index(t.id)
    except ValueError:
        return 28 + t.category_order * 10


def catalog(
    mode: str,
    refs: Sequence[FileRef],
    *,
    minimum_files: dict[str, int],
    tint_for_tool: Callable[[str], RGB],
    targets: Callable[[], Sequence[TargetLike]] = lambda: (),
) -> list[WheelItem]:
    """這個模式下輪盤要列出哪些格子（最多 10 格）。"""
    if mode == "actions":
        return list(FILE_ACTIONS)
    actions = quickactions.wheel_actions(refs, minimum_files)

    def action_item(a: quickactions.QuickAction) -> WheelItem:
        return WheelItem("action", a.id, short_title(a.title), a.icon, tint_for_tool(a.tool_id))

    if mode == "tools":
        items = [action_item(a) for a in actions if not _is_convert_title(a.title)][
            : MAX_SECTORS - 1
        ]
        items.append(WheelItem("more", "tools", "更多工具", "ellipsis.circle", GREY))
        return items

    # 轉換
    primary = [a for a in actions if _is_convert_title(a.title) and not a.id.startswith("other.")]
    secondary = [a for a in actions if _is_convert_title(a.title) and a.id.startswith("other.")]
    items = [action_item(a) for a in primary][: MAX_SECTORS - 1 - len(secondary)]
    seen_titles = {i.title.lower() for i in items}
    seen_formats = {
        ACTION_FORMAT_EQUIV[i.payload] for i in items if i.payload in ACTION_FORMAT_EQUIV
    }
    all_archives = bool(refs) and all(conforms(r, "archive") for r in refs)
    limit = (MAX_SECTORS - 1) - len(secondary)
    for t in sorted(targets(), key=_format_rank):
        if len(items) >= limit:
            break
        if t.category_id in ("archive", "text") and not all_archives:
            continue
        title = short_title(t.label)
        if title.lower() in seen_titles or t.id in seen_formats:
            continue
        items.append(WheelItem("format", t.id, title, t.icon, TINTS["teal"]))
        seen_titles.add(title.lower())
        seen_formats.add(t.id)
    items.extend(action_item(a) for a in secondary)
    items.append(WheelItem("more", "convert", "更多格式", "ellipsis.circle", GREY))
    return items[:MAX_SECTORS]


def hit_test(dx: float, dy: float, count: int) -> int | str | None:
    """相對圓心的位移 → 格子編號（0 在正上方、順時針）；"center"＝中間；None＝圈外。"""
    r = math.hypot(dx, dy)
    if r < INNER_RADIUS:
        return "center"
    if r > HIT_OUTER_RADIUS or count <= 0:
        return None
    angle = (math.degrees(math.atan2(dx, -dy)) + 360.0) % 360.0
    width = 360.0 / count
    return int(((angle + width / 2) % 360.0) // width)


def sector_angles(index: int, count: int) -> tuple[float, float]:
    """第 index 格的起訖角度（度，0 在正上方、順時針）。"""
    width = 360.0 / count
    center = index * width
    return center - width / 2, center + width / 2


def hint_line(mode: str, keys: dict[str, str], key_labels: dict[str, str]) -> str:
    """中間的小提示，例如「⌥ 工具  ⌃ 動作  esc 取消」。"""
    parts = []
    for m in MODES:
        if m == mode:
            continue
        label = key_labels.get(keys.get(m, ""), "")
        symbol = label.split(" ")[0] if label else ""
        parts.append(f"{symbol} {MODE_TITLES[m]}".strip())
    parts.append("esc 取消")
    return "  ".join(parts)


def center_title(mode: str, file_count: int) -> str:
    if file_count > 1:
        return f"{file_count} 個檔案・{MODE_TITLES[mode]}"
    return MODE_TITLES[mode]
