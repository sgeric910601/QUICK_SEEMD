"""預覽視窗的大小與位置計算（大小指內容區；整個視窗再加上標題列）。"""

from __future__ import annotations

from quickseemd.previewer.geometry import (
    HEADER_ALLOWANCE,
    MIN_HEIGHT,
    MIN_WIDTH,
    Rect,
    center_on,
    clamp_size,
    fit_media,
    max_content,
    max_size,
    outer_size,
)

SCREEN = Rect(0, 0, 1280, 800)


def test_max_size_is_85_percent_of_workarea():
    assert max_size(SCREEN) == (1088, 680)
    assert max_size(Rect(100, 50, 1000, 1000)) == (850, 850)


def test_max_content_leaves_room_for_header():
    assert max_content(SCREEN) == (1088, 680 - HEADER_ALLOWANCE)


def test_outer_size_adds_header():
    assert outer_size((400, 300)) == (400, 300 + HEADER_ALLOWANCE)


def test_clamp_keeps_small_sizes():
    assert clamp_size((920, 600), SCREEN) == (920, 600)


def test_clamp_limits_whole_window_to_85_percent():
    assert clamp_size((920, 700), SCREEN) == (920, 680 - HEADER_ALLOWANCE)
    assert clamp_size((3000, 3000), SCREEN) == (1088, 680 - HEADER_ALLOWANCE)
    w, h = outer_size(clamp_size((3000, 3000), SCREEN))
    assert (w, h) == max_size(SCREEN)


def test_clamp_applies_minimum():
    assert clamp_size((10, 10), SCREEN) == (MIN_WIDTH, MIN_HEIGHT)


def test_clamp_on_tiny_screen_never_exceeds_screen_limit():
    tiny = Rect(0, 0, 300, 200)
    w, h = outer_size(clamp_size((920, 700), tiny))
    assert w <= 255 and h <= 170


def test_fit_media_keeps_aspect_ratio_within_limits():
    w, h = fit_media((4000, 3000), SCREEN)
    assert outer_size((w, h))[1] <= 680
    assert abs(w / h - 4000 / 3000) < 0.02


def test_fit_media_never_upscales():
    assert fit_media((400, 300), SCREEN) == (400, 300)


def test_fit_media_small_image_gets_minimum_window():
    assert fit_media((16, 16), SCREEN) == (MIN_WIDTH, MIN_HEIGHT)


def test_fit_media_handles_zero_size():
    assert fit_media((0, 0), SCREEN) == (MIN_WIDTH, MIN_HEIGHT)


def test_center_on_parent():
    parent = Rect(100, 100, 800, 600)
    assert center_on((400, 300), parent, SCREEN) == (300, 250)


def test_center_on_workarea_without_parent():
    assert center_on((400, 300), None, SCREEN) == (440, 250)


def test_center_keeps_window_inside_workarea():
    parent = Rect(1000, 600, 400, 300)  # 部分在螢幕外
    x, y = center_on((600, 500), parent, SCREEN)
    assert 0 <= x <= 1280 - 600
    assert 0 <= y <= 800 - 500


def test_center_respects_workarea_offset():
    wa = Rect(1280, 30, 1920, 1050)  # 第二個螢幕、上方有面板
    x, y = center_on((1000, 800), None, wa)
    assert x == 1280 + (1920 - 1000) // 2
    assert y == 30 + (1050 - 800) // 2
