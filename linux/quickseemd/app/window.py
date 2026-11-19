"""主視窗（對應原版 MainView＋MainWindow）：側欄＋內容頁；內容依 AppState 切換。"""

from __future__ import annotations

import logging
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from ..core import registry  # noqa: E402
from ..core.model import CATEGORIES, ToolDefinition  # noqa: E402
from . import css, routing, winstate  # noqa: E402
from .hub import HubPage  # noqa: E402
from .sidebar import Sidebar  # noqa: E402
from .state import AppState  # noqa: E402
from .toolpage import PageHooks, ToolPage  # noqa: E402

LOG = logging.getLogger(__name__)


class MainWindow(Adw.ApplicationWindow):
    def __init__(self, app: Adw.Application | None, state: AppState) -> None:
        super().__init__(title="QUICK_SEEMD")
        if app is not None:
            self.set_application(app)
        css.install()
        self.state = state
        w, h = winstate.load_size()
        self.set_default_size(w, h)
        self.set_size_request(*winstate.MIN_SIZE)
        self.toasts = Adw.ToastOverlay()
        self.split = Adw.NavigationSplitView(min_sidebar_width=200, max_sidebar_width=260)
        self.split.set_sidebar_width_fraction(0.2)
        self.sidebar = Sidebar(self._sidebar_selected)
        self.split.set_sidebar(Adw.NavigationPage(title="QUICK_SEEMD", child=self.sidebar))
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE, hexpand=True)
        self.stack.set_size_request(720, 560)
        self.split.set_content(Adw.NavigationPage(title="工具", child=self.stack))
        self.toasts.set_child(self.split)
        self.set_content(self.toasts)
        self.hub = HubPage(state)
        self.stack.add_named(self.hub, "hub")
        self.pages: dict[str, Gtk.Widget] = {}
        self.tool_page: ToolPage | None = None
        self._shown_token = -1
        state.converter_sink = lambda files: self.converter().add_files(files)
        state.subscribe(self.refresh)
        self.connect("close-request", self._on_close)
        self.refresh()

    # ------------------------------------------------------------ 導覽

    def _sidebar_selected(self, key: str) -> None:
        self.state.select(key)

    def _page(self, key: str) -> Gtk.Widget:
        if key not in self.pages:
            self.pages[key] = self._make_page(key)
            self.stack.add_named(self.pages[key], key)
        return self.pages[key]

    def _make_page(self, key: str) -> Gtk.Widget:
        if key == "converter":
            from .converter_page import ConverterPage  # noqa: PLC0415

            return ConverterPage(self.state, self.toast)
        if key == "preview":
            from .preview_page import PreviewPage  # noqa: PLC0415

            return PreviewPage(self.toast)
        from .integration_page import IntegrationPage  # noqa: PLC0415

        return IntegrationPage(self.toast)

    def converter(self) -> Gtk.Widget:
        """萬用轉換頁（AppState 打開萬用轉換時要先建好，才能收檔案）。"""
        return self._page("converter")

    def refresh(self) -> None:
        st = self.state
        tool = registry.tool(st.open_tool_id) if st.open_tool_id else None
        self.sidebar.show(None if tool else st.sidebar)
        if tool is not None:
            self._show_tool(tool)
            return
        self._drop_tool_page()
        if st.sidebar == "all" or st.sidebar.startswith("cat:"):
            cat = next((c for c in CATEGORIES if f"cat:{c.id}" == st.sidebar), None)
            self.hub.set_category(cat)
            self.stack.set_visible_child_name("hub")
            return
        page = self._page(st.sidebar)
        if hasattr(page, "show_back"):
            page.show_back(False)
        self.stack.set_visible_child(page)

    def _show_tool(self, tool: ToolDefinition) -> None:
        if routing.is_converter(tool):
            self._drop_tool_page()
            page = self.converter()
            page.show_back(True, tool)
            self.stack.set_visible_child(page)
            return
        if self.tool_page is not None and self._shown_token == self.state.page_token:
            return
        self._drop_tool_page()
        hooks = PageHooks(self.state.close_tool, self._open_next, self.toast)
        self.tool_page = ToolPage(tool, self.state.pending_files, hooks)
        self._shown_token = self.state.page_token
        self.stack.add_named(self.tool_page, "tool")
        self.stack.set_visible_child(self.tool_page)

    def _drop_tool_page(self) -> None:
        if self.tool_page is None:
            return
        page, self.tool_page = self.tool_page, None
        page.shutdown()
        self.stack.remove(page)
        self._shown_token = -1

    def _open_next(self, tool: ToolDefinition, files: list[Path]) -> None:
        self.state.open(tool, files)

    def toast(self, text: str) -> None:
        self.toasts.add_toast(Adw.Toast(title=text, timeout=4))

    # ------------------------------------------------------------ 關閉

    def _on_close(self, _win: Gtk.Window) -> bool:
        width, height = self.get_default_size()
        winstate.save_size(width, height)
        if self.tool_page is not None:
            self.tool_page.shutdown()
        return False
