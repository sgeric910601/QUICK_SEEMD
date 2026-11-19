from __future__ import annotations

import pytest

from quickseemd.core import pagerange


@pytest.mark.parametrize(
    ("text", "count", "expected"),
    [
        ("", 5, [1, 2, 3, 4, 5]),
        ("", 0, [1]),
        ("1-3, 5", 5, [1, 2, 3, 5]),
        ("1-3，5", 5, [1, 2, 3, 5]),
        ("4-", 5, [4, 5]),
        ("-2", 5, [1, 2]),
        ("2–3", 5, [2, 3]),
        ("2~3", 5, [2, 3]),
        ("3, 1, 3", 5, [3, 1, 3]),
        ("6", 5, None),
        ("0", 5, None),
        ("3-2", 5, None),
        ("a", 5, None),
        ("1-3, x", 5, None),
    ],
)
def test_parse(text, count, expected):
    assert pagerange.parse(text, count) == expected


def test_enum_comma_only_when_allowed():
    assert pagerange.parse("1、2", 3) is None
    assert pagerange.parse("1、2", 3, allow_enum_comma=True) == [1, 2]


def test_groups():
    assert pagerange.parse_groups("1-3, 4-6, 7-", 8) == [[1, 2, 3], [4, 5, 6], [7, 8]]
    assert pagerange.parse_groups("", 8) is None
    assert pagerange.parse_groups("1-9", 8) is None


def test_caption():
    assert pagerange.caption("", None) == ("例如：1-3, 5, 8-", False)
    assert pagerange.caption("1-2", 4) == ("例如：1-3, 5, 8-　共 4 頁", False)
    assert pagerange.caption("9", 4) == ("範圍不正確（共 4 頁）", True)
