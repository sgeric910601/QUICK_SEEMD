"""影音轉換（對應原版 CVMediaConverter）；Linux 版全部交給 ffmpeg。

- 影片 → 影片、影片／音訊 → 音訊：照原版的 ffmpeg 參數表（無損格式依來源用 16 或 24 位元）。
- 影片 → GIF：照原版內建路線的規則（1–30 fps、長度上限、最多 900 格、只縮小不放大）。
- 影片 → 單張畫面：檔名「{主檔名}-{秒數}s」。
- 動畫圖片 → 影片：每格疊在白底上，依每格延遲輸出 H.264。
"""

from __future__ import annotations

import json
import math
import os
import tempfile
from pathlib import Path

from .. import backends, errors, naming, process
from .external import ffmpeg_arguments, media_duration, progress_parser, run_ffmpeg, scale_filter
from .formats import Format
from .image import flatten, load_image, write_image
from .profile import Profile

MAX_GIF_FRAMES = 900
MIN_FRAME_DELAY = 0.02
DEFAULT_FRAME_DELAY = 0.1
ANIMATION_MAX_SIDE = 1920
_DEEP_FORMATS = frozenset({"s32", "s32p", "s64", "s64p", "flt", "fltp", "dbl", "dblp"})
_LOSSLESS = frozenset({"flac", "alac", "wavpack", "ape", "tta", "mlp", "truehd"})
_PCM24 = {"wav": "pcm_s24le", "aiff": "pcm_s24be", "caf": "pcm_s24le"}


def _out(path: Path, work: Path, ext: str, stem: str | None = None) -> Path:
    return work / naming.join_name(stem or naming.base_name(path), ext)


def source_bit_depth(path: Path) -> int:
    """第一條音軌的位元深度（浮點或 32 位元視為 32），讀不到時當 16。"""
    tool = backends.find("ffprobe")
    if tool is None:
        return 16
    args = [tool, "-v", "error", "-protocol_whitelist", "file,pipe", "-select_streams", "a:0"]
    args += ["-show_entries", "stream=codec_name,sample_fmt,bits_per_raw_sample", "-of", "json"]
    try:
        res = process.run([*args, os.path.abspath(path)], timeout=30)
        streams = json.loads(res.stdout or "{}").get("streams") or [{}]
    except (ValueError, OSError):
        return 16
    stream = streams[0]
    codec = str(stream.get("codec_name") or "")
    if not (codec.startswith("pcm_") or codec in _LOSSLESS):
        return 16  # 有損來源（AAC、MP3…）原版視為 16 位元
    if codec.startswith("pcm_") and stream.get("sample_fmt") in _DEEP_FORMATS:
        return 32
    try:
        return int(stream.get("bits_per_raw_sample") or 16)
    except (TypeError, ValueError):
        return 16


def _audio_args(src: str, out: Path, fid: str, options: dict, deep: bool) -> list[str]:
    args = ffmpeg_arguments(src, str(out), fid, options)
    if deep and fid in _PCM24:
        args[args.index("pcm_s16le" if fid != "aiff" else "pcm_s16be")] = _PCM24[fid]
    return args


def convert_audio(
    path: Path, profile: Profile, fmt: Format, options: dict, work: Path, ctx
) -> list[Path]:  # noqa: ANN001, E501
    """音訊（或影片裡的音軌）→ 音訊；第一次失敗時改用 16 位元、預設位元率再試一次。"""
    if profile.is_video and not profile.has_audio_track:
        raise errors.no_audio_track()
    out = _out(path, work, fmt.ext)
    src = os.path.abspath(path)
    duration = media_duration(path, profile)
    deep = source_bit_depth(path) > 16
    ctx.progress(0.05)
    try:
        run_ffmpeg(_audio_args(src, out, fmt.id, options, deep), duration, ctx)
    except errors.Cancelled:
        raise
    except errors.QSError:
        out.unlink(missing_ok=True)  # 只刪自己剛產生的暫存輸出
        fallback = {k: v for k, v in options.items() if k != "audio_bitrate"}
        run_ffmpeg(_audio_args(src, out, fmt.id, fallback, False), duration, ctx)
    ctx.progress(1)
    return [out]


def convert_video(
    path: Path, profile: Profile, fmt: Format, options: dict, work: Path, ctx
) -> list[Path]:  # noqa: ANN001, E501
    if not profile.is_video:
        raise errors.no_video_track()
    out = _out(path, work, fmt.ext)
    args = ffmpeg_arguments(os.path.abspath(path), str(out), fmt.id, options)
    run_ffmpeg(args, media_duration(path, profile), ctx)
    ctx.progress(1)
    return [out]


def gif_plan(duration: float | None, options: dict) -> tuple[int, float, int, int]:
    """（fps, 長度, 格數上限, 最大寬度）：fps 1–30、長度 ≥ 0.1 秒、最多 900 格。"""
    fps = max(1, min(30, int(options.get("gif_fps") or 12)))
    limit = float(options.get("gif_seconds") or 15)
    length = max(0.1, min(duration if duration and math.isfinite(duration) else 0.0, limit))
    frames = max(1, min(MAX_GIF_FRAMES, int(length * fps)))
    return fps, length, frames, int(options.get("gif_width") or 480)


def video_to_gif(
    path: Path, profile: Profile, fmt: Format, options: dict, work: Path, ctx
) -> list[Path]:  # noqa: ANN001, ARG001, E501
    if not profile.is_video:
        raise errors.no_video_track()
    fps, length, frames, width = gif_plan(media_duration(path, profile), options)
    out = _out(path, work, "gif")
    fit = (
        f"scale=w='min(iw,{width})':h='min(ih,{width * 4})'"
        ":force_original_aspect_ratio=decrease:flags=lanczos"
    )
    chain = f"fps={fps},{fit},split[a][b];[a]palettegen[p];[b][p]paletteuse"
    args = ["-hide_banner", "-nostdin", "-n", "-protocol_whitelist", "file,pipe"]
    args += ["-i", os.path.abspath(path), "-t", f"{length:.3f}", "-vf", chain]
    args += ["-frames:v", str(frames), "-loop", "0", "-progress", "pipe:1", "-nostats", str(out)]
    run_ffmpeg(args, length, ctx)
    ctx.progress(1)
    return [out]


def frame_label(t: float) -> str:
    """1.0 → "1"、2.5 → "2.5"（原版：%.1f 後拿掉「.0」）。"""
    return f"{t:.1f}".replace(".0", "")


def frame_time(duration: float | None, requested: float | None) -> float:
    total = duration if duration and math.isfinite(duration) else 0.0
    t = requested if requested is not None else min(1.0, total / 2)
    return max(0.0, min(float(t), max(0.0, total - 0.05)))


def video_frame(
    path: Path, profile: Profile, fmt: Format, options: dict, work: Path, ctx
) -> list[Path]:  # noqa: ANN001, E501
    if not profile.is_video:
        raise errors.no_video_track()
    t = frame_time(media_duration(path, profile), options.get("frame_time"))
    scratch = Path(tempfile.mkdtemp(prefix=".frame-", dir=work))
    png = scratch / "frame.png"
    args = ["-hide_banner", "-nostdin", "-n", "-protocol_whitelist", "file,pipe"]
    args += ["-ss", f"{t:.3f}", "-i", os.path.abspath(path), "-frames:v", "1", "-an", str(png)]
    tool = backends.require("ffmpeg")
    ctx.run_process([tool, *args], "ffmpeg")
    if not png.exists():
        raise errors.unreadable("無法擷取影片畫面。")
    loaded = load_image(png, scratch, ctx, options.get("image_max_pixel"))
    out = _out(path, work, fmt.ext, f"{naming.base_name(path)}-{frame_label(t)}s")
    write_image(loaded, out, fmt.id, options.get("image_quality", 0.85), ctx)
    ctx.progress(1)
    return [out]


# ---------------------------------------------------------------- 動畫圖片 → 影片


def _even_floor(v: float) -> int:
    i = max(2, int(v))
    return i - i % 2


def _write_frames(
    path: Path, profile: Profile, options: dict, scratch: Path, ctx
) -> list[str]:  # noqa: ANN001, E501
    """把每一格疊在白底、縮成同一個偶數尺寸，存成 PNG；回傳 concat 清單的各行。"""
    limit = options.get("image_max_pixel") or ANIMATION_MAX_SIDE
    count = max(1, profile.frame_count)
    size: tuple[int, int] | None = None
    lines = ["ffconcat version 1.0"]
    for i in range(count):
        ctx.check_cancelled()
        loaded = load_image(path, scratch, ctx, limit, i)
        img = flatten(loaded.image)
        if size is None:
            size = (_even_floor(img.width), _even_floor(img.height))
        if img.size != size:
            img = img.resize(size)
        name = f"f{i:05d}.png"
        img.save(scratch / name, "PNG")
        delay = max(MIN_FRAME_DELAY, (loaded.duration_ms or 0) / 1000 or DEFAULT_FRAME_DELAY)
        lines += [f"file '{name}'", f"duration {delay:.4f}"]
        ctx.progress((i + 1) / count * 0.6)
    lines.append(f"file 'f{count - 1:05d}.png'")  # concat 規定：最後一格要再列一次才會用到 duration
    return lines


def animated_to_video(
    path: Path, profile: Profile, fmt: Format, options: dict, work: Path, ctx
) -> list[Path]:  # noqa: ANN001, E501
    scratch = Path(tempfile.mkdtemp(prefix=".anim-", dir=work))
    lines = _write_frames(path, profile, options, scratch, ctx)
    listing = scratch / "frames.ffconcat"
    listing.write_text("\n".join(lines) + "\n", "utf-8")
    out = _out(path, work, fmt.ext)
    codec = ["-c:v", "libx265", "-tag:v", "hvc1"] if "hevc" in fmt.id else ["-c:v", "libx264"]
    args = ["-hide_banner", "-nostdin", "-n", "-f", "concat", "-i", str(listing)]
    args += ["-fps_mode", "vfr", *codec, "-crf", "20", "-pix_fmt", "yuv420p"]
    if fmt.ext != "mov":
        args += ["-movflags", "+faststart"]
    args += ["-progress", "pipe:1", "-nostats", str(out)]
    tool = backends.require("ffmpeg")
    report = lambda f: ctx.progress(0.6 + f * 0.4)  # noqa: E731
    ctx.run_process([tool, *args], "ffmpeg", on_line=progress_parser(None, report))
    ctx.progress(1)
    return [out]


__all__ = [
    "animated_to_video",
    "convert_audio",
    "convert_video",
    "frame_label",
    "frame_time",
    "gif_plan",
    "scale_filter",
    "video_frame",
    "video_to_gif",
]
