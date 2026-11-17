"""移除聲音（video.mute）與旋轉／翻轉影片（video.rotate），對應原版 MTVideoOps.mute／rotate。"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ...model import MEDIA, ToolContext, ToolDefinition
from ...options import Choice, Info, Kind, Option, Values, note
from . import common, engine, geometry
from .probe import MediaInfo, probe

# ---------------------------------------------------------------- 移除聲音


def _mute_reencode(file: Path, info: MediaInfo, out: Path, ctx: ToolContext) -> None:
    size = geometry.even_size(*info.display_size)
    enc = engine.standard_encode(info, engine.matching_codec(info), size, keep_audio=False)
    args = engine.input_args(file) + engine.encode_args(info, enc, engine.container_for(file))
    engine.run_ffmpeg(ctx, args, out, duration=info.duration, message="移除聲音（重新編碼）…")


def mute_file(file: Path, ctx: ToolContext) -> list[Path]:
    info = probe(file, ctx.cancel_event)
    info.require_video()
    container = engine.container_for(file)
    with engine.work_dir(ctx, file) as d:
        out = d / f"muted.{container.ext}"
        args = engine.input_args(file) + engine.map_args(info, audio=False)
        args += engine.copy_args(info) + container.args()
        done = engine.try_ffmpeg(
            ctx, args, out, duration=info.duration, message="移除聲音（畫質不變）…"
        )
        if not done:
            out = d / f"muted-encoded.{container.ext}"
            _mute_reencode(file, info, out, ctx)
        summary = "已移除聲音，影像未重新編碼、畫質不變" if done else "已移除聲音"
        name = ctx.output_name(file, "無聲", container.ext)
        return [engine.deliver(ctx, out, name, file, summary)]


def run_mute(files: list[Path], values: Values, ctx: ToolContext) -> list[Path]:
    return engine.batch(files, ctx, mute_file)


MUTE_TOOL = ToolDefinition(
    id="video.mute",
    category=MEDIA,
    title="移除聲音",
    subtitle="刪除影片的所有聲音，畫質完全不變。",
    icon="speaker.slash",
    run=run_mute,
    input_types=common.MOVIE_TYPES,
    input_label="影片",
    action_title="移除聲音",
    keywords=("mute", "remove audio", "silent", "靜音", "無聲", "消音"),
    options=(
        note(
            "直接移除所有音軌，影像不重新編碼：畫質完全不變、速度很快。輸出檔名會加上「無聲」。",
            group_icon="speaker.slash",
        ),
    ),
    presets={"video.remove-audio": {}},
    requires=common.REQUIRES,
)


# ---------------------------------------------------------------- 旋轉／翻轉


@dataclass(frozen=True)
class RotateOp:
    title: str
    subtitle: str
    icon: str
    ccw: int | None  # 方向標記要再轉的逆時針角度；None＝翻轉（一定重新編碼）
    filter: str  # 重新編碼時的濾鏡（套用在已轉正的畫面上）

    @property
    def is_flip(self) -> bool:
        return self.ccw is None

    @property
    def tag(self) -> str:
        return "已翻轉" if self.is_flip else "已旋轉"


OPS: dict[str, RotateOp] = {
    "right": RotateOp("向右轉 90°", "順時針", "rotate.right", -90, "transpose=clock"),
    "left": RotateOp("向左轉 90°", "逆時針", "rotate.left", 90, "transpose=cclock"),
    "upside_down": RotateOp("旋轉 180°", "上下顛倒", "arrow.up.arrow.down", 180, "hflip,vflip"),
    "flip_h": RotateOp(
        "水平翻轉", "左右鏡像（例如自拍鏡頭）", "arrow.left.and.right", None, "hflip"
    ),
    "flip_v": RotateOp("垂直翻轉", "上下鏡像", "arrow.up.and.down", None, "vflip"),
}


def normalized_angle(degrees: int) -> int:
    """換算到 -180 < 角度 ≤ 180。"""
    a = (degrees + 180) % 360 - 180
    return 180 if a == -180 else a


def _rotate_fast(file: Path, info: MediaInfo, op: RotateOp, out: Path, ctx: ToolContext) -> bool:
    """只改方向標記（display matrix）並直接複製串流。"""
    angle = normalized_angle(info.rotation + (op.ccw or 0))
    args = [f"-display_rotation:{info.video_index}", str(angle)] + engine.input_args(file)
    args += engine.map_args(info) + engine.copy_args(info) + engine.container_for(file).args()
    return engine.try_ffmpeg(
        ctx, args, out, duration=info.duration, message="旋轉中（不重新編碼）…"
    )


def _rotate_reencode(
    file: Path, info: MediaInfo, op: RotateOp, out: Path, ctx: ToolContext
) -> None:
    w, h = info.display_size
    size = geometry.even_size(*((h, w) if op.ccw in (90, -90) else (w, h)))
    enc = engine.standard_encode(
        info, engine.matching_codec(info), size, video_filters=(op.filter,)
    )
    message = "翻轉中（重新編碼）…" if op.is_flip else "旋轉中（重新編碼）…"
    args = engine.input_args(file) + engine.encode_args(info, enc, engine.container_for(file))
    engine.run_ffmpeg(ctx, args, out, duration=info.duration, message=message)


def rotate_file(file: Path, op: RotateOp, fast: bool, ctx: ToolContext) -> list[Path]:
    info = probe(file, ctx.cancel_event)
    info.require_video()
    container = engine.container_for(file)
    with engine.work_dir(ctx, file) as d:
        out = d / f"rotated.{container.ext}"
        done = fast and not op.is_flip and _rotate_fast(file, info, op, out, ctx)
        if not done:
            out = d / f"rotated-encoded.{container.ext}"
            _rotate_reencode(file, info, op, out, ctx)
        summary = f"{op.title}完成" + ("（只改方向標記，未重新編碼）" if done else "")
        name = ctx.output_name(file, op.tag, container.ext)
        return [engine.deliver(ctx, out, name, file, summary)]


def run_rotate(files: list[Path], values: Values, ctx: ToolContext) -> list[Path]:
    op = OPS[common.choice(values, "rotation", OPS)]
    fast = common.choice(values, "method", ("fast", "reencode")) == "fast"
    return engine.batch(files, ctx, lambda f, sub: rotate_file(f, op, fast, sub))


def _is_flip(values: Values, info: Info) -> bool:
    op = OPS.get(values.get("rotation"))
    return bool(op and op.is_flip)


ROTATE_OPTIONS: tuple[Option, ...] = (
    Option(
        key="rotation",
        kind=Kind.CARDS,
        label="方向",
        default="right",
        choices=tuple(Choice(k, op.title, op.subtitle, op.icon) for k, op in OPS.items()),
        group="方向",
        group_icon="rotate.right",
    ),
    Option(
        key="method",
        kind=Kind.CARDS,
        label="處理方式",
        default="fast",
        choices=(
            Choice("fast", "快速（不重新編碼）", "只改方向標記，幾秒完成、畫質無損", "bolt"),
            Choice(
                "reencode",
                "重新編碼",
                "所有播放器與網站都一定正確顯示",
                "arrow.triangle.2.circlepath",
            ),
        ),
        group="處理方式",
        group_icon="gearshape",
        visible=lambda v, i: not _is_flip(v, i),
    ),
    note(
        "翻轉需要重新編碼影像，會花一點時間；畫質會盡量維持與原檔相同。",
        group="處理方式",
        group_icon="gearshape",
        visible=_is_flip,
    ),
)

ROTATE_TOOL = ToolDefinition(
    id="video.rotate",
    category=MEDIA,
    title="旋轉／翻轉影片",
    subtitle="把拍歪的影片轉正，或左右、上下翻轉。",
    icon="rotate.right",
    run=run_rotate,
    input_types=common.MOVIE_TYPES,
    input_label="影片",
    action_title=lambda v, i: "翻轉影片" if _is_flip(v, i) else "旋轉影片",
    keywords=("rotate", "flip", "mirror", "轉向", "方向", "鏡像", "直式", "橫式"),
    options=ROTATE_OPTIONS,
    presets={"video.rotate-right": {"rotation": "right", "method": "fast"}},
    requires=common.REQUIRES,
)
