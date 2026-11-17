"""剪輯影片／音訊的畫面（對應原版 MTTrimEditor／MTRangeBar）。

單一檔案時取代檔案縮圖區：播放預覽、縮圖帶（影片）或波形（音訊）上的雙把手範圍、時間欄位。
開始／結束寫回選項 `start`／`end`（時間字串，見 trim_model）。
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

import cairo
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import GLib, Gtk  # noqa: E402

from ..options_model import format_time, parse_time  # noqa: E402
from ..widgets import hbox, icon, label, secondary, vbox  # noqa: E402
from . import ViewContext  # noqa: E402
from . import media_probe as mp  # noqa: E402
from . import trim_model as tm  # noqa: E402

LOG = logging.getLogger(__name__)
TICK_MS = 100
HANDLE_HIT = 14
TIME_HINT = "時間格式不正確，例如 1:05.5 或 65"


class TrimView:
    is_audio = False

    def __init__(self, ctx: ViewContext) -> None:
        self.ctx = ctx
        self.model = tm.TrimModel()
        self.path: Path | None = None
        self.info: mp.MediaInfo | None = None
        self.frames: list[cairo.ImageSurface] = []
        self.wave: list[float] = []
        self.stream: Gtk.MediaFile | None = None
        self._range_end: float | None = None
        self._drag: str | None = None
        self._token = 0
        self._syncing = False
        self.widget = self._build_widget()
        self.panel = self._build_panel()
        self._unsub = ctx.options.subscribe(self._options_changed)
        self._timer = GLib.timeout_add(TICK_MS, self._tick)

    # ------------------------------------------------------------ 讀檔

    def set_file(self, path: Path) -> None:
        if path == self.path:
            return
        self.path = path
        self._token += 1
        self.info, self.frames, self.wave = None, [], []
        self.model = tm.TrimModel()
        self.name.set_label(path.name)
        self._set_stream(path)
        self._status("loading")
        threading.Thread(target=self._load, args=(path, self._token), daemon=True).start()

    def _load(self, path: Path, token: int) -> None:
        info = mp.probe(path)
        GLib.idle_add(self._probed, token, info)
        if info is None:
            return
        if self.is_audio or not info.has_video:
            wave = mp.peaks(path)
            GLib.idle_add(self._media_ready, token, [], wave)
        else:
            frames = [mp.png_surface(d) for d in mp.strip(path, info.duration)]
            GLib.idle_add(self._media_ready, token, frames, [])

    def _probed(self, token: int, info: mp.MediaInfo | None) -> bool:
        if token != self._token:
            return False
        self.info = info
        if info is None:
            self._status("error")
            return False
        self.model.load(info.duration)
        self.summary_label.set_label(info.summary)
        self._status("ready")
        self._emit()
        return False

    def _media_ready(self, token: int, frames: list, wave: list[float]) -> bool:
        if token == self._token:
            self.frames, self.wave = frames, wave
            self.bar.queue_draw()
        return False

    def _set_stream(self, path: Path) -> None:
        if self.stream is not None:
            self.stream.pause()
        self.stream = Gtk.MediaFile.new_for_filename(str(path))
        if self.video is not None:
            self.video.set_media_stream(self.stream)

    # ------------------------------------------------------------ 介面

    def _build_widget(self) -> Gtk.Widget:
        self.name = label("", "heading", ellipsize="middle")
        self.name.set_hexpand(True)
        self.summary_label = label("", "qs-small", "qs-dim", "qs-mono")
        kind_icon = "audio-x-generic-symbolic" if self.is_audio else "video-x-generic-symbolic"
        head = hbox(10, icon(kind_icon, 16, "qs-tinted"), self.name, self.summary_label)
        body = vbox(16, head, self._preview(), self._transport())
        self.bar = Gtk.DrawingArea(hexpand=True, content_height=84 if self.is_audio else 64)
        self.bar.set_draw_func(self._draw_bar)
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._bar_begin)
        drag.connect("drag-update", self._bar_update)
        drag.connect("drag-end", lambda *_a: self._bar_end())
        self.bar.add_controller(drag)
        self.loading = Gtk.Spinner(spinning=True, halign=Gtk.Align.CENTER)
        self.error = hbox(
            6,
            icon("dialog-warning-symbolic", 16, "qs-warning"),
            label("無法讀取這個檔案的長度", "qs-warning"),
        )
        body.append(self.loading)
        body.append(self.error)
        body.append(self.bar)
        body.append(self._time_row())
        for side in ("top", "bottom", "start", "end"):
            getattr(body, f"set_margin_{side}")(20)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        scroll.set_child(body)
        return scroll

    def _preview(self) -> Gtk.Widget:
        self.video: Gtk.Video | None = None
        if self.is_audio:
            self.big_time = label("0:00.00", "qs-pct", xalign=0.5)
            box = vbox(6, icon("audio-x-generic-symbolic", 34, "qs-tinted"), self.big_time)
            box.add_css_class("qs-audio-box")
            box.add_css_class("qs-note")
            box.set_size_request(-1, 140)
            box.set_valign(Gtk.Align.CENTER)
            return box
        self.big_time = None
        self.video = Gtk.Video(autoplay=False, loop=False)
        self.video.set_size_request(-1, 240)
        self.video.add_css_class("qs-video-box")
        return self.video

    def _transport(self) -> Gtk.Widget:
        self.play = Gtk.Button(
            icon_name="media-playback-start-symbolic", tooltip_text="播放（空白鍵）"
        )
        self.play.add_css_class("circular")
        self.play.add_css_class("suggested-action")
        self.play.connect("clicked", lambda _b: self._toggle_play())
        rng = secondary("播放選取範圍", "media-playback-start-symbolic", on_click=self._play_range)
        self.clock = label("0:00.00 / 0:00.00", "qs-dim", "qs-mono", xalign=1)
        self.clock.set_hexpand(True)
        return hbox(12, self.play, rng, self.clock)

    def _time_entry(self, which: str) -> Gtk.Entry:
        entry = Gtk.Entry(width_chars=9, placeholder_text="0:00.0")
        entry.connect("activate", lambda e: self._typed(which, e))
        focus = Gtk.EventControllerFocus()
        focus.connect("leave", lambda _c: self._typed(which, entry))
        entry.add_controller(focus)
        return entry

    def _time_row(self) -> Gtk.Widget:
        self.start_entry = self._time_entry("start")
        self.end_entry = self._time_entry("end")
        set_start = Gtk.Button(icon_name="go-last-symbolic", tooltip_text="把開始設為目前播放位置")
        set_start.connect("clicked", lambda _b: self._to_playhead("start"))
        set_end = Gtk.Button(icon_name="go-first-symbolic", tooltip_text="把結束設為目前播放位置")
        set_end.connect("clicked", lambda _b: self._to_playhead("end"))
        self.length_title = label("保留長度", "qs-small", "qs-dim", xalign=0.5)
        self.length_value = label("0:00.00", "qs-range-length", xalign=0.5)
        mid = vbox(2, self.length_title, self.length_value, hexpand=True)
        start = vbox(4, label("開始"), hbox(6, self.start_entry, set_start))
        end = vbox(4, label("結束", xalign=1), hbox(6, set_end, self.end_entry))
        return hbox(8, start, mid, end)

    def _build_panel(self) -> Gtk.Widget:
        self.sel_text = label("", "qs-semibold", "qs-mono", wrap=True)
        self.out_text = label("", "qs-small", "qs-dim", wrap=True)
        box = vbox(4, self.sel_text, self.out_text)
        box.add_css_class("qs-note")
        return box

    def _status(self, state: str) -> None:
        self.loading.set_visible(state == "loading")
        self.error.set_visible(state == "error")
        self.bar.set_visible(state == "ready")
        self.panel.set_visible(state == "ready")

    # ------------------------------------------------------------ 值的同步

    def _keep(self) -> str:
        return tm.keep_mode(self.ctx.options.values)

    def _emit(self) -> None:
        self._syncing = True
        try:
            for key, value in self.model.values().items():
                self.ctx.options.set_value(key, value)
        finally:
            self._syncing = False
        self._refresh_texts()
        self.ctx.changed()

    def _options_changed(self, key: str | None) -> None:
        if self._syncing or self.model.duration is None:
            return
        if key in ("start", "end"):
            self.model.from_values(self.ctx.options.values)
        self._refresh_texts()

    def _refresh_texts(self) -> None:
        m, keep = self.model, self._keep()
        self.start_entry.set_text(format_time(m.start, decimals=2))
        self.end_entry.set_text(format_time(m.effective_end, decimals=2))
        self.length_title.set_label(tm.length_title(keep))
        self.length_value.set_label(format_time(max(0.0, m.effective_end - m.start), decimals=2))
        sel, out = m.summary(keep)
        self.sel_text.set_label(sel)
        self.out_text.set_label(out)
        self.bar.queue_draw()

    def _typed(self, which: str, entry: Gtk.Entry) -> None:
        value = parse_time(entry.get_text())
        bad = value is None
        pos = Gtk.EntryIconPosition.SECONDARY
        entry.set_icon_from_icon_name(pos, "dialog-warning-symbolic" if bad else None)
        entry.set_icon_tooltip_text(pos, TIME_HINT if bad else None)
        if bad or self.model.duration is None:
            return
        value = min(value, self.model.duration)
        (self.model.set_start if which == "start" else self.model.set_end)(value)
        self._emit()

    def _playhead(self) -> float:
        return (self.stream.get_timestamp() / 1_000_000) if self.stream is not None else 0.0

    def _to_playhead(self, which: str) -> None:
        if self.model.duration is None:
            return
        (self.model.set_start if which == "start" else self.model.set_end)(self._playhead())
        self._emit()

    # ------------------------------------------------------------ 播放

    def _toggle_play(self) -> None:
        if self.stream is None:
            return
        self._range_end = None
        if self.stream.get_playing():
            self.stream.pause()
        else:
            self.stream.play()

    def _play_range(self) -> None:
        if self.stream is None or self.model.duration is None:
            return
        self.stream.seek(int(self.model.start * 1_000_000))
        self._range_end = self.model.effective_end
        self.stream.play()

    def _tick(self) -> bool:
        if self.stream is None:
            return True
        now = self._playhead()
        if self._range_end is not None and now >= self._range_end and self.stream.get_playing():
            self.stream.pause()
            self._range_end = None
        playing = self.stream.get_playing()
        self.play.set_icon_name(
            "media-playback-pause-symbolic" if playing else "media-playback-start-symbolic"
        )
        d = self.model.duration or 0.0
        self.clock.set_label(f"{format_time(now, 2)} / {format_time(d, 2)}")
        if self.big_time is not None:
            self.big_time.set_label(format_time(now, 2))
        if playing:
            self.bar.queue_draw()
        return True

    # ------------------------------------------------------------ 時間軸

    def _x_for(self, t: float, width: float) -> float:
        d = self.model.duration or 1.0
        return 8 + (width - 16) * min(max(t / d, 0.0), 1.0)

    def _t_for(self, x: float, width: float) -> float:
        d = self.model.duration or 0.0
        return d * min(max((x - 8) / max(width - 16, 1.0), 0.0), 1.0)

    def _bar_begin(self, _g: Gtk.GestureDrag, x: float, _y: float) -> None:
        w = self.bar.get_width()
        xs, xe = self._x_for(self.model.start, w), self._x_for(self.model.effective_end, w)
        if abs(x - xs) <= HANDLE_HIT and abs(x - xs) <= abs(x - xe):
            self._drag = "start"
        elif abs(x - xe) <= HANDLE_HIT:
            self._drag = "end"
        else:
            self._drag = "scrub"
        self._drag_x = x
        self._bar_update(_g, 0.0, 0.0)

    def _bar_update(self, _g: Gtk.GestureDrag, dx: float, _dy: float) -> None:
        if self._drag is None or self.model.duration is None:
            return
        t = self._t_for(self._drag_x + dx, self.bar.get_width())
        if self._drag == "start":
            self.model.set_start(t)
        elif self._drag == "end":
            self.model.set_end(t)
        elif self.stream is not None:
            self.stream.pause()
            self.stream.seek(int(t * 1_000_000))
        self._refresh_texts()

    def _bar_end(self) -> None:
        if self._drag in ("start", "end"):
            self._emit()
        self._drag = None

    def _draw_bar(self, _a: Gtk.DrawingArea, cr: cairo.Context, w: int, h: int) -> None:
        cr.set_source_rgba(0.5, 0.5, 0.5, 0.15)
        cr.rectangle(8, 0, w - 16, h)
        cr.fill()
        if self.frames:
            slot = (w - 16) / len(self.frames)
            for i, surf in enumerate(self.frames):
                cr.save()
                cr.rectangle(8 + i * slot, 0, slot, h)
                cr.clip()
                scale = max(slot / surf.get_width(), h / surf.get_height())
                cr.translate(8 + i * slot, 0)
                cr.scale(scale, scale)
                cr.set_source_surface(surf, 0, 0)
                cr.paint()
                cr.restore()
        elif self.wave:
            self._draw_wave(cr, w, h)
        self._draw_selection(cr, w, h)

    def _draw_wave(self, cr: cairo.Context, w: int, h: int) -> None:
        step = (w - 16) / len(self.wave)
        cr.set_source_rgba(0.55, 0.35, 0.85, 0.8)
        for i, p in enumerate(self.wave):
            bar_h = max(2.0, p * (h - 10))
            cr.rectangle(8 + i * step, (h - bar_h) / 2, max(step - 1, 1), bar_h)
        cr.fill()

    def _draw_selection(self, cr: cairo.Context, w: int, h: int) -> None:
        if self.model.duration is None:
            return
        xs, xe = self._x_for(self.model.start, w), self._x_for(self.model.effective_end, w)
        cr.set_source_rgba(0, 0, 0, 0.45)
        cr.rectangle(8, 0, xs - 8, h)
        cr.rectangle(xe, 0, w - 8 - xe, h)
        cr.fill()
        cr.set_source_rgb(1.0, 0.75, 0.1)
        cr.set_line_width(3)
        cr.rectangle(xs, 1.5, xe - xs, h - 3)
        cr.stroke()
        for x in (xs, xe):
            cr.rectangle(x - 5, 0, 10, h)
        cr.fill()
        px = self._x_for(self._playhead(), w)
        cr.set_source_rgb(1, 1, 1)
        cr.set_line_width(2)
        cr.move_to(px, 0)
        cr.line_to(px, h)
        cr.stroke()

    # ------------------------------------------------------------ 介面合約

    def validate(self) -> str | None:
        if self.model.duration is None:
            return None
        return self.model.validate(self._keep())

    def destroy(self) -> None:
        self._token += 1
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0
        if self.stream is not None:
            self.stream.pause()
        self._unsub()


class AudioTrimView(TrimView):
    is_audio = True
