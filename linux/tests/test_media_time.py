"""時間格式（對應原版 MTTime）與擷取畫面的時間點計算。"""

from __future__ import annotations

import math

import pytest

from quickseemd.core.tools.media import timefmt
from quickseemd.core.tools.media.frames import frame_times


@pytest.mark.parametrize(
    ("seconds", "decimals", "expected"),
    [
        (65.25, 1, "1:05.3"),
        (3725, 1, "1:02:05.0"),
        (0, 1, "0:00.0"),
        (-3, 1, "0:00.0"),
        (math.nan, 1, "0:00.0"),
        (math.inf, 1, "0:00.0"),
        (65.5, 2, "1:05.50"),
        (15, 0, "0:15"),
        (59.6, 0, "1:00"),
        (3725, 0, "1:02:05"),
        (0.05, 1, "0:00.1"),
    ],
)
def test_format(seconds, decimals, expected):
    assert timefmt.format_time(seconds, decimals) == expected


def test_short_is_zero_decimals():
    assert timefmt.short(30) == "0:30"
    assert timefmt.short(0) == "0:00"


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("75", 75.0),
        ("75.5", 75.5),
        ("1:15", 75.0),
        ("1:15.5", 75.5),
        ("0:01:15", 75.0),
        (" 5 ", 5.0),
        ("1：05。5", 65.5),
        ("65s", 65.0),
        ("65秒", 65.0),
        (".5", 0.5),
        ("1:60", None),
        ("1.5:00", None),
        ("", None),
        ("   ", None),
        ("abc", None),
        ("1:2:3:4", None),
        ("-5", None),
        ("1::2", None),
        ("nan", None),
        ("inf", None),
        ("１", None),
        ("1_0", None),
    ],
)
def test_parse(text, expected):
    assert timefmt.parse_time(text) == expected


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [
        (65.5, "01m05.5s"),
        (5, "00m05.0s"),
        (3725.25, "1h02m05.2s"),
        (0.06, "00m00.0s"),
        (-1, "00m00.0s"),
        (59.99, "00m59.9s"),
    ],
)
def test_file_label(seconds, expected):
    assert timefmt.file_label(seconds) == expected


def test_parse_times_separators():
    assert timefmt.parse_times("0:05, 1:30 2:00") == [5.0, 90.0, 120.0]
    assert timefmt.parse_times("0:05，1:30、2;3；4\n5") == [5.0, 90.0, 2.0, 3.0, 4.0, 5.0]


def test_parse_times_invalid():
    assert timefmt.parse_times("") is None
    assert timefmt.parse_times(" , ") is None
    assert timefmt.parse_times("0:05, x") is None


def test_frame_times_list_clamps_and_dedupes():
    assert frame_times("times", "5, 90", 5, 10, 10.0) == [5.0, 9.95]
    assert frame_times("times", "1, 1.001, 1.02", 5, 10, 10.0) == [1.0, 1.02]
    assert frame_times("times", "oops", 5, 10, 10.0) is None


def test_frame_times_interval():
    assert frame_times("interval", "", 5, 10, 12.0) == [0.0, 5.0, 10.0]
    assert frame_times("interval", "", 1, 10, 2.0) == [0.0, 1.0]
    assert frame_times("interval", "", 0, 10, 2.0) is None
    assert len(frame_times("interval", "", 1, 10, 5000.0)) == 600


def test_frame_times_count():
    assert frame_times("count", "", 5, 4, 10.0) == [1.25, 3.75, 6.25, 8.75]
    assert len(frame_times("count", "", 5, 9999, 10.0)) == 600
    assert frame_times("count", "", 5, 0, 10.0) == [5.0]
