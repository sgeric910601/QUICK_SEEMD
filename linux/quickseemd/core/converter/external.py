"""外部引擎的指令組合與執行（對應原版 CVExternalTools）：ffmpeg、pandoc、LibreOffice、ImageMagick。

ffmpeg 一律 -nostdin -n（絕不覆蓋）並只允許讀本機檔案；pandoc 加 --sandbox；
LibreOffice 每次用獨立的設定檔資料夾（見 office.py）。
"""

from __future__ import annotations

import os
from collections.abc import Callable
from pathlib import Path

from .. import backends, errors, naming
from .formats import Format
from .profile import Profile, probe_media


def _kbps(options: dict) -> str:
    return f"{max(64, int(options.get('audio_bitrate') or 192000) // 1000)}k"


def scale_filter(video_size: int) -> list[str]:
    """短邊縮到上限（不放大），長邊等比例且為偶數。"""
    if not video_size:
        return []
    cap = int(video_size)
    return ["-vf", f"scale='if(gt(iw,ih),-2,min(iw,{cap}))':'if(gt(iw,ih),min(ih,{cap}),-2)'"]


def _codec_args(fid: str, options: dict, scale: list[str]) -> list[str]:
    kbps = _kbps(options)
    opus = f"{max(64, min(256, int(options.get('audio_bitrate') or 192000) // 1000))}k"
    h264 = ["-c:v", "libx264", "-crf", "20"]
    hevc = ["-c:v", "libx265", "-crf", "24", "-tag:v", "hvc1", "-pix_fmt", "yuv420p"]
    table: dict[str, list[str]] = {
        "mp3": ["-vn", "-c:a", "libmp3lame", "-b:a", kbps],
        "ogg": ["-vn", "-c:a", "libvorbis", "-q:a", "6"],
        "opus": ["-vn", "-c:a", "libopus", "-b:a", opus],
        "flac": ["-vn", "-c:a", "flac"],
        "wav": ["-vn", "-c:a", "pcm_s16le"],
        "aiff": ["-vn", "-c:a", "pcm_s16be"],
        "m4a": ["-vn", "-c:a", "aac", "-b:a", kbps],
        "aac": ["-vn", "-c:a", "aac", "-b:a", kbps],
        "alac": ["-vn", "-c:a", "alac"],
        "caf": ["-vn", "-c:a", "pcm_s16le"],
        "webm": scale
        + ["-c:v", "libvpx-vp9", "-crf", "32", "-b:v", "0", "-row-mt", "1"]
        + ["-c:a", "libopus", "-b:a", "128k"],
        "avi": scale + ["-c:v", "mpeg4", "-q:v", "3", "-c:a", "libmp3lame", "-b:a", "192k"],
        "mp4": scale
        + h264
        + ["-preset", "medium", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", kbps]
        + ["-movflags", "+faststart"],
        "mp4-hevc": scale + hevc + ["-c:a", "aac", "-b:a", kbps, "-movflags", "+faststart"],
        "mov": scale + h264 + ["-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", kbps],
        "mov-hevc": scale + hevc + ["-c:a", "aac", "-b:a", kbps],
        "prores": scale + ["-c:v", "prores_ks", "-profile:v", "2", "-c:a", "pcm_s16le"],
    }
    table["m4v"] = table["mp4"]
    if fid == "mkv":
        if not scale:
            return ["-map", "0", "-c", "copy"]
        return scale + h264 + ["-preset", "medium", "-c:a", "copy"]
    if fid == "anim-gif":
        fps = max(1, min(30, int(options.get("gif_fps") or 12)))
        width = int(options.get("gif_width") or 480)
        chain = f"fps={fps},scale={width}:-1:flags=lanczos,split[a][b];[a]palettegen[p];[b][p]paletteuse"
        return ["-t", str(float(options.get("gif_seconds") or 15)), "-vf", chain, "-loop", "0"]
    return table.get(fid, [])


def ffmpeg_arguments(input_path: str, output: str, fid: str, options: dict) -> list[str]:
    """ffmpeg 參數（原版表格）；-n 確保絕不覆蓋，只允許讀本機檔案。"""
    base = ["-hide_banner", "-nostdin", "-n", "-protocol_whitelist", "file,pipe", "-i", input_path]
    scale = scale_filter(int(options.get("video_size") or 0))
    return base + _codec_args(fid, options, scale) + ["-progress", "pipe:1", "-nostats", output]


def progress_parser(
    duration: float | None, report: Callable[[float], None]
) -> Callable[[str], None]:
    """ffmpeg -progress 的 out_time_us=／out_time_ms=（兩者其實都是微秒）→ 進度（最多 0.99）。"""

    def on_line(line: str) -> None:
        if not duration or not line.startswith(("out_time_us=", "out_time_ms=")):
            return
        try:
            micro = float(line.split("=", 1)[1])
        except ValueError:
            return
        report(min(0.99, max(0.0, micro / 1_000_000 / duration)))

    return on_line


def media_duration(path: Path, profile: Profile) -> float | None:
    if profile.duration:
        return profile.duration
    info = probe_media(path)
    return info.duration if info else None


def run_ffmpeg(args: list[str], duration: float | None, ctx) -> None:  # noqa: ANN001
    tool = backends.require("ffmpeg")
    ctx.run_process([tool, *args], "ffmpeg", on_line=progress_parser(duration, ctx.progress))


def pandoc_arguments(input_path: str, output: str, reader: str | None, fid: str) -> list[str]:
    args = [input_path]
    if reader:
        args += ["-f", reader]
    extra = {"md": ["-t", "gfm", "--wrap=none"], "tex": ["-s"], "html": ["-s"], "epub": ["--toc"]}
    args += extra.get(fid, [])
    return args + ["--sandbox", "-o", output]


def magick_arguments(input_path: str, output: str, options: dict, keep_frames: bool) -> list[str]:
    args = [input_path if keep_frames else input_path + "[0]", "-auto-orient"]
    max_side = options.get("image_max_pixel")
    if max_side:
        args += ["-resize", f"{int(max_side)}x{int(max_side)}>"]
    quality = int(round(float(options.get("image_quality", 0.85)) * 100))
    return args + ["-quality", str(quality), output]


def run(
    path: Path,
    profile: Profile,
    fmt: Format,
    options: dict,
    work: Path,
    ctx,
    backend: str | None = None,
) -> list[Path]:  # noqa: ANN001, E501
    out = work / naming.join_name(naming.base_name(path), fmt.ext)
    src = os.path.abspath(path)
    if backend == "ffmpeg":
        duration = media_duration(path, profile)
        run_ffmpeg(ffmpeg_arguments(src, str(out), fmt.id, options), duration, ctx)
    elif backend == "pandoc":
        from . import pandocrun  # noqa: PLC0415

        pandocrun.convert(path, profile.pandoc_reader, fmt.id, out, work, ctx)
    elif backend == "soffice":
        from . import office  # noqa: PLC0415

        produced = office.convert_file(path, fmt.id, work, ctx)
        os.rename(produced, out)
    elif backend == "magick":
        keep = fmt.id in ("gif", "webp", "tiff")
        args = magick_arguments(src, str(out), options, keep)
        ctx.run_process([backends.require("magick"), *args], "ImageMagick")
    else:
        raise errors.QSError("內部錯誤：原生格式不應交給外部引擎。")
    if not out.exists():
        raise errors.tool_failed(backend, "沒有產生輸出檔。")
    ctx.progress(1)
    return [out]
