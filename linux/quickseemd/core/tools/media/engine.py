"""影音工具共用的 ffmpeg 執行與編碼設定（對應原版 MTTranscoder／MTExport／MTWork）。

- 一律 `ffmpeg -hide_banner -nostdin -n`，檔名用絕對路徑，輸出先寫在暫存資料夾。
- 進度用 `-progress pipe:1 -nostats` 讀 out_time_us，依輸出長度換算成比例。
- 取消時 process 模組會結束整個子程序群組，暫存資料夾整個刪掉，不留半成品。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from ... import backends, errors, naming
from ...model import ToolContext, per_file
from ...result import format_size, size_of
from .bitrate import aac_bitrate, aac_matching, aac_sample_rate, preserving
from .probe import MediaInfo

log = logging.getLogger(__name__)

FFMPEG_BASE: tuple[str, ...] = ("-hide_banner", "-nostdin", "-n", "-v", "error")
SAME_EXT_TAG = "已轉換"


# ---------------------------------------------------------------- 容器


@dataclass(frozen=True)
class Container:
    """輸出容器：副檔名與 ffmpeg 的 muxer。"""

    ext: str
    muxer: str

    def args(self) -> list[str]:
        out = ["-f", self.muxer]
        if self.muxer == "mp4":
            out += ["-movflags", "+faststart"]
        return out


MP4 = Container("mp4", "mp4")
MOV = Container("mov", "mov")
M4V = Container("m4v", "mp4")


def container_for(path: Path) -> Container:
    """mov／qt 與 m4v 維持原本的容器，其他一律 mp4。"""
    ext = Path(path).suffix.lower().lstrip(".")
    if ext in ("mov", "qt"):
        return MOV
    if ext == "m4v":
        return M4V
    return MP4


# ---------------------------------------------------------------- 執行 ffmpeg


class _Progress:
    """解析 -progress 的輸出，換算成 ctx.progress。"""

    def __init__(
        self,
        ctx: ToolContext,
        duration: float,
        span: tuple[float, float],
        message: str | None,
        on_frame: Callable[[int], None] | None,
    ) -> None:
        self.ctx = ctx
        self.duration = duration
        self.lo, self.hi = span
        self.message = message
        self.on_frame = on_frame
        self.frames = 0

    def __call__(self, line: str) -> None:
        key, _, value = line.strip().partition("=")
        if key in ("out_time_us", "out_time_ms") and value.isdigit() and self.duration > 0:
            frac = min(1.0, int(value) / 1_000_000 / self.duration)
            self.ctx.progress(self.lo + (self.hi - self.lo) * frac, self.message)
        elif key == "frame" and value.isdigit():
            self.frames = int(value)
            if self.on_frame is not None:
                self.on_frame(self.frames)


def ffmpeg_command(*parts: str) -> list[str]:
    return [backends.require("ffmpeg"), *FFMPEG_BASE, *parts]


def run_ffmpeg(
    ctx: ToolContext,
    args: Sequence[str],
    out: Path,
    *,
    duration: float,
    message: str | None = None,
    span: tuple[float, float] = (0.0, 0.98),
    on_frame: Callable[[int], None] | None = None,
) -> int:
    """執行 ffmpeg，輸出寫到 out（必須是暫存資料夾裡的新檔）；回傳最後回報的影格數。"""
    if message is not None:
        ctx.progress(span[0], message)
    state = _Progress(ctx, duration, span, message, on_frame)
    full = ffmpeg_command(*args, "-progress", "pipe:1", "-nostats", str(Path(out).absolute()))
    ctx.run_process(full, "ffmpeg", on_line=state)
    if not Path(out).is_file() or Path(out).stat().st_size == 0:
        raise errors.write_failed("ffmpeg 沒有產生輸出檔。")
    return state.frames


def try_ffmpeg(ctx: ToolContext, args: Sequence[str], out: Path, **kw) -> bool:
    """執行 ffmpeg；失敗（不是取消）時回傳 False，讓呼叫端改用重新編碼。"""
    try:
        run_ffmpeg(ctx, args, out, **kw)
        return True
    except errors.Cancelled:
        raise
    except errors.QSError as exc:
        log.info("直接轉封裝失敗，改用重新編碼：%s", exc)
        return False


# ---------------------------------------------------------------- 編碼設定


def input_args(path: Path, *, start: float | None = None) -> list[str]:
    out: list[str] = []
    if start is not None and start > 0:
        out += ["-ss", f"{start:.6f}"]
    return out + ["-i", str(Path(path).absolute())]


def map_args(info: MediaInfo, *, video: bool = True, audio: bool = True) -> list[str]:
    """只取第一條影像與第一條聲音（原版也只處理這兩條），保留檔案層級的中繼資料。"""
    out: list[str] = []
    if video and info.video_index is not None:
        out += ["-map", f"0:{info.video_index}"]
    if audio and info.audio_index is not None:
        out += ["-map", f"0:{info.audio_index}"]
    return out + ["-map_metadata", "0", "-map_chapters", "-1"]


def copy_args(info: MediaInfo) -> list[str]:
    """不重新編碼；HEVC 標成 hvc1 讓 Apple 裝置也能播放。"""
    out = ["-c", "copy"]
    if info.video_codec == "hevc":
        out += ["-tag:v", "hvc1"]
    return out


def gop(fps: float) -> str:
    """最長 2 秒一個關鍵影格。"""
    return str(max(1, round(2 * (fps if fps > 1 else 30))))


def video_codec_args(codec: str, bitrate: int, fps: float, maxrate: int | None = None) -> list[str]:
    """H.264 High／HEVC Main（平均位元率、2 秒關鍵影格、B 影格）或 ProRes 422。"""
    if codec == "prores":
        return ["-c:v", "prores_ks", "-profile:v", "2", "-vendor", "apl0", "-pix_fmt", "yuv422p10le"]
    common = ["-b:v", str(int(bitrate)), "-g", gop(fps), "-pix_fmt", "yuv420p"]
    if maxrate:
        common += ["-maxrate", str(int(maxrate)), "-bufsize", str(int(maxrate))]
    if codec == "hevc":
        return [
            "-c:v",
            "libx265",
            "-preset",
            "medium",
            "-tag:v",
            "hvc1",
            "-x265-params",
            "log-level=error",
            *common,
        ]
    return ["-c:v", "libx264", "-preset", "medium", "-profile:v", "high", *common]


def audio_codec_args(info: MediaInfo, codec: str, bitrate: int | None = None) -> list[str]:
    """影片的聲音：AAC（位元率與取樣率對齊可用值、最多 2 聲道）；ProRes 用 PCM。"""
    channels = max(1, min(2, info.channels))
    if codec == "prores":
        depth = 24 if info.bit_depth > 16 else 16
        return ["-c:a", f"pcm_s{depth}le", "-ar", str(info.sample_rate), "-ac", str(channels)]
    rate = aac_sample_rate(info.sample_rate)
    kbps = aac_bitrate(bitrate or aac_matching(info), rate, channels)
    return ["-c:a", "aac", "-b:a", str(kbps), "-ar", str(rate), "-ac", str(channels)]


def matching_codec(info: MediaInfo) -> str:
    """與來源相同的編碼（H.264／HEVC 以外一律用 H.264）。"""
    return "hevc" if info.video_codec == "hevc" else "h264"


def scale_filter(size: tuple[int, int]) -> str:
    return f"scale={size[0]}:{size[1]}:flags=bicubic,setsar=1"


def fit_filter(size: tuple[int, int], fill: bool = False) -> str:
    """等比例放進 size 並置中：fill=False 補黑邊，True 放大後裁切填滿。"""
    w, h = size
    if fill:
        return (
            f"scale={w}:{h}:force_original_aspect_ratio=increase:flags=bicubic,"
            f"crop={w}:{h},setsar=1"
        )
    return (
        f"scale={w}:{h}:force_original_aspect_ratio=decrease:force_divisible_by=2:flags=bicubic,"
        f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2:color=black,setsar=1"
    )


@dataclass(frozen=True)
class Encode:
    """一次重新編碼的設定（對應原版 MTTranscodeSpec 的影像與聲音部分）。"""

    codec: str  # h264／hevc／prores
    size: tuple[int, int]  # 輸出尺寸（偶數）
    bitrate: int
    fps: float
    keep_audio: bool = True
    audio_bitrate: int | None = None
    maxrate: int | None = None
    video_filters: tuple[str, ...] = ()  # 縮放之前套用（變速、旋轉…）
    audio_filters: tuple[str, ...] = ()
    fit: str | None = None  # None＝直接縮放；"pad" 補黑邊；"crop" 裁切填滿


def standard_encode(info: MediaInfo, codec: str, size: tuple[int, int], **kw) -> Encode:
    """「盡量保持畫質」的位元率（原版 MTVideoOps.spec 的預設）。"""
    fps = kw.pop("fps", None) or info.fps
    rate = kw.pop("bitrate", None) or preserving(info, codec, size[0], size[1], fps)
    return Encode(codec=codec, size=size, bitrate=rate, fps=fps, **kw)


def encode_args(info: MediaInfo, enc: Encode, container: Container) -> list[str]:
    """-map 之後到容器設定為止的參數（輸入與輸出路徑由呼叫端加）。"""
    audio = enc.keep_audio and info.has_audio
    if enc.fit is None:
        sizing = scale_filter(enc.size)
    else:
        sizing = fit_filter(enc.size, fill=enc.fit == "crop")
    args = map_args(info, audio=audio) + ["-vf", ",".join([*enc.video_filters, sizing])]
    args += video_codec_args(enc.codec, enc.bitrate, enc.fps, enc.maxrate)
    if audio:
        if enc.audio_filters:
            args += ["-af", ",".join(enc.audio_filters)]
        args += audio_codec_args(info, enc.codec, enc.audio_bitrate)
    else:
        args += ["-an"]
    return args + container.args()


# ---------------------------------------------------------------- 暫存與交付


@contextmanager
def work_dir(ctx: ToolContext, for_input: Path) -> Iterator[Path]:
    """建立暫存資料夾（和輸出位置同一顆磁碟），不論成功、失敗或取消都刪掉。"""
    d = ctx.make_work_dir(ctx.output_directory(for_input))
    try:
        yield d
    finally:
        naming.remove_work_dir(d)


def output_name(ctx: ToolContext, for_input: Path, tag: str | None, ext: str) -> str:
    """同副檔名時加上「已轉換」，避免與原檔混淆。"""
    same = Path(for_input).suffix.lower().lstrip(".") == ext.lower()
    return ctx.output_name(for_input, tag or (SAME_EXT_TAG if same else None), ext)


def deliver(
    ctx: ToolContext,
    temp: Path,
    name: str,
    for_input: Path,
    summary: str,
    append_size: bool = True,
) -> Path:
    """交付輸出檔，並把摘要（附檔案大小）當作最後的進度訊息。"""
    size = 0 if not append_size or Path(temp).is_dir() else size_of(temp)
    out = ctx.deliver(temp, name, for_input)
    ctx.progress(1, f"{summary}（{format_size(size)}）" if size > 0 else summary)
    return out


def batch(
    files: Sequence[Path], ctx: ToolContext, body: Callable[[Path, ToolContext], list[Path]]
) -> list[Path]:
    """逐檔處理；多個檔案時最後回報整批摘要。"""
    outputs = per_file(files, ctx, body)
    if len(files) > 1:
        size = sum(size_of(p) for p in outputs)
        tail = f"，共 {format_size(size)}" if size > 0 else ""
        ctx.progress(1, f"已完成 {len(files)} 個檔案{tail}")
    return outputs
