"""把輪盤視窗、協調器與 X11 拖曳監看接起來（只在 X11 啟用，Wayland 上安靜地略過）。"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk  # noqa: E402

from .wheel_controller import Dispatch, WheelController  # noqa: E402
from .wheel_window import ChooseFn, WheelWindow  # noqa: E402
from .x11_drag import XDragWatcher  # noqa: E402

log = logging.getLogger(__name__)


def _x11_xid(widget: Gtk.Widget) -> int | None:
    """這個元件的 X 視窗代碼；不是 X11 就回傳 None。"""
    try:
        gi.require_version("GdkX11", "3.0")
        from gi.repository import GdkX11  # noqa: PLC0415
    except (ImportError, ValueError):
        return None
    display = Gdk.Display.get_default()
    if not isinstance(display, GdkX11.X11Display):
        return None
    window = widget.get_window()
    return int(GdkX11.X11Window.get_xid(window)) if window is not None else None


class Wheel:
    def __init__(
        self,
        on_choose: ChooseFn,
        settings_getter: Callable[[], Mapping[str, object]],
        dispatch: Dispatch,
    ) -> None:
        self._on_choose = on_choose
        self._settings = settings_getter
        self._dispatch = dispatch
        self.window: WheelWindow | None = None
        self.watcher: XDragWatcher | None = None

    def start(self, own_widget: Gtk.Widget, *, enabled: bool) -> bool:
        own_xid = _x11_xid(own_widget)
        if own_xid is None:
            log.info("不是 X11 畫面，拖曳輪盤停用")
            return False
        self.window = WheelWindow(self._on_choose)
        controller = WheelController(self.window, self._settings, dispatch=self._dispatch)
        self.watcher = XDragWatcher(controller, own_xid)
        self.watcher.set_enabled(enabled)
        return self.watcher.start()

    def set_enabled(self, enabled: bool) -> None:
        if self.watcher is not None:
            self.watcher.set_enabled(enabled)
        if not enabled and self.window is not None:
            self.window.hide()
