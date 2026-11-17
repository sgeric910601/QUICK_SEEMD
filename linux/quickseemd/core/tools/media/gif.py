"""影片轉 GIF（video.to-gif，對應原版 MTFrameOps.gif 與 MTGifSession）。

原版用 AVAssetImageGenerator 取格、ImageIO 編碼（每格各自的色盤）；這裡用 ffmpeg 的
palettegen（stats_mode=single）＋paletteuse（new=1），同樣是每格各自產生色盤，不必先把整段影片放在記憶體。
「標準」「精簡」原版用 CIColorPosterize 減少色階，這裡用 lutrgb 做相同的色階量化。
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

from ... import errors
from ...model import MEDIA, ToolContext, ToolDefinition
from ...options import Choice, Info, Kind, Option, Values
from . import common, engine, geometry
from .probe import MediaInfo, probe, too_short
from .timefmt import format_time, parse_time

MAX_FRAMES = 1800


@dataclass(frozen=True)
class GifColor:
    title: str
    subtitle: str
    levels: int | None  # 每個色版的色階數；None＝不處理


COLORS: dict[str, GifColor] = {
    "high": GifColor("高畫質", "色彩最豐富、漸層最平滑，檔案較大", None),
    "standard": GifColor("標準", "減少雜訊與色階，畫質與大小平衡", 24),
    "compact": GifColor("精簡", "大幅減少色彩，檔案最小，適合圖示與螢幕錄影", 8),
}
FPS_CHOICES = (5, 10, 15, 20)
WIDTHS = (320, 480, 640, 0)


@dataclass(frozen=True)
class GifOptions:
    start: float
    length: float
    fps: int
    width: int  # 0＝原始寬度
    color: str
    loop: bool


def frame_count(o: GifOptions, video_duration: float | None) -> int:
    """預估影格數。"""
    length = o.length
    if video_duration is not None:
        length = min(length, max(0.0, video_duration - min(o.start, video_duration)))
    return max(1, int(math.floor(length * o.fps)))


def output_size(info: MediaInfo, width: int) -> tuple[int, int]:
    """寬度上限 width、高度上限 4×width（只縮小、不放大）。"""
    dw, dh = info.display_size
    if width <= 0:
        return dw, dh
    return geometry.fit_inside(dw, dh, width, width * 4)


def posterize(levels: int) -> str:
    steps = levels - 1
    expr = f"'round(val*{steps}/255)*255/{steps}'"
    return f"format=rgb24,lutrgb=r={expr}:g={expr}:b={expr}"


def gif_filter(o: GifOptions, size: tuple[int, int], fps: float) -> str:
    chain = [f"fps={fps:g}", f"scale={size[0]}:{size[1]}:flags=lanczos"]
    levels = COLORS[o.color].levels
    if levels:
        chain.append(posterize(levels))
    return (
        ",".join(chain)
        + ",split[a][b];[a]palettegen=stats_mode=single[p];[b][p]paletteuse=new=1"
    )


def gif_file(file: Path, o: GifOptions, ctx: ToolContext) -> list[Path]:
    info = probe(file, ctx.cancel_event)
    info.require_video()
    start = min(max(0.0, o.start), max(0.0, info.duration - 0.1))
    length = min(o.length, info.duration - start)
    if length < 0.1:
        raise too_short(info.name)
    fps = float(max(1, min(30, o.fps)))
    count = max(1, min(MAX_FRAMES, int(math.floor(length * fps))))
    size = output_size(info, o.width)

    def on_frame(n: int) -> None:
        shown = max(1, min(n, count))
        ctx.progress(shown / count * 0.9, f"製作 GIF：第 {shown}／{count} 格")

    args = engine.input_args(file, start=start) + ["-t", f"{count / fps + 1:.6f}"]
    args += ["-map", f"0:{info.video_index}", "-an", "-sn", "-dn", "-map_metadata", "-1"]
    args += ["-vf", gif_filter(o, size, fps), "-frames:v", str(count)]
    args += ["-loop", "0" if o.loop else "-1", "-f", "gif"]
    with engine.work_dir(ctx, file) as d:
        out = d / "animation.gif"
        frames = engine.run_ffmpeg(
            ctx, args, out, duration=count / fps, message="製作 GIF…", span=(0, 0.9), on_frame=on_frame
        )
        added = frames if 0 < frames <= count else count
        summary = f"GIF 動畫：{added} 格、{int(fps)} fps、長 {added / fps:.1f} 秒"
        summary += "、循環播放" if o.loop else ""
        return [engine.deliver(ctx, out, ctx.output_name(file, None, "gif"), file, summary)]


def gif_options(values: Values) -> GifOptions:
    start_text = common.text(values, "start").strip()
    start = parse_time(start_text) if start_text else 0.0
    if start is None:
        raise errors.QSError("時間格式不正確，例如 1:05.5 或 65")
    return GifOptions(
        start=start,
        length=common.number(values, "length", 5.0),
        fps=common.choice(values, "fps", FPS_CHOICES),
        width=common.choice(values, "width", WIDTHS),
        color=common.choice(values, "color", COLORS),
        loop=common.flag(values, "loop", True),
    )


def run(files: list[Path], values: Values, ctx: ToolContext) -> list[Path]:
    o = gif_options(values)
    return engine.batch(files, ctx, lambda f, sub: gif_file(f, o, sub))


# ---------------------------------------------------------------- 選項畫面


def validate(files: list[Path], values: Values, info: Info) -> str | None:
    start_text = common.text(values, "start").strip()
    start = parse_time(start_text) if start_text else 0.0
    if start is None:
        return "時間格式不正確，例如 1:05.5 或 65"
    single = common.single_info(info) if len(files) == 1 else None
    if single is not None and start >= single.duration - 0.1:
        return "開始時間超過影片長度"
    try:
        length = float(values.get("length", 5.0))
    except (TypeError, ValueError):
        return "長度至少 0.2 秒"
    return "長度至少 0.2 秒" if length < 0.2 else None


def _summary_note(values: Values, info: Info) -> tuple[str, bool]:
    single = common.single_info(info)
    try:
        o = gif_options(values)
    except errors.QSError:
        return "GIF 最多 256 色，長片段或大尺寸的檔案會很大。", False
    count = frame_count(o, single.duration if single else None)
    text = f"共 {count} 格"
    if single is not None and single.display_size[0] > 0:
        dw, dh = single.display_size
        w = dw if o.width == 0 else min(o.width, dw)
        text += f"，輸出 {w}×{geometry.round_half_away(dh * w / dw)}"
    return text + "。GIF 最多 256 色，長片段或大尺寸的檔案會很大。", False


OPTIONS: tuple[Option, ...] = (
    common.source_note(),
    Option(
        key="start",
        kind=Kind.TIME,
        label="開始",
        default="0:00",
        placeholder="0:00.0",
        caption="格式例如 1:05.5 或 65",
        group="片段",
        group_icon="timeline.selection",
    ),
    Option(
        key="length",
        kind=Kind.SLIDER,
        label="長度",
        default=5.0,
        minimum=0.5,
        maximum=30.0,
        step=0.5,
        display=lambda v: "%.1f 秒" % float(v),
        group="片段",
        group_icon="timeline.selection",
    ),
    Option(
        key="fps",
        kind=Kind.CHIPS,
        label="每秒影格（流暢度）",
        default=10,
        choices=tuple(
            Choice(f, f"{f} fps", "較流暢、檔案較大" if f >= 15 else "檔案較小") for f in FPS_CHOICES
        ),
        group="每秒影格（流暢度）",
        group_icon="film.stack",
    ),
    Option(
        key="width",
        kind=Kind.CHIPS,
        label="寬度",
        default=480,
        choices=tuple(Choice(w, "原始" if w == 0 else f"{w} px") for w in WIDTHS),
        group="寬度",
        group_icon="arrow.left.and.right",
    ),
    Option(
        key="color",
        kind=Kind.CARDS,
        label="色彩",
        default="standard",
        choices=tuple(Choice(k, c.title, c.subtitle) for k, c in COLORS.items()),
        group="色彩",
        group_icon="paintpalette",
    ),
    Option(
        key="loop",
        kind=Kind.TOGGLE,
        label="循環播放",
        default=True,
        note=lambda v, i: ("播完自動重頭開始" if v.get("loop", True) else "只播放一次", False),
    ),
    Option(key="_summary", kind=Kind.NOTE, note=_summary_note),
)

TOOL = ToolDefinition(
    id="video.to-gif",
    category=MEDIA,
    title="影片轉 GIF",
    subtitle="把一段影片做成 GIF 動畫，可調整流暢度、寬度與色彩。",
    icon="photo.stack",
    run=run,
    input_types=common.MOVIE_TYPES,
    input_label="影片",
    action_title="製作 GIF",
    keywords=("gif", "animation", "動畫", "動圖", "meme"),
    options=OPTIONS,
    badge="GIF",
    presets={"video.gif-default": {}},
    inspect=common.inspect,
    validate=validate,
    requires=common.REQUIRES,
)
