"""工具大廳（對應原版 ToolHubView）：大標題、搜尋、分類篩選、待處理檔案、工具卡片。"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from ..core import registry  # noqa: E402
from ..core.filetypes import file_ref  # noqa: E402
from ..core.model import CATEGORIES, Category, ToolDefinition  # noqa: E402
from . import css, routing  # noqa: E402
from .state import AppState  # noqa: E402
from .widgets import (  # noqa: E402
    FileThumb,
    add_file_drop,
    clear,
    drop_overlay,
    hbox,
    icon,
    label,
    secondary,
    sf_icon,
    tool_tile,
    vbox,
)

ACCENT = (0.21, 0.52, 0.89)
CARD_MIN_WIDTH = 215


def tool_card(tool: ToolDefinition, on_click) -> Gtk.Button:  # noqa: ANN001
    btn = Gtk.Button(tooltip_text=tool.subtitle)
    btn.add_css_class("qs-card")
    btn.add_css_class(css.tint_class(tool.tint_rgb))
    box = vbox(12, valign=Gtk.Align.START)
    tile = tool_tile(tool.icon, tool.tint_rgb, 46, tool.badge)
    tile.set_halign(Gtk.Align.START)
    box.append(tile)
    box.append(label(tool.title, "qs-card-title", wrap=True))
    sub = label(tool.subtitle, "qs-dim", "qs-normal", wrap=True, lines=3, ellipsize="end")
    box.append(sub)
    btn.set_child(box)
    btn.set_size_request(CARD_MIN_WIDTH, 168)
    btn.connect("clicked", lambda _b: on_click(tool))
    return btn


class HubPage(Gtk.Box):
    def __init__(self, state: AppState) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, hexpand=True, vexpand=True)
        self.state = state
        self.category: Category | None = None
        self.query = ""
        toolbar = Adw.ToolbarView()
        bar = Adw.HeaderBar()
        bar.set_title_widget(Gtk.Box())
        toolbar.add_top_bar(bar)
        toolbar.set_top_bar_style(Adw.ToolbarStyle.FLAT)
        over = Gtk.Overlay(hexpand=True, vexpand=True)
        self.bg = Gtk.Box(hexpand=True, vexpand=True)
        self.bg.add_css_class("qs-bg")
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, hexpand=True)
        scroll.set_child(
            Adw.Clamp(maximum_size=1240, tightening_threshold=1240, child=self._body())
        )
        self.bg.append(scroll)
        over.set_child(self.bg)
        hint = drop_overlay("放開即可選擇要使用的工具")
        over.add_overlay(hint)
        add_file_drop(over, self.state.receive, hint.set_visible)
        toolbar.set_content(over)
        self.append(toolbar)
        self.set_category(None)

    def _body(self) -> Gtk.Widget:
        body = vbox(26, margin_top=26, margin_bottom=40, margin_start=32, margin_end=32)
        self.title = label("", "qs-hub-title", xalign=0.5, wrap=True)
        self.subtitle = label("", "qs-subtitle", xalign=0.5, wrap=True)
        self.subtitle.set_justify(Gtk.Justification.CENTER)
        self.subtitle.set_max_width_chars(46)
        self.search = Gtk.SearchEntry(placeholder_text="搜尋工具，例如「壓縮」「Word」「GIF」")
        self.search.add_css_class("qs-search")
        self.search.connect("search-changed", self._on_search)
        head = vbox(10, self.title, self.subtitle)
        head.append(Adw.Clamp(maximum_size=460, child=self.search, margin_top=6))
        body.append(head)
        self.banner = vbox(0)
        body.append(self.banner)
        self.pills = hbox(8, halign=Gtk.Align.CENTER)
        body.append(self.pills)
        self.sections = vbox(26)
        body.append(self.sections)
        return body

    # ------------------------------------------------------------ 更新

    def set_category(self, category: Category | None) -> None:
        self.category = category
        self.title.set_label(routing.hub_title(category))
        self.subtitle.set_label(routing.hub_subtitle(category))
        for c in list(self.bg.get_css_classes()):
            if c.startswith("qs-t-"):
                self.bg.remove_css_class(c)
        self.bg.add_css_class(css.tint_class(category.tint if category else CATEGORIES[0].tint))
        self.rebuild()

    def _on_search(self, entry: Gtk.SearchEntry) -> None:
        self.query = entry.get_text()
        self._rebuild_sections()

    def rebuild(self) -> None:
        self._rebuild_banner()
        self._rebuild_pills()
        self._rebuild_sections()

    def _rebuild_pills(self) -> None:
        clear(self.pills)
        items: list[tuple[str, str, tuple, str]] = [("全部", "square.grid.2x2", ACCENT, "all")]
        for c in registry.categories_with_tools():
            items.append((c.short_title, c.icon, c.tint, f"cat:{c.id}"))
        for title, symbol, tint, key in items:
            btn = Gtk.Button()
            btn.add_css_class("qs-pill")
            btn.add_css_class(css.tint_class(tint))
            btn.set_child(hbox(6, sf_icon(symbol, 16), label(title)))
            selected = (self.category is None and key == "all") or (
                self.category is not None and key == f"cat:{self.category.id}"
            )
            if selected:
                btn.add_css_class("selected")
            btn.connect("clicked", lambda _b, k=key: self.state.select(k))
            self.pills.append(btn)

    def _rebuild_banner(self) -> None:
        clear(self.banner)
        inbox = list(self.state.inbox)
        if not inbox:
            return
        thumbs = Gtk.Fixed()
        for i, p in enumerate(inbox[:3]):
            thumbs.put(FileThumb(p, 44, 52), i * 28, 0)
        thumbs.set_size_request(44 + 28 * (min(len(inbox), 3) - 1), 52)
        thumbs.set_hexpand(False)
        text = vbox(3, label(routing.inbox_title(len(inbox)), "heading"))
        text.append(label(routing.inbox_names(inbox), "qs-dim", ellipsize="end"))
        text.set_hexpand(True)
        row = hbox(14, thumbs, text, secondary("清除", on_click=self.state.clear_inbox))
        row.add_css_class("qs-banner")
        self.banner.append(row)

    def _sections_data(self) -> list[tuple[Category, list[ToolDefinition]]]:
        refs = [file_ref(p) for p in self.state.inbox]
        return routing.hub_sections(self.category, registry.all_tools(), self.query, refs)

    def _rebuild_sections(self) -> None:
        clear(self.sections)
        sections = self._sections_data()
        if not sections:
            self.sections.append(self._empty())
            return
        for cat, tools in sections:
            box = vbox(12)
            if self.category is None:
                head = hbox(8, sf_icon(cat.icon, 18, "qs-tinted"), label(cat.title, "title-3"))
                head.add_css_class(css.tint_class(cat.tint))
                head.append(label(str(len(tools)), "qs-count"))
                box.append(head)
            flow = Gtk.FlowBox(selection_mode=Gtk.SelectionMode.NONE, homogeneous=True)
            flow.set_column_spacing(14)
            flow.set_row_spacing(14)
            flow.set_max_children_per_line(6)
            for t in tools:
                flow.append(tool_card(t, self._open))
            box.append(flow)
            self.sections.append(box)

    def _empty(self) -> Gtk.Widget:
        box = vbox(10, margin_top=50, halign=Gtk.Align.CENTER)
        box.append(icon("system-search-symbolic", 34, "qs-dim"))
        msg = routing.empty_message(self.query, bool(self.state.inbox))
        box.append(label(msg, "title-3", xalign=0.5))
        if self.state.inbox:
            box.append(secondary("清除選取的檔案", on_click=self.state.clear_inbox))
        return box

    def _open(self, tool: ToolDefinition) -> None:
        files: Sequence[Path] = routing.inbox_files_for(tool, list(self.state.inbox))
        self.state.open(tool, files)
