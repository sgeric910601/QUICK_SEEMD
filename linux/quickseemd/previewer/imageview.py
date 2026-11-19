"""圖片與動圖的顯示（GTK3）：等比例縮小到放得下視窗，不放大，置中。"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk  # noqa: E402

from .loaders import LoadedImage  # noqa: E402

MIN_FRAME_DELAY_MS = 20


def fit_scale(image_size: tuple[int, int], box: tuple[int, int]) -> float:
    """放得進 box 的縮放比例，最大 1（不放大）。"""
    iw, ih = image_size
    bw, bh = box
    if iw <= 0 or ih <= 0 or bw <= 0 or bh <= 0:
        return 1.0
    return min(1.0, bw / iw, bh / ih)


class ImageView(Gtk.DrawingArea):
    def __init__(self, image: LoadedImage) -> None:
        super().__init__()
        self._image = image
        self._iter: GdkPixbuf.PixbufAnimationIter | None = None
        self._frame = 0
        self._timer = 0
        self._scaled: GdkPixbuf.Pixbuf | None = None
        self._scaled_key: tuple[int, int, int] | None = None
        self.set_hexpand(True)
        self.set_vexpand(True)
        if image.animation is not None:
            self._iter = image.animation.get_iter(None)
            self._schedule()
        self.connect("destroy", self._on_destroy)

    def _current(self) -> GdkPixbuf.Pixbuf | None:
        if self._iter is not None:
            return self._iter.get_pixbuf()
        return self._image.pixbuf

    def _schedule(self) -> None:
        delay = self._iter.get_delay_time() if self._iter is not None else -1
        if delay >= 0:
            self._timer = GLib.timeout_add(max(delay, MIN_FRAME_DELAY_MS), self._next_frame)

    def _next_frame(self) -> bool:
        self._timer = 0
        if self._iter is None:
            return False
        self._iter.advance(None)
        self._frame += 1
        self.queue_draw()
        self._schedule()
        return False

    def _scaled_pixbuf(self, pixbuf: GdkPixbuf.Pixbuf, box: tuple[int, int]) -> GdkPixbuf.Pixbuf:
        size = (pixbuf.get_width(), pixbuf.get_height())
        scale = fit_scale(size, box)
        width = max(1, round(size[0] * scale))
        height = max(1, round(size[1] * scale))
        key = (self._frame, width, height)
        if key != self._scaled_key:
            same = (width, height) == size
            self._scaled = (
                pixbuf
                if same
                else pixbuf.scale_simple(width, height, GdkPixbuf.InterpType.BILINEAR)
            )
            self._scaled_key = key
        return self._scaled

    def do_draw(self, cr) -> bool:  # noqa: ANN001 — cairo.Context
        pixbuf = self._current()
        if pixbuf is None:
            return False
        box = (self.get_allocated_width(), self.get_allocated_height())
        shown = self._scaled_pixbuf(pixbuf, box)
        x = (box[0] - shown.get_width()) // 2
        y = (box[1] - shown.get_height()) // 2
        Gdk.cairo_set_source_pixbuf(cr, shown, x, y)
        cr.paint()
        return False

    def _on_destroy(self, _widget: Gtk.Widget) -> None:
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0
        self._iter = None
