"""裁剪圖片的畫面（對應原版 ITCropEditor）：預覽圖上拖曳裁剪框，三等分輔助線，四角把手。

框的比例來自選項 `ratio`；拖曳結束後把原圖像素範圍寫回選項 `crop`（JSON，見 crop_model）。
"""

from __future__ import annotations

import logging
import math
import threading
from pathlib import Path

import cairo
import gi

gi.require_version("Gtk", "4.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import Gdk, GdkPixbuf, Gtk  # noqa: E402

from .. import options_model, thumbnails  # noqa: E402
from ..mainloop import to_main  # noqa: E402
from ..widgets import hbox, icon, label, secondary, vbox  # noqa: E402
from . import ViewContext  # noqa: E402
from . import crop_model as cm  # noqa: E402

LOG = logging.getLogger(__name__)
PREVIEW_MAX = 1800
HANDLE_PX = 12
MIN_PX = 24
PAD = 16


def preview_error(preview: thumbnails.Preview) -> str | None:
    """預覽失敗時的說明；有預覽圖時回傳 None。"""
    if preview.pixbuf is not None:
        return None
    if preview.too_big:
        return (
            f"這張圖片太大（{preview.width} × {preview.height} px），無法在這裡預覽；"
            "可以選擇比例自動裁切。"
        )
    return "無法預覽這張圖片"


class CropView:
    def __init__(self, ctx: ViewContext) -> None:
        self.ctx = ctx
        self.path: Path | None = None
        self.pixbuf: GdkPixbuf.Pixbuf | None = None
        self.img_w = self.img_h = 0
        self.rect = cm.FULL
        self._ratio = self._current_ratio()
        self._drag: tuple[str, cm.Rect] | None = None
        self._token = 0
        self.widget = self._build_widget()
        self.panel = None  # 尺寸與拖曳說明由工具自己的說明選項顯示
        self._unsub = ctx.options.subscribe(self._options_changed)

    # ------------------------------------------------------------ 讀檔與比例

    def set_file(self, path: Path) -> None:
        if path == self.path:
            return
        self.path = path
        self._token += 1
        self.pixbuf = None
        self.name.set_label(path.name)
        threading.Thread(target=self._worker, args=(path, self._token), daemon=True).start()

    def _worker(self, path: Path, token: int) -> None:
        """背景執行緒：先讀尺寸，大圖不解碼；任何錯誤都要回報（不能卡在讀取中）。"""
        try:
            preview = thumbnails.load_preview(path, PREVIEW_MAX)
        except Exception:  # noqa: BLE001
            LOG.exception("讀取圖片失敗：%s", path)
            preview = thumbnails.Preview(None, 0, 0)
        to_main(self._loaded, token, preview)

    def _loaded(self, token: int, preview: thumbnails.Preview) -> bool:
        if token != self._token:
            return False
        self.pixbuf, self.img_w, self.img_h = preview.pixbuf, preview.width, preview.height
        message = preview_error(preview)
        self.error_text.set_label(message or "")
        self.error.set_visible(message is not None)
        self.reset()
        return False

    def _current_ratio(self) -> float | None:
        opt = self.ctx.tool.option("ratio")
        value = self.ctx.options.values.get("ratio")
        title = None
        if opt is not None:
            title = next((c.title for c in self.ctx.options.choices(opt) if c.value == value), None)
        return options_model.parse_ratio(value, title)

    def _options_changed(self, key: str | None) -> None:
        ratio = self._current_ratio()
        if ratio != self._ratio:
            self._ratio = ratio
            if ratio is not None:
                self.reset()
            else:
                self.area.queue_draw()

    def reset(self) -> None:
        self.rect = cm.default_rect(self._ratio, self.img_w, self.img_h)
        self._emit()

    def _emit(self) -> None:
        # 自由比例而且框住整張圖＝還沒框選（空字串），工具會提示「請拖曳裁剪框…」
        untouched = self._ratio is None and self.rect == cm.FULL
        value = (
            "" if not self.img_w or untouched else cm.to_value(self.rect, self.img_w, self.img_h)
        )
        self.ctx.options.set_value("crop", value)
        self._show_size()
        self.area.queue_draw()
        self.ctx.changed()

    def _show_size(self) -> None:
        if not self.img_w:
            self.size_label.set_label("")
            return
        size = cm.size_text(self.rect, self.img_w, self.img_h)
        self.size_label.set_label(f"{size}（原始 {self.img_w} × {self.img_h} px）")

    # ------------------------------------------------------------ 介面

    def _build_widget(self) -> Gtk.Widget:
        self.name = label("", "heading", ellipsize="middle")
        self.name.set_hexpand(True)
        self.size_label = label("", "qs-small", "qs-dim", "qs-mono")
        reset = secondary("重設裁剪框", "edit-undo-symbolic", on_click=self.reset)
        remove = Gtk.Button(icon_name="window-close-symbolic", tooltip_text="移除這張圖片")
        remove.add_css_class("flat")
        remove.connect("clicked", lambda _b: self.ctx.remove_file())
        head = hbox(
            8, icon("image-x-generic-symbolic", 16, "qs-tinted"), self.name, self.size_label
        )
        head.append(reset)
        head.append(remove)
        self.area = Gtk.DrawingArea(hexpand=True, vexpand=True)
        self.area.set_draw_func(self._draw)
        drag = Gtk.GestureDrag()
        drag.connect("drag-begin", self._drag_begin)
        drag.connect("drag-update", self._drag_update)
        drag.connect("drag-end", lambda *_a: self._drag_end())
        self.area.add_controller(drag)
        self.error_text = label("無法預覽這張圖片", "qs-dim", wrap=True)
        self.error = hbox(6, icon("dialog-warning-symbolic", 16, "qs-dim"), self.error_text)
        self.error.set_visible(False)
        box = vbox(
            12,
            head,
            self.error,
            self.area,
            margin_top=16,
            margin_bottom=16,
            margin_start=20,
            margin_end=20,
        )
        return box

    # ------------------------------------------------------------ 座標

    def _frame(self) -> tuple[float, float, float, float]:
        """圖片在畫布上的位置與大小 (x, y, w, h)。"""
        aw, ah = self.area.get_width() - 2 * PAD, self.area.get_height() - 2 * PAD
        if not self.pixbuf or aw <= 0 or ah <= 0:
            return PAD, PAD, 0, 0
        pw, ph = self.pixbuf.get_width(), self.pixbuf.get_height()
        s = min(aw / pw, ah / ph)
        w, h = pw * s, ph * s
        return PAD + (aw - w) / 2, PAD + (ah - h) / 2, w, h

    def _to_norm(self, x: float, y: float) -> tuple[float, float]:
        fx, fy, fw, fh = self._frame()
        return ((x - fx) / fw if fw else 0.0, (y - fy) / fh if fh else 0.0)

    def _drag_begin(self, _g: Gtk.GestureDrag, x: float, y: float) -> None:
        _fx, _fy, fw, fh = self._frame()
        if not fw:
            self._drag = None
            return
        px, py = self._to_norm(x, y)
        tol = HANDLE_PX / min(fw, fh)
        handle = cm.hit_test(self.rect, px, py, tol, edges=cm.edge_handles_allowed(self._ratio))
        self._drag = (handle, self.rect) if handle else None

    def _drag_update(self, _g: Gtk.GestureDrag, dx: float, dy: float) -> None:
        if self._drag is None:
            return
        _fx, _fy, fw, fh = self._frame()
        handle, start = self._drag
        ndx, ndy = dx / fw, dy / fh
        if handle == "move":
            self.rect = cm.move(start, ndx, ndy)
        else:
            aspect = self.img_w / self.img_h if self.img_h else 1.0
            min_size = MIN_PX / min(fw, fh)
            self.rect = cm.resize(start, handle, ndx, ndy, self._ratio, aspect, min_size)
        self._show_size()
        self.area.queue_draw()

    def _drag_end(self) -> None:
        if self._drag is not None:
            self._drag = None
            self._emit()

    # ------------------------------------------------------------ 繪製

    def _draw(self, _area: Gtk.DrawingArea, cr: cairo.Context, _w: int, _h: int) -> None:
        fx, fy, fw, fh = self._frame()
        if not self.pixbuf or not fw:
            return
        cr.save()
        cr.translate(fx, fy)
        cr.scale(fw / self.pixbuf.get_width(), fh / self.pixbuf.get_height())
        Gdk.cairo_set_source_pixbuf(cr, self.pixbuf, 0, 0)
        cr.paint()
        cr.restore()
        r = self.rect
        rx, ry, rw, rh = fx + r.x * fw, fy + r.y * fh, r.w * fw, r.h * fh
        cr.set_fill_rule(cairo.FILL_RULE_EVEN_ODD)
        cr.rectangle(fx, fy, fw, fh)
        cr.rectangle(rx, ry, rw, rh)
        cr.set_source_rgba(0, 0, 0, 0.5)
        cr.fill()
        self._draw_frame(cr, rx, ry, rw, rh)

    def _draw_frame(self, cr: cairo.Context, x: float, y: float, w: float, h: float) -> None:
        cr.set_source_rgba(1, 1, 1, 0.45)
        cr.set_line_width(1)
        for i in (1, 2):
            cr.move_to(x + w * i / 3, y)
            cr.line_to(x + w * i / 3, y + h)
            cr.move_to(x, y + h * i / 3)
            cr.line_to(x + w, y + h * i / 3)
        cr.stroke()
        cr.set_source_rgb(1, 1, 1)
        cr.set_line_width(1.5)
        cr.rectangle(x, y, w, h)
        cr.stroke()
        cr.set_line_width(4)
        arm = min(18, w / 3, h / 3)
        for cx, cy, sx, sy in (
            (x, y, 1, 1),
            (x + w, y, -1, 1),
            (x, y + h, 1, -1),
            (x + w, y + h, -1, -1),
        ):
            cr.move_to(cx + sx * arm, cy)
            cr.line_to(cx, cy)
            cr.line_to(cx, cy + sy * arm)
        cr.stroke()
        if cm.edge_handles_allowed(self._ratio):
            for ex, ey in ((x + w / 2, y), (x + w / 2, y + h), (x, y + h / 2), (x + w, y + h / 2)):
                cr.arc(ex, ey, 4, 0, 2 * math.pi)
                cr.fill()

    # ------------------------------------------------------------ 介面合約

    def validate(self) -> str | None:
        return None

    def destroy(self) -> None:
        self._token += 1
        self._unsub()
