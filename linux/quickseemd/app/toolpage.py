"""單一工具的工作頁（對應原版 ToolPageView）：選檔 → 檔案＋選項 → 處理中 → 完成／失敗。"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from ..core import settings  # noqa: E402
from ..core.model import ToolDefinition  # noqa: E402
from ..core.result import summary_text  # noqa: E402
from ..core.runner import JobSpec  # noqa: E402
from . import css, routing, views  # noqa: E402
from .mainloop import to_main  # noqa: E402
from .files_area import FilesArea  # noqa: E402
from .jobrunner import JobRunner  # noqa: E402
from .options_model import OptionsState  # noqa: E402
from .options_panel import OptionsPanel  # noqa: E402
from .output_picker import OutputPicker  # noqa: E402
from .result_page import ResultPage  # noqa: E402
from .running import RunningOverlay, failure_page  # noqa: E402
from .widgets import (  # noqa: E402
    add_file_drop,
    drop_overlay,
    file_dialog_filter,
    hbox,
    label,
    link,
    primary,
    secondary,
    set_warning,
    tool_tile,
    vbox,
    warning_row,
)

LOG = logging.getLogger(__name__)
PANEL_WIDTH = 340


@dataclass
class PageHooks:
    """工具頁需要外面提供的動作。"""

    close: Callable[[], None]
    open_tool: Callable[[ToolDefinition, list[Path]], None]
    toast: Callable[[str], None]


class ToolPage(Gtk.Box):
    def __init__(self, tool: ToolDefinition, files: Sequence[Path], hooks: PageHooks) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, hexpand=True, vexpand=True)
        self.tool = tool
        self.hooks = hooks
        self.add_css_class(css.tint_class(tool.tint_rgb))
        self.files: list[Path] = []
        self.note: str | None = None
        self.options = OptionsState(tool)
        self.custom: views.CustomView | None = None
        self.runner: JobRunner | None = None
        self._gen = 0
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(self._header())
        self.overlay = Gtk.Overlay()
        self.stack = Gtk.Stack(transition_type=Gtk.StackTransitionType.CROSSFADE)
        self.overlay.set_child(self.stack)
        self.running = RunningOverlay(tool, self.cancel)
        self.overlay.add_overlay(self.running)
        toolbar.set_content(self.overlay)
        self.append(toolbar)
        self.stack.add_named(self._landing(), "landing")
        self.stack.add_named(self._workspace(), "workspace")
        self.options.subscribe(lambda _k: self._refresh_footer())
        self._install_shortcuts()
        self.set_files(routing.initial_files(tool, files))

    # ------------------------------------------------------------ 版面

    def _header(self) -> Adw.HeaderBar:
        bar = Adw.HeaderBar()
        self.back = link("所有工具", "go-previous-symbolic", on_click=self.hooks.close)
        bar.pack_start(self.back)
        title = hbox(
            10, tool_tile(self.tool.icon, self.tool.tint_rgb, 22), label(self.tool.title, "heading")
        )
        bar.set_title_widget(title)
        self.add_button = secondary("加入檔案", "list-add-symbolic", on_click=self.pick_files)
        bar.pack_end(self.add_button)
        return bar

    def _landing(self) -> Gtk.Widget:
        t = self.tool
        box = vbox(16, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, vexpand=True)
        box.set_margin_top(30)
        box.set_margin_bottom(30)
        box.append(tool_tile(t.icon, t.tint_rgb, 78, t.badge))
        box.append(label(t.title, "qs-hero-title", xalign=0.5))
        sub = label(t.subtitle, "qs-subtitle", xalign=0.5, wrap=True)
        sub.set_justify(Gtk.Justification.CENTER)
        sub.set_max_width_chars(40)
        box.append(sub)
        choose = primary(
            routing.choose_button_title(t), "list-add-symbolic", on_click=self.pick_files
        )
        choose.set_size_request(280, -1)
        choose.set_halign(Gtk.Align.CENTER)
        choose.set_margin_top(10)
        box.append(choose)
        if routing.accepts_folders(t):
            box.append(link("選擇資料夾…", "folder-symbolic", on_click=self.pick_folders))
        box.append(label(routing.landing_hint(t), "qs-dim", xalign=0.5))
        self.landing_note = warning_row()
        self.landing_note.set_halign(Gtk.Align.CENTER)
        box.append(self.landing_note)
        return self._droppable(box, background=True)

    def _droppable(self, child: Gtk.Widget, background: bool = False) -> Gtk.Widget:
        over = Gtk.Overlay(hexpand=True, vexpand=True)
        holder = Gtk.Box(hexpand=True, vexpand=True)
        if background:
            holder.add_css_class(css.bg_class(self.tool.tint_rgb))
        holder.append(child)
        child.set_hexpand(True)
        over.set_child(holder)
        hint = drop_overlay()
        over.add_overlay(hint)
        add_file_drop(over, self.add_files, hint.set_visible)
        return over

    def _workspace(self) -> Gtk.Widget:
        row = hbox(0, hexpand=True, vexpand=True)
        self.files_area = FilesArea(self.tool, self._files_changed_by_user, self.pick_files)
        row.append(self._droppable(self.files_area))
        row.append(Gtk.Separator(orientation=Gtk.Orientation.VERTICAL))
        row.append(self._side_panel())
        return row

    def _side_panel(self) -> Gtk.Widget:
        t = self.tool
        side = vbox(0, hexpand=False)
        side.add_css_class("qs-panel")
        side.set_size_request(PANEL_WIDTH, -1)
        inner = vbox(18, margin_top=20, margin_bottom=20, margin_start=20, margin_end=20)
        self.panel_inner = inner
        inner.append(
            hbox(10, tool_tile(t.icon, t.tint_rgb, 34, t.badge), label(t.title, "title-2"))
        )
        inner.append(label(t.subtitle, "qs-dim", wrap=True))
        inner.append(Gtk.Separator())
        self.custom_slot = vbox(0)
        inner.append(self.custom_slot)
        self.panel = OptionsPanel(self.options)
        inner.append(self.panel)
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        scroll.set_child(inner)
        side.append(scroll)
        side.append(Gtk.Separator())
        foot = vbox(10, margin_top=16, margin_bottom=16, margin_start=16, margin_end=16)
        self.output_picker = OutputPicker()
        self.output_picker.set_halign(Gtk.Align.START)
        foot.append(self.output_picker)
        self.reason = warning_row()
        foot.append(self.reason)
        self.run_button = primary("", "go-next-symbolic", icon_end=True, on_click=self.run)
        foot.append(self.run_button)
        side.append(foot)
        return side

    def _install_shortcuts(self) -> None:
        ctl = Gtk.ShortcutController(scope=Gtk.ShortcutScope.MANAGED)
        for trigger, fn in (("<Control>Return", self.run), ("Escape", self.cancel)):
            action = Gtk.CallbackAction.new(lambda *_a, fn=fn: fn() or True)
            ctl.add_shortcut(Gtk.Shortcut.new(Gtk.ShortcutTrigger.parse_string(trigger), action))
        self.add_controller(ctl)

    # ------------------------------------------------------------ 檔案

    def pick_files(self) -> None:
        t = self.tool
        dlg = Gtk.FileDialog(title=routing.picker_message(t), accept_label="選擇", modal=True)
        flt = file_dialog_filter(t.input_types, t.input_label)
        if flt is not None:
            store = Gio.ListStore.new(Gtk.FileFilter)
            store.append(flt)
            dlg.set_filters(store)
            dlg.set_default_filter(flt)
        if t.allows_multiple:
            dlg.open_multiple(self.get_root(), None, self._picked_many)
        else:
            dlg.open(self.get_root(), None, self._picked_one)

    def pick_folders(self) -> None:
        dlg = Gtk.FileDialog(
            title=routing.picker_message(self.tool), accept_label="選擇", modal=True
        )
        dlg.select_multiple_folders(self.get_root(), None, self._picked_folders)

    def _finish_list(self, model: Gio.ListModel | None) -> None:
        paths = []
        for i in range(model.get_n_items() if model else 0):
            p = model.get_item(i).get_path()
            if p:
                paths.append(Path(p))
        if paths:
            self.add_files(paths)

    def _picked_many(self, dlg: Gtk.FileDialog, res: Gio.AsyncResult) -> None:
        try:
            self._finish_list(dlg.open_multiple_finish(res))
        except GLib.Error:
            LOG.debug("使用者取消選擇檔案")

    def _picked_folders(self, dlg: Gtk.FileDialog, res: Gio.AsyncResult) -> None:
        try:
            self._finish_list(dlg.select_multiple_folders_finish(res))
        except GLib.Error:
            LOG.debug("使用者取消選擇資料夾")

    def _picked_one(self, dlg: Gtk.FileDialog, res: Gio.AsyncResult) -> None:
        try:
            f = dlg.open_finish(res)
        except GLib.Error:
            return
        if f is not None and f.get_path():
            self.add_files([Path(f.get_path())])

    def add_files(self, paths: Sequence[Path]) -> None:
        if self.runner is not None:
            return
        merged, note = routing.add_files(self.tool, self.files, paths)
        self.set_files(merged, note)

    def _files_changed_by_user(self, files: list[Path]) -> None:
        self.set_files(files, None)

    def set_files(self, files: Sequence[Path], note: str | None = None) -> None:
        self.files = list(files)
        self.note = note
        self.stack.set_visible_child_name("workspace" if self.files else "landing")
        set_warning(self.landing_note, note)
        self._update_custom()  # 先決定要不要用自訂畫面，才不會白建縮圖格
        self.files_area.set_files(self.files)
        self.add_button.set_visible(bool(self.files))
        self.add_button.set_sensitive(self.tool.allows_multiple or not self.files)
        self._inspect()
        self._refresh_footer()

    def _files_note(self) -> str | None:
        if self.tool.files_note is None or not self.files:
            return None
        try:
            return self.tool.files_note(self.files, self.options.values, self.options.info)
        except Exception:  # noqa: BLE001 — 工具的提示出錯就不顯示
            LOG.exception("files_note 出錯")
            return None

    # ------------------------------------------------------------ 自訂畫面

    def _update_custom(self) -> None:
        want = bool(self.tool.custom_view) and len(self.files) == 1
        if not want and self.custom is not None:
            self.custom.destroy()
            self.custom = None
            self.files_area.set_custom(None)
            self._set_custom_panel(None)
            self.panel.set_hidden(frozenset())
        if want and self.custom is None:
            ctx = views.ViewContext(
                self.tool, self.options, lambda: self.set_files([]), self._refresh_footer
            )
            self.custom = views.create(self.tool.custom_view or "", ctx)
            if self.custom is not None:
                self.files_area.set_custom(self.custom.widget)
                self._set_custom_panel(self.custom.panel)
                self.panel.set_hidden(frozenset(getattr(self.custom, "hides", ())))
        if self.custom is not None:
            self.custom.set_file(self.files[0])

    def _set_custom_panel(self, panel: Gtk.Widget | None) -> None:
        child = self.custom_slot.get_first_child()
        if child is not None:
            self.custom_slot.remove(child)
        if panel is not None:
            self.custom_slot.append(panel)
        self.custom_slot.set_visible(panel is not None)

    # ------------------------------------------------------------ 檔案資訊（背景讀取）

    def _inspect(self) -> None:
        self._gen += 1
        gen, files = self._gen, list(self.files)
        self.options.set_info({})
        if self.tool.inspect is None or not files:
            self._prefill({})
            return
        threading.Thread(target=self._inspect_worker, args=(gen, files), daemon=True).start()

    def _inspect_worker(self, gen: int, files: list[Path]) -> None:
        try:
            info = self.tool.inspect(files) if self.tool.inspect else {}
        except Exception:  # noqa: BLE001 — 讀不到資訊時選項照常可用
            LOG.exception("讀取檔案資訊失敗")
            info = {}
        to_main(self._inspect_done, gen, info)

    def _inspect_done(self, gen: int, info: dict[str, Any]) -> bool:
        if gen == self._gen:
            self.options.set_info(info)
            self._prefill(info)
        return False

    def _prefill(self, info: dict[str, Any]) -> None:
        if self.tool.prefill is None or not self.files:
            return
        try:
            values = self.tool.prefill(list(self.files), info) or {}
        except Exception:  # noqa: BLE001
            LOG.exception("prefill 出錯")
            return
        self.options.merge(values)

    # ------------------------------------------------------------ 執行

    def validation(self) -> str | None:
        reason = routing.validation_reason(
            self.tool, self.files, self.options.values, self.options.info
        )
        if reason is None and self.custom is not None:
            reason = self.custom.validate()
        return reason

    def _refresh_footer(self) -> None:
        reason = self.validation() if self.files else None
        set_warning(self.reason, reason)
        self.run_button.qs_label.set_label(self.options.action_title())
        self.run_button.set_sensitive(reason is None and self.runner is None and bool(self.files))
        if self.files:
            self.files_area.set_note(self.note or self._files_note())

    def run(self) -> None:
        if self.runner is not None or not self.files or self.validation() is not None:
            return
        out = settings.output_folder_for_tools()
        spec = JobSpec(
            "tool",
            self.tool.id,
            [str(f) for f in self.files],
            self.options.snapshot(),
            str(out) if out else None,
        )
        self.runner = JobRunner(spec, self.running.update, self._done)
        self.running.start()
        self.back.set_sensitive(False)
        self.add_button.set_sensitive(False)
        self._refresh_footer()
        self.runner.start()

    def cancel(self) -> None:
        if self.runner is not None:
            self.running.cancel_button.set_sensitive(False)
            self.runner.cancel()

    def _done(self, result: dict[str, Any]) -> None:
        self.runner = None
        self.running.set_visible(False)
        self.back.set_sensitive(True)
        self.add_button.set_sensitive(self.tool.allows_multiple or not self.files)
        if result.get("cancelled"):
            self._refresh_footer()
            return
        if result.get("error"):
            self._show(
                "failure", failure_page(self.tool, str(result["error"]), self.back_to_editing)
            )
            return
        outputs = [Path(p) for p in result.get("outputs") or []]
        self._copy_clipboard(result.get("clipboard"))
        summary = summary_text(result.get("summary"), self.files)
        page = ResultPage(
            self.tool,
            self.files,
            outputs,
            summary,
            self.back_to_editing,
            self.restart,
            self.hooks.open_tool,
        )
        self._show("result", page)

    def _copy_clipboard(self, text: object) -> None:
        """工作程序交回要拷貝的文字（例如讀取 QR Code 的內容）。"""
        display = Gdk.Display.get_default()
        if isinstance(text, str) and text and display is not None:
            display.get_clipboard().set(text)

    def _show(self, name: str, widget: Gtk.Widget) -> None:
        old = self.stack.get_child_by_name(name)
        if old is not None:
            self.stack.remove(old)
        self.stack.add_named(widget, name)
        self.stack.set_visible_child_name(name)
        self.add_button.set_visible(False)

    def back_to_editing(self) -> None:
        self.set_files(self.files, self.note)

    def restart(self) -> None:
        self.set_files([])

    def shutdown(self) -> None:
        """離開工具頁：停止背景工作與播放。"""
        if self.runner is not None:
            self.runner.cancel()
        if self.custom is not None:
            self.custom.destroy()
            self.custom = None
        self.panel.detach()
