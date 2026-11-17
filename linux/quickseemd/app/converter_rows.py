"""萬用轉換左側的檔案列（對應原版 CVJobRow）。"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gdk, Gtk  # noqa: E402

from .converter_model import ConvJob, status_text  # noqa: E402
from .widgets import FileThumb, hbox, icon, label, link, mime_icon, vbox  # noqa: E402

THUMB = 48


@dataclass
class RowHooks:
    select: Callable[[int, bool], None]
    remove: Callable[[int], None]
    reveal: Callable[[list[Path]], None]
    open: Callable[[Path], None]


def _thumb(job: ConvJob) -> Gtk.Widget:
    if job.is_image or job.is_video or job.is_pdf:
        return FileThumb(job.path, THUMB, THUMB)
    img = mime_icon(job.path, 40)
    img.set_size_request(THUMB, THUMB)
    return img


def _pill(job: ConvJob) -> Gtk.Widget:
    if job.analyzing:
        return Gtk.Spinner(spinning=True)
    if job.target is not None:
        pill = label(f"→ {job.target.format.display_name}", "qs-target-pill")
    elif not job.targets:
        pill = label("無法轉換", "qs-target-pill", "bad")
    else:
        pill = label("選擇格式", "qs-target-pill", "empty")
    pill.set_valign(Gtk.Align.CENTER)
    return pill


def _meta(job: ConvJob) -> str:
    parts = [p for p in (job.size_text, job.kind, job.detail) if p]
    return " · ".join(parts)


def _status(job: ConvJob, hooks: RowHooks) -> Gtk.Widget | None:
    st = job.status
    text = status_text(st)
    if st.kind == "idle":
        return None
    if st.kind == "running":
        bar = Gtk.ProgressBar(fraction=st.fraction, hexpand=True, valign=Gtk.Align.CENTER)
        return hbox(8, bar, label(text, "qs-small", "qs-dim", "qs-mono"))
    if st.kind == "done":
        row = hbox(6, icon("emblem-ok-symbolic", 14, "qs-success"))
        row.append(label(text, "qs-small", "qs-success", ellipsize="middle"))
        outs = list(st.outputs)
        row.append(link("在資料夾中顯示", on_click=lambda: hooks.reveal(outs)))
        return row
    if st.kind == "failed":
        return label(
            text, "qs-small", "qs-warning", wrap=True, lines=4, ellipsize="end", selectable=True
        )
    name = "document-open-recent-symbolic" if st.kind == "queued" else "process-stop-symbolic"
    return hbox(6, icon(name, 14, "qs-dim"), label(text, "qs-small", "qs-dim"))


def _menu(row: Gtk.Widget, job: ConvJob, hooks: RowHooks) -> Gtk.Popover:
    pop = Gtk.Popover()
    box = vbox(2)

    def item(text: str, fn: Callable[[], None]) -> None:
        box.append(link(text, on_click=lambda: (pop.popdown(), fn())))

    outs = list(job.status.outputs)
    if outs:
        item("在資料夾中顯示", lambda: hooks.reveal(outs))
        item("打開", lambda: hooks.open(outs[0]))
    item("在資料夾中顯示原始檔", lambda: hooks.reveal([job.path]))
    if job.status.kind != "running":
        item("從清單移除", lambda: hooks.remove(job.uid))
    pop.set_child(box)
    pop.set_parent(row)
    return pop


def job_row(job: ConvJob, selected: bool, hooks: RowHooks) -> Gtk.Widget:
    body = vbox(3, hexpand=True)
    top = hbox(8, label(job.name, "qs-semibold", ellipsize="middle", xalign=0), _pill(job))
    top.get_first_child().set_hexpand(True)
    body.append(top)
    body.append(label(_meta(job), "qs-small", "qs-dim", ellipsize="end"))
    status = _status(job, hooks)
    if status is not None:
        body.append(status)
    rm = Gtk.Button(icon_name="window-close-symbolic", tooltip_text="從清單移除")
    rm.add_css_class("flat")
    rm.add_css_class("circular")
    rm.set_valign(Gtk.Align.START)
    rm.set_sensitive(job.status.kind != "running")
    rm.connect("clicked", lambda _b: hooks.remove(job.uid))
    row = hbox(10, _thumb(job), body, rm)
    row.add_css_class("qs-job-row")
    if selected:
        row.add_css_class("selected")
    row.set_tooltip_text(str(job.path))
    pop = _menu(row, job, hooks)
    click = Gtk.GestureClick(button=0)

    def pressed(g: Gtk.GestureClick, _n: int, _x: float, _y: float) -> None:
        if g.get_current_button() == 3:
            pop.popup()
            return
        state = g.get_current_event_state()
        extend = bool(state & (Gdk.ModifierType.CONTROL_MASK | Gdk.ModifierType.SHIFT_MASK))
        hooks.select(job.uid, extend)

    click.connect("released", pressed)
    row.add_controller(click)
    return row
