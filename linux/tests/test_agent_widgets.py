"""背景程式的 GTK3 元件（在測試程序裡直接建立；需要隔離畫面）。

同一個測試程序已經載入 GTK4 時（其他分類的介面測試），這些測試會略過；
單獨執行：QS_TEST_DISPLAY=:137 python3 -m pytest tests/test_agent_widgets.py
"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from agent_fakes import fake_tools  # noqa: F401 — pytest fixture

pytestmark = pytest.mark.gui


@pytest.fixture(scope="module")
def gtk():
    import gi  # noqa: PLC0415

    try:
        gi.require_version("Gtk", "3.0")
        gi.require_version("Gdk", "3.0")
    except ValueError:
        pytest.skip("這個測試程序已經載入另一版 GTK")
    from gi.repository import Gtk  # noqa: PLC0415

    ok, _argv = Gtk.init_check([])
    if not ok:
        pytest.skip("無法連上隔離畫面")
    return Gtk


def pump(gtk, seconds: float = 0.2) -> None:
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        while gtk.events_pending():
            gtk.main_iteration_do(False)
        time.sleep(0.01)


class Recorder:
    def __init__(self):
        self.calls: list[tuple] = []

    def __getattr__(self, name):
        return lambda *a: self.calls.append((name, *a))


def _items(tmp_path):
    from quickseemd.agent.queue import QueueItem, Status  # noqa: PLC0415
    from quickseemd.core.model import TINTS  # noqa: PLC0415

    f = tmp_path / "a.txt"
    out = tmp_path / "a.zip"
    base = dict(icon="doc.zipper", tint=TINTS["teal"], files=(f,), tool_id="other.compress")
    return (
        QueueItem(1, "等待", status=Status.WAITING, **base),
        QueueItem(2, "執行", status=Status.RUNNING, progress=0.4, message="處理中", **base),
        QueueItem(3, "完成", status=Status.DONE, outputs=(out,), **base),
        QueueItem(4, "失敗", status=Status.FAILED, reason="壞了", **base),
        QueueItem(5, "通知", "link", TINTS["slate"], (), None, status=Status.DONE, message="訊"),
    )


def _buttons(widget, gtk):
    found = []
    if isinstance(widget, gtk.Button):
        found.append(widget)
    if isinstance(widget, gtk.Container):
        for child in widget.get_children():
            found.extend(_buttons(child, gtk))
    return found


# ------------------------------------------------------------------ 進度面板


def test_hud_rows_and_buttons(gtk, tmp_path, fake_tools):
    from quickseemd.agent.hud import HudWindow  # noqa: PLC0415

    actions = Recorder()
    hud = HudWindow(actions)
    items = _items(tmp_path)
    hud.set_items(items)
    hud.show_panel()
    pump(gtk, 0.3)
    assert hud.get_visible()
    assert hud.get_size()[0] == 360
    labels = {b.get_label() for b in _buttons(hud, gtk) if b.get_label()}
    assert labels == {"取消", "在資料夾中顯示", "開啟", "在 QUICK_SEEMD 中調整選項…"}
    for b in _buttons(hud, gtk):
        b.clicked()
    names = sorted(c[0] for c in actions.calls)
    assert names == sorted(["cancel", "reveal", "open_files", "adjust"] + ["dismiss"] * 5)
    # 進度更新只改數字，不重建
    from dataclasses import replace  # noqa: PLC0415

    hud.set_items((replace(items[1], progress=0.9, message="快好了"),))
    pump(gtk)
    assert len(hud._box.get_children()) == 1
    hud.hide_panel()
    assert not hud.get_visible()


def test_hud_controller_hides_after_timer(gtk, tmp_path):
    from quickseemd.agent.hud import HudController, HudWindow  # noqa: PLC0415
    from quickseemd.agent.queue import JobQueue  # noqa: PLC0415

    hud = HudWindow(Recorder())
    ctl = HudController(hud)
    queue = JobQueue(ctl)
    ctl.attach(queue)
    queue.notify("已拷貝", "doc.on.doc", "x")
    pump(gtk)
    assert hud.get_visible()
    hud.hovering = True
    ctl._on_timer()
    assert hud.get_visible() and ctl._timer
    hud.hovering = False
    ctl._on_timer()
    assert not hud.get_visible()
    assert queue.items == ()
    item = queue.notify("二", "link", "")
    queue.dismiss(item)
    assert not hud.get_visible()
    finished = []
    HudController(hud, on_item_finished=finished.append).item_finished("x")
    assert finished == ["x"]


# ------------------------------------------------------------------ 浮動暫存


class _Data:
    def __init__(self, payload: bytes):
        self.payload = payload

    def get_data(self):
        return self.payload


def test_shelf_add_remove_clear(gtk, tmp_path):
    from quickseemd.agent.shelf import ShelfWindow  # noqa: PLC0415

    a = tmp_path / "a.txt"
    a.write_text("x")
    b = tmp_path / "資料夾"
    b.mkdir()
    shelf = ShelfWindow()
    shelf.add_files([a, b, a, tmp_path / "missing"])
    pump(gtk)
    assert shelf.items == (a, b)
    assert shelf.get_visible()
    assert shelf.get_size()[1] == 2 * 46 + 58
    shelf._on_drop(None, None, 0, 0, _Data(f"{(tmp_path / 'c').as_uri()}\r\n".encode()), 0, 0)
    assert shelf.items == (a, b)  # 不存在的不加
    c = tmp_path / "c"
    c.write_text("x")
    shelf._on_drop(None, None, 0, 0, _Data(f"{c.as_uri()}\r\n".encode()), 0, 0)
    assert shelf.items == (a, b, c)
    shelf.remove_item(b)
    assert shelf.items == (a, c)
    shelf.clear()
    assert shelf.items == ()
    assert not shelf.get_visible()


# ------------------------------------------------------------------ 系統匣選單


def test_tray_menu_actions(gtk, fake_tools):
    from quickseemd.agent import tray, tray_model  # noqa: PLC0415

    actions = []
    entries = tray_model.build_menu(wheel_enabled=True, autostart_on=False)
    menu = tray.build_gtk_menu(entries, actions.append)
    items = menu.get_children()
    labels = [i.get_label() for i in items if not isinstance(i, gtk.SeparatorMenuItem)]
    assert "結束 QUICK__SEEMD" in labels
    for item in items:
        sub = item.get_submenu()
        if sub is not None:
            for child in sub.get_children():
                if child.get_sensitive():
                    child.activate()
        elif not isinstance(item, gtk.SeparatorMenuItem):
            item.activate()
    assert "quit" in actions and "toggle-wheel" in actions and "tool:video.compress" in actions
    wheel_item = next(i for i in items if i.get_label() == "拖曳轉換輪盤")
    assert wheel_item.get_active() is False  # activate 會切換勾選


def test_tray_indicator_update(gtk, fake_tools):
    from quickseemd.agent.tray import Tray  # noqa: PLC0415

    t = Tray(lambda _a: None)
    t.update(visible=True, wheel_enabled=True, autostart_on=True)
    t.update(visible=False, wheel_enabled=False, autostart_on=False)
    assert isinstance(t.available, bool)


# ------------------------------------------------------------------ 剪貼簿


def test_clipboard_owner_serves_all_targets(gtk, tmp_path):
    from gi.repository import Gdk  # noqa: PLC0415

    from quickseemd.agent import file_actions as fa  # noqa: PLC0415
    from quickseemd.agent.clipboard import ClipboardOwner  # noqa: PLC0415

    owner = ClipboardOwner()
    paths = [tmp_path / "一.txt"]
    assert owner.set_payloads(fa.clipboard_payloads(paths))
    cb = gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
    data = cb.wait_for_contents(Gdk.Atom.intern("x-special/gnome-copied-files", False))
    assert data.get_data() == ("copy\n" + paths[0].as_uri()).encode()
    assert cb.wait_for_text() == str(paths[0])
    assert owner.set_text("純文字")
    assert cb.wait_for_text() == "純文字"


# ------------------------------------------------------------------ 輪盤視窗


def _state(items, files=()):
    from quickseemd.agent.wheel_controller import WheelState  # noqa: PLC0415

    return WheelState("tools", tuple(files), tuple(items), "工具", "⇧ 轉換  ⌃ 動作  esc 取消")


def test_wheel_window_present_draw_and_drop(gtk, tmp_path):
    import cairo  # noqa: PLC0415

    from quickseemd.agent import wheel_draw  # noqa: PLC0415
    from quickseemd.agent.wheel_window import WheelWindow  # noqa: PLC0415
    from quickseemd.core.wheel import FILE_ACTIONS  # noqa: PLC0415

    chosen = []
    win = WheelWindow(lambda item, paths: chosen.append((item, paths)))
    f = tmp_path / "a.txt"
    f.write_text("x")
    win.present(_state(FILE_ACTIONS, [f]), 5, 5)
    pump(gtk)
    assert win.get_visible()
    assert win.get_position() == (0, 0)  # 限制在螢幕內
    win.present(_state(FILE_ACTIONS[:2], [f]), 600, 600)  # 已開著：只換內容
    assert len(win.state.items) == 2
    surface = cairo.ImageSurface(cairo.FORMAT_ARGB32, 440, 440)
    pal = wheel_draw.Palette((1, 1, 1, 1), (0, 0, 0, 1), (0, 0, 0, 0.5), True)
    for hover in (None, 0, 1, "center"):
        wheel_draw.draw_wheel(
            cairo.Context(surface), win.state, hover, pal, lambda *_a: None, lambda *_a: None
        )
    win._drop_hit = 1
    win._on_data(None, _FakeCtx(), 0, 0, _Data(f.as_uri().encode()), 0, 0)
    assert chosen == [(FILE_ACTIONS[1], [f])]
    assert not win.get_visible()
    win.present(_state(FILE_ACTIONS, [f]), 640, 420)
    win._drop_hit = "center"
    win._on_data(None, _FakeCtx(), 0, 0, _Data(f.as_uri().encode()), 0, 0)
    assert chosen[-1] == (None, [f])


def test_wheel_window_hides_after_release(gtk, tmp_path):
    from quickseemd.agent.wheel_window import WheelWindow  # noqa: PLC0415
    from quickseemd.core.wheel import FILE_ACTIONS  # noqa: PLC0415

    win = WheelWindow(lambda *_a: None)
    win.present(_state(FILE_ACTIONS), 640, 420)
    win._drop_pending = True
    win.drag_released()
    pump(gtk, 0.5)
    assert win.get_visible()  # 還在等拖放完成
    win._drop_pending = False
    pump(gtk, 0.3)
    assert not win.get_visible()


class _FakeCtx:
    pass


@pytest.fixture(autouse=True)
def _patch_finish(request, monkeypatch):
    if "gtk" in request.fixturenames:
        gtk = request.getfixturevalue("gtk")
        monkeypatch.setattr(gtk, "drag_finish", lambda *a: None)


# ------------------------------------------------------------------ 檔案動作


def test_file_actions_ui(gtk, tmp_path, monkeypatch):
    from quickseemd.agent import file_actions_ui, launcher  # noqa: PLC0415
    from quickseemd.agent.clipboard import ClipboardOwner  # noqa: PLC0415
    from quickseemd.agent.queue import JobQueue, Status  # noqa: PLC0415
    from quickseemd.agent.shelf import ShelfWindow  # noqa: PLC0415

    spawned = []
    monkeypatch.setattr(launcher, "spawn", lambda argv, env=None, cwd=None: spawned.append(argv))
    queue = JobQueue(Recorder())
    shelf = ShelfWindow()
    ui = file_actions_ui.FileActionsUI(queue, shelf, ClipboardOwner())
    f = tmp_path / "a.txt"
    f.write_text("x")
    ui.perform("copy", [f])
    ui.perform("copy-path", [f])
    assert [i.title for i in queue.items] == ["已拷貝", "已拷貝路徑"]
    ui.perform("shelf", [f])
    assert shelf.items == (f,)
    ui.perform("terminal", [f])
    assert spawned and spawned[0][0].startswith("/usr/bin/")
    ui.perform("bogus", [f])
    ui.perform("copy", [tmp_path / "missing"])
    dest = tmp_path / "dest"
    dest.mkdir()
    ui.enqueue_move([f], dest)
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline and queue.items[-1].status is Status.RUNNING:
        time.sleep(0.02)
    assert (dest / "a.txt").exists()
    shelf.clear()


def test_file_action_dialogs_cancel(gtk, tmp_path):
    from quickseemd.agent import file_actions_ui  # noqa: PLC0415
    from quickseemd.agent.clipboard import ClipboardOwner  # noqa: PLC0415
    from quickseemd.agent.queue import JobQueue  # noqa: PLC0415

    queue = JobQueue(Recorder())
    ui = file_actions_ui.FileActionsUI(queue, Recorder(), ClipboardOwner())
    f = tmp_path / "a.txt"
    f.write_text("x")
    ui.perform("move", [f])
    ui.perform("open-with", [f])
    pump(gtk, 0.5)
    dialogs = [w for w in gtk.Window.list_toplevels() if isinstance(w, gtk.Dialog)]
    assert {d.get_title() for d in dialogs} >= {"要把「a.txt」移到哪裡？", "用其他程式開啟"}
    for d in dialogs:
        d.response(gtk.ResponseType.CANCEL)
    pump(gtk)
    assert queue.items == ()
    assert f.exists()


# ------------------------------------------------------------------ 主程式


@pytest.mark.parametrize(("message", "code"), [("Unable to acquire bus name", 0), ("壞了", 1)])
def test_main_register_errors(gtk, monkeypatch, message, code):
    from gi.repository import GLib  # noqa: PLC0415

    from quickseemd.agent import main  # noqa: PLC0415

    class FakeApp:
        def register(self, _cancellable):
            raise GLib.Error(message)

        def run(self, _argv):
            raise AssertionError("不應該執行")

    monkeypatch.setattr(main, "AgentApp", FakeApp)
    assert main.main(["quickseemd-agent"]) == code


def test_agent_app_routes_requests(gtk, tmp_path, monkeypatch, fake_tools):
    from quickseemd.agent import launcher  # noqa: PLC0415
    from quickseemd.agent import main as agent_main  # noqa: PLC0415
    from quickseemd.core import settings  # noqa: PLC0415
    from quickseemd.core.wheel import FILE_ACTIONS, WheelItem  # noqa: PLC0415

    spawned = []
    monkeypatch.setattr(launcher, "spawn", lambda argv, env=None, cwd=None: spawned.append(argv))
    monkeypatch.setattr(agent_main, "call_soon", lambda fn: fn())
    app = agent_main.AgentApp()
    app._build()
    f = tmp_path / "a.txt"
    f.write_text("x")
    app.notify("標題", "", "訊息")
    app.add_to_shelf([f])
    assert app.shelf.items == (f,)
    app.convert("png", [f])
    app._wheel_chosen(None, [f])
    app._wheel_chosen(WheelItem("more", "tools", "更多工具", "ellipsis.circle", (0, 0, 0)), [f])
    app._wheel_chosen(FILE_ACTIONS[4], [f])  # 拷貝路徑
    assert [s[-2:] for s in spawned[:2]] == [["open", str(f)], ["open", str(f)]]
    for action in ("open-main", "settings", "tool:video.compress", "toggle-autostart"):
        app._tray_action(action)
    assert launcher.autostart_enabled()
    app._tray_action("toggle-autostart")
    assert not launcher.autostart_enabled()
    app._tray_action("toggle-wheel")
    assert settings.load()["wheel.enabled"] is False
    app._tray_action("toggle-wheel")
    assert settings.load()["wheel.enabled"] is True
    app.adjust("other.compress", [f])
    assert spawned[-1][-4:] == ["open", "--tool", "other.compress", str(f)]
    from quickseemd.agent.queue import QueueItem, Status  # noqa: PLC0415

    app._item_finished(
        QueueItem(9, "t", "link", (0, 0, 0), (), None, status=Status.DONE, clipboard="QR")
    )
    app.run_action("other.zip", [f])
    app.queue.shutdown()
    app.shelf.clear()
    app.hud.hide_panel()
