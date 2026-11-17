"""調整大小（對應原版 ITResizeTool）：依像素、百分比或常用尺寸；填滿、完整放入或拉伸。"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from ...model import ToolContext, per_file
from ...options import Choice, Info, Kind, Option, Values
from . import colors, icon_io, pipeline, writer
from .common import color_option, color_value, count, define, dynamic_note, percent_text

LIMIT = 32768
CUSTOM_PAD = "#808080"  # 原版 SwiftUI Color.gray


@dataclass(frozen=True)
class Preset:
    id: str
    title: str
    subtitle: str
    width: int | None
    height: int | None


PRESETS: tuple[Preset, ...] = (
    Preset("w1920", "1920 寬", "Full HD、簡報、桌布", 1920, None),
    Preset("w1280", "1280 寬", "網頁、部落格、Email", 1280, None),
    Preset("ig1080", "1080 × 1080", "Instagram 貼文（正方形）", 1080, 1080),
    Preset("ig1350", "1080 × 1350", "Instagram 直式貼文（4:5）", 1080, 1350),
    Preset("story", "1080 × 1920", "限時動態、Reels（9:16）", 1080, 1920),
    Preset("og1200", "1200 × 630", "社群分享預覽（Facebook、LINE、X）", 1200, 630),
    Preset("icon512", "512 × 512", "App 圖示、頭像", 512, 512),
)
_FIT_HINTS = {
    "fill": "從中間裁掉多出來的部分，畫面填滿不留白。",
    "fit": "完整保留整張圖，空白處補上底色。",
    "stretch": "強制變成這個尺寸，圖片可能變形。",
}


def preset_named(pid: str) -> Preset:
    return next((p for p in PRESETS if p.id == pid), PRESETS[0])


@dataclass(frozen=True)
class ResizeOptions:
    mode: str = "percent"
    width: int = 1920
    height: int = 1080
    lock: bool = True
    percent: float = 50
    preset: str = "w1920"
    fit: str = "fill"
    pad: colors.Rgba = colors.WHITE
    no_upscale: bool = True
    smooth: bool = True

    @classmethod
    def from_values(cls, v: Values) -> ResizeOptions:
        choice = int(v.get("percent", 50) or 0)
        return cls(
            mode=str(v.get("mode", "percent")),
            width=int(v.get("width") or 0),
            height=int(v.get("height") or 0),
            lock=bool(v.get("lock_aspect", True)),
            percent=float(v.get("custom_percent", 60)) if choice == 0 else float(choice),
            preset=str(v.get("preset", "w1920")),
            fit=str(v.get("fit", "fill")),
            pad=color_value(v, "pad", CUSTOM_PAD),
            no_upscale=bool(v.get("no_upscale", True)),
            smooth=bool(v.get("smooth", True)),
        )


@dataclass(frozen=True)
class Plan:
    """先把原圖縮放成 scaled，再放到 canvas 的 offset（左上為原點，可為負＝裁掉）。"""

    canvas: tuple[int, int]
    scaled: tuple[int, int]
    offset: tuple[int, int] = (0, 0)
    crops: bool = False  # True：縮放後從中間裁出畫布（填滿）

    @property
    def is_identity(self) -> bool:
        return self.offset == (0, 0) and self.canvas == self.scaled


def _r(x: float) -> int:
    """Swift 的 rounded()（四捨五入，遠離 0）。"""
    return int(math.floor(x + 0.5)) if x >= 0 else -int(math.floor(-x + 0.5))


def _half(n: int) -> int:
    """Swift 的整數除以 2（往 0 取整）。"""
    return int(n / 2)


def _proportional(w: int, h: int, s: float, o: ResizeOptions) -> Plan:
    s2 = min(s, 1) if o.no_upscale else s
    size = (max(1, _r(w * s2)), max(1, _r(h * s2)))
    return Plan(size, size)


def _fill(w: int, h: int, bw: int, bh: int, o: ResizeOptions) -> Plan:
    crop_w = min(w, h * bw / bh)
    crop_h = crop_w * bh / bw
    out_w, out_h = float(bw), float(bh)
    if o.no_upscale and crop_w < bw:
        out_w, out_h = crop_w, crop_h
    s = out_w / crop_w
    sw, sh = max(1, _r(w * s)), max(1, _r(h * s))
    cw, ch = min(max(1, _r(out_w)), sw), min(max(1, _r(out_h)), sh)
    return Plan((cw, ch), (sw, sh), (_half(cw - sw), _half(ch - sh)), crops=True)


def _fit(w: int, h: int, bw: int, bh: int, o: ResizeOptions) -> Plan:
    s = min(bw / w, bh / h)
    cw, ch = float(bw), float(bh)
    if o.no_upscale and s > 1:
        s = 1
        # 不放大：畫布縮成剛好容納原圖、且維持目標比例
        if w / h > bw / bh:
            cw, ch = w, w * bh / bw
        else:
            ch, cw = h, h * bw / bh
    sw, sh = max(1, _r(w * s)), max(1, _r(h * s))
    icw, ich = max(sw, _r(cw)), max(sh, _r(ch))
    return Plan((icw, ich), (sw, sh), (_half(icw - sw), _half(ich - sh)))


def _box(w: int, h: int, bw: int, bh: int, fit: str, o: ResizeOptions) -> Plan:
    if fit == "stretch":
        tw = min(bw, w) if o.no_upscale else bw
        th = min(bh, h) if o.no_upscale else bh
        return Plan((tw, th), (tw, th))
    return _fill(w, h, bw, bh, o) if fit == "fill" else _fit(w, h, bw, bh, o)


def plan(w: int, h: int, o: ResizeOptions) -> Plan:
    if o.mode == "percent":
        return _proportional(w, h, max(o.percent, 1) / 100, o)
    if o.mode == "pixels":
        bw, bh = max(o.width, 0), max(o.height, 0)
        if o.lock or bw == 0 or bh == 0:
            if bw > 0 and bh > 0:
                return _proportional(w, h, min(bw / w, bh / h), o)
            if bw > 0:
                return _proportional(w, h, bw / w, o)
            if bh > 0:
                return _proportional(w, h, bh / h, o)
            return _proportional(w, h, 1, o)
        return _box(w, h, bw, bh, "stretch", o)
    p = preset_named(o.preset)
    if p.width and p.height:
        return _box(w, h, p.width, p.height, o.fit, o)
    if p.width:
        return _proportional(w, h, p.width / w, o)
    return _proportional(w, h, (p.height or h) / h, o)


def apply(image: Image.Image, pl: Plan, o: ResizeOptions) -> Image.Image:
    scaled = icon_io.resample(image, *pl.scaled, smooth=o.smooth)
    if pl.is_identity:
        return scaled
    cw, ch = pl.canvas
    ox, oy = pl.offset
    if pl.crops:
        return scaled.crop((-ox, -oy, -ox + cw, -oy + ch))
    opaque = o.pad.is_opaque and image.mode != "RGBA"
    if opaque:
        canvas = Image.new("RGB", (cw, ch), o.pad.rgb8())
        canvas.paste(scaled.convert("RGB"), (ox, oy))
        return canvas
    canvas = Image.new("RGBA", (cw, ch), o.pad.to8() if o.pad.a > 0 else (0, 0, 0, 0))
    canvas.alpha_composite(scaled.convert("RGBA"), dest=(ox, oy))
    return canvas


def run(files: list[Path], values: Values, ctx: ToolContext) -> list[Path]:
    o = ResizeOptions.from_values(values)

    def output_for(source: pipeline.Source) -> writer.Output:
        pl = plan(source.width, source.height, o)
        padded = not pl.crops and (pl.canvas[0] > pl.scaled[0] or pl.canvas[1] > pl.scaled[1])
        background = o.pad if o.pad.is_opaque else colors.WHITE
        keeps = source.has_alpha or (padded and not o.pad.is_opaque)
        return writer.Output(source.preferred_format(), 0.92, writer.KEEP, background, keeps)

    def transform(image: Image.Image, _s: pipeline.Source) -> Image.Image:
        return apply(image, plan(image.width, image.height, o), o)

    return per_file(
        files, ctx, lambda f, sub: [pipeline.run_file(f, sub, "已調整大小", output_for, transform)]
    )


def validate(files: list[Path], values: Values, info: Info) -> str | None:
    if values.get("mode") != "pixels":
        return None
    w, h = int(values.get("width") or 0), int(values.get("height") or 0)
    if w <= 0 and h <= 0:
        return "請輸入寬度或高度"
    if w > LIMIT or h > LIMIT:
        return f"尺寸上限為 {LIMIT} px"
    if not values.get("lock_aspect", True) and (w <= 0 or h <= 0):
        return "不鎖定比例時請輸入寬度與高度"
    return None


def adjust(values: Values, changed: str, info: Info) -> Values:
    """鎖定比例時，改寬度就跟著改高度、改高度就跟著改寬度；打開鎖定時以寬度為準。"""
    size = pipeline.first_size(info)
    if size is None or not values.get("lock_aspect", True):
        return {}
    w, h = int(values.get("width") or 0), int(values.get("height") or 0)
    if changed in ("width", "lock_aspect") and w > 0:
        return {"height": max(1, _r(w * size[1] / size[0]))}
    if changed == "height" and h > 0:
        return {"width": max(1, _r(h * size[0] / size[1]))}
    return {}


def prefill(files: list[Path], info: Info) -> Values:
    size = pipeline.first_size(info)
    return {"width": size[0], "height": size[1]} if size else {}


def _preview(values: Values, info: Info) -> tuple[str, bool] | None:
    size = pipeline.first_size(info)
    if size is None:
        return None
    pl = plan(size[0], size[1], ResizeOptions.from_values(values))
    text = f"{size[0]} × {size[1]} → {pl.canvas[0]} × {pl.canvas[1]} px"
    return (("第一張：" if count(info) > 1 else "") + text, False)


def _is(mode: str):  # noqa: ANN202
    return lambda v, i: v.get("mode") == mode


def _fixed_box(v: Values, i: Info) -> bool:
    p = preset_named(str(v.get("preset")))
    return v.get("mode") == "preset" and bool(p.width and p.height)


OPTIONS: tuple[Option, ...] = (
    Option(
        "mode",
        Kind.CARDS,
        "調整方式",
        "percent",
        (
            Choice("pixels", "依像素", "輸入寬度、高度", "ruler"),
            Choice("percent", "依百分比", "等比例縮放", "percent"),
            Choice("preset", "常用尺寸", "IG、限動、分享預覽、圖示", "square.grid.2x2"),
        ),
        group="調整方式",
        group_icon="arrow.up.left.and.arrow.down.right",
    ),
    Option(
        "width",
        Kind.NUMBER,
        "寬度",
        1920,
        minimum=0,
        maximum=LIMIT,
        group="尺寸",
        group_icon="ruler",
        visible=_is("pixels"),
    ),
    Option(
        "height",
        Kind.NUMBER,
        "高度",
        1080,
        minimum=0,
        maximum=LIMIT,
        group="尺寸",
        visible=_is("pixels"),
    ),
    Option(
        "lock_aspect",
        Kind.TOGGLE,
        "鎖定長寬比例",
        True,
        group="尺寸",
        visible=_is("pixels"),
        note=lambda v, i: (
            ("每張圖片都會等比例縮放到這個範圍內。", False)
            if count(i) > 1 and v.get("lock_aspect", True)
            else None
        ),
    ),
    Option(
        "percent",
        Kind.CHIPS,
        "縮放比例",
        50,
        (Choice(25, "25%"), Choice(50, "50%"), Choice(75, "75%"), Choice(0, "自訂")),
        group="縮放比例",
        group_icon="percent",
        visible=_is("percent"),
    ),
    Option(
        "custom_percent",
        Kind.SLIDER,
        "自訂比例",
        60,
        minimum=5,
        maximum=400,
        step=5,
        display=percent_text,
        group="縮放比例",
        visible=lambda v, i: v.get("mode") == "percent" and int(v.get("percent") or 0) == 0,
    ),
    Option(
        "preset",
        Kind.CARDS,
        "常用尺寸",
        "w1920",
        tuple(Choice(p.id, p.title, p.subtitle) for p in PRESETS),
        group="常用尺寸",
        group_icon="square.grid.2x2",
        visible=_is("preset"),
    ),
    Option(
        "fit",
        Kind.SEGMENTED,
        "比例不同時",
        "fill",
        (Choice("fill", "裁切填滿"), Choice("fit", "完整放入"), Choice("stretch", "拉伸")),
        group="比例不同時",
        group_icon="crop",
        visible=_fixed_box,
        note=lambda v, i: (_FIT_HINTS.get(str(v.get("fit")), _FIT_HINTS["fill"]), False),
    ),
    color_option(
        "pad",
        "底色",
        (colors.WHITE_CHOICE, colors.BLACK_CHOICE, colors.TRANSPARENT_CHOICE, colors.CUSTOM_CHOICE),
        "#ffffff",
        custom_default=CUSTOM_PAD,
        group="比例不同時",
        visible=lambda v, i: _fixed_box(v, i) and v.get("fit") == "fit",
    ),
    Option(
        "no_upscale",
        Kind.TOGGLE,
        "不放大",
        True,
        caption="比原圖小的尺寸才縮放，避免變模糊",
        group="選項",
        group_icon="slider.horizontal.3",
    ),
    Option(
        "smooth",
        Kind.TOGGLE,
        "平滑縮放（高品質）",
        True,
        caption="關閉時用最近鄰取樣，適合像素風圖片",
        group="選項",
    ),
    dynamic_note("_preview", _preview, group_icon="aspectratio"),
)

TOOL = define(
    "image.resize",
    "調整大小",
    "依像素、百分比或社群常用尺寸（IG、限動、分享預覽）調整圖片大小，可批次處理。",
    "arrow.up.left.and.arrow.down.right",
    run,
    tint="word",
    badge="IMG",
    action_title="調整大小",
    keywords=(
        "resize",
        "scale",
        "dimension",
        "instagram",
        "縮小",
        "放大",
        "尺寸",
        "解析度",
        "縮圖",
    ),
    options=OPTIONS,
    presets={"image.resize-half": {"mode": "percent", "percent": 50}},
    prefill=prefill,
    validate=validate,
    adjust=adjust,
)
