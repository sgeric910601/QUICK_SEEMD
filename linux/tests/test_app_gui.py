"""主程式介面測試（只在隔離畫面執行：QS_TEST_DISPLAY=:136，並在 dbus-run-session 裡）。

每個測試都建自己的主視窗、用假工具（tests.app_fake_tools）。截圖存在 tmp_path；
設定 QS_SHOT_DIR 時另外存一份到那個資料夾方便人工檢查。
"""

from __future__ import annotations

import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from conftest import make_audio, make_image, make_pdf, make_video

pytestmark = pytest.mark.gui


# ---------------------------------------------------------------- 共用


@pytest.fixture(scope="module")
def gtk():
    os.environ.setdefault("GSK_RENDERER", "cairo")
    import gi

    gi.require_version("Gtk", "4.0")
    gi.require_version("Adw", "1")
    from gi.repository import Adw

    Adw.init()
    from quickseemd.app import css

    css.install()
    return True


@pytest.fixture
def fake(monkeypatch, gtk):
    from quickseemd.core import registry

    monkeypatch.setenv("QUICKSEEMD_EXTRA_TOOLS", "tests.app_fake_tools")
    registry.reload()
    yield registry
    monkeypatch.delenv("QUICKSEEMD_EXTRA_TOOLS")
    registry.reload()


def pump(seconds: float = 0.2) -> None:
    from gi.repository import GLib

    ctx = GLib.MainContext.default()
    end = time.monotonic() + seconds
    while time.monotonic() < end:
        ctx.iteration(False)
        time.sleep(0.003)


def wait(pred, timeout: float = 25.0) -> None:  # noqa: ANN001
    from gi.repository import GLib

    ctx = GLib.MainContext.default()
    end = time.monotonic() + timeout
    while not pred() and time.monotonic() < end:
        ctx.iteration(False)
        time.sleep(0.003)
    assert pred(), "等待逾時"


def shot(widget, path: Path) -> Path:  # noqa: ANN001
    from gi.repository import Graphene, Gtk

    pump(0.4)
    w, h = widget.get_width(), widget.get_height()
    node = None
    for _ in range(5):
        snap = Gtk.Snapshot()
        Gtk.WidgetPaintable.new(widget).snapshot(snap, w, h)
        node = snap.to_node()
        if node is not None:
            break
        pump(0.2)
    assert node is not None, "截圖是空的"
    rect = Graphene.Rect().init(0, 0, w, h)
    widget.get_native().get_renderer().render_texture(node, rect).save_to_png(str(path))
    extra = os.environ.get("QS_SHOT_DIR")
    if extra:
        Path(extra).mkdir(parents=True, exist_ok=True)
        widget.get_native().get_renderer().render_texture(node, rect).save_to_png(
            str(Path(extra) / path.name)
        )
    return path


@pytest.fixture
def window(fake):
    from quickseemd.app.state import AppState
    from quickseemd.app.window import MainWindow

    state = AppState()
    win = MainWindow(None, state)
    win.set_default_size(1160, 760)
    win.present()
    pump(0.5)
    yield win, state
    win.close()
    pump(0.2)


def labels_in(widget) -> list[str]:  # noqa: ANN001
    from gi.repository import Gtk

    out: list[str] = []
    stack = [widget]
    while stack:
        w = stack.pop()
        if isinstance(w, Gtk.Label) and w.get_mapped():
            out.append(w.get_label())
        child = w.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()
    return out


@pytest.fixture
def data(tmp_path: Path) -> dict[str, Path]:
    d = tmp_path / "資料"
    d.mkdir()
    return {
        "png": make_image(d / "相片.png", size=(400, 300)),
        "png2": make_image(d / "相片二.png", size=(300, 400)),
        "pdf": make_pdf(d / "文件.pdf", pages=3),
        "pdf2": make_pdf(d / "文件二.pdf", pages=1),
    }


# ---------------------------------------------------------------- 大廳


@pytest.mark.parametrize("width", [980, 1600])
def test_hub_layout_and_filters(window, data, tmp_path, width):
    win, state = window
    win.set_default_size(width, 760)
    pump(0.5)
    texts = labels_in(win)
    assert "你需要的檔案工具，全都在這裡" in texts and "萬用預覽・轉檔・工具箱" in texts
    shot(win, tmp_path / f"hub-{width}.png")
    win.hub.search.set_text("allkinds")
    wait(lambda: "測試全部選項" in labels_in(win) and "測試複製 PDF" not in labels_in(win))
    win.hub.search.set_text("沒有這個工具")
    wait(lambda: "找不到符合「沒有這個工具」的工具" in labels_in(win))
    win.hub.search.set_text("")
    state.receive([data["png"], data["png2"]])
    pump(0.5)
    texts = labels_in(win)
    assert "已選取 2 個檔案：要對它們做什麼？" in texts
    assert "測試裁剪" in texts and "測試複製 PDF" not in texts
    shot(win, tmp_path / f"hub-inbox-{width}.png")
    state.select("cat:pdf")
    pump(0.3)
    assert win.hub.title.get_label() == "PDF 工具"


def test_hub_card_opens_tool_with_inbox(window, data):
    win, state = window
    state.receive([data["pdf"]])
    pump(0.3)
    from quickseemd.core import registry

    win.hub._open(registry.tool("test.copy"))
    pump(0.5)
    assert state.open_tool_id == "test.copy" and state.inbox == ()
    assert win.tool_page.files == [data["pdf"]]
    assert win.sidebar.list.get_selected_row() is None


# ---------------------------------------------------------------- 工具頁


@pytest.mark.parametrize("width", [980, 1600])
def test_tool_page_all_option_kinds(window, data, tmp_path, width):
    from quickseemd.core import registry

    win, state = window
    win.set_default_size(width, 760)
    tool = registry.tool("test.all-kinds")
    state.open(tool, [])
    pump(0.4)
    page = win.tool_page
    assert page.stack.get_visible_child_name() == "landing"
    shot(win, tmp_path / f"landing-{width}.png")
    page.add_files([data["png"], data["png2"], data["pdf"]])
    wait(lambda: page.options.values["width"] == 640)  # inspect → prefill
    assert page.options.info["page_count"] == 8
    texts = labels_in(win)
    for t in ("壓縮程度", "高度壓縮", "推薦", "JPEG", "H.264", "80%", "例如：1-3, 5, 8-　共 8 頁"):
        assert t in texts, t
    shot(win, tmp_path / f"tool-{width}.png")
    inner = page.panel_inner
    shot(inner, tmp_path / f"panel-{width}.png")
    _exercise_controls(page)


def _exercise_controls(page) -> None:  # noqa: ANN001
    from gi.repository import Gdk

    st, panel = page.options, page.panel
    dyn = panel.control("dyn")
    assert not dyn.root.get_visible()
    panel.control("more").row.set_active(True)
    pump(0.1)
    assert st.values["more"] is True and dyn.root.get_visible()
    assert page.run_button.qs_label.get_label() == "開始處理（更多）"
    panel.control("text")._entry.set_text("壞")
    pump(0.1)
    assert page.reason.get_visible() and page.run_button.get_sensitive() is False
    panel.control("text")._entry.set_text("好")
    pump(0.1)
    assert not page.reason.get_visible()
    cards = panel.control("level")
    cards._buttons[0][1].emit("clicked")
    chips = panel.control("format")
    chips._buttons[1][1].emit("clicked")
    pump(0.1)
    assert st.values["level"] == "small" and st.values["format"] == "png"
    assert st.values["quality"] == 100  # adjust 連動
    panel.control("quality")._scale.set_value(42)
    panel.control("width")._spin.set_value(123.6)
    panel.control("codec")._buttons[1][1].set_active(True)
    panel.control("position")._buttons[0][1].set_active(True)
    panel.control("algos")._checks[0][1].set_active(True)
    panel.control("start")._entry.set_text("x")
    pump(0.1)
    assert st.values["quality"] == 40 and st.values["width"] == 124
    assert st.values["codec"] == "hevc" and st.values["position"] == "tl"
    assert st.values["algos"] == ["sha256", "md5"]
    assert panel.control("start")._entry.get_icon_name(1) == "dialog-warning-symbolic"
    color = panel.control("color")
    rgba = Gdk.RGBA()
    rgba.parse("#00ff00")
    color._custom.set_rgba(rgba)
    pump(0.1)
    assert st.values["color"] == "custom" and st.values["custom_color"] == "#00ff00"
    color._dots[0][1].emit("clicked")
    pump(0.1)
    assert st.values["color"] == "transparent"
    panel.control("secret")._entry.set_text("ab")
    pump(0.1)
    assert panel.control("warn").root.get_visible()
    panel.control("logo")._clear.emit("clicked")
    assert not panel.control("disabled_toggle").root.get_sensitive()


def test_tool_page_files_reorder_sort_and_skip(window, data):
    from quickseemd.core import registry

    win, state = window
    state.open(registry.tool("test.merge"), [data["pdf"]])
    pump(0.3)
    page = win.tool_page
    assert page.reason.qs_label.get_label() == "至少需要 2 個檔案"
    page.add_files([data["pdf2"], data["png"]])
    pump(0.3)
    assert page.files == [data["pdf"], data["pdf2"]]
    assert "已略過 1 個不支援的檔案（需要PDF 檔案）" in labels_in(win)
    assert page.files_area.sort_button.get_visible()
    page.files_area._dropped("qs-file:1", 0)
    assert page.files == [data["pdf2"], data["pdf"]]
    page.files_area._dropped("bogus", 0)
    from gi.repository import GLib

    page.files_area.activate_action("files.sort", GLib.Variant("s", "reverse"))
    pump(0.2)
    assert page.files == [data["pdf"], data["pdf2"]]
    page.files_area._remove(data["pdf"])
    assert page.files == [data["pdf2"]]


def _wait_page(page, name: str) -> None:  # noqa: ANN001
    wait(lambda: page.stack.get_visible_child_name() == name, 40)


def test_run_success_result_page(window, data, tmp_path):
    from quickseemd.app import desktop
    from quickseemd.core import registry

    win, state = window
    state.open(registry.tool("test.copy"), [data["pdf"]])
    pump(0.3)
    page = win.tool_page
    page.run()
    assert page.running.get_visible()
    _wait_page(page, "result")
    out = data["pdf"].parent / "文件-已複製.pdf"
    assert out.exists()
    texts = labels_in(win)
    assert "檔案已經準備好了！" in texts and "儲存在「資料」資料夾" in texts
    assert "接著可以…" in texts and "開啟檔案" in texts
    shot(win, tmp_path / "result.png")
    result = page.stack.get_child_by_name("result")
    desktop.TEST_LOG.clear()
    result.outputs and desktop.reveal(result.outputs)
    assert desktop.TEST_LOG == [("reveal", [str(out)])]
    page.back_to_editing()
    assert page.stack.get_visible_child_name() == "workspace"
    page.restart()
    assert page.stack.get_visible_child_name() == "landing"


def test_run_failure_and_cancel(window, data, tmp_path):
    from quickseemd.core import registry

    win, state = window
    state.open(registry.tool("test.fail"), [data["png"]])
    pump(0.3)
    page = win.tool_page
    page.run()
    _wait_page(page, "failure")
    assert "無法完成" in labels_in(win)
    assert any(t.startswith("故意失敗") for t in labels_in(win))
    shot(win, tmp_path / "failure.png")
    page.back_to_editing()
    state.open(registry.tool("test.slow"), [data["png"]])
    pump(0.3)
    page = win.tool_page
    page.run()
    wait(lambda: page.running.pct.get_label() != "0%", 30)
    shot(win, tmp_path / "running.png")
    page.cancel()
    wait(lambda: page.runner is None, 30)
    assert page.stack.get_visible_child_name() == "workspace"
    assert not page.running.get_visible()


def test_clipboard_result(window, data):
    from gi.repository import Gdk

    from quickseemd.core import registry

    win, state = window
    state.open(registry.tool("test.copy"), [data["pdf"]])
    pump(0.2)
    page = win.tool_page
    page._copy_clipboard("https://example.com")
    clip = Gdk.Display.get_default().get_clipboard()
    got: list[str] = []
    clip.read_text_async(None, lambda c, r: got.append(c.read_text_finish(r)))
    wait(lambda: bool(got), 5)
    assert got == ["https://example.com"]


# ---------------------------------------------------------------- 自訂畫面


def test_organize_view(window, data, tmp_path):
    from quickseemd.core import registry

    win, state = window
    state.open(registry.tool("test.organize"), [data["pdf"]])
    page = win.tool_page
    wait(lambda: page.custom is not None and page.custom.model.pages)
    view = page.custom
    ids = [p.uid for p in view.model.pages]
    view._rotate_one(ids[0], 90)
    view._insert(ids[1])
    view.model.tap(ids[2])
    view._delete_sel()
    view._dropped(f"qs-page:{ids[1]}", ids[0])
    pump(0.5)
    pages = json.loads(page.options.values["pages"])
    assert [p["src"] for p in pages] == [1, 0, None]
    assert pages[1]["rotate"] == 90
    shot(win, tmp_path / "organize.png")
    view._delete_one(view.model.pages[0].uid)
    view._rotate_all(-90)
    view._reverse()
    for p in list(view.model.pages):
        view._delete_one(p.uid)
    pump(0.2)
    assert page.validation() == "至少要保留一頁"
    assert view.stack.get_visible_child_name() == "empty"
    view._reset()
    assert len(json.loads(page.options.values["pages"])) == 3


def test_crop_view(window, data, tmp_path):
    from quickseemd.core import registry

    win, state = window
    state.open(registry.tool("test.crop"), [data["png"]])
    page = win.tool_page
    wait(lambda: page.custom is not None and page.custom.img_w == 400)
    view = page.custom
    assert page.options.values["crop"] == ""
    page.options.set_value("ratio", "r16x9")
    pump(0.3)
    assert json.loads(page.options.values["crop"]) == {"x": 0, "y": 38, "w": 400, "h": 225}
    fx, fy, fw, fh = view._frame()
    view._drag_begin(None, fx + fw / 2, fy + fh / 2)
    view._drag_update(None, 0, 10)
    view._drag_end()
    moved = json.loads(page.options.values["crop"])
    assert moved["h"] == 225 and moved["y"] > 38
    view._drag_begin(None, fx + fw, fy + fh * (moved["y"] + 225) / 300)
    view._drag_update(None, -fw / 4, 0)
    view._drag_end()
    resized = json.loads(page.options.values["crop"])
    assert resized["w"] < 400 and abs(resized["w"] / resized["h"] - 16 / 9) < 0.05
    shot(win, tmp_path / "crop.png")
    page.options.set_value("ratio", "free")
    view.reset()
    assert page.options.values["crop"] == ""


def test_trim_views(window, tmp_path):
    from quickseemd.core import registry

    win, state = window
    vid = make_video(tmp_path / "影片.mp4", seconds=3)
    aud = make_audio(tmp_path / "聲音.wav", seconds=2)
    state.open(registry.tool("test.trim"), [vid])
    page = win.tool_page
    wait(lambda: page.custom is not None and page.custom.model.duration)
    view = page.custom
    assert page.options.values["end"] == "0:03.00"
    assert not page.panel.control("start").root.get_visible()  # 用畫面自己的時間欄位
    view.start_entry.set_text("1")
    view._typed("start", view.start_entry)
    assert page.options.values["start"] == "0:01.00"
    view.end_entry.set_text("abc")
    view._typed("end", view.end_entry)
    assert view.end_entry.get_icon_name(1) == "dialog-warning-symbolic"
    w = view.bar.get_width()
    view._bar_begin(None, view._x_for(3.0, w), 10)
    view._bar_update(None, -w / 3, 0)
    view._bar_end()
    assert page.options.values["end"] != "0:03.00"
    wait(lambda: len(view.frames) > 0)
    shot(win, tmp_path / "trim.png")
    state.open(registry.tool("test.atrim"), [aud])
    page = win.tool_page
    wait(lambda: page.custom is not None and len(page.custom.wave) > 0)
    assert page.custom.length_title.get_label() == "保留長度"
    shot(win, tmp_path / "atrim.png")


# ---------------------------------------------------------------- 其他頁面


def test_converter_page_flow(window, data, monkeypatch, tmp_path):
    from quickseemd.core import converter
    from quickseemd.core.converter import Target, Unavailable
    from quickseemd.core.converter.formats import BY_ID

    win, state = window
    state.select("converter")
    pump(0.3)
    page = win.pages["converter"]
    assert page.stack.get_visible_child_name() == "empty"
    shot(win, tmp_path / "conv-empty.png")
    page.add_files([data["png"]])
    wait(lambda: not page.model.analyzing_scope())
    if not converter_engine_present():
        assert page.model.engine_missing and "轉換引擎尚未安裝" in labels_in(win)
    monkeypatch.setattr(
        converter, "targets_for", lambda p: [Target(BY_ID[f], "image") for f in ("jpeg", "pdf")]
    )
    monkeypatch.setattr(
        converter, "unavailable", lambda p: [Unavailable("HEIC", "需要 libheif", None)]
    )
    page.model.engine_missing = False
    page.add_files([data["png2"], data["pdf"]])
    page.redetect()
    wait(lambda: not page.model.analyzing_scope())
    page.model.select_all()
    page._choose("jpeg")
    pump(0.2)
    texts = labels_in(win)
    assert "轉換成" in texts and "選項" in texts and "合併成一個 PDF" in texts
    shot(win, tmp_path / "conv-work.png")
    page.convert_all()
    wait(lambda: not page.model.running, 60)
    assert all(j.status.kind in ("done", "failed") for j in page.model.jobs if j.target_id)
    assert page.model.banner is not None
    page.merge()
    wait(lambda: not page.model.running, 60)
    assert page.model.banner is not None
    page.model.set_output_folder(tmp_path)
    page._refresh_footer()
    assert page.folder_button.get_visible()
    page._set_mode("same")
    assert page.model.output_folder_spec() is None
    page._remove(page.model.jobs[0].uid)
    page.model.remove_all()
    page.refresh()
    assert page.stack.get_visible_child_name() == "empty"


def converter_engine_present() -> bool:
    import importlib.util

    return importlib.util.find_spec("quickseemd.core.converter.engine") is not None


def test_converter_opened_as_tool_receives_files(window, data):
    from quickseemd.core import registry

    win, state = window
    state.open(registry.tool("test.convert"), [data["png"]])
    pump(0.3)
    page = win.pages["converter"]
    assert page.back.get_visible() and len(page.model.jobs) == 1
    state.close_tool()
    pump(0.2)
    assert win.stack.get_visible_child_name() == "hub"


def test_integration_page_and_autostart(window, tmp_path):
    from quickseemd.app import integration
    from quickseemd.core import settings

    win, state = window
    state.select("integration")
    pump(0.5)
    page = win.pages["integration"]
    texts = labels_in(win)
    assert "在任何地方使用 QUICK_SEEMD" in texts and "已開啟" in texts
    assert any(t.startswith("Nemo 右鍵擴充尚未安裝") for t in texts)
    wait(lambda: "背景程式沒有回應" in labels_in(win), 10)
    shot(win, tmp_path / "integration.png")
    page.keys.rows[1].set_selected(0)
    pump(0.1)
    assert settings.load()["wheel.key.tools"] == "shift"
    assert page.keys.warning.get_visible()
    page.keys.rows[1].set_selected(2)
    launcher = integration.agent_launcher()
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text("#!/bin/sh\n")
    try:
        rows = _switch_rows(page)
        auto = next(r for r in rows if r.get_title() == "登入時自動啟動")
        auto.set_active(True)
        assert integration.autostart_enabled()
        auto.set_active(False)
        assert not integration.autostart_enabled()
        bg = next(r for r in rows if r.get_title() == "關閉視窗後繼續在背景執行")
        bg.set_active(False)
        assert settings.load()["background.enabled"] is False
        bg.set_active(True)
    finally:
        launcher.unlink()
    wheel = next(r for r in _switch_rows(page) if r.get_title() == "啟用拖曳轉換輪盤")
    wheel.set_active(False)
    assert "已關閉" in labels_in(win) and not page.keys.rows[0].get_visible()
    wheel.set_active(True)


def _switch_rows(widget) -> list:  # noqa: ANN001
    from gi.repository import Adw

    out, stack = [], [widget]
    while stack:
        w = stack.pop()
        if isinstance(w, Adw.SwitchRow):
            out.append(w)
        child = w.get_first_child()
        while child is not None:
            stack.append(child)
            child = child.get_next_sibling()
    return out


def test_settings_dialog_and_preview_page(window, data, tmp_path):
    from quickseemd.app.settings_dialog import SettingsDialog
    from quickseemd.core import settings

    win, state = window
    dlg = SettingsDialog()
    dlg.present(win)
    pump(0.6)
    shot(win, tmp_path / "settings.png")
    tray = next(r for r in _switch_rows(dlg) if r.get_title() == "在系統匣顯示圖示")
    tray.set_active(False)
    assert settings.load()["tray.show"] is False
    tray.set_active(True)
    dlg.close()
    state.select("preview")
    pump(0.3)
    page = win.pages["preview"]
    page.load(data["png"])
    wait(lambda: any(t.startswith("無法開啟預覽：") for t in labels_in(win)), 10)
    shot(win, tmp_path / "preview.png")
    from quickseemd.app.preview_page import window_xid

    assert window_xid(page) > 0


# ---------------------------------------------------------------- 單一實例與命令列轉送

BUS_CONFIG = """<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig><type>session</type><listen>unix:dir={dir}</listen><auth>EXTERNAL</auth>
<policy context="default"><allow send_destination="*" eavesdrop="true"/><allow eavesdrop="true"/>
<allow own="*"/></policy></busconfig>
"""


def test_command_line_forwarding(tmp_path, data, gtk):
    """第二次執行 quickseemd open 會轉給已經在跑的那一份（私有匯流排，沒有任何服務目錄）。"""
    sock = tmp_path / "sock"
    sock.mkdir()
    conf = tmp_path / "bus.conf"
    conf.write_text(BUS_CONFIG.format(dir=sock))
    bus = subprocess.Popen(
        ["/usr/bin/dbus-daemon", f"--config-file={conf}", "--print-address=1", "--nofork"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    primary = None
    try:
        env = dict(os.environ)
        env["DBUS_SESSION_BUS_ADDRESS"] = bus.stdout.readline().decode().strip()
        env["QUICKSEEMD_EXTRA_TOOLS"] = "tests.app_fake_tools"
        env["QS_DEBUG"] = "1"
        env["PYTHONPATH"] = str(Path(__file__).resolve().parent.parent)
        log = tmp_path / "primary.log"
        with open(log, "wb") as fh:
            primary = subprocess.Popen(
                ["/usr/bin/python3", "-m", "quickseemd", "open"], env=env, stderr=fh
            )
        wait(lambda: "開啟請求：show" in log.read_text("utf-8", "replace"), 30)
        remote = subprocess.run(
            [
                "/usr/bin/python3",
                "-m",
                "quickseemd",
                "open",
                "--tool",
                "test.copy",
                str(data["pdf"]),
            ],
            env=env,
            capture_output=True,
            timeout=30,
        )
        assert remote.returncode == 0
        wait(
            lambda: "開啟請求：tool test.copy（1 個檔案）" in log.read_text("utf-8", "replace"), 30
        )
    finally:
        if primary is not None:
            primary.terminate()
            primary.wait(timeout=10)
        bus.terminate()
        bus.wait(timeout=10)
