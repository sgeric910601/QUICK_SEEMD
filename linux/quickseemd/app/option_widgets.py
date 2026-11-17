"""選項控制項（對應原版 ToolOptionControls），依 options.Kind 自動產生。

每個控制項：
- build() 建立元件；refresh() 依目前的值與檔案資訊更新顯示／啟用／說明／動態選項
- 使用者操作時呼叫 emit(值) 寫回 OptionsState（更新畫面時用 _syncing 擋住回音）
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from ..core.options import Choice, Kind, Option  # noqa: E402
from ..core.pagerange import PLACEHOLDER as PAGE_PLACEHOLDER  # noqa: E402
from . import css  # noqa: E402
from . import options_model as om  # noqa: E402
from .widgets import clear, hbox, icon, label, sf_icon_optional, vbox  # noqa: E402

LOG = logging.getLogger(__name__)
TIME_HINT = "時間格式不正確，例如 1:05.5 或 65"


def apply_range(adj: Gtk.Adjustment, rng: tuple[float, float]) -> None:
    """動態範圍：範圍變了才更新（避免拖曳中跳動）。"""
    lo, hi = rng
    if adj.get_lower() != lo:
        adj.set_lower(lo)
    if adj.get_upper() != hi:
        adj.set_upper(hi)


def with_suggestions(field: Gtk.Widget, opt: Option, emit) -> Gtk.Widget:  # noqa: ANN001
    """欄位旁的快速填入按鈕（例如 10／25／50 MB），按下就填入 choice.value。"""
    if not opt.suggestions:
        return field
    row = hbox(6, field)
    for ch in opt.suggestions:
        btn = Gtk.Button(label=ch.title, tooltip_text=ch.subtitle)
        btn.add_css_class("qs-chip")
        btn.connect("clicked", lambda _b, v=ch.value: emit(v))
        row.append(btn)
    return row


class Control:
    """一個選項的控制項：標題（有的話）＋本體＋固定說明＋動態說明。"""

    show_title = True

    def __init__(
        self, opt: Option, state: om.OptionsState, section_title: str | None = None
    ) -> None:
        self.opt = opt
        self.state = state
        self._syncing = False
        self._sig: tuple = ()
        self.root = vbox(6)
        if self.show_title and opt.label and opt.label != section_title:
            self.root.append(label(opt.label, "qs-semibold"))
        self.body = self.build()
        self.root.append(self.body)
        self._caption = label(opt.caption or "", "qs-small", "qs-dim", wrap=True)
        self._caption.set_visible(bool(opt.caption) and self.show_caption())
        self.root.append(self._caption)
        self._note = label("", "qs-small", wrap=True)
        self._note.set_visible(False)
        self.root.append(self._note)

    # 子類別實作
    def build(self) -> Gtk.Widget:
        raise NotImplementedError

    def sync(self) -> None:
        raise NotImplementedError

    def show_caption(self) -> bool:
        return True

    @property
    def value(self) -> Any:
        return self.state.values.get(self.opt.key)

    def emit(self, value: Any) -> None:
        if not self._syncing:
            self.state.set_value(self.opt.key, value)

    def refresh(self) -> None:
        visible = self.state.visible(self.opt)
        self.root.set_visible(visible)
        if not visible:
            return
        self.root.set_sensitive(self.state.enabled(self.opt))
        self._syncing = True
        try:
            self.sync()
        finally:
            self._syncing = False
        self._show_note(self.state.note(self.opt))

    def _show_note(self, note: tuple[str, bool] | None) -> None:
        self._note.set_visible(note is not None)
        if note is None:
            return
        self._note.set_label(note[0])
        for cls, on in (("qs-warning", note[1]), ("qs-dim", not note[1])):
            (self._note.add_css_class if on else self._note.remove_css_class)(cls)

    def choices_changed(self) -> tuple[Choice, ...] | None:
        """動態選項變了就回傳新的清單（要重建按鈕），沒變回傳 None。"""
        choices = self.state.choices(self.opt)
        sig = om.choices_signature(choices)
        if sig == self._sig:
            return None
        self._sig = sig
        return choices


# ---------------------------------------------------------------- 單選類


class CardsControl(Control):
    def build(self) -> Gtk.Widget:
        self._buttons: list[tuple[Any, Gtk.Button, Gtk.Image]] = []
        return vbox(8)

    def _card(self, ch: Choice) -> Gtk.Button:
        btn = Gtk.Button()
        btn.add_css_class("qs-choice-card")
        row = hbox(10)
        img = sf_icon_optional(ch.icon, 17, "qs-tinted")
        if img is not None:
            row.append(img)
        text = vbox(2)
        title = hbox(6, label(ch.title, "qs-semibold"))
        if ch.badge:
            title.append(label(ch.badge, "qs-choice-badge"))
        text.append(title)
        if ch.subtitle:
            text.append(label(ch.subtitle, "qs-small", "qs-dim", wrap=True))
        text.set_hexpand(True)
        row.append(text)
        mark = icon("radio-symbolic", 18, "qs-radio")
        row.append(mark)
        btn.set_child(row)
        btn.connect("clicked", lambda _b, v=ch.value: self.emit(v))
        self._buttons.append((ch.value, btn, mark))
        return btn

    def sync(self) -> None:
        choices = self.choices_changed()
        if choices is not None:
            clear(self.body)
            self._buttons = []
            for ch in choices:
                self.body.append(self._card(ch))
        for value, btn, mark in self._buttons:
            on = value == self.value
            (btn.add_css_class if on else btn.remove_css_class)("selected")
            mark.set_from_icon_name("radio-checked-symbolic" if on else "radio-symbolic")


class ChipsControl(Control):
    def build(self) -> Gtk.Widget:
        self._buttons: list[tuple[Any, Gtk.Button]] = []
        box = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, column_spacing=6, row_spacing=6)
        box.set_max_children_per_line(30)
        box.add_css_class("qs-chips")
        return box

    def sync(self) -> None:
        choices = self.choices_changed()
        if choices is not None:
            clear(self.body)
            self._buttons = []
            for ch in choices:
                btn = Gtk.Button(label=ch.title, tooltip_text=ch.subtitle)
                btn.add_css_class("qs-chip")
                btn.connect("clicked", lambda _b, v=ch.value: self.emit(v))
                self.body.append(btn)
                self._buttons.append((ch.value, btn))
        for value, btn in self._buttons:
            (btn.add_css_class if value == self.value else btn.remove_css_class)("selected")


class SegmentedControl(Control):
    def build(self) -> Gtk.Widget:
        self._buttons: list[tuple[Any, Gtk.ToggleButton]] = []
        box = hbox(0, homogeneous=True)
        box.add_css_class("linked")
        return box

    def _toggled(self, btn: Gtk.ToggleButton, value: Any) -> None:
        if btn.get_active():
            self.emit(value)

    def sync(self) -> None:
        choices = self.choices_changed()
        if choices is not None:
            clear(self.body)
            self._buttons = []
            first: Gtk.ToggleButton | None = None
            for ch in choices:
                btn = Gtk.ToggleButton(label=ch.title, tooltip_text=ch.subtitle)
                if first is None:
                    first = btn
                else:
                    btn.set_group(first)
                btn.connect("toggled", self._toggled, ch.value)
                self.body.append(btn)
                self._buttons.append((ch.value, btn))
        for value, btn in self._buttons:
            if value == self.value and not btn.get_active():
                btn.set_active(True)


class GridControl(Control):
    def build(self) -> Gtk.Widget:
        self._buttons: list[tuple[Any, Gtk.ToggleButton]] = []
        return Gtk.Grid(column_spacing=4, row_spacing=4, halign=Gtk.Align.START)

    def _toggled(self, btn: Gtk.ToggleButton, value: Any) -> None:
        if self._syncing:
            return
        if btn.get_active():
            self.emit(value)
        elif value == self.value:
            self._syncing = True
            btn.set_active(True)
            self._syncing = False

    def _rebuild(self, choices: tuple[Choice, ...]) -> None:
        clear(self.body)
        self._buttons = []
        for r, row in enumerate(om.grid_rows(self.opt, choices)):
            for c, ch in enumerate(row):
                btn = Gtk.ToggleButton(label=ch.title, tooltip_text=ch.subtitle)
                btn.add_css_class("qs-grid-cell")
                if ch.value is None:
                    btn.set_sensitive(False)
                else:
                    btn.connect("toggled", self._toggled, ch.value)
                    self._buttons.append((ch.value, btn))
                self.body.attach(btn, c, r, 1, 1)

    def sync(self) -> None:
        choices = self.choices_changed()
        if choices is not None:
            self._rebuild(choices)
        for value, btn in self._buttons:
            btn.set_active(value == self.value)


# ---------------------------------------------------------------- 數值類


class SliderControl(Control):
    show_title = False

    def build(self) -> Gtk.Widget:
        lo, hi = om.number_range(self.opt)
        step = float(self.opt.step or 1)
        self._value_label = label("", "qs-slider-value", xalign=1)
        head = hbox(8, label(self.opt.label, "qs-semibold"))
        head.get_first_child().set_hexpand(True)
        head.append(self._value_label)
        adj = Gtk.Adjustment(lower=lo, upper=hi, step_increment=step, page_increment=step)
        self._scale = Gtk.Scale(orientation=Gtk.Orientation.HORIZONTAL, adjustment=adj)
        self._scale.set_draw_value(False)
        self._scale.set_digits(om.digits_for(self.opt))
        self._scale.connect("value-changed", self._changed)
        return vbox(2, head, self._scale)

    def _changed(self, scale: Gtk.Scale) -> None:
        v = om.coerce_number(self.opt, scale.get_value(), self.state.range(self.opt))
        self._value_label.set_label(om.slider_text(self.opt, v))
        self.emit(v)

    def sync(self) -> None:
        apply_range(self._scale.get_adjustment(), self.state.range(self.opt))
        v = self.value if self.value is not None else self.opt.minimum or 0
        if abs(self._scale.get_value() - float(v)) > 1e-9:
            self._scale.set_value(float(v))
        self._value_label.set_label(om.slider_text(self.opt, v))


class NumberControl(Control):
    def build(self) -> Gtk.Widget:
        lo, hi = om.number_range(self.opt)
        step = float(self.opt.step or 1)
        adj = Gtk.Adjustment(lower=lo, upper=hi, step_increment=step, page_increment=step * 10)
        self._spin = Gtk.SpinButton(adjustment=adj, digits=om.digits_for(self.opt))
        self._spin.set_numeric(True)
        self._spin.set_halign(Gtk.Align.START)
        self._spin.connect("value-changed", self._changed)
        return with_suggestions(self._spin, self.opt, self.emit)

    def _changed(self, spin: Gtk.SpinButton) -> None:
        self.emit(om.coerce_number(self.opt, spin.get_value(), self.state.range(self.opt)))

    def sync(self) -> None:
        apply_range(self._spin.get_adjustment(), self.state.range(self.opt))
        v = self.value
        if v is not None and abs(self._spin.get_value() - float(v)) > 1e-9:
            self._spin.set_value(float(v))


class ToggleControl(Control):
    """開關：Adw.SwitchRow（連續的開關由面板放進同一個圓角清單）。"""

    show_title = False

    def build(self) -> Gtk.Widget:
        self.row = Adw.SwitchRow(title=self.opt.label or "", subtitle=self.opt.caption or "")
        self.row.connect("notify::active", lambda r, _p: self.emit(bool(r.get_active())))
        return Gtk.Box(visible=False)  # 本體放在面板的清單裡

    def show_caption(self) -> bool:
        return False

    def refresh(self) -> None:
        super().refresh()
        self.row.set_visible(self.root.get_visible())
        self.row.set_sensitive(self.root.get_sensitive())
        note = self.state.note(self.opt) if self.root.get_visible() else None
        self.row.set_subtitle(note[0] if note else (self.opt.caption or ""))
        self._note.set_visible(False)

    def sync(self) -> None:
        on = bool(self.value)
        if self.row.get_active() != on:
            self.row.set_active(on)


# ---------------------------------------------------------------- 文字類


class TextControl(Control):
    password = False

    def placeholder(self) -> str:
        return self.opt.placeholder or ""

    def build(self) -> Gtk.Widget:
        if self.password:
            self._entry = Gtk.PasswordEntry(show_peek_icon=True)
            self._entry.set_property("placeholder-text", self.placeholder())
        else:
            self._entry = Gtk.Entry(placeholder_text=self.placeholder())
        self._entry.connect("changed", self._changed)
        if self.password:
            return self._entry
        return with_suggestions(self._entry, self.opt, self.emit)

    def _changed(self, entry: Gtk.Editable) -> None:
        self.emit(entry.get_text())

    def sync(self) -> None:
        text = "" if self.value is None else str(self.value)
        if self._entry.get_text() != text:
            self._entry.set_text(text)


class PasswordControl(TextControl):
    password = True


class PageRangeControl(TextControl):
    def placeholder(self) -> str:
        return self.opt.placeholder or PAGE_PLACEHOLDER


class TimeControl(TextControl):
    def placeholder(self) -> str:
        return self.opt.placeholder or "0:00.0"

    def sync(self) -> None:
        super().sync()
        ok = om.time_ok(self._entry.get_text())
        pos = Gtk.EntryIconPosition.SECONDARY
        self._entry.set_icon_from_icon_name(pos, None if ok else "dialog-warning-symbolic")
        self._entry.set_icon_tooltip_text(pos, None if ok else TIME_HINT)


# ---------------------------------------------------------------- 其他


class ColorControl(Control):
    def build(self) -> Gtk.Widget:
        self._dots: list[tuple[Any, Gtk.Widget]] = []
        self._custom: Gtk.ColorDialogButton | None = None
        return hbox(8)

    def _rebuild(self, choices: tuple[Choice, ...]) -> None:
        clear(self.body)
        self._dots, self._custom = [], None
        for ch in choices:
            if ch.value == "custom":
                dlg = Gtk.ColorDialog(title=ch.title or "自訂顏色", with_alpha=False)
                self._custom = Gtk.ColorDialogButton(dialog=dlg, tooltip_text=ch.title)
                self._custom.connect("notify::rgba", self._custom_changed)
                self.body.append(self._custom)
                self._dots.append((ch.value, self._custom))
                continue
            btn = Gtk.Button(tooltip_text=ch.title)
            btn.add_css_class("qs-dot")
            btn.add_css_class(css.dot_class(str(ch.value)))
            btn.connect("clicked", lambda _b, v=ch.value: self.emit(v))
            self.body.append(btn)
            self._dots.append((ch.value, btn))

    def _custom_changed(self, btn: Gtk.ColorDialogButton, _p: object) -> None:
        if self._syncing:
            return
        c = btn.get_rgba()
        hex_value = om.rgb_to_hex(c.red, c.green, c.blue)
        if self.opt.custom_key:
            self.state.set_value(self.opt.custom_key, hex_value)
        self.emit("custom")

    def sync(self) -> None:
        choices = self.choices_changed()
        if choices is not None:
            self._rebuild(choices)
        for value, w in self._dots:
            (w.add_css_class if value == self.value else w.remove_css_class)("selected")
        if self._custom is not None and self.opt.custom_key:
            rgb = om.hex_to_rgb(str(self.state.values.get(self.opt.custom_key) or ""))
            if rgb is not None:
                rgba = Gdk.RGBA()
                rgba.red, rgba.green, rgba.blue, rgba.alpha = (*rgb, 1.0)
                if not self._custom.get_rgba().equal(rgba):
                    self._custom.set_rgba(rgba)


class FileControl(Control):
    def build(self) -> Gtk.Widget:
        self._name = label("選擇檔案…", ellipsize="middle")
        pick = Gtk.Button()
        pick.set_child(hbox(6, icon("document-open-symbolic"), self._name))
        pick.connect("clicked", lambda _b: self._choose())
        self._clear = Gtk.Button(icon_name="edit-clear-symbolic", tooltip_text="清除")
        self._clear.add_css_class("flat")
        self._clear.connect("clicked", lambda _b: self.emit(""))
        return hbox(6, pick, self._clear)

    def _choose(self) -> None:
        dlg = Gtk.FileDialog(title=self.opt.label or "選擇檔案", accept_label="選擇", modal=True)
        if self.opt.file_mime:
            flt = Gtk.FileFilter(name=self.opt.label or self.opt.file_mime)
            flt.add_mime_type(self.opt.file_mime)
            store = Gio.ListStore.new(Gtk.FileFilter)
            store.append(flt)
            dlg.set_filters(store)
        dlg.open(self.root.get_root(), None, self._picked)

    def _picked(self, dlg: Gtk.FileDialog, res: Gio.AsyncResult) -> None:
        try:
            f = dlg.open_finish(res)
        except GLib.Error:
            return  # 使用者取消
        if f is not None and f.get_path():
            self.emit(f.get_path())

    def sync(self) -> None:
        path = str(self.value or "")
        self._name.set_label(Path(path).name if path else "選擇檔案…")
        self._clear.set_visible(bool(path))


class NoteControl(Control):
    show_title = False

    def build(self) -> Gtk.Widget:
        self._icon = icon("dialog-information-symbolic", 16, "qs-dim")
        self._text = label("", "qs-small", wrap=True)
        self._text.set_hexpand(True)
        box = hbox(8, self._icon, self._text)
        box.add_css_class("qs-note")
        return box

    def show_caption(self) -> bool:
        return False

    def refresh(self) -> None:
        super().refresh()
        self._note.set_visible(False)

    def sync(self) -> None:
        note = self.state.note(self.opt)
        if note is None:
            return
        self._text.set_label(note[0])
        warn = note[1]
        self._icon.set_from_icon_name(
            "dialog-warning-symbolic" if warn else "dialog-information-symbolic"
        )
        (self.body.add_css_class if warn else self.body.remove_css_class)("warn")


class ChecksControl(Control):
    def build(self) -> Gtk.Widget:
        self._checks: list[tuple[Any, Gtk.CheckButton]] = []
        return vbox(4)

    def _toggled(self, btn: Gtk.CheckButton, value: Any) -> None:
        if not self._syncing:
            self.emit(om.toggle_check(self.value, value, btn.get_active()))

    def sync(self) -> None:
        choices = self.choices_changed()
        if choices is not None:
            clear(self.body)
            self._checks = []
            for ch in choices:
                chk = Gtk.CheckButton(label=ch.title, tooltip_text=ch.subtitle)
                chk.connect("toggled", self._toggled, ch.value)
                self.body.append(chk)
                self._checks.append((ch.value, chk))
        current = list(self.value or [])
        for value, chk in self._checks:
            chk.set_active(value in current)


CONTROLS: dict[Kind, type[Control]] = {
    Kind.CARDS: CardsControl,
    Kind.CHIPS: ChipsControl,
    Kind.SEGMENTED: SegmentedControl,
    Kind.SLIDER: SliderControl,
    Kind.TOGGLE: ToggleControl,
    Kind.TEXT: TextControl,
    Kind.PASSWORD: PasswordControl,
    Kind.NUMBER: NumberControl,
    Kind.PAGE_RANGE: PageRangeControl,
    Kind.COLOR: ColorControl,
    Kind.GRID: GridControl,
    Kind.FILE: FileControl,
    Kind.TIME: TimeControl,
    Kind.NOTE: NoteControl,
    Kind.CHECKS: ChecksControl,
}


def make_control(opt: Option, state: om.OptionsState, section_title: str | None = None) -> Control:
    return CONTROLS[opt.kind](opt, state, section_title)
