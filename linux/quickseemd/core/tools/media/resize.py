"""影片調整尺寸（video.resize，對應原版 MTVideoOps.resize 與 MTResizeSession）。

和「轉換影片格式」不同，這裡可以放大。比例不同時可補黑邊或裁切填滿。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ...model import MEDIA, ToolContext, ToolDefinition
from ...options import Choice, Info, Kind, Option, Values
from . import common, engine, geometry
from .probe import probe


@dataclass(frozen=True)
class Aspect:
    title: str
    subtitle: str
    ratio: float | None  # 寬／高；None＝依原始比例


ASPECTS: dict[str, Aspect] = {
    "original": Aspect("原始比例", "只改變大小", None),
    "wide": Aspect("16:9", "橫式（YouTube）", 16 / 9),
    "vertical": Aspect("9:16", "直式（Reels、Shorts、TikTok）", 9 / 16),
    "square": Aspect("1:1", "方形", 1.0),
    "portrait": Aspect("4:5", "直式貼文（Instagram）", 4 / 5),
}
FITS = ("letterbox", "crop")


def resize_target(width: float, height: float, aspect: str, resolution: int) -> tuple[int, int]:
    """輸出尺寸：比例 ≥ 1 時短邊是高，否則短邊是寬（偶數）。"""
    source_short = min(width, height)
    short = source_short if resolution == 0 else float(resolution)
    ratio = ASPECTS[aspect].ratio
    if ratio is None:
        if resolution == 0:
            return geometry.even_size(width, height)
        scale = short / max(1.0, source_short)
        return geometry.even_size(width * scale, height * scale)
    if ratio >= 1:
        return geometry.even_size(short * ratio, short)
    return geometry.even_size(short, short / ratio)


def resize_tag(aspect: str, size: tuple[int, int]) -> str:
    """「1280x720」或「16x9-720p」。"""
    w, h = size
    if aspect == "original":
        return f"{w}x{h}"
    return ASPECTS[aspect].title.replace(":", "x") + f"-{min(w, h)}p"


def resize_file(file: Path, aspect: str, resolution: int, fit: str, ctx: ToolContext) -> list[Path]:
    info = probe(file, ctx.cancel_event)
    info.require_video()
    target = resize_target(*info.display_size, aspect, resolution)
    container = engine.container_for(file)
    mode = None if aspect == "original" else ("crop" if fit == "crop" else "pad")
    enc = engine.standard_encode(info, engine.matching_codec(info), target, fit=mode)
    message = f"調整尺寸為 {target[0]}×{target[1]}…"
    with engine.work_dir(ctx, file) as d:
        out = d / f"resized.{container.ext}"
        args = engine.input_args(file) + engine.encode_args(info, enc, container)
        engine.run_ffmpeg(ctx, args, out, duration=info.duration, message=message)
        name = ctx.output_name(file, resize_tag(aspect, target), container.ext)
        summary = f"已調整為 {target[0]}×{target[1]}"
        return [engine.deliver(ctx, out, name, file, summary)]


def run(files: list[Path], values: Values, ctx: ToolContext) -> list[Path]:
    aspect = common.choice(values, "aspect", ASPECTS)
    resolution = common.choice(values, "resolution", common.RESOLUTION_TITLES)
    fit = common.choice(values, "fit", FITS)
    return engine.batch(files, ctx, lambda f, sub: resize_file(f, aspect, resolution, fit, sub))


def validate(files: list[Path], values: Values, info: Info) -> str | None:
    if values.get("aspect") == "original" and values.get("resolution") == 0:
        return "請選擇新的比例或解析度"
    return None


def _aspect_note(values: Values, info: Info) -> tuple[str, bool] | None:
    a = ASPECTS.get(values.get("aspect"))
    return (a.subtitle, False) if a else None


def _size_note(values: Values, info: Info) -> tuple[str, bool] | None:
    single = common.single_info(info)
    aspect, res = values.get("aspect"), values.get("resolution")
    if single is None or aspect not in ASPECTS or res not in common.RESOLUTION_TITLES:
        return None
    w, h = resize_target(*single.display_size, aspect, res)
    return f"{single.resolution_text} → {w}×{h}", False


OPTIONS: tuple[Option, ...] = (
    common.source_note(),
    Option(
        key="aspect",
        kind=Kind.CHIPS,
        label="畫面比例",
        default="original",
        choices=tuple(Choice(k, a.title, a.subtitle) for k, a in ASPECTS.items()),
        group="畫面比例",
        group_icon="aspectratio",
        note=_aspect_note,
    ),
    Option(
        key="resolution",
        kind=Kind.CHIPS,
        label="解析度（短邊）",
        default=720,
        choices=common.resolution_choices(),
        group="解析度（短邊）",
        group_icon="rectangle.expand.vertical",
        note=_size_note,
    ),
    Option(
        key="fit",
        kind=Kind.CARDS,
        label="比例不同時",
        default="letterbox",
        choices=(
            Choice("letterbox", "完整顯示（補黑邊）", "畫面不會被裁掉", "rectangle.inset.filled"),
            Choice("crop", "裁切填滿", "放大到填滿畫面，邊緣會被裁掉", "crop"),
        ),
        group="比例不同時",
        group_icon="rectangle.center.inset.filled",
        visible=lambda v, i: v.get("aspect") != "original",
    ),
)

TOOL = ToolDefinition(
    id="video.resize",
    category=MEDIA,
    title="影片調整尺寸",
    subtitle="改成 16:9、9:16、1:1 等社群比例或較小的解析度。",
    icon="aspectratio",
    run=run,
    input_types=common.MOVIE_TYPES,
    input_label="影片",
    action_title="調整尺寸",
    keywords=(
        "resize",
        "scale",
        "aspect",
        "crop",
        "instagram",
        "reels",
        "tiktok",
        "尺寸",
        "比例",
        "解析度",
    ),
    options=OPTIONS,
    inspect=common.inspect,
    validate=validate,
    requires=common.REQUIRES,
)
