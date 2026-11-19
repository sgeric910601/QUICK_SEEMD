"""拖曳中「點一下」修飾鍵的判斷（不需要畫面）。"""

from __future__ import annotations

import pytest

from quickseemd.agent import taps
from quickseemd.agent.taps import TapDetector


def test_quick_tap_is_detected():
    d = TapDetector()
    d.reset(set(), 0.0)
    assert d.sample({"shift"}, 0.04) == []
    assert d.sample(set(), 0.16) == ["shift"]


def test_long_press_is_not_a_tap():
    d = TapDetector()
    d.reset(set(), 0.0)
    d.sample({"shift"}, 0.04)
    assert d.sample({"shift"}, 0.20) == []
    assert d.sample(set(), 0.40) == []


def test_exactly_at_limit_counts():
    d = TapDetector(tap_max=0.30)
    d.reset(set(), 0.0)
    d.press("alt", 1.0)
    assert d.release("alt", 1.30) == "alt"


def test_other_modifier_during_press_cancels_tap():
    d = TapDetector()
    d.reset(set(), 0.0)
    d.sample({"shift"}, 0.04)
    d.sample({"shift", "control"}, 0.08)
    assert d.sample({"control"}, 0.12) == []
    assert d.sample(set(), 0.16) == []


def test_press_while_other_held_is_not_clean():
    d = TapDetector()
    d.reset(set(), 0.0)
    d.press("control", 0.0)
    d.press("shift", 1.0)
    d.release("control", 1.5)
    assert d.release("shift", 1.6) is None


def test_key_held_when_tracking_starts_is_ignored():
    d = TapDetector()
    d.reset({"shift"}, 0.0)
    assert d.sample(set(), 0.05) == []
    # 之後重新點一下才算
    d.sample({"shift"}, 0.10)
    assert d.sample(set(), 0.20) == ["shift"]


def test_raw_events_then_stale_poll_sample_do_not_double_count():
    d = TapDetector()
    d.reset(set(), 0.0)
    d.press("shift", 0.10)
    assert d.release("shift", 0.15) == "shift"
    # 輪詢在放開之前就讀到「按著」，但比較晚才處理：要忽略
    assert d.sample({"shift"}, 0.12) == []
    assert d.sample(set(), 0.20) == []


def test_poll_then_raw_release_is_single_tap():
    d = TapDetector()
    d.reset(set(), 0.0)
    assert d.sample({"alt"}, 0.04) == []
    assert d.release("alt", 0.10) == "alt"
    assert d.release("alt", 0.11) is None
    assert d.sample(set(), 0.12) == []


def test_presses_processed_before_releases_in_one_sample():
    d = TapDetector()
    d.reset(set(), 0.0)
    d.sample({"shift"}, 0.04)
    # 同一次輪詢裡 Shift 放開、Ctrl 按下：保守地不算點一下
    assert d.sample({"control"}, 0.08) == []


def test_unknown_key_is_ignored():
    d = TapDetector()
    d.reset(set(), 0.0)
    d.press("hyper", 0.1)
    assert d.release("hyper", 0.2) is None


def test_two_taps_in_a_row():
    d = TapDetector()
    d.reset(set(), 0.0)
    d.sample({"shift"}, 0.04)
    assert d.sample(set(), 0.08) == ["shift"]
    d.sample({"control"}, 0.50)
    assert d.sample(set(), 0.58) == ["control"]


@pytest.mark.parametrize(
    ("mask", "expected"),
    [
        (0, set()),
        (1, {"shift"}),
        (4, {"control"}),
        (8, {"alt"}),
        (64, {"super"}),
        (1 | 4 | 256, {"shift", "control"}),  # 256＝Button1，不是修飾鍵
    ],
)
def test_mask_to_keys(mask, expected):
    assert taps.mask_to_keys(mask) == frozenset(expected)


@pytest.mark.parametrize(
    ("keysym_name", "expected"),
    [
        ("Shift_L", "shift"),
        ("Shift_R", "shift"),
        ("Control_R", "control"),
        ("Alt_L", "alt"),
        ("Meta_L", "alt"),
        ("Super_L", "super"),
        ("Escape", "escape"),
        ("a", None),
    ],
)
def test_key_name_for_keysym(keysym_name, expected):
    assert taps.key_for_keysym_name(keysym_name) == expected


def test_mode_for_key_uses_settings_order():
    keys = {"convert": "shift", "tools": "alt", "actions": "control"}
    assert taps.mode_for_key("shift", keys) == "convert"
    assert taps.mode_for_key("alt", keys) == "tools"
    assert taps.mode_for_key("control", keys) == "actions"
    assert taps.mode_for_key("super", keys) is None


def test_mode_for_key_shared_key_picks_first():
    keys = {"convert": "shift", "tools": "shift", "actions": "control"}
    assert taps.mode_for_key("shift", keys) == "convert"


def test_wheel_keys_from_settings():
    s = {"wheel.key.convert": "super", "wheel.key.tools": "alt", "wheel.key.actions": "bogus"}
    assert taps.wheel_keys(s) == {"convert": "super", "tools": "alt", "actions": "control"}
