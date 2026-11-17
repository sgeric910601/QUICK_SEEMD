"""讀取影音資訊（對應原版 MTMediaProbe／MTMediaInfo；原版用 AVFoundation，這裡用 ffprobe）。"""

from __future__ import annotations

import json
import threading
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any

from ... import backends, errors, process
from ...result import format_size
from . import timefmt

PROBE_TIMEOUT = 60.0
# 超過這個大小就不在選項畫面掃描全部關鍵影格（要讀完整個檔案）
KEYFRAME_SCAN_LIMIT = 300 * 1000 * 1000
# 找「某個時間點以前的關鍵影格」時，先往前看這麼多秒
KEYFRAME_WINDOW = 20.0

_VIDEO_TITLES = {
    "h264": "H.264",
    "hevc": "HEVC",
    "prores": "ProRes",
    "mjpeg": "Motion JPEG",
    "mpeg4": "MPEG-4",
    "av1": "AV1",
    "vp9": "VP9",
}
_AUDIO_TITLES = {
    "aac": "AAC",
    "alac": "ALAC",
    "flac": "FLAC",
    "mp3": "MP3",
    "ac3": "Dolby",
    "eac3": "Dolby",
    "opus": "Opus",
}


# ---------------------------------------------------------------- 錯誤訊息


def unreadable_media(name: str) -> errors.QSError:
    return errors.QSError(f"無法讀取「{name}」的影音格式（格式不支援，或檔案已損毀）。")


def undecodable(name: str, codec: str) -> errors.QSError:
    return errors.QSError(f"系統無法解碼「{name}」使用的 {codec} 編碼，無法處理這個檔案。")


def too_short(name: str) -> errors.QSError:
    return errors.QSError(f"「{name}」選取的範圍太短（至少需要 0.1 秒）。")


# ---------------------------------------------------------------- 資訊


@dataclass(frozen=True)
class MediaInfo:
    """一個影音檔的資訊；width／height 是編碼尺寸，display_size 才是播放時看到的尺寸。"""

    path: Path
    duration: float = 0.0
    file_size: int = 0
    has_video: bool = False
    has_audio: bool = False
    width: int = 0
    height: int = 0
    rotation: int = 0  # 逆時針角度（ffprobe 的 display matrix）
    sar: float = 1.0
    fps: float = 30.0
    fps_rational: str = "30"
    video_bitrate: float = 0.0
    video_codec: str = ""  # ffmpeg 的 codec_name（h264、hevc、prores…）
    video_index: int | None = None
    video_duration: float = 0.0  # 影像軌本身的長度（0＝不知道）
    audio_codec: str = ""
    audio_bitrate: float = 0.0
    sample_rate: int = 44_100
    channels: int = 2
    bit_depth: int = 16
    audio_index: int | None = None

    @property
    def name(self) -> str:
        return Path(self.path).name

    @property
    def display_size(self) -> tuple[int, int]:
        w = round(self.width * self.sar) if self.sar > 0 else self.width
        h = self.height
        return (h, w) if abs(self.rotation) % 180 == 90 else (w, h)

    @property
    def resolution_text(self) -> str:
        w, h = self.display_size
        return f"{w}×{h}"

    @property
    def video_codec_title(self) -> str:
        if self.video_codec in _VIDEO_TITLES:
            return _VIDEO_TITLES[self.video_codec]
        return self.video_codec.upper() or "未知"

    @property
    def audio_codec_title(self) -> str:
        if self.audio_codec.startswith("pcm_"):
            return "PCM"
        return _AUDIO_TITLES.get(self.audio_codec, "音訊")

    @property
    def codec_kind(self) -> str:
        """和輸出編碼比對用：h264／hevc／prores，其他一律 other。"""
        return self.video_codec if self.video_codec in ("h264", "hevc", "prores") else "other"

    @property
    def summary(self) -> str:
        """例如「1920×1080 · 0:15 · H.264 · 24.1 MB」。"""
        parts: list[str] = []
        if self.has_video:
            parts.append(self.resolution_text)
        parts.append(timefmt.short(self.duration))
        if self.has_video:
            parts.append(self.video_codec_title)
        elif self.has_audio:
            parts.append(self.audio_codec_title)
            parts.append("%g kHz" % (round(self.sample_rate / 100) / 10))
        parts.append(format_size(self.file_size))
        return " · ".join(parts)

    @property
    def clip_duration(self) -> float:
        """影像軌的長度（不超過整個檔案的長度；不知道時用檔案長度）。"""
        if self.video_duration > 0:
            return (
                min(self.video_duration, self.duration)
                if self.duration > 0
                else self.video_duration
            )
        return self.duration

    def require_video(self) -> None:
        if not self.has_video:
            raise errors.no_video_track()
        if not self.video_codec:
            raise undecodable(self.name, self.video_codec_title)

    def require_audio(self) -> None:
        if not self.has_audio:
            raise errors.no_audio_track()

    def to_dict(self) -> dict[str, Any]:
        d = asdict(self)
        d["path"] = str(self.path)
        return d

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> MediaInfo:
        return cls(**{**raw, "path": Path(raw["path"])})


# ---------------------------------------------------------------- ffprobe


def _number(value: Any, default: float = 0.0) -> float:
    try:
        f = float(value)
    except (TypeError, ValueError):
        return default
    return f if f == f and f not in (float("inf"), float("-inf")) else default


def _rational(text: Any) -> float:
    num, _, den = str(text or "").partition("/")
    d = _number(den, 1.0) if den else 1.0
    return _number(num) / d if d else 0.0


def _tag_bitrate(stream: dict[str, Any]) -> float:
    tags = stream.get("tags") or {}
    for key in ("BPS", "BPS-eng"):
        if key in tags:
            return _number(tags[key])
    return 0.0


def _rotation(stream: dict[str, Any]) -> int:
    for item in stream.get("side_data_list") or []:
        if "rotation" in item:
            return round(_number(item["rotation"]))
    rotate = (stream.get("tags") or {}).get("rotate")
    return -round(_number(rotate)) if rotate is not None else 0


def _bit_depth(stream: dict[str, Any]) -> int:
    codec = str(stream.get("codec_name") or "")
    if codec.startswith("pcm_"):
        if codec.startswith(("pcm_f32", "pcm_f64")):
            return 24
        return 24 if _number(stream.get("bits_per_sample")) > 16 else 16
    if codec in ("flac", "alac"):
        return 24 if _number(stream.get("bits_per_raw_sample")) > 16 else 16
    return 16


def _pick_streams(data: dict[str, Any]) -> tuple[dict | None, dict | None]:
    video = audio = None
    for s in data.get("streams") or []:
        kind = s.get("codec_type")
        cover = (s.get("disposition") or {}).get("attached_pic") == 1
        if kind == "video" and video is None and not cover:
            video = s
        elif kind == "audio" and audio is None:
            audio = s
    return video, audio


def _video_fields(s: dict[str, Any], fmt_rate: float, audio_rate: float) -> dict[str, Any]:
    rate_text = str(s.get("avg_frame_rate") or "")
    fps = _rational(rate_text)
    if fps <= 1:
        rate_text = str(s.get("r_frame_rate") or "")
        fps = _rational(rate_text)
    if fps <= 1:
        fps, rate_text = 30.0, "30"
    bitrate = _number(s.get("bit_rate")) or _tag_bitrate(s)
    if bitrate <= 0 and fmt_rate > 0:
        bitrate = max(0.0, fmt_rate - audio_rate)
    sar = _rational(s.get("sample_aspect_ratio")) or 1.0
    return {
        "has_video": True,
        "width": int(_number(s.get("width"))),
        "height": int(_number(s.get("height"))),
        "rotation": _rotation(s),
        "sar": sar if 0.1 < sar < 10 else 1.0,
        "fps": fps,
        "fps_rational": rate_text,
        "video_bitrate": bitrate,
        "video_codec": str(s.get("codec_name") or ""),
        "video_index": int(s.get("index", 0)),
        "video_duration": _number(s.get("duration")),
    }


def _audio_fields(s: dict[str, Any], fmt_rate: float, has_video: bool) -> dict[str, Any]:
    bitrate = _number(s.get("bit_rate")) or _tag_bitrate(s)
    if bitrate <= 0 and not has_video:
        bitrate = fmt_rate
    rate = int(_number(s.get("sample_rate"))) or 44_100
    return {
        "has_audio": True,
        "audio_codec": str(s.get("codec_name") or ""),
        "audio_bitrate": bitrate,
        "sample_rate": rate,
        "channels": max(1, int(_number(s.get("channels"), 2))),
        "bit_depth": _bit_depth(s),
        "audio_index": int(s.get("index", 0)),
    }


def _run_ffprobe(path: Path, cancel: threading.Event | None) -> dict[str, Any]:
    args = [
        backends.require("ffprobe"),
        "-v",
        "error",
        "-print_format",
        "json",
        "-show_format",
        "-show_streams",
        str(path),
    ]
    res = process.run(args, cancel=cancel, timeout=PROBE_TIMEOUT)
    if res.returncode != 0:
        raise unreadable_media(path.name)
    try:
        data = json.loads(res.stdout or "{}")
    except json.JSONDecodeError as exc:
        raise unreadable_media(path.name) from exc
    return data if isinstance(data, dict) else {}


def probe(path: Path, cancel: threading.Event | None = None) -> MediaInfo:
    """讀取一個影音檔的資訊；沒有任何影像或聲音時丟出無法讀取的錯誤。"""
    p = Path(path).absolute()
    if not p.is_file():
        raise errors.file_not_found(p.name)
    data = _run_ffprobe(p, cancel)
    video, audio = _pick_streams(data)
    if video is None and audio is None:
        raise unreadable_media(p.name)
    fmt = data.get("format") or {}
    fmt_rate = _number(fmt.get("bit_rate"))
    duration = _number(fmt.get("duration"))
    if duration <= 0:
        duration = max(_number(s.get("duration")) for s in (video or {}, audio or {}))
    info = MediaInfo(path=p, duration=max(0.0, duration), file_size=p.stat().st_size)
    audio_rate = _number(audio.get("bit_rate")) if audio else 0.0
    if video is not None:
        info = replace(info, **_video_fields(video, fmt_rate, audio_rate))
    if audio is not None:
        info = replace(info, **_audio_fields(audio, fmt_rate, video is not None))
    return info


# ---------------------------------------------------------------- 關鍵影格


def keyframes(
    path: Path,
    stream: int | None = None,
    interval: tuple[float, float] | None = None,
    cancel: threading.Event | None = None,
) -> list[float]:
    """影像關鍵影格的時間（秒，遞增）；只讀封包、不解碼。"""
    args = [backends.require("ffprobe"), "-v", "error"]
    args += ["-select_streams", str(stream) if stream is not None else "v:0"]
    if interval is not None:
        args += ["-read_intervals", f"{max(0.0, interval[0]):.3f}%{interval[1]:.3f}"]
    args += ["-show_entries", "packet=pts_time,flags", "-of", "csv=p=0", str(Path(path).absolute())]
    res = process.run(args, cancel=cancel, timeout=PROBE_TIMEOUT * 5)
    if res.returncode != 0:
        return []
    times: set[float] = set()
    for line in res.stdout.splitlines():
        pts, _, flags = line.partition(",")
        if "K" in flags and pts not in ("", "N/A"):
            times.add(round(_number(pts), 6))
    return sorted(times)


def keyframe_at_or_before(
    info: MediaInfo, seconds: float, cancel: threading.Event | None = None
) -> float:
    """某個時間點以前（含）最近的關鍵影格；無法判斷時回傳原時間。"""
    if seconds <= 0:
        return seconds
    for lower in (seconds - KEYFRAME_WINDOW, 0.0):
        found = [
            t
            for t in keyframes(info.path, info.video_index, (lower, seconds + 0.05), cancel)
            if t <= seconds + 1e-6
        ]
        if found:
            return min(seconds, max(0.0, found[-1]))
        if lower <= 0:
            break
    return seconds


# ---------------------------------------------------------------- 選項畫面用


def source_summary(infos: list[MediaInfo], count: int) -> str:
    """選項面板上方的來源摘要（對應原版 MTSourceSummary）。"""
    if count == 0:
        return "尚未加入檔案"
    if not infos:
        return f"{count} 個檔案"
    if count == 1:
        return infos[0].summary
    total = sum(i.duration for i in infos)
    size = sum(i.file_size for i in infos)
    return f"{count} 個檔案 · 共 {timefmt.short(total)} · {format_size(size)}"


def probe_all(files: list[Path]) -> list[MediaInfo]:
    """逐一讀取；讀不到的檔案略過（選項畫面用，不丟錯誤）。"""
    out: list[MediaInfo] = []
    for f in files:
        try:
            out.append(probe(Path(f)))
        except errors.QSError:
            continue
    return out


def inspect_media(files: list[Path], with_keyframes: bool = False) -> dict[str, Any]:
    """ToolDefinition.inspect：檔案數、各檔資訊、摘要；第一個檔案的長度與尺寸放在最外層。"""
    infos = probe_all(files)
    out: dict[str, Any] = {
        "count": len(files),
        "items": [i.to_dict() for i in infos],
        "summary": source_summary(infos, len(files)),
    }
    if not infos:
        return out
    first = infos[0]
    w, h = first.display_size
    out.update(
        duration=first.duration,
        width=w,
        height=h,
        has_audio=first.has_audio,
        has_video=first.has_video,
    )
    small = first.file_size <= KEYFRAME_SCAN_LIMIT
    if with_keyframes and len(files) == 1 and first.has_video and small:
        out["keyframes"] = keyframes(first.path, first.video_index)
    return out


def infos_from(info: dict[str, Any]) -> list[MediaInfo]:
    """inspect 結果裡的各檔資訊。"""
    out: list[MediaInfo] = []
    for raw in info.get("items") or []:
        try:
            out.append(MediaInfo.from_dict(raw))
        except (TypeError, KeyError, ValueError):
            continue
    return out
