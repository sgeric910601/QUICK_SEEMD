"""預覽視窗的文字格式。"""

from __future__ import annotations

import time

import pytest

from quickseemd.previewer.formatting import format_duration, format_size, format_time


@pytest.mark.parametrize(
    ("seconds", "text"),
    [(0, "0:00"), (5.9, "0:05"), (65, "1:05"), (3600, "1:00:00"), (3725, "1:02:05"), (-3, "0:00")],
)
def test_format_duration(seconds: float, text: str):
    assert format_duration(seconds) == text


def test_format_duration_nan():
    assert format_duration(float("nan")) == "0:00"


def test_format_size_small_and_large():
    assert format_size(0) == "0 位元組"
    assert format_size(1023) == "1023 位元組"
    assert "MB" in format_size(2_100_000)


def test_format_time():
    epoch = time.mktime((2026, 11, 17, 9, 5, 0, 0, 0, -1))
    assert format_time(epoch) == "2026/11/17 09:05"
