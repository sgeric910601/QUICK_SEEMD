"""剪輯畫面用的影音資訊（背景執行緒呼叫）：長度、縮圖帶、波形。

一律用 backends 找到的 ffprobe／ffmpeg（絕對路徑），暫存檔放在自己的快取資料夾並清掉。
"""

from __future__ import annotations

import io
import json
import logging
from dataclasses import dataclass
from pathlib import Path

import numpy as np

from ...core import backends, naming, process
from ..thumbnails import video_frame
from .trim_model import compute_peaks, strip_times

LOG = logging.getLogger(__name__)
PROBE_TIMEOUT = 20.0
WAVE_RATE = 4000
PEAK_COUNT = 280
STRIP_COUNT = 12
STRIP_HEIGHT = 64


@dataclass(frozen=True)
class MediaInfo:
    duration: float
    has_video: bool
    width: int = 0
    height: int = 0
    summary: str = ""


def _summary(duration: float, has_video: bool, w: int, h: int, codec: str) -> str:
    from ..options_model import format_time  # noqa: PLC0415

    parts = [format_time(duration, decimals=0)]
    if has_video and w and h:
        parts.append(f"{w}×{h}")
    if codec:
        parts.append(codec.upper())
    return " · ".join(parts)


def probe(path: Path) -> MediaInfo | None:
    ffprobe = backends.find("ffprobe")
    if ffprobe is None:
        return None
    args = [
        ffprobe,
        "-v",
        "error",
        "-show_entries",
        "format=duration:stream=codec_type,codec_name,width,height",
    ]
    args += ["-of", "json", str(Path(path).resolve())]
    try:
        res = process.run(args, timeout=PROBE_TIMEOUT)
        data = json.loads(res.stdout or "{}")
    except (ValueError, OSError) as exc:
        LOG.info("讀不到影音資訊：%s", exc)
        return None
    duration = float((data.get("format") or {}).get("duration") or 0)
    streams = data.get("streams") or []
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if duration <= 0:
        return None
    w, h = (int(video.get("width") or 0), int(video.get("height") or 0)) if video else (0, 0)
    codec = str((video or audio or {}).get("codec_name") or "")
    return MediaInfo(
        duration, video is not None, w, h, _summary(duration, video is not None, w, h, codec)
    )


def strip(path: Path, duration: float) -> list[bytes]:
    """縮圖帶：平均取 12 張畫面（PNG）。"""
    frames = []
    for t in strip_times(duration, STRIP_COUNT):
        data = video_frame(path, t, STRIP_HEIGHT)
        if data:
            frames.append(data)
    return frames


def peaks(path: Path) -> list[float]:
    """波形：轉成單聲道 4000 Hz 的 16 位元樣本，切 280 段取最大振幅。"""
    ffmpeg = backends.find("ffmpeg")
    if ffmpeg is None:
        return []
    cache = backends.cache_dir()
    cache.mkdir(parents=True, exist_ok=True)
    work = naming.make_work_dir(cache)
    try:
        out = work / "wave.raw"
        args = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-n"]
        args += ["-i", str(Path(path).resolve()), "-ac", "1", "-ar", str(WAVE_RATE)]
        args += ["-f", "s16le", str(out)]
        res = process.run(args, timeout=120)
        if res.returncode != 0 or not out.is_file():
            return []
        samples = np.frombuffer(out.read_bytes(), dtype="<i2")
        return compute_peaks(samples, PEAK_COUNT)
    except (OSError, ValueError) as exc:
        LOG.info("無法產生波形：%s", exc)
        return []
    finally:
        naming.remove_work_dir(work)


def png_surface(data: bytes):  # noqa: ANN201
    import cairo  # noqa: PLC0415

    return cairo.ImageSurface.create_from_png(io.BytesIO(data))
