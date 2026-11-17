"""整理 PDF 的畫面（對應原版 PTOrganizeGrid／PTPageCell／PTOrganizePanel）。

頁面縮圖用 Poppler 在背景執行緒畫（最長邊 260），結果用 cairo 畫在 DrawingArea 上（含旋轉）。
每次修改都把頁面清單寫回選項 `pages`（JSON 字串，見 organize_model）。
"""

from __future__ import annotations

import io
import logging
import threading
from pathlib import Path

import cairo
import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, GLib, GObject, Gtk  # noqa: E402

from ..mainloop import to_main  # noqa: E402
from ..thumbnails import render_pdf_page  # noqa: E402
from ..widgets import clear, hbox, icon, label, secondary, vbox  # noqa: E402
from . import ViewContext  # noqa: E402
from . import organize_model as om  # noqa: E402

LOG = logging.getLogger(__name__)
BOX = (132, 150)
THUMB_SIDE = 260
DRAG_PREFIX = "qs-page:"
NOTE = "拖曳縮圖可以調整順序；把滑鼠移到頁面上可旋轉、刪除或在後面插入空白頁。按住 Ctrl 或 Shift 點選可一次選取多頁。"


def _read_pdf(path: Path) -> list[tuple[float, float]]:
    gi.require_version("Poppler", "0.18")
    from gi.repository import Poppler  # noqa: PLC0415

    try:
        doc = Poppler.Document.new_from_file(path.as_uri(), None)
    except GLib.Error as exc:
        LOG.info("讀不到 PDF：%s", exc.message)
        return []
    return [tuple(doc.get_page(i).get_size()) for i in range(doc.get_n_pages())]


class OrganizeView:
    def __init__(self, ctx: ViewContext) -> None:
        self.ctx = ctx
        self.model = om.OrganizeModel()
        self.path: Path | None = None
        self.sizes: list[tuple[float, float]] = []
        self.thumbs: dict[int, cairo.ImageSurface] = {}
        self._cells: dict[int, Gtk.DrawingArea] = {}
        self._token = 0
        self.widget = self._build_widget()
        self.panel = self._build_panel()

    # ------------------------------------------------------------ 讀檔

    def set_file(self, path: Path) -> None:
        if path == self.path:
            return
        self.path = path
        self._token += 1
        self.model.unload()
        self.thumbs.clear()
        self._refresh()
        threading.Thread(target=self._load, args=(path, self._token), daemon=True).start()

    def _load(self, path: Path, token: int) -> None:
        sizes = _read_pdf(path)
        to_main(self._loaded, token, sizes)
        for i in range(len(sizes)):
            if token != self._token:
                return
            data = render_pdf_page(path, i, THUMB_SIDE)
            if data:
                surf = cairo.ImageSurface.create_from_png(io.BytesIO(data))
                to_main(self._thumb_ready, token, i, surf)

    def _loaded(self, token: int, sizes: list[tuple[float, float]]) -> bool:
        if token == self._token:
            self.sizes = sizes
            self.model.load(len(sizes))
            self._changed()
        return False

    def _thumb_ready(self, token: int, index: int, surf: cairo.ImageSurface) -> bool:
        if token == self._token:
            self.thumbs[index] = surf
            for uid, area in self._cells.items():
                page = next((p for p in self.model.pages if p.uid == uid), None)
                if page is not None and page.src == index:
                    area.queue_draw()
        return False

    # ------------------------------------------------------------ 介面

    def _build_widget(self) -> Gtk.Widget:
        self.stack = Gtk.Stack(vexpand=True, hexpand=True)
        self.flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True)
        self.flow.set_column_spacing(18)
        self.flow.set_row_spacing(18)
        self.flow.set_max_children_per_line(12)
        self.flow.set_valign(Gtk.Align.START)
        for side in ("top", "bottom", "start", "end"):
            getattr(self.flow, f"set_margin_{side}")(20)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        scroll.set_child(self.flow)
        self.stack.add_named(scroll, "grid")
        self.empty_label = label("", "heading", xalign=0.5)
        self.restore = secondary("還原全部頁面", on_click=self._reset)
        empty = vbox(
            10, icon("x-office-document-symbolic", 34, "qs-dim"), self.empty_label, self.restore
        )
        empty.set_valign(Gtk.Align.CENTER)
        empty.set_halign(Gtk.Align.CENTER)
        self.stack.add_named(empty, "empty")
        self.stack.add_named(Gtk.Spinner(spinning=True, halign=Gtk.Align.CENTER), "loading")
        return self.stack

    def _action(self, text: str, icon_name: str, fn) -> Gtk.Button:  # noqa: ANN001
        btn = secondary(text, icon_name, on_click=fn)
        btn.set_hexpand(True)
        return btn

    def _build_panel(self) -> Gtk.Widget:
        box = vbox(20)
        self.stat_before = label("0", "qs-stat")
        self.stat_after = label("0", "qs-stat")
        stats = hbox(12, vbox(0, self.stat_before, label("原本頁數", "qs-small", "qs-dim")))
        stats.append(icon("go-next-symbolic", 16, "qs-dim"))
        stats.append(vbox(0, self.stat_after, label("整理後", "qs-small", "qs-dim")))
        box.append(stats)
        self.sel_title = label("", "qs-group-title")
        sel = vbox(8, hbox(6, icon("object-select-symbolic", 16, "qs-dim"), self.sel_title))
        sel.append(
            hbox(
                8,
                self._action(
                    "向左轉", "object-rotate-left-symbolic", lambda: self._rotate_sel(-90)
                ),
                self._action(
                    "向右轉", "object-rotate-right-symbolic", lambda: self._rotate_sel(90)
                ),
            )
        )
        sel.append(
            hbox(
                8,
                self._action("刪除", "user-trash-symbolic", self._delete_sel),
                self._action("取消選取", "window-close-symbolic", self._clear_sel),
            )
        )
        self.sel_group = sel
        box.append(sel)
        allp = vbox(
            8,
            hbox(6, icon("edit-copy-symbolic", 16, "qs-dim"), label("所有頁面", "qs-group-title")),
        )
        allp.append(
            hbox(
                8,
                self._action(
                    "全部向左轉", "object-rotate-left-symbolic", lambda: self._rotate_all(-90)
                ),
                self._action(
                    "全部向右轉", "object-rotate-right-symbolic", lambda: self._rotate_all(90)
                ),
            )
        )
        allp.append(
            hbox(
                8,
                self._action("反轉順序", "view-sort-descending-symbolic", self._reverse),
                self._action("加入空白頁", "document-new-symbolic", lambda: self._insert(None)),
            )
        )
        self.reset_all = self._action("還原所有變更", "edit-undo-symbolic", self._reset)
        allp.append(self.reset_all)
        box.append(allp)
        note = hbox(
            8, icon("dialog-information-symbolic", 16, "qs-dim"), label(NOTE, "qs-small", wrap=True)
        )
        note.add_css_class("qs-note")
        box.append(note)
        return box

    # ------------------------------------------------------------ 動作

    def _rotate_sel(self, deg: int) -> None:
        self.model.rotate(self.model.selection, deg)
        self._changed()

    def _rotate_all(self, deg: int) -> None:
        self.model.rotate([p.uid for p in self.model.pages], deg)
        self._changed()

    def _delete_sel(self) -> None:
        self.model.delete(self.model.selection)
        self._changed()

    def _clear_sel(self) -> None:
        self.model.clear_selection()
        self._refresh()

    def _reverse(self) -> None:
        self.model.reverse()
        self._changed()

    def _insert(self, after: int | None) -> None:
        self.model.insert_blank(after)
        self._changed()

    def _reset(self) -> None:
        self.model.reset()
        self._changed()

    def _changed(self) -> None:
        self.ctx.options.set_value("pages", self.model.to_value())
        self._refresh()
        self.ctx.changed()

    # ------------------------------------------------------------ 畫面更新

    def _refresh(self) -> None:
        m = self.model
        self.stat_before.set_label(str(m.original_count))
        self.stat_after.set_label(str(len(m.pages)))
        self.sel_group.set_visible(bool(m.selection))
        self.sel_title.set_label(om.selection_title(len(m.selection)))
        self.reset_all.set_sensitive(m.is_modified)
        if not m.loaded:
            self.stack.set_visible_child_name("loading")
            return
        if not m.pages:
            self.empty_label.set_label(om.empty_text(m.original_count))
            self.restore.set_visible(m.original_count > 0)
            self.stack.set_visible_child_name("empty")
            return
        self.stack.set_visible_child_name("grid")
        self._rebuild_grid()

    def _rebuild_grid(self) -> None:
        self.flow.remove_all()
        self._cells = {}
        for n, page in enumerate(self.model.pages, start=1):
            self.flow.append(self._cell(page, n))
        blank = Gtk.Button()
        blank.add_css_class("qs-org-blank")
        blank.set_child(vbox(8, icon("list-add-symbolic", 22), label("加入空白頁", xalign=0.5)))
        blank.set_size_request(BOX[0] + 16, BOX[1] + 16)
        blank.set_halign(Gtk.Align.CENTER)
        blank.set_valign(Gtk.Align.START)
        blank.connect("clicked", lambda _b: self._insert(None))
        self.flow.append(blank)

    def _cell(self, page: om.OrgPage, number: int) -> Gtk.Widget:
        area = Gtk.DrawingArea(content_width=BOX[0], content_height=BOX[1])
        area.set_draw_func(self._draw, page)
        self._cells[page.uid] = area
        over = Gtk.Overlay()
        over.set_child(area)
        over.add_overlay(self._cell_tools(page))
        cell = vbox(6, over)
        cell.add_css_class("qs-org-cell")
        if page.uid in self.model.selection:
            cell.add_css_class("selected")
        num, sub = om.cell_label(page, number)
        caption = hbox(4, label(num, "qs-semibold"), halign=Gtk.Align.CENTER)
        if sub:
            caption.append(label(sub, "qs-small", "qs-dim"))
        if page.rotate:
            caption.append(icon("object-rotate-right-symbolic", 12, "qs-tinted"))
        cell.append(caption)
        cell.set_tooltip_text(om.cell_tooltip(page))
        cell.set_halign(Gtk.Align.CENTER)
        self._cell_input(cell, area, page)
        return cell

    def _cell_tools(self, page: om.OrgPage) -> Gtk.Widget:
        tools = hbox(0, halign=Gtk.Align.END, valign=Gtk.Align.START, margin_top=4, margin_end=4)
        tools.add_css_class("qs-org-tools")
        for icon_name, tip, fn in (
            ("object-rotate-left-symbolic", "向左轉", lambda: self._rotate_one(page.uid, -90)),
            ("object-rotate-right-symbolic", "向右轉", lambda: self._rotate_one(page.uid, 90)),
            ("document-new-symbolic", "在後面插入空白頁", lambda: self._insert(page.uid)),
            ("user-trash-symbolic", "刪除這一頁", lambda: self._delete_one(page.uid)),
        ):
            btn = Gtk.Button(icon_name=icon_name, tooltip_text=tip)
            btn.add_css_class("flat")
            btn.add_css_class("circular")
            btn.connect("clicked", lambda _b, fn=fn: fn())
            tools.append(btn)
        return tools

    def _rotate_one(self, uid: int, deg: int) -> None:
        self.model.rotate([uid], deg)
        self._changed()

    def _delete_one(self, uid: int) -> None:
        self.model.delete([uid])
        self._changed()

    def _cell_input(self, cell: Gtk.Widget, area: Gtk.Widget, page: om.OrgPage) -> None:
        click = Gtk.GestureClick()

        def pressed(g: Gtk.GestureClick, _n: int, _x: float, _y: float) -> None:
            state = g.get_current_event_state()
            ctrl = bool(state & Gdk.ModifierType.CONTROL_MASK)
            shift = bool(state & Gdk.ModifierType.SHIFT_MASK)
            self.model.tap(page.uid, ctrl=ctrl, shift=shift)
            to_main(self._refresh)

        click.connect("released", pressed)
        area.add_controller(click)
        src = Gtk.DragSource(actions=Gdk.DragAction.MOVE)
        value = GObject.Value(GObject.TYPE_STRING, f"{DRAG_PREFIX}{page.uid}")
        src.connect("prepare", lambda *_a: Gdk.ContentProvider.new_for_value(value))
        cell.add_controller(src)
        dst = Gtk.DropTarget.new(GObject.TYPE_STRING, Gdk.DragAction.MOVE)
        dst.connect("drop", lambda _t, v, _x, _y: self._dropped(v, page.uid))
        cell.add_controller(dst)

    def _dropped(self, value: object, target: int) -> bool:
        text = str(value or "")
        if not text.startswith(DRAG_PREFIX) or not text[len(DRAG_PREFIX) :].isdigit():
            return False
        self.model.move(int(text[len(DRAG_PREFIX) :]), target)
        to_main(self._changed)
        return True

    # ------------------------------------------------------------ 繪製

    def _natural(self, page: om.OrgPage) -> tuple[float, float]:
        if page.src is not None and page.src < len(self.sizes):
            return self.sizes[page.src]
        pages = list(self.model.pages)
        idx = pages.index(page) if page in pages else len(pages)
        for prev in reversed(pages[:idx]):
            if prev.src is not None and prev.src < len(self.sizes):
                return self.sizes[prev.src]
        return self.sizes[0] if self.sizes else om.DEFAULT_PAGE_SIZE

    def _draw(
        self, _area: Gtk.DrawingArea, cr: cairo.Context, w: int, h: int, page: om.OrgPage
    ) -> None:
        nw, nh = self._natural(page)
        fw, fh = om.fit_size((nw, nh), page.rotate, (w, h))
        cr.translate(w / 2, h / 2)
        cr.set_source_rgba(0, 0, 0, 0.18)
        cr.rectangle(-fw / 2 + 1, -fh / 2 + 2, fw, fh)
        cr.fill()
        cr.rotate(page.rotate * 3.141592653589793 / 180)
        uw, uh = (fh, fw) if page.rotate % 180 else (fw, fh)
        cr.set_source_rgb(1, 1, 1)
        cr.rectangle(-uw / 2, -uh / 2, uw, uh)
        cr.fill()
        surf = self.thumbs.get(page.src) if page.src is not None else None
        if surf is not None:
            cr.save()
            cr.translate(-uw / 2, -uh / 2)
            cr.scale(uw / surf.get_width(), uh / surf.get_height())
            cr.set_source_surface(surf, 0, 0)
            cr.paint()
            cr.restore()
        elif page.src is None:
            cr.set_source_rgba(0, 0, 0, 0.3)
            cr.select_font_face("Noto Sans CJK TC")
            cr.set_font_size(12)
            ext = cr.text_extents("空白頁")
            cr.move_to(-ext.width / 2, ext.height / 2)
            cr.show_text("空白頁")

    # ------------------------------------------------------------ 介面合約

    def validate(self) -> str | None:
        return self.model.validate()

    def destroy(self) -> None:
        self._token += 1
        clear(self.flow)
