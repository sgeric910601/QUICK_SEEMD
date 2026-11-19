"""畫面測試用的工具：找視窗、截圖、量畫面內容、模擬按鍵與滑鼠、等條件成立（有期限）。

只在隔離畫面（DISPLAY ≥ :120）使用。等待一律是「輪詢條件、到期限就放棄」，不用固定秒數；
唯一的固定間隔是模擬按鍵／滑鼠時按下與放開之間的極短時間（輸入本身的節奏）。
"""

from __future__ import annotations

import hashlib
import os
import signal
import subprocess
import time
from collections.abc import Callable
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gio, GLib, Gtk  # noqa: E402
from Xlib import X, XK  # noqa: E402
from Xlib import display as xdisplay  # noqa: E402
from Xlib.ext import xtest  # noqa: E402

PREVIEW = ("org.nemo.Preview", "/org/nemo/Preview", "org.nemo.Preview")
POLL = 0.1
INPUT_GAP = 0.05  # 按下與放開之間（輸入的節奏，不是等待）


def check_display() -> None:
    number = int(os.environ["DISPLAY"].lstrip(":").split(".")[0])
    if number < 120:
        raise SystemExit("只能在隔離畫面（:120 以上）執行")


def wait_until(cond: Callable[[], object], timeout: float, interval: float = POLL) -> object:
    """反覆檢查 cond，成立（回傳真值）就回傳該值；到期限回傳 None。期間持續處理 GTK 事件。"""
    end = time.time() + timeout
    while True:
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)
        value = cond()
        if value:
            return value
        if time.time() >= end:
            return None
        time.sleep(interval)


class Region:
    """畫面上一塊區域的像素（RGB）。"""

    def __init__(self, info: dict) -> None:
        root = Gdk.get_default_root_window()
        pb = Gdk.pixbuf_get_from_window(root, info["x"], info["y"], info["width"], info["height"])
        from PIL import Image  # noqa: PLC0415

        mode = "RGBA" if pb.get_has_alpha() else "RGB"
        self.image = Image.frombytes(
            mode,
            (pb.get_width(), pb.get_height()),
            pb.get_pixels(),
            "raw",
            mode,
            pb.get_rowstride(),
        ).convert("RGB")

    @property
    def digest(self) -> str:
        return hashlib.sha1(self.image.tobytes(), usedforsecurity=False).hexdigest()

    @property
    def colors(self) -> int:
        found = self.image.getcolors(maxcolors=1 << 20)
        return len(found) if found else 1 << 20

    def dark_ratio(self, limit: int = 60) -> float:
        gray = self.image.convert("L")
        histogram = gray.histogram()
        return sum(histogram[:limit]) / max(1, gray.width * gray.height)


class Desktop:
    def __init__(self, outdir: Path) -> None:
        self.x = xdisplay.Display()
        self.root = self.x.screen().root
        self.out = outdir
        self.atom = self.x.intern_atom
        self.seen_titles: set[str] = set()

    # ------------------------------------------------------------ 視窗
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
                self.seen_titles.add(title)
            stack.extend(children)
        return found

    def find(self, match: Callable[[str], bool], timeout: float = 15.0):  # noqa: ANN201
        def look():  # noqa: ANN202
            return next((w for w, t in self.windows() if match(t)), None)

        return wait_until(look, timeout)

    def gone(self, match: Callable[[str], bool], timeout: float = 5.0) -> bool:
        return bool(wait_until(lambda: not any(match(t) for _w, t in self.windows()), timeout))

    def wm_ready(self, timeout: float = 10.0) -> bool:
        check = self.atom("_NET_SUPPORTING_WM_CHECK")
        return bool(
            wait_until(lambda: self.root.get_full_property(check, X.AnyPropertyType), timeout)
        )

    def focused(self, win) -> bool:  # noqa: ANN001
        """輸入焦點在 win（或它的子視窗）上。"""
        try:
            node = self.x.get_input_focus().focus
            while node and node != X.NONE and node != self.root:
                if node.id == win.id:
                    return True
                node = node.query_tree().parent
        except Exception:  # noqa: BLE001
            return False
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

    # ------------------------------------------------------------ 內容
    def settle(
        self, win, min_colors: int, stable: bool, timeout: float = 25.0
    ) -> int:  # noqa: ANN001
        """等視窗內容畫好：顏色數夠多，位置大小連續兩次相同；stable 時畫面也要完全相同。

        回傳最後量到的顏色數（到期限也照樣回傳，由測試判斷夠不夠）。
        """
        history: list[str] = []

        def ready() -> int:
            info = self.info(win)
            region = Region(info)
            mark = f"{info['x']},{info['y']},{info['width']},{info['height']}"
            mark += region.digest if stable else ""
            ok = region.colors >= min_colors and history[-1:] == [mark]
            history.append(mark)
            return region.colors if ok else 0

        result = wait_until(ready, timeout, interval=0.25)
        return int(result) if result else Region(self.info(win)).colors

    def screenshot(self, name: str) -> str:
        root = Gdk.get_default_root_window()
        pb = Gdk.pixbuf_get_from_window(root, 0, 0, root.get_width(), root.get_height())
        path = self.out / f"{name}.png"
        pb.savev(str(path), "png", [], [])
        return str(path)

    # ------------------------------------------------------------ 輸入
    def key(self, name: str) -> None:
        code = self.x.keysym_to_keycode(XK.string_to_keysym(name))
        xtest.fake_input(self.x, X.KeyPress, code)
        self.x.sync()
        time.sleep(INPUT_GAP)
        xtest.fake_input(self.x, X.KeyRelease, code)
        self.x.sync()

    def move(self, x: int, y: int) -> None:
        xtest.fake_input(self.x, X.MotionNotify, x=int(x), y=int(y))
        self.x.sync()

    def click(self, x: int, y: int, button: int = 1) -> None:
        self.move(x, y)
        time.sleep(INPUT_GAP)
        xtest.fake_input(self.x, X.ButtonPress, button)
        self.x.sync()
        time.sleep(INPUT_GAP)
        xtest.fake_input(self.x, X.ButtonRelease, button)
        self.x.sync()


# ------------------------------------------------------------------ D-Bus


class Preview:
    def __init__(self) -> None:
        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)

    def show(self, uri: str, xid: int = 0, close: bool = True) -> float:
        start = time.time()
        params = GLib.Variant("(sib)", (uri, xid, close))
        self.bus.call_sync(*PREVIEW, "ShowFile", params, None, 0, 30000, None)
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

    def stop_service(self, name: str) -> bool:
        """結束私有工作階段裡的服務：先確認是這次的 dbus-daemon 啟動的，才送結束訊號。"""
        pid = self.owner_pid(name)
        if pid is None:
            return True
        if not started_by_our_bus(pid):
            raise RuntimeError(f"{name} 不是這個測試工作階段啟動的，不送訊號")
        os.kill(pid, signal.SIGTERM)
        return bool(wait_until(lambda: not _alive(pid), 15))


def _stat_fields(pid: int) -> list[str]:
    raw = Path(f"/proc/{pid}/stat").read_text()
    return raw[raw.rindex(")") + 2 :].split()  # 跳過「(程式名稱)」


def _alive(pid: int) -> bool:
    try:
        return _stat_fields(pid)[0] != "Z"
    except OSError:
        return False


def started_by_our_bus(pid: int) -> bool:
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
