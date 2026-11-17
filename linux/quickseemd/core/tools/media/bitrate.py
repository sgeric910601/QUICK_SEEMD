"""位元率公式（對應原版 MTBitrate）。

原版的 AAC 可用位元率來自 CoreAudio 編碼器；這裡用固定表格近似：
立體聲 64–320 kbps、單聲道 32–256 kbps，並受 AAC 每個取樣最多 6 位元的上限限制。
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .probe import MediaInfo

_AAC_STEREO = (64, 80, 96, 112, 128, 160, 192, 224, 256, 288, 320)
_AAC_MONO = (32, 40, 48, 56, 64, 72, 80, 96, 112, 128, 144, 160, 192, 224, 256)
_AAC_SAMPLE_RATES = (8_000, 11_025, 12_000, 16_000, 22_050, 24_000, 32_000, 44_100, 48_000)
_AAC_MATCH_STEPS = (96_000, 128_000, 160_000, 192_000, 256_000, 320_000)


def effective_fps(fps: float) -> float:
    """高於 30 fps 的部分只算一半（高影格率的畫面差異較小）。"""
    f = fps if fps > 1 else 30.0
    return min(f, 30.0) + max(0.0, f - 30.0) * 0.5


def preserving(
    info: MediaInfo, codec: str, out_w: int, out_h: int, fps: float | None = None
) -> int:
    """「盡量保持畫質」的位元率：不超過來源（換算解析度與編碼效率後）太多，也不低於基本畫質。"""
    eff = effective_fps(fps if fps is not None else info.fps)
    pixels = float(out_w * out_h) * eff
    hq = pixels * (0.065 if codec == "hevc" else 0.10)
    rate = hq
    src_w, src_h = info.display_size
    if info.video_bitrate > 0 and src_w > 0 and src_h > 0:
        equivalent = info.video_bitrate * min(1.0, (out_w * out_h) / (src_w * src_h))
        if info.video_codec == "hevc" and codec == "h264":
            equivalent *= 1.4
        elif info.video_codec == "h264" and codec == "hevc":
            equivalent *= 0.75
        rate = min(hq, equivalent * 1.1)
    return int(max(rate, pixels * 0.03, 300_000))


def _aac_allowed(sample_rate: float, channels: int) -> list[int]:
    ch = max(1, min(2, channels))
    table = _AAC_STEREO if ch == 2 else _AAC_MONO
    ceiling = 6 * sample_rate * ch
    return [k * 1000 for k in table if k * 1000 <= ceiling]


def aac_bitrate(requested: int, sample_rate: float, channels: int) -> int:
    """AAC 可用的位元率：取不超過要求值的最大可用值。"""
    allowed = _aac_allowed(sample_rate, channels)
    if not allowed:
        return requested
    lower = [b for b in allowed if b <= requested]
    return max(lower) if lower else min(allowed)


def aac_sample_rate(rate: float) -> int:
    """AAC 支援的取樣率（最高 48 kHz）。"""
    if rate >= 48_000:
        return 48_000
    lower = [r for r in _AAC_SAMPLE_RATES if r <= rate + 1]
    return lower[-1] if lower else 44_100


def aac_matching(info: MediaInfo, fallback: int = 192_000) -> int:
    """常見的 AAC 位元率（依來源推估）；來源不是 AAC 或不知道位元率時用 fallback。"""
    if info.audio_codec != "aac" or info.audio_bitrate <= 0:
        return fallback
    for step in _AAC_MATCH_STEPS:
        if step >= info.audio_bitrate * 0.95:
            return step
    return 320_000
