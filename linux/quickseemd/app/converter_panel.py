"""萬用轉換右側（對應原版 CVTargetPanel／CVOptionsCard）：格式、選項、合併、目前無法輸出。"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from . import converter_model as cmod  # noqa: E402
from .widgets import clear, hbox, icon, label, secondary, sf_icon, vbox  # noqa: E402

SECTION_ICONS = {
    "quality": "dialog-information-symbolic",
    "size": "view-fullscreen-symbolic",
    "pdf_dpi": "document-open-symbolic",
    "video_size": "video-x-generic-symbolic",
    "gif": "image-x-generic-symbolic",
    "frame": "camera-photo-symbolic",
    "bitrate": "audio-x-generic-symbolic",
    "encoding": "text-x-generic-symbolic",
}


def _card(*children: Gtk.Widget, spacing: int = 12) -> Gtk.Box:
    box = vbox(spacing, *children)
    box.add_css_class("qs-cv-card")
    return box


def _dropdown(
    choices: list[tuple[Any, str]], current: Any, on_pick: Callable[[Any], None]
) -> Gtk.DropDown:
    dd = Gtk.DropDown.new_from_strings([t for _v, t in choices])
    values = [v for v, _t in choices]
    dd.set_selected(values.index(current) if current in values else 0)
    dd.connect("notify::selected", lambda d, _p: on_pick(values[d.get_selected()]))
    dd.set_halign(Gtk.Align.START)
    return dd


def _segmented(choices: tuple, current: Any, on_pick: Callable[[Any], None]) -> Gtk.Widget:
    box = hbox(0, halign=Gtk.Align.START)
    box.add_css_class("linked")
    first: Gtk.ToggleButton | None = None
    for value, title in choices:
        btn = Gtk.ToggleButton(label=title, active=value == current)
        if first is None:
            first = btn
        else:
            btn.set_group(first)
        btn.connect("toggled", lambda b, v=value: b.get_active() and on_pick(v))
        box.append(btn)
    return box


class ConverterPanel(Gtk.Box):
    """右側所有卡片；refresh() 依模型重畫（選項卡只在區塊改變時重建）。"""

    def __init__(self, model: cmod.ConverterModel, actions: dict[str, Callable[..., Any]]) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self.model = model
        self.actions = actions
        self._sections: list[str] | None = None
        self.banner_box = vbox(0)
        self.targets = vbox(0)
        self.options_box = vbox(0)
        self.merge_box = vbox(0)
        self.unavailable_box = vbox(0)
        for w in (
            self.banner_box,
            self.targets,
            self.options_box,
            self.merge_box,
            self.unavailable_box,
        ):
            self.append(w)

    def refresh(self) -> None:
        self._banner()
        self._targets()
        self._options()
        self._merge()
        self._unavailable()

    # ------------------------------------------------------------ 橫幅

    def _banner(self) -> None:
        clear(self.banner_box)
        b = self.model.banner
        if b is None:
            return
        name = "dialog-warning-symbolic" if b.is_error else "emblem-ok-symbolic"
        msg = label(b.message, "qs-semibold", wrap=True, selectable=True)
        msg.set_hexpand(True)
        row = hbox(10, icon(name, 18, "qs-warning" if b.is_error else "qs-success"), msg)
        if b.paths:
            paths = list(b.paths)
            row.append(secondary("在資料夾中顯示", on_click=lambda: self.actions["reveal"](paths)))
        close = Gtk.Button(icon_name="window-close-symbolic", tooltip_text="關閉")
        close.add_css_class("flat")
        close.connect("clicked", lambda _b: self.actions["close_banner"]())
        row.append(close)
        self.banner_box.append(_card(row))

    # ------------------------------------------------------------ 轉換成

    def _targets(self) -> None:
        clear(self.targets)
        m = self.model
        head = hbox(
            8,
            label("轉換成", "title-4"),
            label(m.scope_description(), "qs-dim", ellipsize="middle"),
        )
        if m.analyzing_scope():
            head.append(Gtk.Spinner(spinning=True))
        card = _card(head, spacing=14)
        if m.engine_missing:
            card.append(
                hbox(
                    10,
                    icon("dialog-warning-symbolic", 20, "qs-warning"),
                    label("轉換引擎尚未安裝", "qs-warning", "qs-semibold"),
                )
            )
            self.targets.append(card)
            return
        if m.scope_is_mixed():
            hint = "選取的檔案類型不同，只列出它們都能轉換的格式。點選左側單一檔案可個別設定。"
            card.append(
                hbox(
                    6,
                    icon("dialog-information-symbolic", 14, "qs-dim"),
                    label(hint, "qs-small", "qs-dim", wrap=True),
                )
            )
        groups = m.grouped_targets()
        if not groups and not m.analyzing_scope():
            text = (
                "這些檔案沒有共同可轉換的格式。"
                if len(m.scope_jobs()) > 1
                else "這個檔案目前沒有可轉換的格式。"
            )
            card.append(
                hbox(10, icon("action-unavailable-symbolic", 20, "qs-dim"), label(text, "qs-dim"))
            )
        for cat, formats in groups:
            card.append(self._group(cat, formats))
        self.targets.append(card)

    def _group(self, cat: Any, formats: list) -> Gtk.Widget:
        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, column_spacing=7, row_spacing=7)
        flow.set_max_children_per_line(20)
        flow.add_css_class("qs-chips")
        for t in formats:
            all_set, some = self.model.selection_state(t.id)
            btn = Gtk.Button(label=t.format.display_name, tooltip_text=cmod.format_tooltip(t))
            btn.add_css_class("qs-format-chip")
            if all_set:
                btn.add_css_class("selected")
            elif some:
                btn.add_css_class("partial")
            btn.connect("clicked", lambda _b, fid=t.id: self.actions["choose"](fid))
            flow.append(btn)
        head = hbox(
            6,
            sf_icon(cat.icon, 14, "qs-dim"),
            label(cat.title, "qs-small", "qs-dim", "qs-semibold"),
        )
        return vbox(7, head, flow)

    # ------------------------------------------------------------ 選項

    def _options(self) -> None:
        sections = self.model.relevant_sections()
        if sections == self._sections:
            self._update_detected()
            return
        self._sections = sections
        clear(self.options_box)
        if not sections:
            return
        card = _card(hbox(6, icon("preferences-system-symbolic", 16), label("選項", "qs-semibold")))
        for i, sec in enumerate(sections):
            if i:
                card.append(Gtk.Separator())
            card.append(self._section(sec))
        self.options_box.append(card)

    def _section(self, sec: str) -> Gtk.Widget:
        title, foot = cmod.SECTION_TITLES[sec]
        head = hbox(
            6,
            icon(SECTION_ICONS[sec], 14, "qs-dim"),
            label(title, "qs-small", "qs-dim", "qs-semibold"),
        )
        control = getattr(self, f"_ctl_{sec}")()
        return vbox(6, head, control, label(foot, "qs-tiny", "qs-dim", wrap=True))

    def _set(self, key: str) -> Callable[[Any], None]:
        return lambda v: self.model.set_option(key, v)

    def _ctl_quality(self) -> Gtk.Widget:
        q = float(self.model.options["image_quality"])
        scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0.3, 1.0, 0.05)
        scale.set_value(q)
        scale.set_draw_value(False)
        scale.set_hexpand(True)
        text = label(cmod.quality_text(q), "qs-small", "qs-dim", "qs-mono")

        def changed(s: Gtk.Scale) -> None:
            v = round(round(s.get_value() / 0.05) * 0.05, 2)
            text.set_label(cmod.quality_text(v))
            self.model.set_option("image_quality", v)

        scale.connect("value-changed", changed)
        return hbox(10, scale, text)

    def _ctl_size(self) -> Gtk.Widget:
        return _dropdown(
            list(cmod.SIZE_CHOICES),
            self.model.options["image_max_pixel"],
            self._set("image_max_pixel"),
        )

    def _ctl_pdf_dpi(self) -> Gtk.Widget:
        return _dropdown(
            list(cmod.DPI_CHOICES), self.model.options["pdf_dpi"], self._set("pdf_dpi")
        )

    def _ctl_video_size(self) -> Gtk.Widget:
        return _segmented(
            cmod.VIDEO_SIZES, self.model.options["video_size"], self._set("video_size")
        )

    def _ctl_bitrate(self) -> Gtk.Widget:
        return _segmented(
            cmod.BITRATES, self.model.options["audio_bitrate"], self._set("audio_bitrate")
        )

    def _ctl_gif(self) -> Gtk.Widget:
        o = self.model.options
        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, column_spacing=14, row_spacing=8)
        flow.set_max_children_per_line(3)
        for title, key, values, unit in (
            ("每秒張數", "gif_fps", cmod.GIF_FPS, "fps"),
            ("寬度", "gif_width", cmod.GIF_WIDTHS, "px"),
            ("最長", "gif_seconds", cmod.GIF_SECONDS, "秒"),
        ):
            choices = [(v, f"{v} {unit}") for v in values]
            flow.append(
                hbox(6, label(title, "qs-small"), _dropdown(choices, o[key], self._set(key)))
            )
        return flow

    def _ctl_frame(self) -> Gtk.Widget:
        current = self.model.options["frame_time"]
        auto = Gtk.CheckButton(label="自動", active=current is None)
        spin = Gtk.SpinButton.new_with_range(0, 36000, 0.5)
        spin.set_digits(2)
        spin.set_value(float(current or 0))
        unit = label("秒", "qs-dim")
        spin.set_visible(current is not None)
        unit.set_visible(current is not None)

        def toggled(c: Gtk.CheckButton) -> None:
            on = c.get_active()
            spin.set_visible(not on)
            unit.set_visible(not on)
            self.model.set_option("frame_time", None if on else max(0.0, spin.get_value()))

        auto.connect("toggled", toggled)
        spin.connect(
            "value-changed", lambda s: self.model.set_option("frame_time", max(0.0, s.get_value()))
        )
        return hbox(8, auto, spin, unit)

    def _ctl_encoding(self) -> Gtk.Widget:
        self._detected = label("", "qs-tiny", "qs-dim")
        dd = _dropdown(
            list(cmod.ENCODINGS), self.model.options["source_encoding"], self._encoding_picked
        )
        self._update_detected()
        return hbox(10, dd, self._detected)

    def _encoding_picked(self, value: Any) -> None:
        self.model.set_option("source_encoding", value)
        self._update_detected()

    def _update_detected(self) -> None:
        lbl = getattr(self, "_detected", None)
        if lbl is None:
            return
        detected = self.model.detected_encoding()
        auto = self.model.options["source_encoding"] == "auto"
        lbl.set_label(f"偵測到：{detected}" if detected and auto else "")

    # ------------------------------------------------------------ 合併與無法輸出

    def _merge(self) -> None:
        clear(self.merge_box)
        n = len(self.model.mergeable_jobs())
        if n < 2:
            return
        text = vbox(2, label("合併成一個 PDF", "qs-semibold"))
        text.append(
            label(f"依清單順序，把 {n} 個圖片／PDF 合成一份文件", "qs-small", "qs-dim", wrap=True)
        )
        text.set_hexpand(True)
        btn = secondary("合併", on_click=self.actions["merge"])
        btn.set_sensitive(not self.model.running)
        btn.set_valign(Gtk.Align.CENTER)
        row = hbox(12, icon("list-add-symbolic", 20, "qs-tinted"), text, btn)
        self.merge_box.append(_card(row))

    def _unavailable(self) -> None:
        clear(self.unavailable_box)
        hints = self.model.unavailable_hints()
        if not hints:
            return
        card = _card(
            hbox(
                6,
                icon("changes-prevent-symbolic", 14, "qs-dim"),
                label("目前無法輸出", "qs-small", "qs-dim", "qs-semibold"),
            ),
            spacing=9,
        )
        for h in hints:
            reason = label(h.reason, "qs-tiny", "qs-dim", wrap=True)
            reason.set_hexpand(True)
            card.append(hbox(8, label(h.formats, "qs-small", "qs-dim", "qs-semibold"), reason))
        self.unavailable_box.append(card)
