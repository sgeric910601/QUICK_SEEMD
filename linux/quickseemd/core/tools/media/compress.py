"""壓縮影片（video.compress，對應原版 MTVideoOps.compressPlan／compress 與 MTCompressSession）。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ... import errors
from ...model import MEDIA, ToolContext, ToolDefinition
from ...options import Choice, Info, Kind, Option, Values
from ...result import format_size
from . import common, engine, geometry
from .bitrate import effective_fps
from .probe import MediaInfo, infos_from, probe


@dataclass(frozen=True)
class Level:
    title: str
    subtitle: str
    icon: str
    short_side: int  # 0＝保留原始解析度
    bpp: float  # H.264 每像素每影格位元數（HEVC 另乘 0.65）
    source_factor: float  # 與來源位元率相比的上限比例
    audio: int


LEVELS: dict[str, Level] = {
    "strong": Level(
        "高度壓縮",
        "檔案最小，最高 720p，適合傳訊息或 Email",
        "arrow.down.right.and.arrow.up.left",
        720,
        0.045,
        0.35,
        96_000,
    ),
    "balanced": Level(
        "建議壓縮", "畫質與大小兼顧，最高 1080p", "checkmark.seal", 1080, 0.07, 0.55, 128_000
    ),
    "quality": Level("高畫質", "幾乎看不出差異，保留原始解析度", "sparkles", 0, 0.10, 0.75, 160_000),
}
CODEC_TITLES = {"h264": "H.264", "hevc": "HEVC"}
MIN_SAVING = 0.95  # 至少要小 5% 才交付


@dataclass(frozen=True)
class CompressPlan:
    width: int
    height: int
    scaled: bool
    video_bitrate: int
    audio_bitrate: int
    estimated_bytes: int
    problem: str | None = None


def _pixels(size: tuple[float, float]) -> float:
    return float(size[0] * size[1])


def _target_plan(info: MediaInfo, mb: float, codec: str) -> CompressPlan:
    """指定目標大小：先扣掉聲音，再挑能維持基本畫質的最大解析度。"""
    display = info.display_size
    duration = max(info.duration, 0.1)
    fps = effective_fps(info.fps)
    factor = 0.65 if codec == "hevc" else 1.0
    total = mb * 1_000_000 * 8 * 0.97 / duration
    audio = 0
    if info.has_audio:
        audio = 64_000 if total < 600_000 else 96_000 if total < 1_500_000 else 128_000
    video = total - audio
    if video < 100_000:
        need = (100_000 + (64_000 if info.has_audio else 0)) * duration / 8 / 1_000_000 / 0.97
        problem = "目標太小：這段影片至少需要約 %.1f MB。" % need
        return CompressPlan(*display, False, 100_000, audio, 0, problem)
    short = geometry.round_half_away(min(display))
    size, scaled = display, False
    for cap in (c for c in (short, 1080, 720, 540, 480, 360) if c <= short):
        size = geometry.capped(*display, cap) or display
        scaled = cap < short
        if video / (_pixels(size) * fps) >= 0.035 * factor:
            break
    est = int((video + audio) * duration / 8 * 1.01)
    problem = None
    if info.file_size > 0 and est >= info.file_size * 0.98:
        problem = "原檔已經小於目標大小，不需要壓縮。"
    return CompressPlan(*size, scaled, int(video), audio, est, problem)


def _level_plan(info: MediaInfo, level: Level, codec: str) -> CompressPlan:
    display = info.display_size
    duration = max(info.duration, 0.1)
    fps = effective_fps(info.fps)
    hevc = codec == "hevc"
    factor = 0.65 if hevc else 1.0
    capped = geometry.capped(*display, level.short_side) if level.short_side > 0 else None
    size = capped or display
    px = _pixels(size) * fps
    video = max(px * level.bpp * factor, px * 0.02 * factor, 150_000)
    if info.video_bitrate > 0 and _pixels(display) > 0:
        scale = min(1.0, _pixels(size) / _pixels(display))
        to_hevc = 0.8 if hevc and info.video_codec != "hevc" else 1.0
        video = min(video, info.video_bitrate * scale * level.source_factor * to_hevc)
    video = max(video, 80_000)
    audio = 0
    if info.has_audio:
        audio = level.audio
        if info.audio_codec == "aac" and info.audio_bitrate > 0:
            audio = min(audio, max(64_000, int(info.audio_bitrate)))
    est = int((video + audio) * duration / 8 * 1.01)
    problem = None
    if info.file_size > 0 and est >= info.file_size * 0.95:
        problem = "這個影片已經很精簡，可能無法再變小。"
    return CompressPlan(*size, capped is not None, int(video), audio, est, problem)


def compress_plan(info: MediaInfo, level: str, codec: str, target_mb: float | None) -> CompressPlan:
    if target_mb is not None and target_mb > 0:
        return _target_plan(info, target_mb, codec)
    return _level_plan(info, LEVELS[level], codec)


# ---------------------------------------------------------------- 執行


@dataclass(frozen=True)
class CompressOptions:
    level: str
    codec: str
    target_mb: float | None


def compress_file(file: Path, o: CompressOptions, ctx: ToolContext) -> Path | None:
    """壓縮一個影片；沒有變小時回傳 None（保留原檔、不產生檔案）。"""
    info = probe(file, ctx.cancel_event)
    info.require_video()
    plan = compress_plan(info, o.level, o.codec, o.target_mb)
    if plan.problem and plan.estimated_bytes == 0:
        raise errors.QSError(f"「{file.name}」{plan.problem}")
    if o.target_mb is not None and plan.problem:
        ctx.progress(1, f"「{file.name}」已經小於目標大小，保留原檔。")
        return None
    container = engine.container_for(file)
    title = CODEC_TITLES[o.codec]
    message = f"壓縮中（{title}，{plan.width}×{plan.height}）…"
    peak = plan.video_bitrate * (1.1 if o.target_mb is not None else 1.5)
    enc = engine.Encode(
        codec=o.codec,
        size=geometry.even_size(plan.width, plan.height),
        bitrate=plan.video_bitrate,
        fps=info.fps,
        keep_audio=plan.audio_bitrate > 0,
        audio_bitrate=plan.audio_bitrate or None,
        maxrate=int(peak),
    )
    with engine.work_dir(ctx, file) as d:
        out = d / f"compressed.{container.ext}"
        args = engine.input_args(file) + engine.encode_args(info, enc, container)
        engine.run_ffmpeg(ctx, args, out, duration=info.duration, message=message, span=(0, 0.97))
        new_size = out.stat().st_size
        if not (0 < new_size < info.file_size * MIN_SAVING):
            ctx.progress(1, f"「{file.name}」壓縮後沒有明顯變小，保留原檔。")
            return None
        saved = int((1 - new_size / max(1, info.file_size)) * 100)
        name = ctx.output_name(file, "已壓縮", container.ext)
        delivered = ctx.deliver(out, name, file)
        old_text, new_text = format_size(info.file_size), format_size(new_size)
        ctx.progress(1, f"從 {old_text} 縮小到 {new_text}，省下 {saved}%")
        return delivered


def _nothing_smaller(kept: list[str], target_mb: float | None) -> errors.QSError:
    names = f"「{kept[0]}」" if len(kept) == 1 else f"這 {len(kept)} 個影片"
    if target_mb is not None:
        return errors.QSError(
            f"{names}已經小於目標大小（{target_mb:g} MB）或無法再明顯變小，"
            "因此保留原檔、沒有產生新檔案。"
        )
    return errors.QSError(
        f"{names}已經很精簡，壓縮後沒有明顯變小（不到 5%），因此保留原檔、沒有產生新檔案。"
        "可以試試「高度壓縮」、HEVC 或指定目標大小。"
    )


def target_mb(values: Values) -> float | None:
    """「指定目標大小」開啟時的 MB 數；格式不對回傳 None（逗號當小數點）。"""
    raw = common.text(values, "target_mb").strip().replace(",", ".")
    try:
        mb = float(raw)
    except ValueError:
        return None
    return mb if mb == mb and mb not in (float("inf"), float("-inf")) else None


def _options(values: Values) -> CompressOptions:
    use_target = common.flag(values, "use_target", False)
    mb = target_mb(values) if use_target else None
    if use_target and (mb is None or mb <= 0):
        raise errors.QSError("請輸入目標大小（MB）")
    return CompressOptions(
        level=common.choice(values, "level", LEVELS),
        codec=common.choice(values, "codec", CODEC_TITLES),
        target_mb=mb,
    )


def run(files: list[Path], values: Values, ctx: ToolContext) -> list[Path]:
    o = _options(values)
    outputs: list[Path] = []
    kept: list[str] = []
    total_in = total_out = 0
    for i, f in enumerate(files):
        ctx.check_cancelled()
        sub = ctx.segment(i, len(files))
        sub.progress(0, f"壓縮中：{Path(f).name}")
        out = compress_file(Path(f), o, sub)
        if out is None:
            kept.append(Path(f).name)
        else:
            outputs.append(out)
            total_in += Path(f).stat().st_size
            total_out += out.stat().st_size
        sub.progress(1)
    if not outputs and kept:
        raise _nothing_smaller(kept, o.target_mb)
    saved = int((1 - total_out / total_in) * 100) if total_in > 0 else 0
    head = f"{len(outputs)} 個影片" if len(outputs) > 1 else ""
    summary = f"{head}從 {format_size(total_in)} 縮小到 {format_size(total_out)}，省下 {saved}%"
    if kept:
        summary += f"；{len(kept)} 個影片沒有明顯變小，已保留原檔"
    ctx.progress(1, summary)
    return outputs


# ---------------------------------------------------------------- 選項畫面


def validate(files: list[Path], values: Values, info: Info) -> str | None:
    if not common.flag(values, "use_target", False):
        return None
    mb = target_mb(values)
    if mb is None or mb <= 0:
        return "請輸入目標大小（MB）"
    single = common.single_info(info) if len(files) == 1 else None
    if single is not None:
        plan = compress_plan(single, values.get("level", "balanced"), values.get("codec"), mb)
        if plan.estimated_bytes == 0 and plan.problem:
            return plan.problem
    return None


def _kbps(bps: int) -> str:
    return "%.1f Mbps" % (bps / 1_000_000) if bps >= 1_000_000 else f"{bps // 1000} kbps"


def _estimate_note(values: Values, info: Info) -> tuple[str, bool] | None:
    """預估輸出大小（對應原版選項面板的預估區塊）。"""
    infos = infos_from(info)
    if not infos:
        return None
    use_target = common.flag(values, "use_target", False)
    mb = target_mb(values) if use_target else None
    level = values.get("level") if values.get("level") in LEVELS else "balanced"
    codec = values.get("codec") if values.get("codec") in CODEC_TITLES else "h264"
    plans = [compress_plan(i, level, codec, mb) for i in infos]
    size_in = sum(i.file_size for i in infos)
    size_out = sum(p.estimated_bytes for p in plans)
    lines = ["預估輸出" + (f"　約 {format_size(size_out)}" if size_out > 0 else "")]
    if size_out > 0 and 0 < size_in and size_out < size_in:
        saving = int((1 - size_out / size_in) * 100)
        lines.append(f"原始 {format_size(size_in)}，約可節省 {saving}%")
    if len(infos) == 1 and size_out > 0:
        p = plans[0]
        line = f"{p.width}×{p.height} · 影像 {_kbps(p.video_bitrate)}"
        lines.append(line + (f" · 聲音 {p.audio_bitrate // 1000} kbps" if p.audio_bitrate else ""))
    problem = next((p.problem for p in plans if p.problem), None)
    if problem:
        lines.append(problem)
    return "\n".join(lines), problem is not None


def _codec_note(values: Values, info: Info) -> tuple[str, bool]:
    if values.get("codec") == "hevc":
        return "HEVC 檔案約再小 35%，但較舊的裝置或 Windows 可能無法播放。", False
    return "幾乎所有裝置、瀏覽器與通訊軟體都能播放。", False


def _use_target(values: Values, info: Info) -> bool:
    return bool(values.get("use_target"))


OPTIONS: tuple[Option, ...] = (
    common.source_note(),
    Option(
        key="level",
        kind=Kind.CARDS,
        label="壓縮程度",
        default="balanced",
        choices=tuple(
            Choice(k, lv.title, lv.subtitle, lv.icon, "推薦" if k == "balanced" else None)
            for k, lv in LEVELS.items()
        ),
        group="壓縮程度",
        group_icon="gauge.with.dots.needle.50percent",
        enabled=lambda v, i: not _use_target(v, i),
    ),
    Option(key="_estimate", kind=Kind.NOTE, note=_estimate_note),
    Option(
        key="codec",
        kind=Kind.SEGMENTED,
        label="影片編碼",
        default="h264",
        choices=(Choice("h264", "H.264（相容性最佳）"), Choice("hevc", "HEVC（更小）")),
        group="影片編碼",
        group_icon="cpu",
        note=_codec_note,
    ),
    Option(
        key="use_target",
        kind=Kind.TOGGLE,
        label="指定目標大小",
        default=False,
        caption="自動計算位元率，必要時降低解析度",
        group="目標大小",
        group_icon="scalemass",
    ),
    Option(
        key="target_mb",
        kind=Kind.TEXT,
        label="MB",
        default="25",
        placeholder="25",
        caption="常見上限：Email 約 25 MB、Discord 免費版 10 MB",
        group="目標大小",
        group_icon="scalemass",
        visible=_use_target,
    ),
)

TOOL = ToolDefinition(
    id="video.compress",
    category=MEDIA,
    title="壓縮影片",
    subtitle="縮小影片檔案，方便傳訊息或寄 Email，並盡量保持畫質。",
    icon="arrow.down.right.and.arrow.up.left",
    run=run,
    input_types=common.MOVIE_TYPES,
    input_label="影片",
    action_title="壓縮影片",
    keywords=("compress", "reduce", "shrink", "smaller", "縮小", "瘦身", "檔案大小"),
    options=OPTIONS,
    presets={"video.compress-medium": {"level": "balanced", "codec": "h264"}},
    inspect=common.inspect,
    validate=validate,
    requires=common.REQUIRES,
)
