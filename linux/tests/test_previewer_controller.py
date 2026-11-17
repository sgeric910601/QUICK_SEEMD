"""預覽流程控制：D-Bus 請求 → 背景載入 → 視窗（用替身，不需要畫面）。"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from quickseemd.core.preview import Document
from quickseemd.previewer import content
from quickseemd.previewer.controller import LOADING_DELAY_MS, Controller
from quickseemd.previewer.state import Decision
from quickseemd.previewer.uri import Target, TargetKind


class FakeUi:
    def __init__(self) -> None:
        self.calls: list[tuple] = []

    def prepare(self, xid: int) -> None:
        self.calls.append(("prepare", xid))

    def show_loading(self, title: str, text: str) -> None:
        self.calls.append(("loading", title, text))

    def show_content(self, c: content.Content) -> None:
        self.calls.append(("content", c.view, c.title))

    def present(self) -> None:
        self.calls.append(("present",))

    def close_window(self) -> None:
        self.calls.append(("close",))

    def names(self) -> list[str]:
        return [c[0] for c in self.calls]


class Deferred:
    """背景工作與計時器都先排隊，測試決定什麼時候完成。"""

    def __init__(self) -> None:
        self.jobs: list[tuple[Callable, Callable]] = []
        self.timers: list[tuple[int, Callable]] = []

    def background(self, job: Callable, done: Callable) -> None:
        self.jobs.append((job, done))

    def later(self, ms: int, fn: Callable) -> None:
        self.timers.append((ms, fn))

    def finish_job(self, index: int = 0) -> None:
        job, done = self.jobs.pop(index)
        done(job())

    def fire_timers(self) -> None:
        timers, self.timers = self.timers, []
        for _ms, fn in timers:
            fn()


def _page(target: Target) -> content.Content:
    doc = Document(html="<p>x</p>", kind_label="純文字")
    return content.Content(content.PAGE, target, target.name, "純文字", document=doc)


def _fake_load(target: Target) -> content.Content:
    if target.name.endswith(".docx"):
        return content.Content(content.OFFICE, target, target.name, "Word", path=target.path)
    return _page(target)


def _fake_resolve(uri: str) -> Target:
    name = uri.rsplit("/", 1)[-1]
    return Target(TargetKind.FILE, uri, name, Path("/tmp") / name)


def _finish_office(c: content.Content) -> content.Content:
    return content.Content(content.PDF, c.target, c.title, "PDF", payload=object())


@pytest.fixture
def setup():
    ui = FakeUi()
    d = Deferred()
    ctl = Controller(
        ui,
        d.background,
        d.later,
        resolve=_fake_resolve,
        load=_fake_load,
        finish_office=_finish_office,
    )
    return ctl, ui, d


def test_show_file_loads_in_background_then_shows(setup):
    ctl, ui, d = setup
    assert ctl.show_file("file:///tmp/a.txt", 42, True) is Decision.SHOW
    # 回覆 D-Bus 前不做任何載入；只準備視窗並排好背景工作
    assert ui.names() == ["prepare"]
    assert ui.calls[0] == ("prepare", 42)
    assert len(d.jobs) == 1
    d.finish_job()
    assert ui.calls[-1] == ("content", content.PAGE, "a.txt")


def test_loading_indicator_only_when_slow(setup):
    ctl, ui, d = setup
    ctl.show_file("file:///tmp/slow.log", 1, True)
    assert d.timers[0][0] == LOADING_DELAY_MS
    d.fire_timers()
    assert ("loading", "slow.log", "正在載入…") in ui.calls
    d.finish_job()
    assert ui.calls[-1][0] == "content"


def test_no_loading_indicator_when_fast(setup):
    ctl, ui, d = setup
    ctl.show_file("file:///tmp/fast.txt", 1, True)
    d.finish_job()
    d.fire_timers()
    assert "loading" not in ui.names()


def test_same_file_twice_closes_and_drops_pending_result(setup):
    ctl, ui, d = setup
    ctl.show_file("file:///tmp/a.txt", 1, True)
    assert ctl.show_file("file:///tmp/a.txt", 1, True) is Decision.CLOSE
    assert ui.names()[-1] == "close"
    d.finish_job()
    d.fire_timers()
    assert "content" not in ui.names()
    assert "loading" not in ui.names()


def test_switching_files_ignores_stale_result(setup):
    ctl, ui, d = setup
    ctl.show_file("file:///tmp/a.txt", 1, True)
    ctl.show_file("file:///tmp/b.txt", 1, True)
    d.finish_job(1)  # b 先完成
    d.finish_job(0)  # a 的舊結果晚到
    shown = [c for c in ui.calls if c[0] == "content"]
    assert shown == [("content", content.PAGE, "b.txt")]


def test_present_for_same_file_without_close_flag(setup):
    ctl, ui, d = setup
    ctl.show_file("file:///tmp/a.txt", 1, True)
    d.finish_job()
    assert ctl.show_file("file:///tmp/a.txt", 1, False) is Decision.PRESENT
    assert ui.names()[-1] == "present"
    assert d.jobs == []


def test_office_runs_second_phase(setup):
    ctl, ui, d = setup
    ctl.show_file("file:///tmp/報告.docx", 1, True)
    d.finish_job()
    assert ui.calls[-1] == ("content", content.OFFICE, "報告.docx")
    assert len(d.jobs) == 1
    d.finish_job()
    assert ui.calls[-1] == ("content", content.PDF, "報告.docx")


def test_office_second_phase_dropped_after_close(setup):
    ctl, ui, d = setup
    ctl.show_file("file:///tmp/報告.docx", 1, True)
    d.finish_job()
    ctl.close()
    d.finish_job()
    assert ui.calls[-1] == ("close",)


def test_close_when_nothing_open_does_nothing(setup):
    ctl, ui, _d = setup
    ctl.close()
    assert ui.calls == []


def test_window_closed_by_user_then_same_file_opens_again(setup):
    ctl, ui, d = setup
    ctl.show_file("file:///tmp/a.txt", 1, True)
    d.finish_job()
    ctl.close()
    assert ctl.show_file("file:///tmp/a.txt", 1, True) is Decision.SHOW


def test_background_crash_shows_error_page():
    ui = FakeUi()
    d = Deferred()

    def boom(_target: Target) -> content.Content:
        raise RuntimeError("壞了")

    ctl = Controller(ui, d.background, d.later, resolve=_fake_resolve, load=boom)
    ctl.show_file("file:///tmp/a.txt", 1, True)
    d.finish_job()
    assert ui.calls[-1] == ("content", content.PAGE, "a.txt")


def test_default_functions_are_real(tmp_path: Path):
    """沒有替身時用真正的解析與載入（純文字檔走預覽核心或備援頁）。"""
    f = tmp_path / "真的.txt"
    f.write_text("hello", "utf-8")
    ui = FakeUi()
    d = Deferred()
    ctl = Controller(ui, d.background, d.later)
    ctl.show_file(f.as_uri(), 0, True)
    d.finish_job()
    assert ui.calls[-1] == ("content", content.PAGE, "真的.txt")
