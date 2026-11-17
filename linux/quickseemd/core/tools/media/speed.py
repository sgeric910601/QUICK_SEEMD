"""調整影片速度（video.speed，對應原版 MTVideoOps.speed 與 MTSpeedSession）。

影格率不變，一律重新編碼（與來源相同的編碼）。保持音高用 atempo（原版用頻譜法）；
不保持音高時用 asetrate（像錄音帶一樣，速度與音高一起改變）。
"""

from __future__ import annotations

from pathlib import Path

from ... import errors
from ...model import MEDIA, ToolContext, ToolDefinition
from ...options import Choice, Info, Kind, Option, Values
from . import common, engine, geometry
from .probe import MediaInfo, probe
from .timefmt import short

RATES: tuple[float, ...] = (0.25, 0.5, 0.75, 1.25, 1.5, 2.0, 4.0)


def speed_tag(rate: float) -> str:
    """2 → 「2倍速」、0.5 → 「0.5倍速」。"""
    text = str(int(rate)) if rate == int(rate) else f"{rate:g}"
    return f"{text}倍速"


def atempo_chain(rate: float) -> list[str]:
    """atempo 每段限制在 0.5–2 倍（品質較好），超過就串接多段。"""
    out: list[str] = []
    r = rate
    while r > 2.0:
        out.append("atempo=2")
        r /= 2.0
    while r < 0.5:
        out.append("atempo=0.5")
        r /= 0.5
    out.append(f"atempo={r:.6g}")
    return out


def audio_filters(info: MediaInfo, rate: float, keep_pitch: bool) -> tuple[str, ...]:
    if keep_pitch:
        return tuple(atempo_chain(rate))
    sr = info.sample_rate
    return (f"asetrate={sr * rate:.6g}", f"aresample={sr}")


def speed_file(
    file: Path, rate: float, keep_pitch: bool, keep_audio: bool, ctx: ToolContext
) -> list[Path]:
    info = probe(file, ctx.cancel_event)
    info.require_video()
    if rate <= 0 or rate == 1:
        raise errors.QSError("請選擇 1× 以外的速度。")
    container = engine.container_for(file)
    new_duration = info.duration / rate
    enc = engine.standard_encode(
        info,
        engine.matching_codec(info),
        geometry.even_size(*info.display_size),
        keep_audio=keep_audio,
        video_filters=(f"setpts=PTS/{rate:g}", f"fps={info.fps_rational}"),
        audio_filters=audio_filters(info, rate, keep_pitch),
    )
    message = f"調整速度為 {rate:g}×…"
    with engine.work_dir(ctx, file) as d:
        out = d / f"speed.{container.ext}"
        args = engine.input_args(file) + engine.encode_args(info, enc, container)
        args += ["-t", f"{new_duration:.6f}"]
        engine.run_ffmpeg(ctx, args, out, duration=new_duration, message=message)
        summary = f"{rate:g}× 速度：{short(info.duration)} → {short(new_duration)}"
        name = ctx.output_name(file, speed_tag(rate), container.ext)
        return [engine.deliver(ctx, out, name, file, summary)]


def run(files: list[Path], values: Values, ctx: ToolContext) -> list[Path]:
    rate = common.number(values, "rate", 2.0)
    keep_audio = common.flag(values, "keep_audio", True)
    keep_pitch = common.flag(values, "keep_pitch", True)
    return engine.batch(
        files, ctx, lambda f, sub: speed_file(f, rate, keep_pitch, keep_audio, sub)
    )


def _length_note(values: Values, info: Info) -> tuple[str, bool] | None:
    single = common.single_info(info)
    try:
        rate = float(values.get("rate") or 0)
    except (TypeError, ValueError):
        return None
    if single is None or rate <= 0:
        return None
    return f"長度 {short(single.duration)} → {short(single.duration / rate)}", False


def _pitch_note(values: Values, info: Info) -> tuple[str, bool]:
    if values.get("keep_pitch", True):
        return "人聲不會變尖或變低沉", False
    return "像錄音帶一樣，速度與音高一起改變", False


OPTIONS: tuple[Option, ...] = (
    common.source_note(),
    Option(
        key="rate",
        kind=Kind.CHIPS,
        label="播放速度",
        default=2.0,
        choices=tuple(Choice(r, f"{r:g}×", "慢動作" if r < 1 else "快轉") for r in RATES),
        group="播放速度",
        group_icon="hare",
        note=_length_note,
    ),
    Option(
        key="keep_audio",
        kind=Kind.TOGGLE,
        label="保留聲音",
        default=True,
        group="聲音",
        group_icon="speaker.wave.2",
    ),
    Option(
        key="keep_pitch",
        kind=Kind.TOGGLE,
        label="保持原本音高",
        default=True,
        group="聲音",
        group_icon="speaker.wave.2",
        visible=lambda v, i: bool(v.get("keep_audio", True)),
        note=_pitch_note,
    ),
)

TOOL = ToolDefinition(
    id="video.speed",
    category=MEDIA,
    title="調整影片速度",
    subtitle="做出慢動作或快轉影片，可保持人聲音高。",
    icon="hare",
    run=run,
    input_types=common.MOVIE_TYPES,
    input_label="影片",
    action_title="調整速度",
    keywords=(
        "speed",
        "slow motion",
        "fast forward",
        "timelapse",
        "慢動作",
        "快轉",
        "加速",
        "倍速",
    ),
    options=OPTIONS,
    inspect=common.inspect,
    requires=common.REQUIRES,
)
