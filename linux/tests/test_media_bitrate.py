"""位元率公式、幾何計算與壓縮計畫（對應原版 MTBitrate、MTGeometry、compressPlan）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from quickseemd.core.tools.media import bitrate, geometry
from quickseemd.core.tools.media.compress import compress_plan
from quickseemd.core.tools.media.merge import merge_render_size
from quickseemd.core.tools.media.probe import MediaInfo
from quickseemd.core.tools.media.resize import resize_tag, resize_target
from quickseemd.core.tools.media.speed import speed_tag


def info(**kw) -> MediaInfo:
    base = dict(
        path=Path("/tmp/x.mp4"),
        duration=60.0,
        file_size=61_000_000,
        has_video=True,
        has_audio=True,
        width=1920,
        height=1080,
        fps=30.0,
        video_bitrate=8_000_000.0,
        video_codec="h264",
        audio_codec="aac",
        audio_bitrate=128_000.0,
        sample_rate=44_100,
        channels=2,
    )
    base.update(kw)
    return MediaInfo(**base)


def test_effective_fps():
    assert bitrate.effective_fps(60) == 45
    assert bitrate.effective_fps(0) == 30
    assert bitrate.effective_fps(24) == 24


def test_preserving_caps_at_high_quality():
    assert bitrate.preserving(info(), "h264", 1920, 1080) == 6_220_800


def test_preserving_follows_source_bitrate():
    assert bitrate.preserving(info(video_bitrate=2e6), "h264", 1920, 1080) == 2_200_000
    hevc_src = info(video_bitrate=2e6, video_codec="hevc")
    assert bitrate.preserving(hevc_src, "h264", 1920, 1080) == 3_080_000
    # 1.65 Mbps 低於基本畫質（像素 × 0.03）時用基本畫質
    assert bitrate.preserving(info(video_bitrate=2e6), "hevc", 1920, 1080) == 1_866_240
    assert bitrate.preserving(info(video_bitrate=4e6), "hevc", 1920, 1080) == 3_300_000


def test_preserving_floor_and_unknown_source():
    assert bitrate.preserving(info(video_bitrate=0), "hevc", 1920, 1080) == 4_043_520
    assert bitrate.preserving(info(video_bitrate=1e4), "h264", 320, 240, fps=25) == 300_000


def test_aac_bitrate_snapping():
    assert bitrate.aac_bitrate(192_000, 44_100, 2) == 192_000
    assert bitrate.aac_bitrate(100_000, 44_100, 2) == 96_000
    assert bitrate.aac_bitrate(500_000, 44_100, 2) == 320_000
    assert bitrate.aac_bitrate(20_000, 44_100, 2) == 64_000
    assert bitrate.aac_bitrate(320_000, 44_100, 1) == 256_000
    assert bitrate.aac_bitrate(192_000, 8_000, 1) == 48_000


def test_aac_sample_rate():
    assert bitrate.aac_sample_rate(96_000) == 48_000
    assert bitrate.aac_sample_rate(44_100) == 44_100
    assert bitrate.aac_sample_rate(22_050) == 22_050
    assert bitrate.aac_sample_rate(37_800) == 32_000
    assert bitrate.aac_sample_rate(7_000) == 44_100


def test_aac_matching():
    assert bitrate.aac_matching(info(audio_bitrate=125_000)) == 128_000
    assert bitrate.aac_matching(info(audio_bitrate=400_000)) == 320_000
    assert bitrate.aac_matching(info(audio_codec="mp3")) == 192_000
    assert bitrate.aac_matching(info(audio_bitrate=0), fallback=256_000) == 256_000


def test_even_and_capped():
    assert geometry.even(321.0) == 320
    assert geometry.even(1.0) == 2
    assert geometry.even(5.5) == 6
    assert geometry.capped(1920, 1080, 720) == (1280, 720)
    assert geometry.capped(1080, 1920, 720) == (720, 1280)
    assert geometry.capped(1280, 720, 720) is None
    assert geometry.capped(1281, 721, 720) is None
    assert geometry.capped(1920, 1080, 0) is None


def test_fit_inside():
    assert geometry.fit_inside(1920, 1080, 480, 1920) == (480, 270)
    assert geometry.fit_inside(320, 240, 480, 1920) == (320, 240)
    assert geometry.fit_inside(1080, 1920, 640, 640) == (360, 640)


def test_resize_target_and_tag():
    assert resize_target(1920, 1080, "original", 720) == (1280, 720)
    assert resize_target(1920, 1080, "wide", 720) == (1280, 720)
    assert resize_target(1920, 1080, "vertical", 720) == (720, 1280)
    assert resize_target(1920, 1080, "square", 720) == (720, 720)
    assert resize_target(1920, 1080, "portrait", 720) == (720, 900)
    assert resize_target(1921, 1081, "original", 0) == (1920, 1080)
    assert resize_target(1920, 1080, "wide", 2160) == (3840, 2160)
    assert resize_target(320, 240, "wide", 0) == (426, 240)
    assert resize_tag("original", (1280, 720)) == "1280x720"
    assert resize_tag("wide", (1280, 720)) == "16x9-720p"
    assert resize_tag("vertical", (720, 1280)) == "9x16-720p"


def test_merge_render_size():
    assert merge_render_size(1920, 1080, "first") == (1920, 1080)
    assert merge_render_size(1920, 1080, "hd") == (1280, 720)
    assert merge_render_size(1080, 1920, "fhd") == (1080, 1920)
    assert merge_render_size(0, 0, "fhd") == (1920, 1080)


def test_speed_tag():
    assert speed_tag(2.0) == "2倍速"
    assert speed_tag(0.5) == "0.5倍速"
    assert speed_tag(0.25) == "0.25倍速"
    assert speed_tag(4) == "4倍速"


def test_plan_balanced():
    p = compress_plan(info(), "balanced", "h264", None)
    assert (p.width, p.height, p.scaled) == (1920, 1080, False)
    assert p.video_bitrate == 4_354_560
    assert p.audio_bitrate == 128_000
    assert p.estimated_bytes == pytest.approx(33_955_392, abs=2)
    assert p.problem is None


def test_plan_strong_scales_and_lowers_audio():
    p = compress_plan(info(), "strong", "h264", None)
    assert (p.width, p.height, p.scaled) == (1280, 720, True)
    assert p.video_bitrate == 1_244_160
    assert p.audio_bitrate == 96_000


def test_plan_quality_hevc():
    p = compress_plan(info(), "quality", "hevc", None)
    assert (p.width, p.height, p.scaled) == (1920, 1080, False)
    assert p.video_bitrate == 4_043_520
    assert p.audio_bitrate == 128_000


def test_plan_source_cap_and_problem():
    p = compress_plan(info(video_bitrate=100_000, file_size=800_000), "balanced", "h264", None)
    assert p.video_bitrate == 80_000
    assert p.problem == "這個影片已經很精簡，可能無法再變小。"


def test_plan_without_audio():
    p = compress_plan(info(has_audio=False), "balanced", "h264", None)
    assert p.audio_bitrate == 0


def test_plan_target_keeps_resolution():
    p = compress_plan(info(), "balanced", "h264", 25)
    assert (p.width, p.height, p.scaled) == (1920, 1080, False)
    assert p.audio_bitrate == 128_000
    assert p.video_bitrate == 3_105_333
    assert p.problem is None


def test_plan_target_steps_down_resolution():
    p = compress_plan(info(), "balanced", "h264", 5)
    assert p.audio_bitrate == 96_000
    assert (p.width, p.height, p.scaled) == (960, 540, True)


def test_plan_target_too_small():
    p = compress_plan(info(), "balanced", "h264", 0.3)
    assert p.estimated_bytes == 0
    assert p.problem == "目標太小：這段影片至少需要約 1.3 MB。"


def test_plan_target_already_small():
    p = compress_plan(info(file_size=10_000_000), "balanced", "h264", 25)
    assert p.estimated_bytes > 0
    assert p.problem == "原檔已經小於目標大小，不需要壓縮。"
