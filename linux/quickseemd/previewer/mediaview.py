"""影片與音訊的播放（GStreamer playbin＋gtksink，GTK3）。

- 影片畫面用 gtksink 的元件；音訊（或沒有畫面的檔案）顯示圖示，畫面送到 fakesink，
  避免 playbin 自己另外開視窗。
- 下方控制列：播放／暫停按鈕、可拖曳的進度條、時間。
- 開啟後自動播放；播完停在最後，再按播放從頭開始。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gst", "1.0")
from gi.repository import Gio, GLib, Gst, Gtk  # noqa: E402

from ..core import errors  # noqa: E402
from .formatting import format_duration  # noqa: E402

log = logging.getLogger(__name__)

UPDATE_INTERVAL_MS = 250
PLAY_ICON = "media-playback-start-symbolic"
PAUSE_ICON = "media-playback-pause-symbolic"
AUDIO_ICON = "audio-x-generic"

SizeCallback = Callable[[int, int], None]


def _ensure_gst() -> None:
    if not Gst.is_initialized():
        Gst.init(None)


def _make(factory: str) -> Gst.Element:
    element = Gst.ElementFactory.make(factory, None)
    if element is None:
        raise errors.QSError(f"缺少 GStreamer 元件「{factory}」，無法播放。")
    return element


def video_size_from_caps(caps: Gst.Caps | None) -> tuple[int, int] | None:
    """從影像格式讀出顯示用的寬高（套用像素比例）。"""
    if caps is None or caps.get_size() == 0:
        return None
    s = caps.get_structure(0)
    ok_w, width = s.get_int("width")
    ok_h, height = s.get_int("height")
    if not (ok_w and ok_h) or width <= 0 or height <= 0:
        return None
    ok_par, num, den = s.get_fraction("pixel-aspect-ratio")
    if ok_par and num > 0 and den > 0:
        width = round(width * num / den)
    return width, height


class MediaView(Gtk.Box):
    def __init__(self, path: Path, *, video: bool, on_natural_size: SizeCallback | None) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        _ensure_gst()
        self._on_natural_size = on_natural_size
        self._sized = False
        self._ended = False
        self._seeking = False
        self._playbin = _make("playbin")
        self._playbin.set_property("uri", Gio.File.new_for_path(str(path)).get_uri())
        self.pack_start(self._picture(video, path), True, True, 0)
        self.pack_start(self._controls(), False, False, 0)
        bus = self._playbin.get_bus()
        bus.add_signal_watch()
        self._bus_handler = bus.connect("message", self._on_message)
        self._timer = GLib.timeout_add(UPDATE_INTERVAL_MS, self._update)
        self.connect("destroy", self._on_destroy)
        self._playbin.set_state(Gst.State.PLAYING)

    # ------------------------------------------------------------ 畫面與控制列

    def _picture(self, video: bool, path: Path) -> Gtk.Widget:
        if video:
            sink = _make("gtksink")
            self._playbin.set_property("video-sink", sink)
            return sink.get_property("widget")
        self._playbin.set_property("video-sink", _make("fakesink"))
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12, valign=Gtk.Align.CENTER)
        icon = Gtk.Image.new_from_icon_name(AUDIO_ICON, Gtk.IconSize.DIALOG)
        icon.set_pixel_size(96)
        box.pack_start(icon, False, False, 0)
        box.pack_start(Gtk.Label(label=path.name, wrap=True, max_width_chars=40), False, False, 0)
        return box

    def _controls(self) -> Gtk.Widget:
        bar = Gtk.Box(spacing=8, margin=8)
        self._button = Gtk.Button.new_from_icon_name(PAUSE_ICON, Gtk.IconSize.BUTTON)
        self._button.set_relief(Gtk.ReliefStyle.NONE)
        self._button.connect("clicked", self._on_toggle)
        self._scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 1, 0.1)
        self._scale.set_draw_value(False)
        self._scale.connect("change-value", self._on_seek)
        self._time = Gtk.Label(label="0:00 / 0:00")
        bar.pack_start(self._button, False, False, 0)
        bar.pack_start(self._scale, True, True, 0)
        bar.pack_start(self._time, False, False, 0)
        return bar

    # ------------------------------------------------------------ 播放控制

    @property
    def playing(self) -> bool:
        _ret, state, _pending = self._playbin.get_state(0)
        return state == Gst.State.PLAYING

    def toggle(self) -> None:
        if self.playing:
            self._playbin.set_state(Gst.State.PAUSED)
            self._set_icon(PLAY_ICON)
            return
        if self._ended:
            self._ended = False
            self._playbin.seek_simple(Gst.Format.TIME, Gst.SeekFlags.FLUSH, 0)
        self._playbin.set_state(Gst.State.PLAYING)
        self._set_icon(PAUSE_ICON)

    def _set_icon(self, name: str) -> None:
        self._button.set_image(Gtk.Image.new_from_icon_name(name, Gtk.IconSize.BUTTON))

    def _on_toggle(self, _button: Gtk.Button) -> None:
        self.toggle()

    def _on_seek(self, _scale: Gtk.Scale, _scroll: Gtk.ScrollType, value: float) -> bool:
        self._ended = False
        flags = Gst.SeekFlags.FLUSH | Gst.SeekFlags.KEY_UNIT
        self._playbin.seek_simple(Gst.Format.TIME, flags, int(max(value, 0) * Gst.SECOND))
        return False

    def _update(self) -> bool:
        ok_d, duration = self._playbin.query_duration(Gst.Format.TIME)
        ok_p, position = self._playbin.query_position(Gst.Format.TIME)
        if ok_d and duration > 0:
            total = duration / Gst.SECOND
            current = (position / Gst.SECOND) if ok_p else 0.0
            self._scale.set_range(0, total)
            self._scale.set_value(current)
            self._time.set_text(f"{format_duration(current)} / {format_duration(total)}")
        return True

    # ------------------------------------------------------------ GStreamer 訊息

    def _on_message(self, _bus: Gst.Bus, message: Gst.Message) -> None:
        kind = message.type
        if kind == Gst.MessageType.ERROR:
            err, _debug = message.parse_error()
            self._show_error(err.message)
        elif kind == Gst.MessageType.EOS:
            self._ended = True
            self._playbin.set_state(Gst.State.PAUSED)
            self._set_icon(PLAY_ICON)
        elif kind == Gst.MessageType.ASYNC_DONE and not self._sized:
            self._report_size()

    def _report_size(self) -> None:
        self._sized = True
        pad = self._playbin.emit("get-video-pad", 0)
        size = video_size_from_caps(pad.get_current_caps()) if pad is not None else None
        if size is not None and self._on_natural_size is not None:
            self._on_natural_size(*size)

    def _show_error(self, message: str) -> None:
        log.warning("無法播放：%s", message)
        self._playbin.set_state(Gst.State.NULL)
        for child in self.get_children():
            self.remove(child)
        label = Gtk.Label(label=f"無法播放這個檔案：{message}", wrap=True, margin=24)
        self.pack_start(label, True, True, 0)
        label.show()

    def _on_destroy(self, _widget: Gtk.Widget) -> None:
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0
        bus = self._playbin.get_bus()
        bus.disconnect(self._bus_handler)
        bus.remove_signal_watch()
        self._playbin.set_state(Gst.State.NULL)
