"""擷取音訊（video.extract-audio）、轉換音訊格式（audio.convert）、剪輯音訊（audio.trim），
對應原版 MTAudioOps 與 MTAudioOutputSession。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

from ... import errors
from ...model import MEDIA, TINTS, ToolContext, ToolDefinition
from ...options import Choice, Info, Kind, Option, Values, note
from ...result import format_size
from . import audio_engine, common, engine, trim
from .audio_engine import FORMATS, AudioOutput
from .probe import probe
from .timefmt import format_time, short

FADE_SECONDS = 2.0


# ---------------------------------------------------------------- 工作


def extract_file(file: Path, output: AudioOutput, ctx: ToolContext) -> list[Path]:
    info = probe(file, ctx.cancel_event)
    info.require_audio()
    fmt = FORMATS[output.format]
    with engine.work_dir(ctx, file) as d:
        out = d / f"audio.{fmt.ext}"
        audio_engine.render(file, info, output, out, ctx, message=f"擷取音訊（{fmt.title}）…")
        summary = f"已擷取 {short(info.duration)} 的音訊，存成 {fmt.title}"
        name = engine.output_name(ctx, file, None, fmt.ext)
        return [engine.deliver(ctx, out, name, file, summary)]


def convert_file(file: Path, output: AudioOutput, ctx: ToolContext) -> list[Path]:
    info = probe(file, ctx.cancel_event)
    info.require_audio()
    fmt = FORMATS[output.format]
    with engine.work_dir(ctx, file) as d:
        out = d / f"audio.{fmt.ext}"
        audio_engine.render(file, info, output, out, ctx, message=f"轉換成 {fmt.title}…")
        sizes = f"{format_size(info.file_size)} → {format_size(out.stat().st_size)}"
        name = engine.output_name(ctx, file, None, fmt.ext)
        return [engine.deliver(ctx, out, name, file, f"已轉成 {fmt.title}：{sizes}", False)]


def trim_file(
    file: Path, r: trim.TrimRange, fades: tuple[float, float], ctx: ToolContext
) -> list[Path]:
    info = probe(file, ctx.cancel_event)
    info.require_audio()
    s, e = trim.clamp(info, r)
    segments = [(s, e)] if r.keep else trim.remove_segments(s, e, info.duration, 0.01)
    if not segments:
        raise errors.QSError(f"「{info.name}」不能刪除整段內容。")
    fmt_key = audio_engine.matching_format(info)
    output = AudioOutput(fmt_key, audio_engine.matching_bitrate(info, fmt_key))
    ext = FORMATS[fmt_key].ext
    with engine.work_dir(ctx, file) as d:
        out = d / f"trimmed.{ext}"
        total = audio_engine.render(
            file, info, output, out, ctx, message="剪輯音訊…", segments=segments, fades=fades
        )
        summary = f"已剪輯音訊，長度 {format_time(total)}"
        summary += "，含淡入淡出" if any(f > 0 for f in fades) else ""
        name = ctx.output_name(file, "剪輯", ext)
        return [engine.deliver(ctx, out, name, file, summary)]


def output_from(values: Values, *, convert: bool) -> AudioOutput:
    fmt = common.choice(values, "format", FORMATS)
    kbps = common.choice(values, "bitrate", audio_engine.BITRATES_KBPS)
    rate = common.choice(values, "sample_rate", (0, 44_100, 48_000)) if convert else 0
    channels = common.choice(values, "channels", (0, 1, 2)) if convert else 0
    return AudioOutput(fmt, kbps * 1000, rate or None, channels or None)


def run_extract(files: list[Path], values: Values, ctx: ToolContext) -> list[Path]:
    output = output_from(values, convert=False)
    return engine.batch(files, ctx, lambda f, sub: extract_file(f, output, sub))


def run_convert(files: list[Path], values: Values, ctx: ToolContext) -> list[Path]:
    output = output_from(values, convert=True)
    return engine.batch(files, ctx, lambda f, sub: convert_file(f, output, sub))


def run_trim(files: list[Path], values: Values, ctx: ToolContext) -> list[Path]:
    r = trim.trim_options(values)
    fade_in = FADE_SECONDS if common.flag(values, "fade_in", False) else 0.0
    fade_out = FADE_SECONDS if common.flag(values, "fade_out", False) else 0.0
    return engine.batch(files, ctx, lambda f, sub: trim_file(f, r, (fade_in, fade_out), sub))


# ---------------------------------------------------------------- 選項


def _format_note(values: Values, info: Info) -> tuple[str, bool] | None:
    fmt = FORMATS.get(values.get("format"))
    return (fmt.subtitle, False) if fmt else None


def _uses_bitrate(values: Values, info: Info) -> bool:
    fmt = FORMATS.get(values.get("format"))
    return bool(fmt and fmt.uses_bitrate)


def _bitrate_note(values: Values, info: Info) -> tuple[str, bool] | None:
    kbps = values.get("bitrate")
    return (audio_engine.bitrate_hint(kbps), False) if isinstance(kbps, int | float) else None


def _estimate_note(values: Values, info: Info) -> tuple[str, bool] | None:
    single = common.single_info(info)
    kbps = values.get("bitrate")
    if single is None or single.duration <= 0 or not isinstance(kbps, int | float):
        return None
    return f"預估約 {format_size(int(kbps * 1000 * single.duration / 8))}", False


# 轉換音訊才有的選項
CONVERT_EXTRA: tuple[Option, ...] = (
    Option(
        key="sample_rate",
        kind=Kind.SEGMENTED,
        label="取樣率",
        default=0,
        choices=(Choice(0, "原始"), Choice(44_100, "44.1 kHz"), Choice(48_000, "48 kHz")),
        group="取樣率",
        group_icon="waveform",
    ),
    Option(
        key="channels",
        kind=Kind.SEGMENTED,
        label="聲道",
        default=0,
        choices=(Choice(0, "原始"), Choice(1, "單聲道"), Choice(2, "立體聲")),
        group="聲道",
        group_icon="hifispeaker.2",
    ),
)


def output_options(default_bitrate: int, convert: bool) -> tuple[Option, ...]:
    """擷取音訊與轉換音訊共用的選項；轉換音訊另有取樣率與聲道。"""
    opts = [
        common.source_note(),
        Option(
            key="format",
            kind=Kind.CHIPS,
            label="輸出格式",
            default="m4a",
            choices=tuple(Choice(k, f.title, f.subtitle) for k, f in FORMATS.items()),
            group="輸出格式",
            group_icon="music.note",
            note=_format_note,
        ),
        Option(
            key="bitrate",
            kind=Kind.CHIPS,
            label="位元率（音質）",
            default=default_bitrate,
            choices=tuple(
                Choice(k, f"{k} kbps", audio_engine.bitrate_hint(k))
                for k in audio_engine.BITRATES_KBPS
            ),
            group="位元率（音質）",
            group_icon="dial.medium",
            visible=_uses_bitrate,
            note=_bitrate_note,
        ),
    ]
    if convert:
        opts += CONVERT_EXTRA
    opts.append(Option(key="_estimate", kind=Kind.NOTE, note=_estimate_note, visible=_uses_bitrate))
    return tuple(opts)


def _title(prefix: str) -> Callable[[Values, Info], str]:
    """執行按鈕文字：「擷取成 M4A (AAC)」「轉成 WAV」。"""

    def title(values: Values, info: Info) -> str:
        fmt = FORMATS.get(values.get("format"))
        return f"{prefix} {fmt.title}" if fmt else prefix

    return title


EXTRACT_TOOL = ToolDefinition(
    id="video.extract-audio",
    category=MEDIA,
    title="擷取音訊",
    subtitle="把影片的聲音存成 M4A、WAV、AIFF、FLAC 等音訊檔。",
    icon="waveform",
    run=run_extract,
    input_types=common.MOVIE_TYPES,
    input_label="影片",
    action_title=_title("擷取成"),
    keywords=("extract", "audio", "sound", "mp3", "m4a", "wav", "聲音", "音樂", "配樂"),
    options=output_options(192, convert=False),
    badge="M4A",
    presets={"video.audio-m4a": {"format": "m4a", "bitrate": 192}},
    inspect=common.inspect,
    requires=common.REQUIRES,
)

CONVERT_TOOL = ToolDefinition(
    id="audio.convert",
    category=MEDIA,
    title="轉換音訊格式",
    subtitle="在 M4A、ALAC、WAV、AIFF、FLAC、CAF 之間轉換，可調整音質與聲道。",
    icon="music.note",
    run=run_convert,
    input_types=common.AUDIO_TYPES,
    input_label="音訊檔",
    action_title=_title("轉成"),
    keywords=(
        "audio",
        "convert",
        "mp3",
        "m4a",
        "aac",
        "wav",
        "flac",
        "aiff",
        "alac",
        "音樂",
        "轉檔",
    ),
    options=output_options(256, convert=True),
    tint=TINTS["audio"],
    badge="M4A",
    presets={
        "audio.convert-m4a": {"format": "m4a", "bitrate": 256},
        "audio.convert-wav": {"format": "wav"},
    },
    inspect=common.inspect,
    requires=common.REQUIRES,
)

TRIM_OPTIONS: tuple[Option, ...] = (
    common.source_note(),
    trim.mode_option(),
    *trim.time_options(),
    Option(
        key="fade_in",
        kind=Kind.TOGGLE,
        label="開頭淡入",
        default=False,
        caption="2 秒內由無聲漸強",
        group="淡入淡出",
        group_icon="waveform.path",
    ),
    Option(
        key="fade_out",
        kind=Kind.TOGGLE,
        label="結尾淡出",
        default=False,
        caption="最後 2 秒漸弱到無聲",
        group="淡入淡出",
        group_icon="waveform.path",
    ),
    note("輸出格式與原檔相同（MP3 會輸出成 M4A；若已安裝 ffmpeg 則維持 MP3）。"),
)

TRIM_TOOL = ToolDefinition(
    id="audio.trim",
    category=MEDIA,
    title="剪輯音訊",
    subtitle="看著波形挑出要的片段，可加上淡入淡出。",
    icon="waveform.badge.minus",
    run=run_trim,
    input_types=common.AUDIO_TYPES,
    input_label="音訊檔",
    action_title="剪輯音訊",
    keywords=("trim", "cut", "audio", "ringtone", "剪輯", "鈴聲", "片段", "淡入", "淡出"),
    options=TRIM_OPTIONS,
    tint=TINTS["audio"],
    inspect=common.inspect,
    validate=trim.validate_range,
    custom_view="audio-trim",
    requires=common.REQUIRES,
)
