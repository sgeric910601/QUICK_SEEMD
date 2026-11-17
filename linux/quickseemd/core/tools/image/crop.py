"""裁剪圖片（對應原版 ITCropTool）。

與介面的約定（custom_view="image-crop"）：
- 選項 crop（隱藏的文字）＝單張圖片時使用者框選的範圍，JSON {"x","y","w","h"}，單位是
  轉正後的原圖像素（左上為原點）；空字串＝還沒框選。
- ratio：比例膠囊；有比例時框選範圍的高度會依比例換算。
- anchor：多張圖片時自動裁切的位置（智慧／置中／靠上）。
- inspect 回傳 {"sizes": [[寬, 高], …]}（轉正後的像素）。
"""

from __future__ import annotations

import json
import math
from pathlib import Path
from typing import Any

from PIL import Image

from ... import errors
from ...model import ToolContext, per_file
from ...options import Choice, Info, Kind, Option, Values
from . import faces, pipeline, writer
from .common import count, define, dynamic_note

PixelRect = tuple[int, int, int, int]

# 比例代碼 →（標題, 說明, 寬÷高）
RATIOS: dict[str, tuple[str, str, float | None]] = {
    "free": ("自由", "自由拖曳", None),
    "square": ("1:1", "大頭貼、IG 貼文", 1.0),
    "r4x3": ("4:3", "一般相機、iPad", 4 / 3),
    "r3x4": ("3:4", "一般相機、iPad", 3 / 4),
    "r3x2": ("3:2", "單眼相機、沖印", 3 / 2),
    "r2x3": ("2:3", "單眼相機、沖印", 2 / 3),
    "r16x9": ("16:9", "影片、簡報、桌布", 16 / 9),
    "r9x16": ("9:16", "限時動態、短影音", 9 / 16),
    "a4Portrait": ("A4 直", "列印 A4 紙張", 210 / 297),
    "a4Landscape": ("A4 橫", "列印 A4 紙張", 297 / 210),
}
_ANCHOR_HINTS = {
    "smart": "自動找出人臉或主體，讓它留在畫面中。",
    "center": "從正中央裁切。",
    "top": "保留上方（適合直式人像、文件）。",
}
_EPS = 1e-6


def ratio_of(key: Any) -> float | None:
    return RATIOS.get(str(key), RATIOS["free"])[2]


def parse_rect(value: Any) -> PixelRect | None:
    """crop 選項值 → 像素範圍；格式不對或寬高不是正數時回傳 None。"""
    data = value
    if isinstance(value, str):
        if not value.strip():
            return None
        try:
            data = json.loads(value)
        except json.JSONDecodeError:
            return None
    if not isinstance(data, dict):
        return None
    try:
        x, y, w, h = (int(round(float(data[k]))) for k in ("x", "y", "w", "h"))
    except (KeyError, TypeError, ValueError):
        return None
    if x < 0 or y < 0 or w <= 0 or h <= 0:
        return None
    return (x, y, w, h)


def _centered(ratio: float, aspect: float) -> tuple[float, float, float, float]:
    """依比例選出最大、置中的範圍（0–1）。"""
    if ratio > aspect:
        h = aspect / ratio
        return (0.0, (1 - h) / 2, 1.0, h)
    w = ratio / aspect
    return ((1 - w) / 2, 0.0, w, 1.0)


def _clip(x0: int, y0: int, x1: int, y1: int, w: int, h: int) -> PixelRect:
    x0, y0 = max(0, x0), max(0, y0)
    x1, y1 = min(w, x1), min(h, y1)
    return (x0, y0, max(0, x1 - x0), max(0, y1 - y0))


def _auto_rect(
    w: int, h: int, ratio: float, anchor: str, focus: tuple[float, float] | None
) -> PixelRect:
    cx, cy, cw, ch = _centered(ratio, w / h)
    rx, ry, rw, rh = cx * w, cy * h, cw * w, ch * h
    if anchor == "top":
        ry = 0.0
    elif anchor == "smart" and focus is not None:
        # 移動後的起點取整數，裁出來的尺寸才會和置中時一樣
        rx = float(round(min(max(0.0, focus[0] * w - rw / 2), w - rw)))
        ry = float(round(min(max(0.0, focus[1] * h - rh / 2), h - rh)))
    x0, y0 = math.floor(rx + _EPS), math.floor(ry + _EPS)
    x1, y1 = math.ceil(rx + rw - _EPS), math.ceil(ry + rh - _EPS)
    return _clip(x0, y0, x1, y1, w, h)


def pixel_rect(
    w: int,
    h: int,
    ratio_key: str,
    rect: PixelRect | None,
    anchor: str,
    focus: tuple[float, float] | None = None,
) -> PixelRect:
    """實際裁剪的像素範圍（左上為原點）。"""
    ratio = ratio_of(ratio_key)
    if rect is None:
        return (0, 0, w, h) if ratio is None else _auto_rect(w, h, ratio, anchor, focus)
    x, y, rw, rh = rect
    if ratio is not None:
        rh = max(1, round(rw / ratio))  # 換算成整數像素後仍維持正確比例
    if x + rw > w:
        x = max(0, w - rw)
    if y + rh > h:
        y = max(0, h - rh)
    return _clip(x, y, x + rw, y + rh, w, h)


def _crop(image: Image.Image, values: Values, rect: PixelRect | None) -> Image.Image:
    ratio_key, anchor = str(values.get("ratio", "free")), str(values.get("anchor", "smart"))
    use_focus = rect is None and anchor == "smart" and ratio_of(ratio_key) is not None
    focus = faces.focus_point(image) if use_focus else None
    x, y, w, h = pixel_rect(image.width, image.height, ratio_key, rect, anchor, focus)
    if w < 1 or h < 1:
        raise errors.QSError("裁剪範圍不正確。")
    return image.crop((x, y, x + w, y + h))


def run(files: list[Path], values: Values, ctx: ToolContext) -> list[Path]:
    rect = parse_rect(values.get("crop")) if len(files) == 1 else None
    if ratio_of(values.get("ratio")) is None and rect is None:
        raise errors.QSError(
            "多張圖片請選擇裁剪比例（會從中間裁切）"
            if len(files) > 1
            else "請拖曳裁剪框，選擇要保留的範圍"
        )
    # 多張圖片時沒有框選範圍；單張未框選但有比例時從中間裁（原版的「重設裁剪框」）
    if len(files) == 1 and rect is None:
        values = {**values, "anchor": "center"}

    def output_for(source: pipeline.Source) -> writer.Output:
        return writer.Output(
            source.preferred_format(), 0.92, writer.KEEP, keeps_alpha=source.has_alpha
        )

    def body(f: Path, sub: ToolContext) -> list[Path]:
        return [
            pipeline.run_file(f, sub, "已裁剪", output_for, lambda im, _s: _crop(im, values, rect))
        ]

    return per_file(files, ctx, body)


def validate(files: list[Path], values: Values, info: Info) -> str | None:
    free = ratio_of(values.get("ratio")) is None
    if len(files) > 1 and free:
        return "多張圖片請選擇裁剪比例（會從中間裁切）"
    if len(files) == 1 and free:
        rect = parse_rect(values.get("crop"))
        size = pipeline.first_size(info)
        if rect is None or (size and rect[2] >= size[0] and rect[3] >= size[1]):
            return "請拖曳裁剪框，選擇要保留的範圍"
    return None


def _size_note(values: Values, info: Info) -> tuple[str, bool] | None:
    size = pipeline.first_size(info)
    if size is None:
        return None
    rect = parse_rect(values.get("crop"))
    _x, _y, w, h = pixel_rect(size[0], size[1], str(values.get("ratio")), rect, "center")
    return (f"輸出尺寸：{w} × {h} px　原始尺寸：{size[0]} × {size[1]} px", False)


def _one(v: Values, i: Info) -> bool:
    return count(i) == 1


def _many(v: Values, i: Info) -> bool:
    return count(i) > 1


OPTIONS: tuple[Option, ...] = (
    Option(
        "ratio",
        Kind.CHIPS,
        "比例",
        "free",
        tuple(Choice(k, t, hint) for k, (t, hint, _r) in RATIOS.items()),
        group="比例",
        group_icon="aspectratio",
    ),
    dynamic_note(
        "_ratio_hint",
        lambda v, i: (RATIOS.get(str(v.get("ratio")), RATIOS["free"])[1], False),
        group="比例",
    ),
    Option("crop", Kind.TEXT, "裁剪範圍", "", visible=lambda v, i: False),
    dynamic_note("_crop_size", _size_note, group="裁剪範圍", group_icon="crop", visible=_one),
    Option(
        "_note_drag",
        Kind.NOTE,
        "在圖片上拖曳裁剪框移動位置，拖曳四角調整大小。",
        group_icon="hand.draw",
        visible=_one,
    ),
    Option(
        "anchor",
        Kind.SEGMENTED,
        "裁切位置",
        "smart",
        (Choice("smart", "智慧"), Choice("center", "置中"), Choice("top", "靠上")),
        group="裁切位置",
        group_icon="viewfinder",
        visible=_many,
        note=lambda v, i: (_ANCHOR_HINTS.get(str(v.get("anchor")), _ANCHOR_HINTS["smart"]), False),
    ),
    Option(
        "_note_many",
        Kind.NOTE,
        "多張圖片時，每張都會依所選比例裁切出最大的範圍。",
        group_icon="square.on.square",
        visible=_many,
    ),
)

TOOL = define(
    "image.crop",
    "裁剪圖片",
    "拖曳框選要保留的範圍，或依 1:1、16:9、A4 等比例一次裁切多張圖片。",
    "crop",
    run,
    tint="teal",
    badge="IMG",
    action_title="裁剪圖片",
    keywords=("crop", "trim", "cut", "aspect", "裁切", "剪裁", "比例"),
    options=OPTIONS,
    validate=validate,
    custom_view="image-crop",
)
