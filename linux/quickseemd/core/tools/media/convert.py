"""轉換影片格式（video.convert，對應原版 MTVideoOps.convert／MTConvertSession）。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ...model import MEDIA, ToolContext, ToolDefinition
from ...options import Choice, Info, Kind, Option, Values
from . import common, engine, geometry
from .engine import Container
from .probe import MediaInfo, probe


@dataclass(frozen=True)
class VideoFormat:
    title: str
    subtitle: str
    codec: str
    container: Container


FORMATS: dict[str, VideoFormat] = {
    "mp4": VideoFormat(
        "MP4 (H.264)", "最通用：網頁、Windows、手機、社群平台都能播放。", "h264", engine.MP4
    ),
    "mp4hevc": VideoFormat(
        "MP4 (HEVC)",
        "畫質相同時檔案約小 40%；較舊的裝置或 Windows 可能無法播放。",
        "hevc",
        engine.MP4,
    ),
    "mov": VideoFormat(
        "MOV", "QuickTime 影片（H.264），適合 Mac 與 iPhone 剪輯。", "h264", engine.MOV
    ),
    "m4v": VideoFormat(
        "M4V", "Apple 裝置用的 MP4 變體（H.264），適合 Apple TV、iTunes。", "h264", engine.M4V
    ),
    "prores": VideoFormat(
        "ProRes 422", "專業剪輯用的高畫質格式，檔案非常大，適合交給剪輯軟體。", "prores", engine.MOV
    ),
}


@dataclass(frozen=True)
class ConvertOptions:
    format: str
    resolution: int
    keep_audio: bool
    allow_remux: bool


def _remux(
    file: Path, info: MediaInfo, fmt: VideoFormat, keep_audio: bool, out: Path, ctx: ToolContext
) -> bool:
    args = engine.input_args(file) + engine.map_args(info, audio=keep_audio)
    args += engine.copy_args(info) + fmt.container.args()
    return engine.try_ffmpeg(
        ctx, args, out, duration=info.duration, message="快速轉換（不重新編碼）…"
    )


def _transcode(
    file: Path,
    info: MediaInfo,
    fmt: VideoFormat,
    size: tuple[int, int],
    keep_audio: bool,
    out: Path,
    ctx: ToolContext,
) -> None:
    message = f"重新編碼成 {fmt.title}…"
    enc = engine.standard_encode(info, fmt.codec, size, keep_audio=keep_audio)
    args = engine.input_args(file) + engine.encode_args(info, enc, fmt.container)
    engine.run_ffmpeg(ctx, args, out, duration=info.duration, message=message)


def convert_file(file: Path, o: ConvertOptions, ctx: ToolContext) -> list[Path]:
    info = probe(file, ctx.cancel_event)
    info.require_video()
    fmt = FORMATS[o.format]
    dw, dh = info.display_size
    scaled = geometry.capped(dw, dh, o.resolution)
    ext = fmt.container.ext
    with engine.work_dir(ctx, file) as d:
        done = False
        if o.allow_remux and scaled is None and info.codec_kind == fmt.codec:
            done = _remux(file, info, fmt, o.keep_audio, d / f"remux.{ext}", ctx)
        out = d / (f"remux.{ext}" if done else f"converted.{ext}")
        size = scaled or geometry.even_size(dw, dh)
        if not done:
            _transcode(file, info, fmt, size, o.keep_audio, out, ctx)
        silent = not o.keep_audio and info.has_audio
        tag = common.RESOLUTION_TITLES[o.resolution] if scaled else ("無聲" if silent else None)
        if done:
            summary = f"已轉成 {fmt.title}（直接轉封裝，畫質無損）"
        else:
            summary = f"已轉成 {fmt.title}，{size[0]}×{size[1]}" + ("、無聲" if silent else "")
        name = engine.output_name(ctx, file, tag, ext)
        return [engine.deliver(ctx, out, name, file, summary)]


def run(files: list[Path], values: Values, ctx: ToolContext) -> list[Path]:
    o = ConvertOptions(
        format=common.choice(values, "format", FORMATS),
        resolution=common.choice(values, "resolution", common.RESOLUTION_TITLES),
        keep_audio=common.flag(values, "keep_audio", True),
        allow_remux=common.flag(values, "allow_remux", True),
    )
    return engine.batch(files, ctx, lambda f, sub: convert_file(f, o, sub))


def _format_note(values: Values, info: Info) -> tuple[str, bool] | None:
    fmt = FORMATS.get(values.get("format"))
    return (fmt.subtitle, False) if fmt else None


def _action_title(values: Values, info: Info) -> str:
    fmt = FORMATS.get(values.get("format"))
    return f"轉成 {fmt.title}" if fmt else "轉換影片"


OPTIONS: tuple[Option, ...] = (
    common.source_note(),
    Option(
        key="format",
        kind=Kind.CHIPS,
        label="輸出格式",
        default="mp4",
        choices=tuple(Choice(k, f.title, f.subtitle) for k, f in FORMATS.items()),
        group="輸出格式",
        group_icon="film",
        note=_format_note,
    ),
    Option(
        key="resolution",
        kind=Kind.CHIPS,
        label="解析度",
        default=0,
        choices=common.resolution_choices(),
        caption="以短邊計算，只會縮小、不會放大；直式影片會保持直式。",
        group="解析度",
        group_icon="aspectratio",
    ),
    Option(
        key="keep_audio",
        kind=Kind.TOGGLE,
        label="保留聲音",
        default=True,
        caption="關閉時輸出無聲影片",
        group="聲音與速度",
        group_icon="speaker.wave.2",
    ),
    Option(
        key="allow_remux",
        kind=Kind.TOGGLE,
        label="可行時不重新編碼",
        default=True,
        caption="編碼相同時直接轉封裝，速度快、畫質無損",
        group="聲音與速度",
        group_icon="speaker.wave.2",
    ),
)

TOOL = ToolDefinition(
    id="video.convert",
    category=MEDIA,
    title="轉換影片格式",
    subtitle="在 MP4、MOV、M4V、HEVC 與 ProRes 之間轉換，可同時縮小解析度。",
    icon="arrow.triangle.2.circlepath",
    run=run,
    input_types=common.MOVIE_TYPES,
    input_label="影片",
    action_title=_action_title,
    keywords=(
        "convert",
        "video",
        "mp4",
        "mov",
        "m4v",
        "hevc",
        "h265",
        "h264",
        "prores",
        "轉檔",
        "格式",
    ),
    options=OPTIONS,
    tint=None,
    badge="MP4",
    presets={"video.convert-mp4": {"format": "mp4"}},
    inspect=common.inspect,
    requires=common.REQUIRES,
)
