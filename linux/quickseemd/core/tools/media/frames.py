"""擷取畫面（video.frames，對應原版 MTFrameOps.frames 與 MTFramesSession）。

每個時間點各跑一次 ffmpeg（輸入端精確定位），取「該時間點正在顯示的那一格」，
存成 PNG；JPG 再交給 Pillow 以品質 92 編碼（與原版 ImageIO 的 0.92 相同）。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from ... import errors, naming
from ...model import MEDIA, ToolContext, ToolDefinition
from ...options import Choice, Info, Kind, Option, Values
from . import common, engine, geometry
from .probe import MediaInfo, probe
from .timefmt import file_label, format_time, parse_times

MAX_FRAMES = 600
JPEG_QUALITY = 92
INTERVALS = (1, 2, 5, 10, 30, 60)
MAX_SIDES = (0, 1920, 1280, 640)
FORMATS = ("jpg", "png")
MODES: dict[str, tuple[str, str, str]] = {
    "times": ("指定時間點", "輸入一或多個時間，例如 0:05, 1:30", "clock"),
    "interval": ("每隔 N 秒", "從開頭起固定間隔擷取", "metronome"),
    "count": ("平均擷取 N 張", "平均分布在整段影片", "square.grid.3x3"),
}
BAD_TIMES = "時間點格式不正確，請用逗號分隔，例如 0:05, 1:30。"


@dataclass(frozen=True)
class FrameOptions:
    mode: str
    times_text: str
    interval: float
    count: int
    format: str
    max_side: int


def _interval_times(interval: float, last: float) -> list[float]:
    out: list[float] = []
    i = 0
    while len(out) < MAX_FRAMES:
        t = i * interval
        if t > last + 1e-9:
            break
        out.append(float(t))
        i += 1
    return out


def frame_times(
    mode: str, times_text: str, interval: float, count: int, duration: float
) -> list[float] | None:
    """依影片長度算出要擷取的時間點；時間點格式錯誤或間隔不正確時回傳 None。"""
    last = max(0.0, duration - 0.05)
    if mode == "times":
        parsed = parse_times(times_text)
        if parsed is None:
            return None
        seen: set[int] = set()
        out: list[float] = []
        for t in (min(p, last) for p in parsed):
            key = int(round(t * 100))
            if key not in seen:
                seen.add(key)
                out.append(t)
        return out
    if mode == "interval":
        return _interval_times(interval, last) if interval > 0 else None
    n = max(1, min(count, MAX_FRAMES))
    return [duration * (i + 0.5) / n for i in range(n)]


def _grab(
    file: Path, info: MediaInfo, t: float, size: tuple[int, int], out: Path, ctx: ToolContext
) -> None:
    """取出 t 秒正在顯示的那一格（往前退不到一格，取第一個不早於該處的畫面）。"""
    seek = max(0.0, t - 1.0 / max(info.fps, 1.0) + 0.001)
    args = engine.input_args(file, start=seek) + ["-map", f"0:{info.video_index}"]
    args += ["-frames:v", "1", "-vf", f"scale={size[0]}:{size[1]}:flags=lanczos,setsar=1"]
    args += ["-an", "-sn", "-dn", "-update", "1", "-c:v", "png", "-f", "image2"]
    try:
        engine.run_ffmpeg(ctx, args, out, duration=0)
    except errors.Cancelled:
        raise
    except errors.QSError as exc:
        raise errors.unreadable(f"無法擷取 {format_time(t)} 的畫面（{exc}）。") from exc


def _unique(folder: Path, name: str) -> Path:
    for cand in naming.candidate_names(name):
        if not (folder / cand).exists():
            return folder / cand
    raise errors.write_failed("找不到可用的檔名。")


def _write(png: Path, target: Path, fmt: str) -> None:
    if fmt == "png":
        png.rename(target)
        return
    with Image.open(png) as img:
        img.convert("RGB").save(target, "JPEG", quality=JPEG_QUALITY)


def frames_file(file: Path, o: FrameOptions, ctx: ToolContext) -> list[Path]:
    info = probe(file, ctx.cancel_event)
    info.require_video()
    times = frame_times(o.mode, o.times_text, o.interval, o.count, info.duration)
    if not times:
        raise errors.QSError(BAD_TIMES)
    dw, dh = info.display_size
    size = geometry.fit_inside(dw, dh, o.max_side, o.max_side) if o.max_side > 0 else (dw, dh)
    base = naming.sanitize(naming.base_name(file))
    with engine.work_dir(ctx, file) as d:
        folder = d / "frames"
        folder.mkdir()
        written: list[Path] = []
        for i, t in enumerate(times):
            ctx.check_cancelled()
            ctx.progress(i / len(times) * 0.95, f"擷取畫面：{i + 1}／{len(times)}（{format_time(t)}）")
            png = d / f"grab-{i}.png"
            _grab(file, info, t, size, png, ctx)
            target = _unique(folder, f"{base}-{file_label(t)}.{o.format}")
            _write(png, target, o.format)
            written.append(target)
        if len(written) == 1:
            summary = f"已擷取 {format_time(times[0])} 的畫面"
            return [engine.deliver(ctx, written[0], written[0].name, file, summary)]
        summary = f"已擷取 {len(written)} 張畫面，存在「{base}-畫面」資料夾"
        return [engine.deliver(ctx, folder, f"{base}-畫面", file, summary)]


def frame_options(values: Values) -> FrameOptions:
    return FrameOptions(
        mode=common.choice(values, "mode", MODES),
        times_text=common.text(values, "times"),
        interval=common.number(values, "interval", 5),
        count=int(common.number(values, "count", 10)),
        format=common.choice(values, "format", FORMATS),
        max_side=common.choice(values, "max_side", MAX_SIDES),
    )


def run(files: list[Path], values: Values, ctx: ToolContext) -> list[Path]:
    o = frame_options(values)
    return engine.batch(files, ctx, lambda f, sub: frames_file(f, o, sub))


# ---------------------------------------------------------------- 選項畫面


def validate(files: list[Path], values: Values, info: Info) -> str | None:
    if values.get("mode") == "times" and parse_times(common.text(values, "times")) is None:
        return "時間點格式不正確，例如 0:05, 1:30"
    return None


def _times_note(values: Values, info: Info) -> tuple[str, bool] | None:
    if parse_times(common.text(values, "times")) is None:
        return "格式不正確，請用逗號分隔，例如 0:05, 1:30", True
    return None


def _summary_note(values: Values, info: Info) -> tuple[str, bool]:
    single = common.single_info(info)
    if single is None:
        return "每個影片擷取多張時，會存成一個「影片名稱-畫面」資料夾。", False
    try:
        o = frame_options(values)
    except errors.QSError:
        return "請輸入正確的時間點。", False
    n = len(frame_times(o.mode, o.times_text, o.interval, o.count, single.duration) or [])
    if n == 0:
        return "請輸入正確的時間點。", False
    if n > 1:
        return f"將擷取 {n} 張，存成「{naming.base_name(single.path)}-畫面」資料夾。", False
    return "將擷取 1 張圖片。", False


def _mode_is(mode: str):  # noqa: ANN202 — 回傳 visible 用的判斷函式
    return lambda v, i: v.get("mode") == mode


GROUP = ("擷取方式", "camera.viewfinder")
OPTIONS: tuple[Option, ...] = (
    common.source_note(),
    Option(
        key="mode",
        kind=Kind.CARDS,
        label="擷取方式",
        default="count",
        choices=tuple(Choice(k, t, s, icon) for k, (t, s, icon) in MODES.items()),
        group=GROUP[0],
        group_icon=GROUP[1],
    ),
    Option(
        key="times",
        kind=Kind.TEXT,
        label="時間點",
        default="0:01",
        placeholder="0:05, 1:30, 2:15.5",
        group=GROUP[0],
        group_icon=GROUP[1],
        visible=_mode_is("times"),
        note=_times_note,
    ),
    Option(
        key="interval",
        kind=Kind.CHIPS,
        label="間隔",
        default=5,
        choices=tuple(Choice(s, f"{s} 秒") for s in INTERVALS),
        group=GROUP[0],
        group_icon=GROUP[1],
        visible=_mode_is("interval"),
    ),
    Option(
        key="count",
        kind=Kind.SLIDER,
        label="張數",
        default=10,
        minimum=2,
        maximum=60,
        step=1,
        display=lambda v: f"{int(v)} 張",
        group=GROUP[0],
        group_icon=GROUP[1],
        visible=_mode_is("count"),
    ),
    Option(
        key="format",
        kind=Kind.SEGMENTED,
        label="圖片格式",
        default="jpg",
        choices=(Choice("jpg", "JPG（檔案小）"), Choice("png", "PNG（無損）")),
        group="圖片格式",
        group_icon="photo",
    ),
    Option(
        key="max_side",
        kind=Kind.CHIPS,
        label="圖片大小（最長邊）",
        default=0,
        choices=tuple(Choice(s, "原始" if s == 0 else f"{s} px") for s in MAX_SIDES),
        group="圖片大小（最長邊）",
        group_icon="arrow.up.left.and.arrow.down.right",
    ),
    Option(key="_summary", kind=Kind.NOTE, note=_summary_note),
)

TOOL = ToolDefinition(
    id="video.frames",
    category=MEDIA,
    title="擷取畫面",
    subtitle="從影片存出 JPG 或 PNG 圖片：指定時間、固定間隔或平均擷取。",
    icon="camera.viewfinder",
    run=run,
    input_types=common.MOVIE_TYPES,
    input_label="影片",
    action_title="擷取畫面",
    keywords=("frame", "screenshot", "still", "thumbnail", "截圖", "快照", "縮圖", "圖片"),
    options=OPTIONS,
    badge="JPG",
    inspect=common.inspect,
    validate=validate,
    requires=common.REQUIRES,
)
