"""背景程式的畫面測試工具（只能在 :120 以上的隔離畫面使用）。

用法（各自是獨立程序，GTK3／GTK4 不會混在同一個程序）：
  agent_gui_driver.py shot 輸出.png
  agent_gui_driver.py drag 起點x 起點y 終點x 終點y 按鍵 按住秒數 [放下x 放下y]
  agent_gui_driver.py click x y
  agent_gui_driver.py clipboard 格式          把剪貼簿內容印到標準輸出
  agent_gui_driver.py source3 檔案 x y        GTK3 拖曳來源視窗
  agent_gui_driver.py source4 檔案            GTK4 拖曳來源視窗（放在左上角）
"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

_display = os.environ.get("DISPLAY", "")
_num = _display.lstrip(":").split(".")[0]
if not _num.isdigit() or int(_num) < 120:
    sys.exit("只能在 :120 以上的隔離畫面使用")


def _xtest():
    from Xlib import X, XK, display  # noqa: PLC0415
    from Xlib.ext import xtest  # noqa: PLC0415

    d = display.Display()

    def move(x: float, y: float) -> None:
        xtest.fake_input(d, X.MotionNotify, x=int(x), y=int(y))
        d.sync()

    def button(down: bool, b: int = 1) -> None:
        xtest.fake_input(d, X.ButtonPress if down else X.ButtonRelease, b)
        d.sync()

    def key(name: str, down: bool) -> None:
        code = d.keysym_to_keycode(XK.string_to_keysym(name))
        xtest.fake_input(d, X.KeyPress if down else X.KeyRelease, code)
        d.sync()

    return move, button, key


def _glide(move, x0, y0, x1, y1, steps=20, dt=0.03) -> None:  # noqa: ANN001
    for i in range(1, steps + 1):
        move(x0 + (x1 - x0) * i / steps, y0 + (y1 - y0) * i / steps)
        time.sleep(dt)


def drag(args: list[str]) -> None:
    sx, sy, ex, ey = (int(a) for a in args[:4])
    keyname, hold = args[4], float(args[5])
    target = (int(args[6]), int(args[7])) if len(args) >= 8 else None
    move, button, key = _xtest()
    move(sx, sy)
    time.sleep(0.2)
    button(True)
    _glide(move, sx, sy, ex, ey)
    time.sleep(0.4)
    if keyname != "none":
        key(keyname, True)
        time.sleep(hold)
        key(keyname, False)
    time.sleep(0.8)
    if target:
        _glide(move, ex, ey, target[0], target[1], steps=10)
        time.sleep(0.5)
    if len(args) >= 9 and args[8] == "shot":
        shot([args[9]])
    button(False)
    time.sleep(1.0)


def click(args: list[str]) -> None:
    move, button, _key = _xtest()
    move(int(args[0]), int(args[1]))
    time.sleep(0.2)
    button(True)
    time.sleep(0.05)
    button(False)


def shot(args: list[str]) -> None:
    import gi  # noqa: PLC0415

    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk  # noqa: PLC0415

    root = Gdk.get_default_root_window()
    w, h = root.get_width(), root.get_height()
    Gdk.pixbuf_get_from_window(root, 0, 0, w, h).savev(args[0], "png", [], [])


def clipboard(args: list[str]) -> None:
    import gi  # noqa: PLC0415

    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk, Gtk  # noqa: PLC0415

    cb = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
    data = cb.wait_for_contents(Gdk.Atom.intern(args[0], False))
    out = data.get_data() if data is not None else b""
    sys.stdout.buffer.write(bytes(out or b""))


def source3(args: list[str]) -> None:
    import gi  # noqa: PLC0415

    gi.require_version("Gtk", "3.0")
    gi.require_version("Gdk", "3.0")
    from gi.repository import Gdk, Gtk  # noqa: PLC0415

    uri = Path(os.path.abspath(args[0])).as_uri()
    win = Gtk.Window()
    win.set_default_size(200, 200)
    win.move(int(args[1]), int(args[2]))
    box = Gtk.EventBox()
    box.add(Gtk.Label(label="GTK3 拖曳來源"))
    win.add(box)
    targets = [Gtk.TargetEntry.new("text/uri-list", 0, 0)]
    actions = Gdk.DragAction.COPY | Gdk.DragAction.MOVE
    box.drag_source_set(Gdk.ModifierType.BUTTON1_MASK, targets, actions)
    box.connect("drag-data-get", lambda _w, _c, data, _i, _t: data.set_uris([uri]))
    box.connect("drag-data-delete", lambda *_a: print("DELETE-REQUESTED", flush=True))
    win.connect("destroy", Gtk.main_quit)
    win.show_all()
    Gtk.main()


def source4(args: list[str]) -> None:
    import gi  # noqa: PLC0415

    gi.require_version("Gtk", "4.0")
    gi.require_version("Gdk", "4.0")
    from gi.repository import Gdk, Gio, GObject, Gtk  # noqa: PLC0415

    uri = Path(os.path.abspath(args[0])).as_uri()
    app = Gtk.Application(application_id=None, flags=Gio.ApplicationFlags.NON_UNIQUE)

    def activate(a: Gtk.Application) -> None:
        win = Gtk.ApplicationWindow(application=a)
        win.set_default_size(200, 200)
        label = Gtk.Label(label="GTK4 拖曳來源")
        win.set_child(label)
        src = Gtk.DragSource()
        src.set_actions(Gdk.DragAction.COPY)

        def prepare(_s, _x, _y):  # noqa: ANN001, ANN202
            files = Gdk.FileList.new_from_list([Gio.File.new_for_uri(uri)])
            return Gdk.ContentProvider.new_for_value(GObject.Value(Gdk.FileList, files))

        src.connect("prepare", prepare)
        label.add_controller(src)
        win.present()

    app.connect("activate", activate)
    app.run([])


COMMANDS = {
    "drag": drag,
    "click": click,
    "shot": shot,
    "clipboard": clipboard,
    "source3": source3,
    "source4": source4,
}

if __name__ == "__main__":
    COMMANDS[sys.argv[1]](sys.argv[2:])
