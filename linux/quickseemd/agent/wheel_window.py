"""輪盤視窗（GTK3 透明彈出視窗；對應原版 DragWheelController 的面板與 WheelDropView）。

- 不搶焦點、蓋在所有視窗上（override-redirect），先定位再顯示，處理高解析度縮放。
- 只有圓盤範圍接收滑鼠（輸入範圍），放在圈外的會落到底下的視窗。
- 放下時一律當成「拷貝」、只要 text/uri-list，絕不要求來源刪除原檔。
- 放開滑鼠後：有拖放就等拖放完成，沒有就 250 ms 後收起。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk  # noqa: E402

from ..core import wheel as qs_wheel  # noqa: E402
from ..core.wheel import WheelItem  # noqa: E402
from . import icons_gtk, wheel_draw, wheel_model  # noqa: E402
from .wheel_controller import WheelState  # noqa: E402

log = logging.getLogger(__name__)

URI_LIST = "text/uri-list"
HIDE_AFTER_RELEASE_MS = 250
DROP_WAIT_MS = 2000
FADE_MS = 120
FADE_STEP_MS = 16

ChooseFn = Callable[["WheelItem | None", list[Path]], None]


class WheelWindow(Gtk.Window):
    """輪盤。on_choose(格子或 None＝中心, 檔案) 在放下時呼叫。"""

    def __init__(self, on_choose: ChooseFn) -> None:
        super().__init__(type=Gtk.WindowType.POPUP)
        self._on_choose = on_choose
        self._state: WheelState | None = None
        self._hover: object = None
        self._drop_hit: object = None
        self._drop_pending = False
        self._released = False
        self._fade_id = 0
        self._composited = self.get_screen().is_composited()
        visual = self.get_screen().get_rgba_visual()
        if visual is not None and self._composited:
            self.set_visual(visual)
        self.set_app_paintable(True)
        self.set_size_request(wheel_model.SIZE, wheel_model.SIZE)
        self.set_default_size(wheel_model.SIZE, wheel_model.SIZE)
        self.set_type_hint(Gdk.WindowTypeHint.DND)
        self.connect("draw", self._on_draw)
        self.connect("realize", self._on_realize)
        self._setup_drop()

    # ------------------------------------------------------------ 對外

    @property
    def state(self) -> WheelState | None:
        return self._state

    def present(self, state: WheelState, x: int, y: int) -> None:
        """在游標（裝置像素）處打開；已經開著就只切換內容。"""
        self._state = state
        self._hover = None
        self._released = False
        if self.get_visible():
            self.queue_draw()
            return
        ox, oy = self._origin_for(x, y)
        self.move(ox, oy)
        if self._composited:
            self.set_opacity(0.0)
        self.show()
        self._start_fade()
        log.info("輪盤出現：%s，位置 %d,%d", state.mode, ox, oy)

    def hide(self) -> None:  # noqa: D102 — 覆寫 Gtk.Widget.hide
        if self.get_visible():
            log.info("輪盤收起")
        self._hover = None
        self._drop_pending = False
        Gtk.Window.hide(self)

    def drag_released(self) -> None:
        """滑鼠放開了：等拖放處理完或 250 ms 後收起。"""
        self._released = True
        GLib.timeout_add(HIDE_AFTER_RELEASE_MS, self._hide_when_done, DROP_WAIT_MS)

    # ------------------------------------------------------------ 位置與外形

    def _origin_for(self, x: int, y: int) -> tuple[int, int]:
        display = self.get_display()
        monitor = display.get_monitor_at_point(x, y) or display.get_monitor(0)
        scale = monitor.get_scale_factor() if monitor is not None else 1
        lx, ly = wheel_model.device_to_logical(x, y, scale)
        if monitor is None:
            return lx - wheel_model.SIZE // 2, ly - wheel_model.SIZE // 2
        wa = monitor.get_workarea()
        return wheel_model.ring_origin(lx, ly, (wa.x, wa.y, wa.width, wa.height))

    def _on_realize(self, _widget: Gtk.Widget) -> None:
        size = wheel_model.SIZE
        hit = wheel_draw.circle_mask(size, qs_wheel.HIT_OUTER_RADIUS)
        self.input_shape_combine_region(Gdk.cairo_region_create_from_surface(hit))
        if not self._composited:
            # 沒有合成器時透明的地方會是黑色：直接把視窗外形裁成圓形
            disk = wheel_draw.circle_mask(size, qs_wheel.OUTER_RADIUS + 1)
            self.shape_combine_region(Gdk.cairo_region_create_from_surface(disk))

    def _start_fade(self) -> None:
        if not self._composited:
            return
        if self._fade_id:
            GLib.source_remove(self._fade_id)
        steps = max(FADE_MS // FADE_STEP_MS, 1)
        counter = {"i": 0}

        def step() -> bool:
            counter["i"] += 1
            self.set_opacity(min(counter["i"] / steps, 1.0))
            if counter["i"] >= steps:
                self._fade_id = 0
                return False
            return True

        self._fade_id = GLib.timeout_add(FADE_STEP_MS, step)

    def _hide_when_done(self, remaining_ms: int) -> bool:
        if not self._released:
            return False  # 又開始新的拖曳了
        if self._drop_pending and remaining_ms > 0:
            GLib.timeout_add(100, self._hide_when_done, remaining_ms - 100)
            return False
        self.hide()
        return False

    # ------------------------------------------------------------ 繪圖

    def _on_draw(self, _widget: Gtk.Widget, cr) -> bool:  # noqa: ANN001
        if self._state is None:
            return False
        colors = icons_gtk.theme_colors(self)
        pal = wheel_draw.Palette(colors["bg"], colors["fg"], colors["secondary"], self._composited)
        wheel_draw.draw_wheel(
            cr,
            self._state,
            self._hover,
            pal,
            lambda name, size, rgba: icons_gtk.symbolic_surface(name, size, rgba, self),
            lambda path, size: icons_gtk.file_surface(path, size, self),
        )
        return True

    def _set_hover(self, hover: object) -> None:
        if hover != self._hover:
            self._hover = hover
            self.queue_draw()

    # ------------------------------------------------------------ 拖放

    def _setup_drop(self) -> None:
        target = Gtk.TargetEntry.new(URI_LIST, 0, 0)
        self.drag_dest_set(0, [target], Gdk.DragAction.COPY)
        self.connect("drag-motion", self._on_motion)
        self.connect("drag-leave", lambda *_a: self._set_hover(None))
        self.connect("drag-drop", self._on_drop)
        self.connect("drag-data-received", self._on_data)

    def _hit(self, x: int, y: int) -> object:
        count = len(self._state.items) if self._state else 0
        return wheel_model.hit_at(x, y, count)

    def _on_motion(self, _w: Gtk.Widget, ctx: Gdk.DragContext, x: int, y: int, t: int) -> bool:
        hit = self._hit(x, y)
        self._set_hover(hit)
        Gdk.drag_status(ctx, Gdk.DragAction.COPY if hit is not None else 0, t)
        return True

    def _on_drop(self, _w: Gtk.Widget, ctx: Gdk.DragContext, x: int, y: int, t: int) -> bool:
        hit = self._hit(x, y)
        targets = [a.name() for a in ctx.list_targets()]
        log.info("放下：位置 %s，格式 %s", hit, targets)
        if hit is None or URI_LIST not in targets:
            Gtk.drag_finish(ctx, False, False, t)
            return True
        self._drop_hit = hit
        self._drop_pending = True
        self.drag_get_data(ctx, Gdk.Atom.intern(URI_LIST, False), t)
        return True

    def _on_data(self, _w, ctx, _x, _y, data, _info, t) -> None:  # noqa: ANN001
        raw = data.get_data() if data is not None else b""
        paths = wheel_model.parse_uri_list(bytes(raw or b""))
        Gtk.drag_finish(ctx, bool(paths), False, t)  # 第三個參數 False：絕不要求刪除來源
        hit, self._drop_hit = self._drop_hit, None
        self._drop_pending = False
        items = self._state.items if self._state is not None else ()
        valid = hit == "center" or (isinstance(hit, int) and 0 <= hit < len(items))
        if paths and valid:
            item = items[hit] if isinstance(hit, int) else None
            log.info("放到：%s，%d 個檔案", item.title if item else "中心", len(paths))
            self._on_choose(item, paths)
        self.hide()
