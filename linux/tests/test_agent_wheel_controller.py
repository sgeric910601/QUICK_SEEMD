"""拖曳偵測與輪盤之間的協調（假的輪盤視窗，不需要畫面）。"""

from __future__ import annotations

from pathlib import Path

from quickseemd.agent.wheel_controller import WheelController
from quickseemd.core.wheel import WheelItem


class FakeUI:
    def __init__(self):
        self.calls: list[tuple] = []

    def present(self, state, x, y):
        self.calls.append(("present", state, x, y))

    def hide(self):
        self.calls.append(("hide",))

    def drag_released(self):
        self.calls.append(("released",))


def _setup(keys=None, files=(Path("/tmp/a.txt"),)):
    ui = FakeUI()
    catalog_calls = []

    def catalog(mode, paths):
        catalog_calls.append((mode, tuple(paths)))
        return [WheelItem("more", mode, "更多", "ellipsis.circle", (0.5, 0.5, 0.5))]

    fetches = []

    def fetch():
        fetches.append(1)
        return list(files)

    keys = keys or {"wheel.key.convert": "shift", "wheel.key.tools": "alt"}
    ctl = WheelController(ui, lambda: keys, catalog, dispatch=lambda fn: fn())
    return ctl, ui, catalog_calls, fetch, fetches


def test_tap_presents_wheel_for_mode():
    ctl, ui, catalog_calls, fetch, fetches = _setup()
    ctl.drag_began()
    ctl.key_tapped("shift", 100, 200, fetch)
    (call,) = ui.calls
    assert call[0] == "present"
    state = call[1]
    assert state.mode == "convert"
    assert state.files == (Path("/tmp/a.txt"),)
    assert state.center_title == "轉換"
    assert state.hint == "⌥ 工具  ⌃ 動作  esc 取消"
    assert call[2:] == (100, 200)
    assert catalog_calls == [("convert", (Path("/tmp/a.txt"),))]


def test_files_fetched_once_and_catalog_cached_per_drag():
    ctl, ui, catalog_calls, fetch, fetches = _setup()
    ctl.drag_began()
    ctl.key_tapped("shift", 1, 1, fetch)
    ctl.key_tapped("alt", 1, 1, fetch)
    ctl.key_tapped("shift", 1, 1, fetch)
    assert len(fetches) == 1
    assert [m for m, _ in catalog_calls] == ["convert", "tools"]
    assert [c[1].mode for c in ui.calls] == ["convert", "tools", "convert"]
    ctl.drag_began()
    ctl.key_tapped("shift", 1, 1, fetch)
    assert len(fetches) == 2


def test_unmapped_key_does_nothing():
    ctl, ui, _c, fetch, fetches = _setup()
    ctl.drag_began()
    ctl.key_tapped("super", 1, 1, fetch)
    assert ui.calls == []
    assert fetches == []


def test_escape_and_release_forward_to_ui():
    ctl, ui, _c, fetch, _f = _setup()
    ctl.drag_began()
    ctl.escape_pressed()
    ctl.drag_ended()
    assert ui.calls == [("hide",), ("released",)]


def test_failed_fetch_gives_empty_files():
    ui = FakeUI()
    ctl = WheelController(ui, lambda: {}, lambda m, p: [], dispatch=lambda fn: fn())
    ctl.drag_began()
    ctl.key_tapped("control", 5, 6, lambda: None)
    state = ui.calls[0][1]
    assert state.mode == "actions"
    assert state.files == ()
    assert state.center_title == "動作"


def test_many_files_center_title():
    files = tuple(Path(f"/tmp/{i}") for i in range(3))
    ctl, ui, _c, fetch, _f = _setup(files=files)
    ctl.drag_began()
    ctl.key_tapped("alt", 1, 1, fetch)
    assert ui.calls[0][1].center_title == "3 個檔案・工具"


def test_catalog_error_is_logged_not_raised():
    ui = FakeUI()

    def broken(_m, _p):
        raise RuntimeError("壞了")

    ctl = WheelController(ui, lambda: {}, broken, dispatch=lambda fn: fn())
    ctl.drag_began()
    ctl.key_tapped("shift", 1, 1, lambda: [])
    assert ui.calls[0][1].items == ()
