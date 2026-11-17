"""剪輯影片（video.trim，對應原版 MTVideoOps.trim 與 MTTrimSession）。

快速模式不重新編碼：起點對齊到前一個關鍵影格；刪除模式把後段的起點對齊關鍵影格後接起來。
精確模式重新編碼（與來源相同的編碼），準確到每一格畫面。
介面（GTK 主程式）用 custom_view="media-trim" 畫播放器與時間軸，選項鍵是 start／end／mode／method。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ... import errors
from ...model import MEDIA, ToolContext, ToolDefinition
from ...options import Choice, Info, Kind, Option, Values
from . import common, engine, geometry
from .probe import MediaInfo, inspect_media, keyframe_at_or_before, probe, too_short
from .timefmt import format_time, parse_time

EDGE = 0.05  # 這麼靠近開頭／結尾就當成開頭／結尾
SEEK_PAD = 0.0005  # 往後一點點，確保定位到該關鍵影格而不是前一個


@dataclass(frozen=True)
class TrimRange:
    start: float
    end: float | None  # None＝到結尾
    keep: bool


def parse_range(values: Values) -> tuple[float, float | None] | None:
    """選項的開始／結束時間；格式不正確時回傳 None。開始留白＝0、結束留白＝到結尾。"""
    start_text = common.text(values, "start").strip()
    end_text = common.text(values, "end").strip()
    start = parse_time(start_text) if start_text else 0.0
    end = parse_time(end_text) if end_text else None
    if start is None or (end_text and end is None):
        return None
    return start, end


def validate_range(files: list[Path], values: Values, info: Info) -> str | None:
    """剪輯影片與剪輯音訊共用的檢查（訊息照原版）。"""
    rng = parse_range(values)
    if rng is None:
        return "時間格式不正確，例如 1:05.5 或 65"
    start, end = rng
    if end is not None and end - start < 0.1:
        return "結束時間必須比開始時間晚至少 0.1 秒"
    single = common.single_info(info) if len(files) == 1 else None
    if single is not None:
        d = single.duration
        if start >= d:
            return f"開始時間超過檔案長度（{format_time(d)}）"
        effective_end = end if end is not None else d
        remove = values.get("mode") == "remove"
        if remove and start <= EDGE and effective_end >= d - EDGE:
            return "不能刪除整段內容"
    return None


def trim_options(values: Values) -> TrimRange:
    rng = parse_range(values)
    if rng is None:
        raise errors.QSError("時間格式不正確，例如 1:05.5 或 65")
    keep = common.choice(values, "mode", ("keep", "remove")) == "keep"
    return TrimRange(rng[0], rng[1], keep)


def clamp(info: MediaInfo, r: TrimRange) -> tuple[float, float]:
    """把範圍限制在檔案長度內，太短時丟出錯誤。"""
    s = max(0.0, min(r.start, info.duration))
    e = min(r.end if r.end is not None else info.duration, info.duration)
    if e - s < 0.1:
        raise too_short(info.name)
    return s, e


def remove_segments(s: float, e: float, duration: float, edge: float) -> list[tuple[float, float]]:
    """刪除 s–e 之後要保留的前後兩段。"""
    out: list[tuple[float, float]] = []
    if s > edge:
        out.append((0.0, s))
    if e < duration - edge:
        out.append((e, duration))
    return out


# ---------------------------------------------------------------- 快速（不重新編碼）


def _copy_part(
    file: Path, info: MediaInfo, seg: tuple[float, float], out: Path, ctx: ToolContext
) -> bool:
    a, b = seg
    args = ["-ss", f"{a + SEEK_PAD:.6f}"] if a > 0 else []
    args += ["-i", str(file.absolute()), "-t", f"{b - a:.6f}"]
    args += engine.map_args(info) + engine.copy_args(info) + engine.container_for(file).args()
    return engine.try_ffmpeg(
        ctx, args, out, duration=b - a, message="快速剪輯（不重新編碼）…", span=(0, 0.9)
    )


def _concat_copy(parts: list[Path], info: MediaInfo, out: Path, ctx: ToolContext) -> bool:
    """用 concat 讀取清單把各段接起來（清單只寫同資料夾的簡單檔名，不必跳脫）。"""
    listing = out.parent / "parts.txt"
    with open(listing, "x", encoding="utf-8") as fh:
        fh.writelines(f"file '{p.name}'\n" for p in parts)
    args = ["-f", "concat", "-i", str(listing.absolute()), "-map", "0"]
    args += engine.copy_args(info) + engine.container_for(out).args()
    return engine.try_ffmpeg(ctx, args, out, duration=info.duration, span=(0.9, 0.98))


def fast_trim(
    file: Path, info: MediaInfo, segments: list[tuple[float, float]], d: Path, ctx: ToolContext
) -> Path | None:
    """各段直接複製後接起來；任何一步失敗回傳 None（改用重新編碼）。"""
    if not segments:
        return None
    ext = engine.container_for(file).ext
    parts: list[Path] = []
    for i, seg in enumerate(segments):
        part = d / f"part{i + 1}.{ext}"
        if not _copy_part(file, info, seg, part, ctx):
            return None
        parts.append(part)
    if len(parts) == 1:
        return parts[0]
    out = d / f"joined.{ext}"
    return out if _concat_copy(parts, info, out, ctx) else None


# ---------------------------------------------------------------- 精確（重新編碼）


def segment_filter(info: MediaInfo, segments: list[tuple[float, float]], vf: str) -> str:
    """trim／atrim 切出各段，再用 concat 濾鏡接起來，最後套用 vf（縮放）。"""
    n = len(segments)
    has_audio = info.has_audio
    chains = [f"[0:{info.video_index}]split={n}" + "".join(f"[vs{i}]" for i in range(n))]
    if has_audio:
        chains.append(f"[0:{info.audio_index}]asplit={n}" + "".join(f"[as{i}]" for i in range(n)))
    joined = ""
    for i, (a, b) in enumerate(segments):
        chains.append(f"[vs{i}]trim=start={a:.6f}:end={b:.6f},setpts=PTS-STARTPTS[v{i}]")
        joined += f"[v{i}]"
        if has_audio:
            chains.append(f"[as{i}]atrim=start={a:.6f}:end={b:.6f},asetpts=PTS-STARTPTS[a{i}]")
            joined += f"[a{i}]"
    concat = f"concat=n={n}:v=1:a={1 if has_audio else 0}"
    chains.append(joined + concat + "[cv]" + ("[outa]" if has_audio else ""))
    chains.append(f"[cv]{vf}[outv]")
    return ";".join(chains)


def precise_trim(
    file: Path, info: MediaInfo, segments: list[tuple[float, float]], out: Path, ctx: ToolContext
) -> None:
    enc = engine.standard_encode(
        info, engine.matching_codec(info), geometry.even_size(*info.display_size)
    )
    total = sum(b - a for a, b in segments)
    message = "精確剪輯（重新編碼）…"
    container = engine.container_for(file)
    if len(segments) == 1:
        a, b = segments[0]
        args = engine.input_args(file, start=a) + ["-t", f"{b - a:.6f}"]
        args += engine.encode_args(info, enc, container)
    else:
        graph = segment_filter(info, segments, engine.scale_filter(enc.size))
        args = ["-i", str(file.absolute()), "-filter_complex", graph, "-map", "[outv]"]
        args += ["-map", "[outa]"] if info.has_audio else []
        args += ["-map_metadata", "0", "-map_chapters", "-1"]
        args += engine.video_codec_args(enc.codec, enc.bitrate, enc.fps)
        args += engine.audio_codec_args(info, enc.codec) if info.has_audio else ["-an"]
        args += container.args()
    engine.run_ffmpeg(ctx, args, out, duration=total, message=message)


# ---------------------------------------------------------------- 工作


def _summary(r: TrimRange, s: float, e: float, actual_start: float, duration: float) -> str:
    if not r.keep:
        rest = format_time(duration - (e - s))
        return f"已刪除 {format_time(s)}–{format_time(e)}，剩下約 {rest}"
    text = (
        f"已剪下 {format_time(actual_start)}–{format_time(e)}，長度 {format_time(e - actual_start)}"
    )
    return text + ("（起點對齊關鍵影格）" if actual_start < s - 0.01 else "")


def trim_file(file: Path, r: TrimRange, precise: bool, ctx: ToolContext) -> list[Path]:
    info = probe(file, ctx.cancel_event)
    info.require_video()
    s, e = clamp(info, r)
    if not r.keep and s <= EDGE and e >= info.duration - EDGE:
        raise errors.QSError(f"「{info.name}」不能刪除整段影片。")
    container = engine.container_for(file)
    with engine.work_dir(ctx, file) as d:
        out, actual_start = None, s
        if not precise:
            if r.keep:
                ks = keyframe_at_or_before(info, s, ctx.cancel_event)
                out = fast_trim(file, info, [(ks, e)], d, ctx)
                actual_start = ks if out else s
            else:
                ke = keyframe_at_or_before(info, e, ctx.cancel_event)
                out = fast_trim(file, info, remove_segments(s, ke, info.duration, EDGE), d, ctx)
        if out is None:
            segs = [(s, e)] if r.keep else remove_segments(s, e, info.duration, 0.01)
            out = d / f"trimmed.{container.ext}"
            precise_trim(file, info, segs, out, ctx)
            actual_start = s
        summary = _summary(r, s, e, actual_start, info.duration)
        name = ctx.output_name(file, "剪輯", container.ext)
        return [engine.deliver(ctx, out, name, file, summary)]


def run(files: list[Path], values: Values, ctx: ToolContext) -> list[Path]:
    r = trim_options(values)
    precise = common.choice(values, "method", ("fast", "precise")) == "precise"
    return engine.batch(files, ctx, lambda f, sub: trim_file(f, r, precise, sub))


# ---------------------------------------------------------------- 選項


def mode_option() -> Option:
    def note(values: Values, info: Info) -> tuple[str, bool]:
        if values.get("mode") == "remove":
            return "刪掉選取的片段，前後兩段接在一起。", False
        return "只留下選取的片段。", False

    return Option(
        key="mode",
        kind=Kind.SEGMENTED,
        label="要保留的部分",
        default="keep",
        choices=(Choice("keep", "保留選取範圍"), Choice("remove", "刪除選取範圍")),
        group="要保留的部分",
        group_icon="scissors",
        note=note,
    )


def time_options() -> tuple[Option, ...]:
    group = "時間範圍（套用到每個檔案）"
    return (
        Option(
            key="start",
            kind=Kind.TIME,
            label="開始",
            default="0:00",
            placeholder="0:00.0",
            group=group,
            group_icon="clock",
        ),
        Option(
            key="end",
            kind=Kind.TIME,
            label="結束",
            default="",
            placeholder="結尾",
            caption="格式例如 1:05.5 或 65；結束留白代表到結尾。",
            group=group,
            group_icon="clock",
        ),
    )


def inspect(files: list[Path]) -> Info:
    return inspect_media(list(files), with_keyframes=True)


OPTIONS: tuple[Option, ...] = (
    common.source_note(),
    mode_option(),
    *time_options(),
    Option(
        key="method",
        kind=Kind.CARDS,
        label="剪輯方式",
        default="fast",
        choices=(
            Choice(
                "fast",
                "快速（不重新編碼）",
                "幾秒完成、畫質無損；起點會對齊到前一個關鍵影格",
                "bolt",
            ),
            Choice("precise", "精確剪輯（重新編碼）", "準確到每一格畫面，需要較長時間", "scope"),
        ),
        group="剪輯方式",
        group_icon="gearshape",
    ),
)

TOOL = ToolDefinition(
    id="video.trim",
    category=MEDIA,
    title="剪輯影片",
    subtitle="預覽影片並拖曳時間軸，保留或刪除其中一段。",
    icon="scissors",
    run=run,
    input_types=common.MOVIE_TYPES,
    input_label="影片",
    action_title="剪輯影片",
    keywords=("trim", "cut", "clip", "剪片", "裁剪", "片段"),
    options=OPTIONS,
    inspect=inspect,
    validate=validate_range,
    custom_view="media-trim",
    requires=common.REQUIRES,
)
