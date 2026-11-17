"""工具頁右側的選項面板：依 tool.options 自動畫出所有段落與控制項。

段落（OptionGroup）＝連續同一個 group 的選項；段落裡連續的開關放進同一個圓角清單。
選項值或檔案資訊一變，就重新評估每個控制項（顯示、啟用、說明、動態選項）。
"""

from __future__ import annotations

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from ..core.options import Kind  # noqa: E402
from . import options_model as om  # noqa: E402
from .option_widgets import Control, ToggleControl, make_control  # noqa: E402
from .widgets import hbox, label, sf_icon, vbox  # noqa: E402


class _Section:
    def __init__(self, spec: om.GroupSpec, state: om.OptionsState) -> None:
        self.box = vbox(10)
        self.controls: list[Control] = []
        self.lists: list[tuple[Gtk.ListBox, list[ToggleControl]]] = []
        if spec.title:
            head = hbox(6)
            if spec.icon:
                head.append(sf_icon(spec.icon, 16, "qs-dim"))
            head.append(label(spec.title, "qs-group-title"))
            self.box.append(head)
        current: tuple[Gtk.ListBox, list[ToggleControl]] | None = None
        for opt in spec.options:
            ctl = make_control(opt, state)
            self.controls.append(ctl)
            if opt.kind is Kind.TOGGLE and isinstance(ctl, ToggleControl):
                if current is None:
                    lb = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
                    lb.add_css_class("boxed-list")
                    current = (lb, [])
                    self.lists.append(current)
                    self.box.append(lb)
                current[0].append(ctl.row)
                current[1].append(ctl)
                continue
            current = None
            self.box.append(ctl.root)

    def refresh(self) -> bool:
        for ctl in self.controls:
            ctl.refresh()
        for lb, toggles in self.lists:
            lb.set_visible(any(t.root.get_visible() for t in toggles))
        shown = any(c.root.get_visible() for c in self.controls)
        self.box.set_visible(shown)
        return shown


class OptionsPanel(Gtk.Box):
    """所有選項；refresh() 由選項狀態的變動觸發。"""

    def __init__(self, state: om.OptionsState) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=20)
        self.state = state
        self.sections = [_Section(spec, state) for spec in om.group_options(state.tool.options)]
        for sec in self.sections:
            self.append(sec.box)
        self._unsub = state.subscribe(lambda _key: self.refresh())
        self.refresh()

    def refresh(self) -> None:
        any_shown = False
        for sec in self.sections:
            any_shown = sec.refresh() or any_shown
        self.set_visible(any_shown)

    def control(self, key: str) -> Control | None:
        for sec in self.sections:
            for ctl in sec.controls:
                if ctl.opt.key == key:
                    return ctl
        return None

    def detach(self) -> None:
        self._unsub()
