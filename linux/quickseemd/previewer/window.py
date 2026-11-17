"""預覽視窗（GTK3）：標題列、內容區、按鍵、掛在 Nemo 視窗上方置中。

- 標題列：檔名、類型，右邊「用預設程式開啟」（開啟後關閉預覽）。
- 對話框類型、設成 Nemo 視窗的附屬視窗（transient-for），在它上方置中；
  視窗代碼無效時在螢幕置中。
- 按鍵：空白鍵、Esc、q 關閉；f／F11 全螢幕；Ctrl+W 關閉。
- 大小：內容想要的大小，限制在螢幕的 85% 以內。
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkX11", "3.0")
from gi.repository import Gdk, GdkX11, Gio, GLib, Gtk  # noqa: E402

from ..core import errors  # noqa: E402
from . import content as content_mod  # noqa: E402
from .content import Content  # noqa: E402
from .geometry import Rect, center_on, clamp_size, fit_media, outer_size  # noqa: E402

log = logging.getLogger(__name__)

APP_ICON = "dev.quickseemd.App"
APP_TITLE = "QUICK_SEEMD"
OPEN_LABEL = "用預設程式開啟"
LOADING_TEXT = "正在載入…"
CONVERTING_TEXT = "正在轉換文件…"
DEFAULT_SIZE = (920, 700)
AUDIO_SIZE = (520, 320)
VIDEO_SIZE = (960, 540)
OFFICE_SIZE = (900, 1200)
CLOSE_KEYS = frozenset({Gdk.KEY_space, Gdk.KEY_Escape, Gdk.KEY_q, Gdk.KEY_Q})
FULLSCREEN_KEYS = frozenset({Gdk.KEY_f, Gdk.KEY_F, Gdk.KEY_F11})
CLOSE_WITH_CTRL = frozenset({Gdk.KEY_w, Gdk.KEY_W})
_MODIFIERS = (
    Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.MOD1_MASK | Gdk.ModifierType.SUPER_MASK
)


def loading_widget(text: str) -> Gtk.Widget:
    box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    box.set_valign(Gtk.Align.CENTER)
    box.set_halign(Gtk.Align.CENTER)
    spinner = Gtk.Spinner()
    spinner.set_size_request(32, 32)
    spinner.start()
    box.pack_start(spinner, False, False, 0)
    box.pack_start(Gtk.Label(label=text), False, False, 0)
    return box


def _trapped(display: Gdk.Display, action: Callable[[], object]) -> object | None:
    """對別人的視窗做 X 操作時攔下錯誤（Nemo 視窗可能已經關了），不讓程式當掉。"""
    GdkX11.X11Display.error_trap_push(display)
    try:
        result = action()
    finally:
        code = GdkX11.X11Display.error_trap_pop(display)
    return None if code else result


def foreign_window(display: Gdk.Display, xid: int) -> Gdk.Window | None:
    if not xid or xid < 0:
        return None
    return _trapped(display, lambda: GdkX11.X11Window.foreign_new_for_display(display, xid))


class PreviewWindow(Gtk.Window):
    def __init__(self, app: Gtk.Application, on_close: Callable[[], None]) -> None:
        super().__init__(application=app, title=APP_TITLE)
        self._on_close = on_close
        self._content: Content | None = None
        self._parent: Gdk.Window | None = None
        self._body: Gtk.Widget | None = None
        self._fullscreen = False
        self.set_type_hint(Gdk.WindowTypeHint.DIALOG)
        self.set_icon_name(APP_ICON)
        self._header = Gtk.HeaderBar(show_close_button=True, title=APP_TITLE)
        self._open = Gtk.Button(label=OPEN_LABEL)
        self._open.connect("clicked", self._on_open)
        self._header.pack_end(self._open)
        self.set_titlebar(self._header)
        self.connect("key-press-event", self._on_key)
        self.connect("delete-event", self._on_delete)
        self.connect("window-state-event", self._on_window_state)

    # ------------------------------------------------------------ 父視窗與位置

    def attach_to(self, xid: int) -> None:
        self.realize()
        parent = foreign_window(self.get_display(), xid)
        self._parent = parent
        if parent is not None:
            _trapped(self.get_display(), lambda: self.get_window().set_transient_for(parent))

    def _parent_rect(self) -> Rect | None:
        if self._parent is None:
            return None
        r = _trapped(self.get_display(), self._parent.get_frame_extents)
        if r is None or r.width <= 1:
            return None
        return Rect(r.x, r.y, r.width, r.height)

    def _monitor(self) -> Gdk.Monitor | None:
        display = self.get_display()
        if self._parent is not None:
            mon = _trapped(display, lambda: display.get_monitor_at_window(self._parent))
            if mon is not None:
                return mon
        seat = display.get_default_seat()
        pointer = seat.get_pointer() if seat is not None else None
        if pointer is not None:
            _screen, x, y = pointer.get_position()
            return display.get_monitor_at_point(x, y)
        return display.get_primary_monitor() or display.get_monitor(0)

    def workarea(self) -> Rect:
        mon = self._monitor()
        if mon is None:
            return Rect(0, 0, 1280, 800)
        r = mon.get_workarea()
        return Rect(r.x, r.y, r.width, r.height)

    def _present(self) -> None:
        self.show_all()
        self.present_with_time(GdkX11.x11_get_server_time(self.get_window()))

    def _place(self, size: tuple[int, int]) -> None:
        if self._fullscreen:  # 全螢幕時換檔案就維持全螢幕
            self._present()
            return
        workarea = self.workarea()
        width, height = clamp_size(size, workarea)
        x, y = center_on(outer_size((width, height)), self._parent_rect(), workarea)
        if self.get_visible():
            self.resize(width, height)
        else:
            self.set_default_size(width, height)
        self.move(x, y)
        self._present()

    def present_now(self) -> None:
        """同一個檔案又要求顯示：只把視窗叫到前面。"""
        if self.get_visible():
            self._present()

    # ------------------------------------------------------------ 內容

    @property
    def content(self) -> Content | None:
        return self._content

    def _set_header(self, title: str, subtitle: str) -> None:
        self._header.set_title(title or APP_TITLE)
        self._header.set_subtitle(subtitle or None)
        self.set_title(title or APP_TITLE)

    def _set_body(self, widget: Gtk.Widget) -> None:
        if self._body is not None:
            self.remove(self._body)
            self._body.destroy()
        self._body = widget
        self.add(widget)
        widget.show_all()

    def show_loading(self, title: str, text: str = LOADING_TEXT) -> None:
        self._content = None
        self._set_header(title, "")
        self._open.set_sensitive(False)
        self._set_body(loading_widget(text))
        self._place(DEFAULT_SIZE)

    def show_content(self, content: Content) -> None:
        self._content = content
        self._set_header(content.title, content.subtitle)
        self._open.set_sensitive(content.can_open)
        try:
            widget, size = self._build(content)
        except errors.QSError as exc:
            widget, size = self._error_view(content, str(exc))
        self._set_body(widget)
        self._place(size)

    def _build(self, content: Content) -> tuple[Gtk.Widget, tuple[int, int]]:
        view = content.view
        if view == content_mod.IMAGE:
            from .imageview import ImageView  # noqa: PLC0415

            return ImageView(content.payload), fit_media(content.natural_size, self.workarea())
        if view == content_mod.PDF:
            from .pdfview import PdfView, pdf_window_size  # noqa: PLC0415

            return PdfView(content.payload), pdf_window_size(content.natural_size)
        if view in (content_mod.VIDEO, content_mod.AUDIO):
            return self._media_view(content)
        if view == content_mod.OFFICE:
            return loading_widget(CONVERTING_TEXT), OFFICE_SIZE
        from .pageview import PageView  # noqa: PLC0415

        return PageView(content.document), content.document.preferred_size

    def _media_view(self, content: Content) -> tuple[Gtk.Widget, tuple[int, int]]:
        from .mediaview import MediaView  # noqa: PLC0415

        video = content.view == content_mod.VIDEO
        widget = MediaView(content.path, video=video, on_natural_size=self._on_media_size)
        if not video:
            return widget, AUDIO_SIZE
        return widget, fit_media(VIDEO_SIZE, self.workarea())

    def _error_view(self, content: Content, message: str) -> tuple[Gtk.Widget, tuple[int, int]]:
        from . import fallback  # noqa: PLC0415
        from .pageview import PageView  # noqa: PLC0415

        doc = fallback.error_page(content.title, message)
        return PageView(doc), doc.preferred_size

    def _on_media_size(self, width: int, height: int) -> None:
        """影片的實際大小知道後，照比例調整視窗。"""
        if self._fullscreen or self._content is None or self._content.view != content_mod.VIDEO:
            return
        self._place(fit_media((width, height), self.workarea()))

    # ------------------------------------------------------------ 使用者操作

    def toggle_fullscreen(self) -> None:
        if self._fullscreen:
            self.unfullscreen()
        else:
            self.fullscreen()

    def _on_window_state(self, _widget: Gtk.Widget, event: Gdk.EventWindowState) -> bool:
        self._fullscreen = bool(event.new_window_state & Gdk.WindowState.FULLSCREEN)
        return False

    def _on_key(self, _widget: Gtk.Widget, event: Gdk.EventKey) -> bool:
        mods = event.state & _MODIFIERS
        if mods:
            if mods == Gdk.ModifierType.CONTROL_MASK and event.keyval in CLOSE_WITH_CTRL:
                self._on_close()
                return True
            return False
        if event.keyval in CLOSE_KEYS:
            self._on_close()
            return True
        if event.keyval in FULLSCREEN_KEYS:
            self.toggle_fullscreen()
            return True
        return False

    def _on_delete(self, _widget: Gtk.Widget, _event: Gdk.Event) -> bool:
        self._on_close()
        return True

    def _on_open(self, _button: Gtk.Button) -> None:
        if self._content is None or not self._content.can_open:
            return
        uri = self._content.target.local_uri
        launch = self.get_display().get_app_launch_context()
        launch.set_timestamp(Gtk.get_current_event_time())
        try:
            Gio.AppInfo.launch_default_for_uri(uri, launch)
        except GLib.Error as exc:
            log.warning("無法用預設程式開啟 %s：%s", uri, exc.message)
            self._header.set_subtitle(f"無法開啟：{exc.message}")
            return
        self._on_close()
