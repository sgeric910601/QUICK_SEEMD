"""完成頁（對應原版 ToolResultView）：標題、摘要、輸出檔卡片、在資料夾中顯示、接著可以…"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from ..core import registry, result  # noqa: E402
from ..core.model import ToolDefinition  # noqa: E402
from . import css, desktop  # noqa: E402
from .files_area import FileCard  # noqa: E402
from .widgets import hbox, icon, label, link, primary, secondary, tool_tile, vbox  # noqa: E402

GREEN = (0.20, 0.70, 0.35)
RESULT_CARD_WIDTH = 176


def _card_menu(card: Gtk.Widget, path: Path) -> None:
    """輸出檔卡片：雙按開啟；右鍵選單「開啟」「在資料夾中顯示」。"""
    pop = Gtk.Popover()
    pop.set_child(
        vbox(
            2,
            link("開啟", on_click=lambda: (pop.popdown(), desktop.open_paths([path]))),
            link("在資料夾中顯示", on_click=lambda: (pop.popdown(), desktop.reveal([path]))),
        )
    )
    pop.set_parent(card)
    click = Gtk.GestureClick(button=0)

    def pressed(g: Gtk.GestureClick, n: int, _x: float, _y: float) -> None:
        if g.get_current_button() == 3:
            pop.popup()
        elif n == 2:
            desktop.open_paths([path])

    click.connect("pressed", pressed)
    card.add_controller(click)


class ResultPage(Gtk.ScrolledWindow):
    def __init__(
        self,
        tool: ToolDefinition,
        inputs: Sequence[Path],
        outputs: Sequence[Path],
        summary: str | None,
        on_back: Callable[[], None],
        on_restart: Callable[[], None],
        on_next: Callable[[ToolDefinition, list[Path]], None],
    ) -> None:
        super().__init__(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True, hexpand=True)
        self.outputs = [Path(p) for p in outputs]
        body = vbox(20, margin_top=36, margin_bottom=36, margin_start=36, margin_end=36)
        body.set_halign(Gtk.Align.FILL)
        body.add_css_class(css.tint_class(tool.tint_rgb))
        body.append(icon("emblem-ok-symbolic", 64, "qs-ok-icon"))
        body.append(label(result.headline(self.outputs), "qs-result-title", xalign=0.5))
        body.append(self._details(inputs, summary))
        if self.outputs:
            body.append(self._cards())
        body.append(self._buttons())
        links = hbox(18, halign=Gtk.Align.CENTER)
        links.append(link("返回修改選項", "go-previous-symbolic", on_click=on_back))
        links.append(link("處理其他檔案", "view-refresh-symbolic", on_click=on_restart))
        body.append(links)
        nxt = result.next_tools(tool, self.outputs, registry.all_tools())
        if nxt:
            body.append(self._next(nxt, on_next))
        wrap = Gtk.Box(hexpand=True)
        wrap.add_css_class("qs-bg")
        wrap.add_css_class(css.tint_class(GREEN))
        wrap.append(body)
        body.set_hexpand(True)
        self.set_child(wrap)

    def _details(self, inputs: Sequence[Path], summary: str | None) -> Gtk.Widget:
        box = vbox(4)
        for i, (text, style) in enumerate(result.detail_lines(inputs, self.outputs, summary)):
            classes = ["qs-semibold"] if i == 0 else ["qs-dim"]
            classes.append("qs-success" if style == "good" else "qs-dim")
            lbl = label(text, *classes, xalign=0.5, wrap=True)
            lbl.set_justify(Gtk.Justification.CENTER)
            box.append(lbl)
        return box

    def _cards(self) -> Gtk.Widget:
        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True)
        flow.set_column_spacing(16)
        flow.set_row_spacing(16)
        flow.set_halign(Gtk.Align.CENTER)
        flow.set_max_children_per_line(4)
        for p in self.outputs[: result.MAX_CARDS]:
            card = FileCard(p, width=RESULT_CARD_WIDTH)
            _card_menu(card, p)
            flow.append(card)
        clamp = Adw.Clamp(maximum_size=820, child=flow)
        box = vbox(8, clamp)
        extra = len(self.outputs) - result.MAX_CARDS
        if extra > 0:
            box.append(label(f"還有 {extra} 個檔案", "qs-dim", xalign=0.5))
        return box

    def _buttons(self) -> Gtk.Widget:
        row = hbox(12, halign=Gtk.Align.CENTER, margin_top=4)
        reveal = primary("在資料夾中顯示", "folder-symbolic")
        reveal.connect("clicked", lambda _b: desktop.reveal(self.outputs))
        reveal.set_size_request(200, -1)
        reveal.set_sensitive(bool(self.outputs))
        row.append(reveal)
        n = len(self.outputs)
        if 1 <= n <= result.MAX_OPEN_BUTTON:
            title = "開啟檔案" if n == 1 else "全部開啟"
            row.append(
                secondary(
                    title,
                    "document-open-symbolic",
                    on_click=lambda: desktop.open_paths(self.outputs),
                )
            )
        return row

    def _next(
        self, tools: list[ToolDefinition], on_next: Callable[[ToolDefinition, list[Path]], None]
    ) -> Gtk.Widget:
        box = vbox(10, margin_top=14)
        box.append(label("接著可以…", "heading", "qs-dim", xalign=0.5))
        flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE)
        flow.set_column_spacing(8)
        flow.set_row_spacing(8)
        flow.set_halign(Gtk.Align.CENTER)
        flow.set_max_children_per_line(8)
        for t in tools:
            btn = Gtk.Button()
            btn.add_css_class("qs-next")
            btn.set_child(hbox(8, tool_tile(t.icon, t.tint_rgb, 22), label(t.title)))
            btn.connect("clicked", lambda _b, t=t: on_next(t, list(self.outputs)))
            flow.append(btn)
        box.append(Adw.Clamp(maximum_size=720, child=flow))
        return box
