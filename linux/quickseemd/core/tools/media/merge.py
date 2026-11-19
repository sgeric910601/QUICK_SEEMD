"""合併影片（video.merge，對應原版 MTVideoOps.merge 與 MTMergeSession）。

依順序接成一個 MP4：每段等比例縮放並補黑邊、影格率統一（最多 60 fps），
沒有聲音的片段用無聲音訊補上，有聲音的片段裁到與畫面等長。
所有輸入都用各自的 -i（絕對路徑），不需要清單檔，因此檔名不必跳脫。
"""

from __future__ import annotations

from pathlib import Path

from ... import errors
from ...model import MEDIA, ToolContext, ToolDefinition
from ...options import Choice, Info, Kind, Option, Values, note
from . import common, engine, geometry
from .bitrate import aac_bitrate, aac_sample_rate, effective_fps
from .probe import MediaInfo, infos_from, probe
from .timefmt import short

SIZES: dict[str, tuple[str, str]] = {
    "first": ("依第一個影片", "使用第一個影片的解析度與方向"),
    "fhd": ("1080p", "短邊 1080 像素，方向依第一個影片"),
    "hd": ("720p", "短邊 720 像素，檔案較小"),
}
CODECS = {"h264": "H.264", "hevc": "HEVC"}
MAX_FPS = 60.0
AUDIO_BITRATE = 192_000


def merge_render_size(width: float, height: float, option: str) -> tuple[int, int]:
    if option == "first":
        return geometry.even_size(width, height)
    target = 1080 if option == "fhd" else 720
    s = min(width, height)
    if s <= 0:
        return (1920, 1080)
    scale = target / s
    return geometry.even_size(width * scale, height * scale)


def _probe_all(files: list[Path], ctx: ToolContext) -> list[MediaInfo]:
    infos: list[MediaInfo] = []
    for i, f in enumerate(files):
        ctx.progress(0.01 * i / len(files), "讀取影片資訊…")
        info = probe(f, ctx.cancel_event)
        if not info.has_video:
            raise errors.QSError(f"「{info.name}」沒有影像軌，無法合併。")
        info.require_video()
        infos.append(info)
    return infos


def _frame_rate(infos: list[MediaInfo]) -> tuple[float, str]:
    """各片段最高的影格率（最多 60）；回傳數值與給 fps 濾鏡用的文字。"""
    best = max(infos, key=lambda i: i.fps)
    if best.fps > MAX_FPS:
        return MAX_FPS, "60"
    return best.fps, best.fps_rational


def merge_graph(
    infos: list[MediaInfo], size: tuple[int, int], fps_text: str, audio_rate: int | None
) -> str:
    chains: list[str] = []
    joined = ""
    sizing = engine.fit_filter(size)
    for i, info in enumerate(infos):
        d = f"{info.clip_duration:.6f}"
        chains.append(f"[{i}:{info.video_index}]{sizing},fps={fps_text},format=yuv420p[v{i}]")
        joined += f"[v{i}]"
        if audio_rate is None:
            continue
        if info.has_audio:
            chains.append(
                f"[{i}:{info.audio_index}]aresample={audio_rate},"
                f"aformat=sample_fmts=fltp:channel_layouts=stereo,"
                f"atrim=end={d},apad=whole_dur={d}[a{i}]"
            )
        else:
            chains.append(f"anullsrc=r={audio_rate}:cl=stereo:d={d}[a{i}]")
        joined += f"[a{i}]"
    has_audio = audio_rate is not None
    tail = f"concat=n={len(infos)}:v=1:a={1 if has_audio else 0}[v]" + ("[a]" if has_audio else "")
    return ";".join([*chains, joined + tail])


def _merge_args(
    files: list[Path],
    infos: list[MediaInfo],
    codec: str,
    size: tuple[int, int],
    fps: tuple[float, str],
) -> list[str]:
    rates = [i.sample_rate for i in infos if i.has_audio]
    audio_rate = aac_sample_rate(max(rates)) if rates else None
    args: list[str] = []
    for f in files:
        args += engine.input_args(Path(f))
    args += ["-filter_complex", merge_graph(infos, size, fps[1], audio_rate), "-map", "[v]"]
    if audio_rate is not None:
        args += ["-map", "[a]"]
    args += ["-map_metadata", "0", "-map_chapters", "-1"]
    factor = 0.06 if codec == "hevc" else 0.09
    bitrate = int(size[0] * size[1] * effective_fps(fps[0]) * factor)
    args += engine.video_codec_args(codec, bitrate, fps[0])
    if audio_rate is not None:
        kbps = aac_bitrate(AUDIO_BITRATE, audio_rate, 2)
        args += ["-c:a", "aac", "-b:a", str(kbps), "-ar", str(audio_rate), "-ac", "2"]
    return args + engine.MP4.args()


def run(files: list[Path], values: Values, ctx: ToolContext) -> list[Path]:
    if len(files) < 2:
        raise errors.QSError("至少需要 2 個影片才能合併。")
    option = common.choice(values, "size", SIZES)
    codec = common.choice(values, "codec", CODECS)
    infos = _probe_all([Path(f) for f in files], ctx)
    size = merge_render_size(*infos[0].display_size, option)
    fps = _frame_rate(infos)
    total = sum(i.clip_duration for i in infos)
    first = Path(files[0])
    message = f"合併 {len(files)} 個影片（{size[0]}×{size[1]}）…"
    with engine.work_dir(ctx, first) as d:
        out = d / "merged.mp4"
        args = _merge_args([Path(f) for f in files], infos, codec, size, fps)
        engine.run_ffmpeg(ctx, args, out, duration=total, message=message, span=(0.02, 0.98))
        summary = f"已合併 {len(files)} 個影片：長度 {short(total)}，{size[0]}×{size[1]}"
        name = ctx.output_name(first, "合併", "mp4")
        return [engine.deliver(ctx, out, name, first, summary)]


def validate(files: list[Path], values: Values, info: Info) -> str | None:
    return "至少需要 2 個影片" if len(files) < 2 else None


def _size_note(values: Values, info: Info) -> tuple[str, bool] | None:
    infos = infos_from(info)
    option = values.get("size")
    if not infos or option not in SIZES:
        return None
    w, h = merge_render_size(*infos[0].display_size, option)
    return f"輸出 {w}×{h}（MP4）", False


OPTIONS: tuple[Option, ...] = (
    common.source_note(),
    Option(
        key="size",
        kind=Kind.CARDS,
        label="輸出解析度",
        default="first",
        choices=tuple(Choice(k, t, s) for k, (t, s) in SIZES.items()),
        group="輸出解析度",
        group_icon="aspectratio",
        note=_size_note,
    ),
    Option(
        key="codec",
        kind=Kind.SEGMENTED,
        label="影片編碼",
        default="h264",
        choices=tuple(Choice(k, t) for k, t in CODECS.items()),
        group="影片編碼",
        group_icon="cpu",
    ),
    note(
        "依左側順序接成一個 MP4（拖曳卡片可調整順序）。尺寸或方向不同的影片會等比例縮放並補黑邊；"
        "沒有聲音的片段保持靜音。"
    ),
)

TOOL = ToolDefinition(
    id="video.merge",
    category=MEDIA,
    title="合併影片",
    subtitle="依順序把多個影片接成一個 MP4，尺寸不同時自動補黑邊。",
    icon="rectangle.stack.badge.plus",
    run=run,
    input_types=common.MOVIE_TYPES,
    input_label="影片",
    minimum_files=2,
    reorderable=True,
    action_title="合併影片",
    keywords=("merge", "join", "combine", "concat", "串接", "結合", "接起來"),
    options=OPTIONS,
    inspect=common.inspect,
    validate=validate,
    requires=common.REQUIRES,
)
