"""畫面測試的驅動程式：在私有 D-Bus 工作階段（dbus-run-session）裡操作預覽視窗與 Nemo。

只能在隔離畫面（DISPLAY ≥ :120）執行；只結束自己啟動的程序（以及私有工作階段裡的預覽服務）。
所有等待都是「輪詢條件、有期限」（見 previewer_gui_desktop.wait_until）。
用法：python3 previewer_gui_driver.py preview|nemo 設定.json
      python3 previewer_gui_driver.py fake-agent 紀錄檔
"""

from __future__ import annotations

import json
import subprocess
import sys
import traceback
from pathlib import Path

from previewer_gui_desktop import (
    Desktop,
    Preview,
    Region,
    check_display,
    ocr_find,
    uri_of,
    wait_until,
)

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkX11", "3.0")
from gi.repository import GdkX11, Gio, GLib, Gtk  # noqa: E402


def _is(title: str):  # noqa: ANN202
    return lambda t: t == title


# ------------------------------------------------------------------ 預覽視窗


def show_and_capture(d: Desktop, p: Preview, uri: str, title: str, name: str, **kw) -> dict:
    min_colors = kw.pop("min_colors", 3)
    stable = kw.pop("stable", True)
    settle_timeout = kw.pop("settle_timeout", 25.0)
    reply_ms = p.show(uri, **kw)
    win = d.find(_is(title), timeout=20)
    if win is None:
        return {"shown": False, "reply_ms": reply_ms, "titles": [t for _w, t in d.windows()]}
    colors = d.settle(win, min_colors, stable, timeout=settle_timeout)
    info = d.info(win)
    shot = d.screenshot(name)
    titles = sorted(t for _w, t in d.windows())
    region = Region(info)
    return {
        "shown": True,
        "reply_ms": reply_ms,
        "shot": shot,
        "colors": colors,
        "dark_ratio": region.dark_ratio(),
        "all_titles": titles,
        **info,
    }


def _show(d: Desktop, p: Preview, f: dict, key: str, **kw) -> dict:
    return show_and_capture(d, p, uri_of(f[key]), Path(f[key]).name, key, **kw)


def scenario_basics(d: Desktop, p: Preview, f: dict) -> dict:
    r: dict = {}
    r["text"] = _show(d, p, f, "text", min_colors=10)
    r["image"] = _show(d, p, f, "image", min_colors=50)
    r["gif"] = _show(d, p, f, "gif", stable=False)
    r["pdf"] = _show(d, p, f, "pdf", min_colors=10)
    r["video"] = _show(d, p, f, "video", min_colors=20, stable=False)
    p.show(uri_of(f["video"]))  # 同一個檔案再按一次：關閉
    r["same_file_closes"] = d.gone(_is(Path(f["video"]).name))
    r["audio"] = _show(d, p, f, "audio", stable=False)
    r["fifo"] = _show(d, p, f, "fifo")
    r["folder"] = _show(d, p, f, "folder")
    r["invalid"] = show_and_capture(d, p, "not-a-uri", "QUICK_SEEMD", "invalid")
    r["http"] = show_and_capture(d, p, f["http_uri"], "遠端文件.txt", "http")
    r["office"] = _show(d, p, f, "office", min_colors=10, settle_timeout=40)
    p.close()
    r["close_method"] = d.gone(_is(Path(f["office"]).name))
    return r


def scenario_engine_pages(d: Desktop, p: Preview, f: dict) -> dict:
    """真正的預覽核心產生的各種頁面。"""
    return {key: _show(d, p, f, key, min_colors=8) for key in f["engine_pages"]}


def scenario_font_and_stream(d: Desktop, p: Preview, f: dict) -> dict:
    r = {"font": _show(d, p, f, "font", min_colors=3)}
    r["hls"] = _show(d, p, f, "hls")
    p.close()
    d.gone(_is(Path(f["hls"]).name))
    return r


def _closes_with(d: Desktop, p: Preview, uri: str, title: str, key: str) -> bool:
    p.show(uri)
    win = d.find(_is(title))
    if win is None or not wait_until(lambda: d.focused(win), 10):
        return False
    d.key(key)
    return d.gone(_is(title))


def _width(d: Desktop, win, want_full: bool):  # noqa: ANN001, ANN202
    info = d.info(win)
    full = info["width"] == d.root.get_geometry().width
    return info if full == want_full else None


def scenario_keys(d: Desktop, p: Preview, f: dict) -> dict:
    text_uri, title = uri_of(f["text"]), Path(f["text"]).name
    r: dict = {k: _closes_with(d, p, text_uri, title, k) for k in ("space", "Escape", "q")}
    image = Path(f["image"]).name
    p.show(uri_of(f["image"]))
    win = d.find(_is(image))
    if win is not None and wait_until(lambda: d.focused(win), 10):
        d.key("f")
        r["fullscreen"] = wait_until(lambda: _width(d, win, True), 10)
        r["fullscreen_shot"] = d.screenshot("fullscreen")
        d.key("f")
        r["unfullscreen"] = wait_until(lambda: _width(d, win, False), 10)
    p.close()
    d.gone(_is(image))
    return r


def _button_region(info: dict) -> dict:
    return {"x": info["x"] + 15, "y": info["y"] + info["height"] - 38, "width": 24, "height": 24}


def scenario_media_controls(d: Desktop, p: Preview, f: dict) -> dict:
    """播完後按鈕變成「播放」；按下從頭播（變回「暫停」）；再按暫停；在進度條上點一下。"""
    title = Path(f["short_audio"]).name
    p.show(uri_of(f["short_audio"]))
    win = d.find(_is(title))
    if win is None:
        return {"shown": False}
    d.settle(win, 3, stable=False)
    info = d.info(win)
    button = _button_region(info)
    playing = Region(button).digest
    r = {"shown": True}
    r["ended"] = bool(wait_until(lambda: Region(button).digest != playing, 15))
    center = (button["x"] + 12, button["y"] + 12)
    away = (info["x"] + info["width"] // 2, info["y"] + info["height"] // 3)

    def press() -> None:  # 點完把滑鼠移開，按鈕的「滑過」底色才不會影響比對
        d.click(*center)
        d.move(*away)

    press()
    r["replaying"] = bool(wait_until(lambda: Region(button).digest == playing, 5))
    press()
    r["paused"] = bool(wait_until(lambda: Region(button).digest != playing, 5))
    d.click(info["x"] + info["width"] // 2, center[1])
    r["still_open"] = d.find(_is(title), timeout=2) is not None
    r["shot"] = d.screenshot("media-controls")
    p.close()
    d.gone(_is(title))
    return r


def scenario_rapid(d: Desktop, p: Preview, f: dict) -> dict:
    text, pdf, big = (Path(f[k]).name for k in ("text", "pdf", "big"))
    r: dict = {}
    p.show(uri_of(f["text"]))
    p.show(uri_of(f["pdf"]))
    win = d.find(_is(pdf))
    r["switch_final_title"] = d.info(win)["title"] if win else None
    r["stale_title_absent"] = not any(t == text for _w, t in d.windows())
    p.close()
    d.gone(_is(pdf))
    d.seen_titles.clear()
    p.show(uri_of(f["big"]))
    p.show(uri_of(f["big"]))  # 內容還沒準備好就再按一次
    p.show(uri_of(f["text"]))  # 服務照順序處理：這個出現時，前兩個一定處理完了
    r["after_double_press"] = d.find(_is(text)) is not None
    r["double_press_closed"] = big not in d.seen_titles
    p.close()
    d.gone(_is(text))
    return r


def _parent_window(d: Desktop) -> tuple[Gtk.Window, int]:
    parent = Gtk.Window(title="父視窗")
    parent.set_default_size(700, 500)
    parent.move(40, 60)
    parent.show_all()
    d.find(_is("父視窗"), timeout=10)
    return parent, GdkX11.X11Window.get_xid(parent.get_window())


def scenario_transient(d: Desktop, p: Preview, f: dict) -> dict:
    parent, xid = _parent_window(d)
    r = show_and_capture(d, p, uri_of(f["text"]), Path(f["text"]).name, "transient", xid=xid)
    r["parent_xid"] = xid
    r["parent"] = d.info(d.find(_is("父視窗"), timeout=5))
    d.key("Escape")
    r["closed"] = d.gone(_is(Path(f["text"]).name))
    parent.destroy()
    d.gone(_is("父視窗"))
    return r


def scenario_open_button(d: Desktop, p: Preview, f: dict, marker: Path) -> dict:
    title = Path(f["text"]).name
    p.show(uri_of(f["text"]))
    win = d.find(_is(title))
    if win is None:
        return {"shown": False}
    d.settle(win, 10, stable=True)
    info = d.info(win)
    d.click(info["x"] + info["width"] - 118, info["y"] + 24)
    wait_until(marker.exists, 10)
    opened = marker.read_text("utf-8").strip() if marker.exists() else ""
    return {"opened": opened, "closed": d.gone(_is(title))}


def run_preview(cfg: dict, d: Desktop) -> dict:
    p = Preview()
    f = cfg["files"]
    report = {"basics": scenario_basics(d, p, f)}
    report["engine"] = scenario_engine_pages(d, p, f)
    report["font_stream"] = scenario_font_and_stream(d, p, f)
    report["keys"] = scenario_keys(d, p, f)
    report["media"] = scenario_media_controls(d, p, f)
    report["rapid"] = scenario_rapid(d, p, f)
    report["transient"] = scenario_transient(d, p, f)
    report["open_button"] = scenario_open_button(d, p, f, Path(cfg["marker"]))
    report["service_stopped"] = p.stop_service("org.nemo.Preview")
    return report


# ------------------------------------------------------------------ Nemo


def _read_log(path: Path) -> list:
    if not path.exists():
        return []
    return [json.loads(ln) for ln in path.read_text("utf-8").splitlines() if ln]


def _focus_nemo(d: Desktop, nemo_win, info: dict) -> bool:  # noqa: ANN001
    """點一下空白處（openbox 第一下只會讓視窗取得焦點），等 Nemo 真的拿到焦點。"""
    spot = (info["x"] + info["width"] * 3 // 4, info["y"] + info["height"] * 3 // 4)
    for _ in range(3):
        d.click(*spot)
        if wait_until(lambda: d.focused(nemo_win), 3):
            return True
    return False


def nemo_preview_steps(d: Desktop, nemo_win, cfg: dict) -> dict:  # noqa: ANN001
    r: dict = {"nemo": d.info(nemo_win)}
    r["nemo_focused"] = _focus_nemo(d, nemo_win, r["nemo"])
    d.key("Home")
    title = Path(cfg["first"]).name
    win = None
    for _ in range(3):  # 選取第一個檔案、按空白鍵，直到預覽出現
        d.key("space")
        win = d.find(_is(title), timeout=8)
        if win is not None:
            break
        d.key("Home")
    r["preview_shown"] = win is not None
    if win is not None:
        d.settle(win, 10, stable=True)
        r["preview"] = d.info(win)
        r["preview_shot"] = d.screenshot("nemo-preview")
        wait_until(lambda: d.focused(win), 10)
        d.key("space")
        r["preview_closed"] = d.gone(_is(title))
    return r


def nemo_menu_steps(d: Desktop, nemo_win, cfg: dict) -> dict:  # noqa: ANN001
    r: dict = {"refocused": bool(wait_until(lambda: d.focused(nemo_win), 10))}
    d.key("Menu")
    menus = wait_until(d.popups, 10) or []
    r["popups"] = menus
    shot = d.screenshot("nemo-menu")
    r["menu_shot"] = shot
    spot = (
        (ocr_find(shot, menus[0], "SEEMD") or ocr_find(shot, menus[0], "QUICK")) if menus else None
    )
    r["menu_item_found"] = spot is not None
    if spot is None:
        return r
    d.move(*spot)
    r["submenu_opened"] = bool(wait_until(lambda: len(d.popups()) >= 2, 10))
    r["submenu_shot"] = d.screenshot("nemo-submenu")
    d.key("Right")
    d.key("Return")
    log_path = Path(cfg["agent_log"])
    r["agent_calls"] = wait_until(lambda: _read_log(log_path), 15) or []
    return r


def run_nemo(cfg: dict, d: Desktop) -> dict:
    with open(cfg["nemo_log"], "w", encoding="utf-8") as log_file:
        nemo = subprocess.Popen(  # noqa: S603
            ["/usr/bin/nemo", "--no-desktop", cfg["folder"]], stdout=log_file, stderr=log_file
        )
    report: dict = {}
    try:
        folder = Path(cfg["folder"]).name
        win = d.find(lambda t: folder in t, timeout=30)
        report["nemo_started"] = win is not None
        if win is not None:
            d.settle(win, 10, stable=True)
            report["start_shot"] = d.screenshot("nemo-start")
            report.update(nemo_preview_steps(d, win, cfg))
            report.update(nemo_menu_steps(d, win, cfg))
    finally:
        d.key("Escape")
        report["service_stopped"] = Preview().stop_service("org.nemo.Preview")
        nemo.terminate()
        try:
            nemo.wait(timeout=10)
        except subprocess.TimeoutExpired:
            nemo.kill()
            nemo.wait()
    return report


# ------------------------------------------------------------------ 假的背景程式


def fake_agent(log_path: str) -> None:
    """只記錄收到的呼叫（RunAction 等），給 Nemo 右鍵選單的測試用。"""
    from quickseemd.core import ipc  # noqa: PLC0415

    node = Gio.DBusNodeInfo.new_for_xml(ipc.AGENT_XML)
    loop = GLib.MainLoop()

    def on_call(_c, _s, _p, _i, method, params, invocation) -> None:  # noqa: ANN001
        with open(log_path, "a", encoding="utf-8") as fh:
            fh.write(json.dumps([method, params.unpack()], ensure_ascii=False) + "\n")
        if method == "Ping":
            invocation.return_value(GLib.Variant("(s)", ("test",)))
        else:
            invocation.return_value(None)

    def on_bus(conn: Gio.DBusConnection, _name: str) -> None:
        conn.register_object(ipc.AGENT_OBJECT_PATH, node.interfaces[0], on_call, None, None)

    Gio.bus_own_name(
        Gio.BusType.SESSION, ipc.AGENT_BUS_NAME, 0, on_bus, None, lambda *_a: loop.quit()
    )
    GLib.timeout_add_seconds(180, loop.quit)  # 保險：測試忘了結束時自己離開
    loop.run()


# ------------------------------------------------------------------ 進入點


def main(argv: list[str]) -> int:
    check_display()
    if argv[1] == "fake-agent":
        fake_agent(argv[2])
        return 0
    cfg = json.loads(Path(argv[2]).read_text("utf-8"))
    out = Path(cfg["outdir"])
    out.mkdir(parents=True, exist_ok=True)
    openbox = subprocess.Popen(["/usr/bin/openbox", "--sm-disable"])  # noqa: S603
    report: dict = {}
    try:
        d = Desktop(out)
        report["wm_ready"] = d.wm_ready()
        report.update(run_preview(cfg, d) if argv[1] == "preview" else run_nemo(cfg, d))
    except Exception:  # noqa: BLE001 — 把錯誤寫進報告，測試端會顯示
        report["error"] = traceback.format_exc()
    finally:
        openbox.terminate()
        openbox.wait(timeout=10)
        Path(cfg["report"]).write_text(json.dumps(report, ensure_ascii=False, indent=1), "utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
