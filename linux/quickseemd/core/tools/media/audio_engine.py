"""音訊輸出（對應原版 MTAudioEngine）：解碼、重新取樣、最多 2 聲道、剪輯片段、淡入淡出，
再編碼成 M4A（AAC／ALAC）、WAV、AIFF、FLAC、CAF、MP3、OGG。

原版的 MP3／OGG 需要使用者另外安裝 ffmpeg；Linux 版本來就用 ffmpeg，所以一律提供。
原版剪輯壓縮格式時先解碼成 PCM 再切；ffmpeg 的 atrim 本來就以取樣為單位，不需要中間檔。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ...model import ToolContext
from . import engine
from .bitrate import aac_bitrate, aac_matching, aac_sample_rate
from .probe import MediaInfo


@dataclass(frozen=True)
class AudioFormat:
    title: str
    subtitle: str
    ext: str
    uses_bitrate: bool


FORMATS: dict[str, AudioFormat] = {
    "m4a": AudioFormat("M4A (AAC)", "檔案小、幾乎所有裝置都能播放", "m4a", True),
    "alac": AudioFormat("M4A (ALAC 無損)", "Apple 無損格式，音質與原始相同", "m4a", False),
    "wav": AudioFormat("WAV", "未壓縮，適合剪輯軟體", "wav", False),
    "aiff": AudioFormat("AIFF", "未壓縮，Mac 常用", "aiff", False),
    "flac": AudioFormat("FLAC", "開放的無損壓縮格式", "flac", False),
    "caf": AudioFormat("CAF", "Core Audio 格式，未壓縮", "caf", False),
    "mp3": AudioFormat("MP3", "最通用的有損格式（使用 ffmpeg）", "mp3", True),
    "ogg": AudioFormat("OGG", "開放的有損格式（使用 ffmpeg）", "ogg", True),
}
BITRATES_KBPS: tuple[int, ...] = (96, 128, 192, 256, 320)
# libvorbis 實測可接受的最高平均位元率（kbps）：(取樣率下限, 上限)，依聲道數
_VORBIS_CAPS: dict[int, tuple[tuple[int, int], ...]] = {
    1: ((44_100, 240), (32_000, 160), (16_000, 80), (11_025, 48), (0, 32)),
    2: ((32_000, 320), (16_000, 160), (11_025, 96), (0, 80)),
}


def bitrate_hint(kbps: int) -> str:
    if kbps < 128:
        return "適合語音、Podcast"
    if kbps < 192:
        return "一般音樂"
    if kbps < 256:
        return "高音質（建議）"
    if kbps < 320:
        return "非常高音質"
    return "最高音質"


@dataclass(frozen=True)
class AudioOutput:
    format: str = "m4a"
    bitrate: int = 192_000  # AAC／MP3／OGG 位元率（bps）
    sample_rate: int | None = None  # None＝與來源相同
    channels: int | None = None  # None＝與來源相同（最多 2 聲道）


def matching_format(info: MediaInfo) -> str:
    """與來源相同的格式（剪輯音訊用）。"""
    codec = info.audio_codec
    if codec in ("alac", "flac", "mp3"):
        return codec
    if codec.startswith("pcm_"):
        ext = Path(info.path).suffix.lower().lstrip(".")
        if ext in ("aif", "aiff", "aifc"):
            return "aiff"
        return "caf" if ext == "caf" else "wav"
    return "m4a"


def matching_bitrate(info: MediaInfo, fmt: str) -> int:
    if fmt == "mp3":
        return max(128_000, int(info.audio_bitrate // 32_000) * 32_000)
    return aac_matching(info, fallback=256_000)


def vorbis_kbps(bitrate: int, rate: int, channels: int) -> int:
    """libvorbis 的平均位元率有上限（依取樣率與聲道），超過會直接拒絕編碼，所以先限制。"""
    caps = _VORBIS_CAPS[2 if channels >= 2 else 1]
    cap = next(c for threshold, c in caps if rate >= threshold)
    return min(max(64, bitrate // 1000), cap)


def codec_args(info: MediaInfo, out: AudioOutput) -> list[str]:
    """編碼器、取樣率、聲道與容器參數。"""
    channels = max(1, min(2, out.channels or info.channels))
    rate = out.sample_rate or info.sample_rate
    if out.format in ("m4a", "mp3", "ogg"):
        # AAC 與 MP3 最高 48 kHz；libvorbis 的位元率模式也不接受 48 kHz 以上
        rate = aac_sample_rate(rate)
    deep = info.bit_depth > 16
    kbps = f"{max(64, out.bitrate // 1000)}k"
    codec: list[str] = {
        "m4a": ["-c:a", "aac", "-b:a", str(aac_bitrate(out.bitrate, rate, channels))],
        "alac": ["-c:a", "alac", "-sample_fmt", "s32p" if deep else "s16p"],
        "wav": ["-c:a", "pcm_s24le" if deep else "pcm_s16le"],
        "aiff": ["-c:a", "pcm_s24be" if deep else "pcm_s16be"],
        "caf": ["-c:a", "pcm_s24le" if deep else "pcm_s16le"],
        "flac": ["-c:a", "flac", "-sample_fmt", "s32" if deep else "s16"],
        "mp3": ["-c:a", "libmp3lame", "-b:a", kbps],
        "ogg": ["-c:a", "libvorbis", "-b:a", f"{vorbis_kbps(out.bitrate, rate, channels)}k"],
    }[out.format]
    muxer = {"m4a": "ipod", "alac": "ipod"}.get(out.format, FORMATS[out.format].ext)
    container = ["-f", muxer] + (["-movflags", "+faststart"] if muxer == "ipod" else [])
    return codec + ["-ar", str(rate), "-ac", str(channels)] + container


def _fades(total: float, fade_in: float, fade_out: float) -> list[str]:
    fi, fo = min(fade_in, total / 2), min(fade_out, total / 2)
    out: list[str] = []
    if fi > 0:
        out.append(f"afade=t=in:st=0:d={fi:.6f}")
    if fo > 0:
        out.append(f"afade=t=out:st={total - fo:.6f}:d={fo:.6f}")
    return out


def audio_graph(
    info: MediaInfo, segments: list[tuple[float, float]], fade_in: float, fade_out: float
) -> str:
    """切出各段後接起來，再加淡入淡出；輸出標籤 [out]。"""
    src = f"[0:{info.audio_index}]"
    total = sum(b - a for a, b in segments)
    fades = _fades(total, fade_in, fade_out)
    n = len(segments)
    chains: list[str] = []
    if n == 1:
        a, b = segments[0]
        trim = f"atrim=start={a:.6f}:end={b:.6f},asetpts=PTS-STARTPTS"
        return f"{src}{','.join([trim, *fades])}[out]"
    chains.append(f"{src}asplit={n}" + "".join(f"[s{i}]" for i in range(n)))
    for i, (a, b) in enumerate(segments):
        chains.append(f"[s{i}]atrim=start={a:.6f}:end={b:.6f},asetpts=PTS-STARTPTS[p{i}]")
    joined = "".join(f"[p{i}]" for i in range(n)) + f"concat=n={n}:v=0:a=1"
    chains.append(joined + ("," + ",".join(fades) if fades else "") + "[out]")
    return ";".join(chains)


def render(
    file: Path,
    info: MediaInfo,
    output: AudioOutput,
    out: Path,
    ctx: ToolContext,
    *,
    message: str,
    segments: list[tuple[float, float]] | None = None,
    fades: tuple[float, float] = (0.0, 0.0),
) -> float:
    """輸出音訊到 out；回傳輸出長度（秒）。"""
    info.require_audio()
    args = ["-i", str(Path(file).absolute())]
    total = info.duration
    if segments is not None or any(f > 0 for f in fades):
        segs = segments if segments is not None else [(0.0, info.duration)]
        total = sum(b - a for a, b in segs)
        args += ["-filter_complex", audio_graph(info, segs, *fades), "-map", "[out]"]
    else:
        args += ["-map", f"0:{info.audio_index}"]
    args += ["-vn", "-sn", "-dn", "-map_metadata", "0", "-map_chapters", "-1"]
    args += codec_args(info, output)
    engine.run_ffmpeg(ctx, args, out, duration=total, message=message)
    return total
