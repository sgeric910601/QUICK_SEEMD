"""畫面測試的驅動程式：在私有 D-Bus 工作階段（dbus-run-session）裡操作預覽視窗與 Nemo。

只能在隔離畫面（DISPLAY ≥ :120）執行；只結束自己啟動的程序（以及私有工作階段裡的預覽服務）。
用法：python3 previewer_gui_driver.py preview|nemo 設定.json
      python3 previewer_gui_driver.py fake-agent 紀錄檔
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
import traceback
from collections.abc import Callable
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("GdkX11", "3.0")
from gi.repository import Gdk, GdkX11, Gio, GLib, Gtk  # noqa: E402
from Xlib import X, XK  # noqa: E402
from Xlib import display as xdisplay  # noqa: E402
from Xlib.ext import xtest  # noqa: E402

PREVIEW = ("org.nemo.Preview", "/org/nemo/Preview", "org.nemo.Preview")
AGENT_NAME = "dev.quickseemd.Agent"


def _check_display() -> None:
    number = int(os.environ["DISPLAY"].lstrip(":").split(".")[0])
    if number < 120:
        raise SystemExit("只能在隔離畫面（:120 以上）執行")


# ------------------------------------------------------------------ 畫面操作


class Desktop:
    def __init__(self, outdir: Path) -> None:
        self.x = xdisplay.Display()
        self.root = self.x.screen().root
        self.out = outdir
        self.atom = self.x.intern_atom

    def pump(self, seconds: float) -> None:
        end = time.time() + seconds
        while time.time() < end:
            while Gtk.events_pending():
                Gtk.main_iteration_do(False)
            time.sleep(0.02)

    def _title(self, win) -> str | None:  # noqa: ANN001
        try:
            prop = win.get_full_property(self.atom("_NET_WM_NAME"), self.atom("UTF8_STRING"))
            if prop is not None:
                return bytes(prop.value).decode("utf-8", "replace")
            name = win.get_wm_name()
        except Exception:  # noqa: BLE001 — 視窗可能剛好關掉
            return None
        return name.decode() if isinstance(name, bytes) else name

    def windows(self) -> list[tuple[object, str]]:
        found: list[tuple[object, str]] = []
        stack = [self.root]
        while stack:
            win = stack.pop()
            try:
                children = win.query_tree().children
                viewable = win.get_attributes().map_state == X.IsViewable
            except Exception:  # noqa: BLE001
                continue
            title = self._title(win)
            if title and viewable and win != self.root:
                found.append((win, title))
            stack.extend(children)
        return found

    def find(self, match: Callable[[str], bool], timeout: float = 10.0):  # noqa: ANN201
        end = time.time() + timeout
        while time.time() < end:
            for win, title in self.windows():
                if match(title):
                    return win
            self.pump(0.1)
        return None

    def gone(self, match: Callable[[str], bool], timeout: float = 5.0) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            if not any(match(t) for _w, t in self.windows()):
                return True
            self.pump(0.1)
        return False

    def info(self, win) -> dict:  # noqa: ANN001
        geo = win.get_geometry()
        pos = self.root.translate_coords(win, 0, 0)
        transient = win.get_full_property(self.atom("WM_TRANSIENT_FOR"), X.AnyPropertyType)
        wtype = win.get_full_property(self.atom("_NET_WM_WINDOW_TYPE"), X.AnyPropertyType)
        return {
            "id": win.id,
            "x": pos.x,
            "y": pos.y,
            "width": geo.width,
            "height": geo.height,
            "transient_for": int(transient.value[0]) if transient else None,
            "types": [self.x.get_atom_name(a) for a in wtype.value] if wtype else [],
            "title": self._title(win),
        }

    def popups(self) -> list[dict]:
        """畫面上的彈出選單（override-redirect 視窗），由大到小。"""
        found = []
        for win in self.root.query_tree().children:
            try:
                attrs = win.get_attributes()
                geo = win.get_geometry()
            except Exception:  # noqa: BLE001 — 選單可能剛好關掉
                continue
            if attrs.override_redirect and attrs.map_state == X.IsViewable and geo.width > 50:
                found.append({"x": geo.x, "y": geo.y, "width": geo.width, "height": geo.height})
        return sorted(found, key=lambda r: r["width"] * r["height"], reverse=True)

    def screenshot(self, name: str) -> str:
        root = Gdk.get_default_root_window()
        pb = Gdk.pixbuf_get_from_window(root, 0, 0, root.get_width(), root.get_height())
        path = self.out / f"{name}.png"
        pb.savev(str(path), "png", [], [])
        return str(path)

    def key(self, name: str) -> None:
        code = self.x.keysym_to_keycode(XK.string_to_keysym(name))
        xtest.fake_input(self.x, X.KeyPress, code)
        self.x.sync()
        time.sleep(0.05)
        xtest.fake_input(self.x, X.KeyRelease, code)
        self.x.sync()

    def move(self, x: int, y: int) -> None:
        xtest.fake_input(self.x, X.MotionNotify, x=int(x), y=int(y))
        self.x.sync()

    def click(self, x: int, y: int, button: int = 1) -> None:
        self.move(x, y)
        time.sleep(0.15)
        xtest.fake_input(self.x, X.ButtonPress, button)
        self.x.sync()
        time.sleep(0.05)
        xtest.fake_input(self.x, X.ButtonRelease, button)
        self.x.sync()


def colors_in(png: str, rect: dict) -> int:
    """視窗範圍內有幾種顏色（確認真的畫出內容，不是一整片空白）。"""
    from PIL import Image  # noqa: PLC0415

    with Image.open(png) as img:
        box = (rect["x"], rect["y"], rect["x"] + rect["width"], rect["y"] + rect["height"])
        colors = img.convert("RGB").crop(box).getcolors(maxcolors=1 << 20)
    return len(colors) if colors else 1 << 20


# ------------------------------------------------------------------ D-Bus


class Preview:
    def __init__(self) -> None:
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

    def show(self, uri: str, xid: int = 0, close: bool = True) -> float:
        start = time.time()
        self.bus.call_sync(
            *PREVIEW, "ShowFile", GLib.Variant("(sib)", (uri, xid, close)), None, 0, 30000, None
        )
        return round((time.time() - start) * 1000)

    def close(self) -> None:
        self.bus.call_sync(*PREVIEW, "Close", None, None, 0, 10000, None)

    def owner_pid(self, name: str) -> int | None:
        try:
            res = self.bus.call_sync(
                "org.freedesktop.DBus",
                "/org/freedesktop/DBus",
                "org.freedesktop.DBus",
                "GetConnectionUnixProcessID",
                GLib.Variant("(s)", (name,)),
                None,
                0,
                5000,
                None,
            )
        except GLib.Error:
            return None
        return int(res.unpack()[0])

    def stop_service(self, name: str, bus_config: str) -> bool:
        """結束私有工作階段裡的服務：先確認它是我們這次的 dbus-daemon 啟動的，才送結束訊號。"""
        pid = self.owner_pid(name)
        if pid is None:
            return True
        if not _started_by_our_bus(pid, bus_config):
            raise RuntimeError(f"{name} 不是這個測試工作階段啟動的，不送訊號")
        os.kill(pid, signal.SIGTERM)
        end = time.time() + 15
        while time.time() < end:
            if not _alive(pid):
                return True
            time.sleep(0.1)
        return False


def _stat_fields(pid: int) -> list[str]:
    raw = Path(f"/proc/{pid}/stat").read_text()
    return raw[raw.rindex(")") + 2 :].split()  # 跳過「(程式名稱)」


def _alive(pid: int) -> bool:
    try:
        return _stat_fields(pid)[0] != "Z"
    except OSError:
        return False


def _started_by_our_bus(pid: int, _bus_config: str) -> bool:
    """D-Bus 自動啟動的服務會帶 DBUS_STARTER_ADDRESS：必須是這次私有工作階段的位址，
    而且 HOME 是測試的暫存家目錄（服務會被系統重新認養，不能用父程序判斷）。"""
    try:
        environ = Path(f"/proc/{pid}/environ").read_bytes().split(b"\0")
    except OSError:
        return False
    entries = {e.decode(errors="replace") for e in environ if e}
    address = os.environ.get("DBUS_SESSION_BUS_ADDRESS", "")
    home = os.environ.get("HOME", "")
    return (
        bool(address) and f"DBUS_STARTER_ADDRESS={address}" in entries and f"HOME={home}" in entries
    )


def uri_of(path: str) -> str:
    return Gio.File.new_for_path(path).get_uri()


# ------------------------------------------------------------------ 預覽視窗的情境


def show_and_capture(d: Desktop, p: Preview, uri: str, title: str, name: str, **kw) -> dict:
    settle = kw.pop("settle", 1.5)
    reply_ms = p.show(uri, **kw)
    win = d.find(lambda t: t == title, timeout=20)
    if win is None:
        return {"shown": False, "reply_ms": reply_ms, "titles": [t for _w, t in d.windows()]}
    d.pump(settle)
    info = d.info(win)
    shot = d.screenshot(name)
    titles = sorted(t for _w, t in d.windows())
    colors = colors_in(shot, info)
    return {
        "shown": True,
        "reply_ms": reply_ms,
        "shot": shot,
        "colors": colors,
        "all_titles": titles,
        **info,
    }


def _closes_with(d: Desktop, p: Preview, uri: str, title: str, key: str) -> bool:
    p.show(uri)
    if d.find(lambda t: t == title, timeout=15) is None:
        return False
    d.pump(1.0)
    d.key(key)
    return d.gone(lambda t: t == title, timeout=5)


def _parent_window(d: Desktop) -> tuple[Gtk.Window, int]:
    parent = Gtk.Window(title="父視窗")
    parent.set_default_size(700, 500)
    parent.move(40, 60)
    parent.show_all()
    d.pump(1.0)
    return parent, GdkX11.X11Window.get_xid(parent.get_window())


def scenario_basics(d: Desktop, p: Preview, f: dict) -> dict:
    r: dict = {}
    r["text"] = show_and_capture(d, p, uri_of(f["text"]), Path(f["text"]).name, "text", settle=2.5)
    r["image"] = show_and_capture(d, p, uri_of(f["image"]), Path(f["image"]).name, "image")
    r["pdf"] = show_and_capture(d, p, uri_of(f["pdf"]), Path(f["pdf"]).name, "pdf", settle=2)
    r["video"] = show_and_capture(
        d, p, uri_of(f["video"]), Path(f["video"]).name, "video", settle=3
    )
    p.show(uri_of(f["video"]))  # 同一個檔案再按一次：關閉
    r["same_file_closes"] = d.gone(lambda t: t == Path(f["video"]).name, timeout=5)
    r["audio"] = show_and_capture(
        d, p, uri_of(f["audio"]), Path(f["audio"]).name, "audio", settle=2
    )
    r["fifo"] = show_and_capture(d, p, uri_of(f["fifo"]), Path(f["fifo"]).name, "fifo", settle=2)
    r["folder"] = show_and_capture(d, p, uri_of(f["folder"]), Path(f["folder"]).name, "folder")
    r["invalid"] = show_and_capture(d, p, "not-a-uri", "QUICK_SEEMD", "invalid")
    r["office"] = show_and_capture(
        d, p, uri_of(f["office"]), Path(f["office"]).name, "office", settle=8
    )
    p.close()
    r["close_method"] = d.gone(lambda t: t == Path(f["office"]).name, timeout=5)
    return r


def scenario_keys(d: Desktop, p: Preview, f: dict) -> dict:
    text_uri, title = uri_of(f["text"]), Path(f["text"]).name
    r = {k: _closes_with(d, p, text_uri, title, k) for k in ("space", "Escape", "q")}
    p.show(uri_of(f["image"]))
    win = d.find(lambda t: t == Path(f["image"]).name, timeout=15)
    d.pump(1.0)
    d.key("f")
    d.pump(1.5)
    r["fullscreen"] = d.info(win) if win else None
    r["fullscreen_shot"] = d.screenshot("fullscreen")
    d.key("f")
    d.pump(1.5)
    r["unfullscreen"] = d.info(win) if win else None
    p.close()
    d.gone(lambda t: t == Path(f["image"]).name)
    return r


def scenario_rapid(d: Desktop, p: Preview, f: dict) -> dict:
    r: dict = {}
    p.show(uri_of(f["text"]))
    p.show(uri_of(f["pdf"]))
    win = d.find(lambda t: t == Path(f["pdf"]).name, timeout=15)
    d.pump(1.5)
    r["switch_final_title"] = d.info(win)["title"] if win else None
    r["stale_title_absent"] = not any(t == Path(f["text"]).name for _w, t in d.windows())
    p.close()
    d.gone(lambda t: t == Path(f["pdf"]).name)
    big = uri_of(f["big"])
    p.show(big)
    p.show(big)  # 內容還沒準備好就再按一次
    d.pump(3.0)
    r["double_press_closed"] = not any(t == Path(f["big"]).name for _w, t in d.windows())
    return r


def scenario_transient(d: Desktop, p: Preview, f: dict) -> dict:
    parent, xid = _parent_window(d)
    r = show_and_capture(d, p, uri_of(f["text"]), Path(f["text"]).name, "transient", xid=xid)
    r["parent_xid"] = xid
    r["parent"] = d.info(d.find(lambda t: t == "父視窗", timeout=5))
    d.key("Escape")
    r["closed"] = d.gone(lambda t: t == Path(f["text"]).name)
    parent.destroy()
    d.pump(0.5)
    return r


def scenario_open_button(d: Desktop, p: Preview, f: dict, marker: Path) -> dict:
    p.show(uri_of(f["text"]))
    win = d.find(lambda t: t == Path(f["text"]).name, timeout=15)
    if win is None:
        return {"shown": False}
    d.pump(1.5)
    info = d.info(win)
    d.click(info["x"] + info["width"] - 118, info["y"] + 24)
    end = time.time() + 10
    while time.time() < end and not marker.exists():
        d.pump(0.2)
    opened = marker.read_text("utf-8").strip() if marker.exists() else ""
    return {"opened": opened, "closed": d.gone(lambda t: t == Path(f["text"]).name, timeout=5)}


def run_preview(cfg: dict, d: Desktop) -> dict:
    p = Preview()
    f = cfg["files"]
    report = {"basics": scenario_basics(d, p, f)}
    report["keys"] = scenario_keys(d, p, f)
    report["rapid"] = scenario_rapid(d, p, f)
    report["transient"] = scenario_transient(d, p, f)
    report["open_button"] = scenario_open_button(d, p, f, Path(cfg["marker"]))
    report["service_stopped"] = p.stop_service(PREVIEW[0], cfg["bus_config"])
    return report


# ------------------------------------------------------------------ Nemo 的情境


def ocr_find(png: str, rect: dict, word: str) -> tuple[int, int] | None:
    """在畫面的某個範圍裡（放大兩倍後）用 tesseract 找某個字，回傳它在畫面上的中心點。"""
    from PIL import Image  # noqa: PLC0415

    scale = 2
    crop = Path(png).with_name(Path(png).stem + "-crop.png")
    with Image.open(png) as img:
        box = (rect["x"], rect["y"], rect["x"] + rect["width"], rect["y"] + rect["height"])
        part = img.convert("RGB").crop(box)
        part.resize((part.width * scale, part.height * scale), Image.LANCZOS).save(crop)
    res = subprocess.run(
        ["/usr/bin/tesseract", str(crop), "stdout", "-l", "eng", "tsv"],
        capture_output=True,
        text=True,
        check=False,
    )
    for line in res.stdout.splitlines()[1:]:
        cols = line.split("\t")
        if len(cols) == 12 and word in cols[11].upper():
            left, top, width, height = (int(v) for v in cols[6:10])
            return (
                rect["x"] + (left + width // 2) // scale,
                rect["y"] + (top + height // 2) // scale,
            )
    return None


def _wait_log(path: Path, timeout: float, d: Desktop) -> list:
    end = time.time() + timeout
    while time.time() < end:
        if path.exists() and path.read_text("utf-8").strip():
            return [json.loads(ln) for ln in path.read_text("utf-8").splitlines() if ln]
        d.pump(0.2)
    return []


def nemo_preview_steps(d: Desktop, nemo_win, cfg: dict) -> dict:  # noqa: ANN001
    r: dict = {"nemo": d.info(nemo_win)}
    info = r["nemo"]
    d.click(info["x"] + info["width"] * 3 // 4, info["y"] + info["height"] * 3 // 4)
    d.pump(0.5)
    d.click(info["x"] + info["width"] * 3 // 4, info["y"] + info["height"] * 3 // 4)
    d.pump(0.5)
    d.key("Home")
    d.pump(0.8)
    r["selected_shot"] = d.screenshot("nemo-selected")
    d.key("space")
    title = Path(cfg["first"]).name
    win = d.find(lambda t: t == title, timeout=20)
    r["preview_shown"] = win is not None
    if win is not None:
        d.pump(2.0)
        r["preview"] = d.info(win)
        r["preview_shot"] = d.screenshot("nemo-preview")
        d.key("space")
        r["preview_closed"] = d.gone(lambda t: t == title, timeout=5)
    return r


def nemo_menu_steps(d: Desktop, cfg: dict) -> dict:
    r: dict = {}
    d.pump(1.0)
    d.key("Menu")
    d.pump(1.5)
    shot = d.screenshot("nemo-menu")
    r["menu_shot"] = shot
    menus = d.popups()
    r["popups"] = menus
    if not menus:
        return r
    spot = ocr_find(shot, menus[0], "SEEMD") or ocr_find(shot, menus[0], "QUICK")
    r["menu_item_found"] = spot is not None
    if spot is None:
        return r
    d.move(*spot)
    d.pump(1.5)
    r["submenu_shot"] = d.screenshot("nemo-submenu")
    d.key("Right")
    d.pump(0.5)
    d.key("Return")
    r["agent_calls"] = _wait_log(Path(cfg["agent_log"]), 15, d)
    return r


def run_nemo(cfg: dict, d: Desktop) -> dict:
    nemo = subprocess.Popen(  # noqa: S603
        ["/usr/bin/nemo", "--no-desktop", cfg["folder"]],
        stdout=open(cfg["nemo_log"], "w"),  # noqa: SIM115
        stderr=subprocess.STDOUT,
    )
    report: dict = {}
    try:
        folder = Path(cfg["folder"]).name
        win = d.find(lambda t: folder in t, timeout=30)
        report["nemo_started"] = win is not None
        if win is not None:
            d.pump(2.0)
            report["start_shot"] = d.screenshot("nemo-start")
            report.update(nemo_preview_steps(d, win, cfg))
            report.update(nemo_menu_steps(d, cfg))
    finally:
        d.key("Escape")
        report["service_stopped"] = Preview().stop_service(PREVIEW[0], cfg["bus_config"])
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
    GLib.timeout_add_seconds(180, loop.quit)
    loop.run()


# ------------------------------------------------------------------ 進入點


def main(argv: list[str]) -> int:
    _check_display()
    if argv[1] == "fake-agent":
        fake_agent(argv[2])
        return 0
    cfg = json.loads(Path(argv[2]).read_text("utf-8"))
    out = Path(cfg["outdir"])
    out.mkdir(parents=True, exist_ok=True)
    openbox = subprocess.Popen(["/usr/bin/openbox", "--sm-disable"])  # noqa: S603
    report: dict = {}
    try:
        time.sleep(1.0)
        d = Desktop(out)
        report = run_preview(cfg, d) if argv[1] == "preview" else run_nemo(cfg, d)
    except Exception:  # noqa: BLE001 — 把錯誤寫進報告，測試端會顯示
        report["error"] = traceback.format_exc()
    finally:
        openbox.terminate()
        openbox.wait(timeout=10)
        Path(cfg["report"]).write_text(json.dumps(report, ensure_ascii=False, indent=1), "utf-8")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
