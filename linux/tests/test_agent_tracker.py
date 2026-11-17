"""拖曳追蹤迴圈：用假的游標樣本與原始按鍵事件測試（不需要 X）。"""

from __future__ import annotations

import queue
import struct

from quickseemd.agent import x11_drag as xd
from quickseemd.agent.x11_drag import PointerSample, RawKey, Tracker

B1 = xd.BUTTON1_MASK
SHIFT, CTRL, ALT = 1, 4, 8


class Listener:
    def __init__(self):
        self.events: list[tuple] = []

    def drag_began(self):
        self.events.append(("began",))

    def key_tapped(self, key, x, y, fetch):
        self.events.append(("tap", key, x, y, fetch()))

    def escape_pressed(self):
        self.events.append(("escape",))

    def drag_ended(self):
        self.events.append(("ended",))


def _run(samples, raw=(), escape=(), active=None):
    """samples：(遮罩, x, y, 時間)；raw：在第幾次輪詢之前送進來的 RawKey。"""
    listener = Listener()
    inbox: queue.Queue = queue.Queue()
    seq = iter(samples)
    count = {"n": 0}
    raw_by_step = {}
    for step, ev in raw:
        raw_by_step.setdefault(step, []).append(ev)
    esc = list(escape)

    def query():
        count["n"] += 1
        for ev in raw_by_step.get(count["n"], []):
            inbox.put(ev)
        mask, x, y, t = next(seq)
        return PointerSample(mask, x, y, t)

    def escape_down():
        return esc.pop(0) if esc else False

    tracker = Tracker(
        listener, query, lambda: ["/tmp/a.txt"], escape_down, inbox, active=active or (lambda: True),
        poll=0.001,
    )  # fmt: skip
    result = tracker.run()
    return result, listener.events


def test_not_a_drag_when_button_is_up():
    result, events = _run([(0, 0, 0, 0.0)])
    assert result is False
    assert events == []


def test_tap_during_drag_opens_with_position():
    samples = [
        (B1, 10, 10, 0.00),
        (B1 | SHIFT, 20, 20, 0.04),
        (B1, 30, 30, 0.12),
        (0, 30, 30, 0.16),
    ]
    result, events = _run(samples)
    assert result is True
    assert events == [("began",), ("tap", "shift", 30, 30, ["/tmp/a.txt"]), ("ended",)]


def test_long_press_does_nothing():
    samples = [(B1, 0, 0, 0.0), (B1 | ALT, 0, 0, 0.04), (B1 | ALT, 0, 0, 0.5), (B1, 0, 0, 0.6)]
    samples.append((0, 0, 0, 0.7))
    _r, events = _run(samples)
    assert events == [("began",), ("ended",)]


def test_modifier_held_at_start_is_not_a_tap():
    samples = [(B1 | CTRL, 0, 0, 0.0), (B1, 0, 0, 0.05), (0, 0, 0, 0.1)]
    _r, events = _run(samples)
    assert events == [("began",), ("ended",)]


def test_raw_events_catch_tap_between_polls():
    samples = [(B1, 5, 6, 0.0), (B1, 5, 6, 0.04), (B1, 7, 8, 0.08), (0, 7, 8, 0.2)]
    raw = [(2, RawKey("control", True, 0.041)), (2, RawKey("control", False, 0.06))]
    _r, events = _run(samples, raw)
    assert ("tap", "control", 5, 6, ["/tmp/a.txt"]) in events
    assert sum(1 for e in events if e[0] == "tap") == 1


def test_escape_from_keymap_is_edge_triggered():
    samples = [(B1, 0, 0, 0.0), (B1, 0, 0, 0.04), (B1, 0, 0, 0.08), (B1, 0, 0, 0.12)]
    samples.append((0, 0, 0, 0.16))
    _r, events = _run(samples, escape=[True, True, False])
    assert events.count(("escape",)) == 1


def test_raw_escape_press():
    samples = [(B1, 0, 0, 0.0), (B1, 0, 0, 0.04), (0, 0, 0, 0.08)]
    raw = [(2, RawKey("escape", True, 0.03)), (2, RawKey("escape", False, 0.035))]
    _r, events = _run(samples, raw)
    assert events.count(("escape",)) == 1


def test_disabling_stops_tracking():
    flag = {"on": True}
    samples = [(B1, 0, 0, 0.0), (B1, 0, 0, 0.04), (B1, 0, 0, 0.08)]

    def active():
        on = flag["on"]
        flag["on"] = False
        return on

    _r, events = _run(samples, active=active)
    assert events[-1] == ("ended",)


def test_stale_inbox_is_dropped_at_start():
    listener = Listener()
    inbox: queue.Queue = queue.Queue()
    inbox.put(RawKey("shift", True, -5.0))
    inbox.put(RawKey("shift", False, -4.9))
    samples = iter([PointerSample(B1, 0, 0, 0.0), PointerSample(0, 0, 0, 0.04)])
    Tracker(listener, lambda: next(samples), lambda: None, lambda: False, inbox, poll=0.001).run()
    assert listener.events == [("began",), ("ended",)]


def test_parse_raw_key():
    data = struct.pack("=HII", 3, 123456, 50) + b"\0" * 20
    assert xd.parse_raw_key(data) == 50
    assert xd.parse_raw_key(b"\0") is None
