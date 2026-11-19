"""萬用格式轉換頁（對應原版 ConverterView）：加入檔案 → 選格式 → 依序轉換或合併成 PDF。

分析（converter.targets_for／unavailable）在背景執行緒；轉換一次一個檔案，
每個都用工作程序 JobSpec("convert", 格式, [檔案], 選項, 輸出資料夾) 執行。
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gdk, Gio, GLib, Gtk  # noqa: E402

from ..core import backends  # noqa: E402
from ..core.model import TINTS, ToolDefinition  # noqa: E402
from ..core.runner import JobSpec  # noqa: E402
from . import converter_model as cmod  # noqa: E402
from . import css, desktop  # noqa: E402
from .converter_panel import ConverterPanel  # noqa: E402
from .converter_rows import RowHooks, job_row  # noqa: E402
from .jobrunner import JobRunner  # noqa: E402
from .mainloop import to_main  # noqa: E402
from .state import AppState  # noqa: E402
from .widgets import add_file_drop, clear, hbox, icon, label, link, vbox  # noqa: E402

LOG = logging.getLogger(__name__)
ENGINES = (
    "ffmpeg",
    "soffice",
    "pandoc",
    "magick",
    "tesseract",
    "7z",
    "bsdtar",
    "heif-enc",
    "weasyprint",
)
CHIPS = (
    "圖片",
    "PDF",
    "Word／RTF",
    "Markdown",
    "影片",
    "音訊",
    "JSON／CSV／Excel",
    "文字編碼",
    "ZIP／7z",
)
SUBTITLE = "把檔案轉成你需要的格式，全部在這台電腦上離線完成"
EMPTY_HINT = "或按一下選擇檔案。也可以在 Nemo 對檔案按右鍵，選「QUICK_SEEMD」→「更多工具與選項…」。"


def engine_label(count: int) -> str:
    return "內建引擎" if count == 0 else f"內建 + {count} 個外部引擎"


class ConverterPage(Gtk.Box):
    def __init__(self, state: AppState, toast: Callable[[str], None]) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, hexpand=True, vexpand=True)
        self.state = state
        self.toast = toast
        self.model = cmod.ConverterModel()
        self.runner: JobRunner | None = None
        self._uid: int | None = None
        self.add_css_class(css.tint_class(TINTS["teal"]))
        toolbar = Adw.ToolbarView()
        toolbar.add_top_bar(self._header())
        self.stack = Gtk.Stack(vexpand=True)
        self.stack.add_named(self._empty(), "empty")
        self.stack.add_named(self._workspace(), "work")
        toolbar.set_content(self.stack)
        self.footer = self._footer()
        toolbar.add_bottom_bar(self.footer)
        self.append(toolbar)
        add_file_drop(self, self.add_files, self._drag_hover)
        self._install_actions()
        self.refresh()

    # ------------------------------------------------------------ 標題列

    def _header(self) -> Adw.HeaderBar:
        bar = Adw.HeaderBar()
        self.back = link("所有工具", "go-previous-symbolic", on_click=self.state.close_tool)
        self.back.set_visible(False)
        bar.pack_start(self.back)
        icon_box = Gtk.Box()
        icon_box.add_css_class("qs-cv-header-icon")
        img = icon("media-playlist-repeat-symbolic", 18)
        img.set_hexpand(True)
        icon_box.append(img)
        icon_box.set_hexpand(False)
        title = vbox(0, label("格式轉換", "heading"), label(SUBTITLE, "qs-tiny", "qs-dim"))
        bar.set_title_widget(hbox(10, icon_box, title))
        more = Gio.Menu()
        more.append("全選", "cv.select-all")
        more.append("清除已完成的項目", "cv.clear-finished")
        more.append("全部移除", "cv.remove-all")
        self.more = Gtk.MenuButton(
            icon_name="view-more-symbolic", menu_model=more, tooltip_text="更多動作"
        )
        bar.pack_end(self.more)
        self.engines = Gtk.MenuButton(tooltip_text="查看可用的轉換引擎")
        self.engines.add_css_class("flat")
        self.engines_label = label("", "qs-small")
        self.engines.set_child(hbox(6, icon("system-run-symbolic", 14), self.engines_label))
        self.engines.set_popover(Gtk.Popover())
        self.engines.get_popover().connect("show", lambda p: self._fill_engines(p))
        bar.pack_end(self.engines)
        return bar

    def _engine_rows(self) -> list[tuple[str, str | None, str, str]]:
        rows = []
        for key in ENGINES:
            b = backends.BACKENDS.get(key)
            if b is not None:
                rows.append((b.title, backends.find(key), b.summary, backends.install_hint(key)))
        return rows

    def _fill_engines(self, pop: Gtk.Popover) -> None:
        box = vbox(10, margin_top=10, margin_bottom=10, margin_start=10, margin_end=10)
        head = hbox(8, label("轉換引擎", "heading"))
        head.get_first_child().set_hexpand(True)
        head.append(link("重新偵測", "view-refresh-symbolic", on_click=self.redetect))
        box.append(head)
        box.append(
            vbox(
                2,
                hbox(6, label("內建", "qs-semibold"), label("可用", "qs-small", "qs-success")),
                label("圖片、PDF、文件、音訊、影片、資料、壓縮檔", "qs-small", "qs-dim"),
            )
        )
        box.append(label("選用的外部工具（自行安裝後自動啟用）", "qs-small", "qs-dim"))
        for title, path, summary, hint in self._engine_rows():
            status = label(
                "可用" if path else "未安裝", "qs-small", "qs-success" if path else "qs-dim"
            )
            row = vbox(
                2,
                hbox(6, label(title, "qs-semibold"), status),
                label(summary, "qs-small", "qs-dim"),
            )
            detail = hbox(6, label(path or hint, "qs-tiny", "qs-dim", "qs-mono", selectable=True))
            if not path and hint:
                copy = Gtk.Button(icon_name="edit-copy-symbolic", tooltip_text="拷貝安裝指令")
                copy.add_css_class("flat")
                copy.connect("clicked", lambda _b, h=hint: self._copy(h))
                detail.append(copy)
            row.append(detail)
            box.append(row)
        pop.set_child(
            Gtk.ScrolledWindow(
                child=box,
                propagate_natural_height=True,
                max_content_height=520,
                propagate_natural_width=True,
            )
        )

    def _copy(self, text: str) -> None:
        display = Gdk.Display.get_default()
        if display is not None:
            display.get_clipboard().set(text)
            self.toast("已拷貝")

    def redetect(self) -> None:
        backends.refresh()
        for job in self.model.jobs:
            if not job.analyzing:
                self._analyze(job.uid, job.path)
        self.refresh()

    def _install_actions(self) -> None:
        group = Gio.SimpleActionGroup()
        for name, fn in (
            ("select-all", self.model.select_all),
            ("clear-finished", self.model.clear_finished),
            ("remove-all", self.model.remove_all),
        ):
            act = Gio.SimpleAction.new(name, None)
            act.connect("activate", lambda _a, _p, fn=fn: (fn(), self.refresh()))
            group.add_action(act)
        self.insert_action_group("cv", group)
        keys = Gtk.EventControllerKey()
        keys.connect("key-pressed", self._key)
        self.add_controller(keys)

    def _key(
        self, _c: Gtk.EventControllerKey, keyval: int, _code: int, state: Gdk.ModifierType
    ) -> bool:
        if keyval == Gdk.KEY_Delete and self.model.selection:
            self.model.remove_selected()
            self.refresh()
            return True
        if keyval == Gdk.KEY_Return and state & Gdk.ModifierType.CONTROL_MASK:
            self.convert_all()
            return True
        return False

    def show_back(self, on: bool, tool: ToolDefinition | None = None) -> None:
        self.back.set_visible(on)

    # ------------------------------------------------------------ 版面

    def _drop_zone(self, compact: bool) -> Gtk.Button:
        btn = Gtk.Button()
        btn.add_css_class("flat")
        btn.add_css_class("qs-cv-drop")
        if compact:
            btn.add_css_class("compact")
            title = label("加入更多檔案", "qs-semibold")
            text = vbox(2, title, label("拖放到這裡，或按一下選擇", "qs-small", "qs-dim"))
            btn.set_child(hbox(10, icon("list-add-symbolic", 20, "qs-tinted"), text))
        else:
            title = label("把檔案或資料夾拖到這裡", "title-2", xalign=0.5)
            box = vbox(12, icon("folder-download-symbolic", 48, "qs-dim"), title)
            box.append(label(EMPTY_HINT, "qs-dim", xalign=0.5, wrap=True))
            chips = hbox(6, halign=Gtk.Align.CENTER)
            for c in CHIPS:
                chips.append(label(c, "qs-count"))
            box.append(chips)
            box.set_valign(Gtk.Align.CENTER)
            btn.set_child(box)
        btn.qs_title = title  # type: ignore[attr-defined]
        btn.connect("clicked", lambda _b: self.pick_files())
        return btn

    def _empty(self) -> Gtk.Widget:
        self.big_drop = self._drop_zone(False)
        self.big_drop.set_vexpand(True)
        box = vbox(
            10, self.big_drop, margin_start=20, margin_end=20, margin_bottom=20, margin_top=8
        )
        box.append(link("選擇資料夾…", "folder-symbolic", on_click=self.pick_folders))
        return box

    def _workspace(self) -> Gtk.Widget:
        self.small_drop = self._drop_zone(True)
        self.count = label("", "qs-count")
        self.multi_hint = label("Ctrl 點按可複選", "qs-tiny", "qs-dim")
        head = hbox(6, label("檔案", "qs-small", "qs-dim", "qs-semibold"), self.count)
        spacer = Gtk.Box(hexpand=True)
        head.append(spacer)
        head.append(self.multi_hint)
        self.rows = vbox(7)
        scroll = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True, child=self.rows
        )
        left = vbox(10, self.small_drop, head, scroll, hexpand=False)
        left.set_size_request(340, -1)
        self.left = left
        self.panel = ConverterPanel(
            self.model,
            {
                "reveal": desktop.reveal,
                "close_banner": self._close_banner,
                "choose": self._choose,
                "merge": self.merge,
            },
        )
        right = Gtk.ScrolledWindow(
            hscrollbar_policy=Gtk.PolicyType.NEVER, hexpand=True, child=self.panel
        )
        row = hbox(14, left, right, margin_start=20, margin_end=20, margin_bottom=14, margin_top=8)
        return row

    def _footer(self) -> Gtk.Widget:
        self.mode_same = Gtk.ToggleButton(label="與原始檔相同")
        self.mode_folder = Gtk.ToggleButton(label="自訂資料夾", group=self.mode_same)
        self.mode_same.connect("toggled", lambda b: b.get_active() and self._set_mode("same"))
        self.mode_folder.connect("toggled", lambda b: b.get_active() and self._set_mode("folder"))
        seg = hbox(0, self.mode_same, self.mode_folder)
        seg.add_css_class("linked")
        self.folder_button = Gtk.Button()
        self.folder_label = label("選擇…", ellipsize="middle")
        self.folder_label.set_max_width_chars(18)
        self.folder_button.set_child(self.folder_label)
        self.folder_button.connect("clicked", lambda _b: self.choose_folder())
        self.progress = Gtk.ProgressBar(valign=Gtk.Align.CENTER)
        self.progress.set_size_request(80, -1)
        self.stop = Gtk.Button(label="停止")
        self.stop.connect("clicked", lambda _b: self.cancel())
        self.convert = Gtk.Button()
        self.convert.add_css_class("suggested-action")
        self.convert_label = label("轉換")
        self.convert.set_child(hbox(6, icon("media-playlist-repeat-symbolic"), self.convert_label))
        self.convert.connect("clicked", lambda _b: self.convert_all())
        spacer = Gtk.Box(hexpand=True)
        bar = hbox(
            12,
            icon("folder-symbolic", 16, "qs-dim"),
            label("輸出位置", "qs-small", "qs-dim"),
            seg,
            self.folder_button,
            spacer,
            self.progress,
            self.stop,
            self.convert,
        )
        for side in ("start", "end"):
            getattr(bar, f"set_margin_{side}")(20)
        bar.set_margin_top(10)
        bar.set_margin_bottom(10)
        return bar

    # ------------------------------------------------------------ 更新畫面

    def refresh(self) -> None:
        m = self.model
        has = bool(m.jobs)
        self.stack.set_visible_child_name("work" if has else "empty")
        self.footer.set_visible(has)
        self.more.set_visible(has)
        self.engines_label.set_label(engine_label(sum(1 for r in self._engine_rows() if r[1])))
        self.count.set_label(str(len(m.jobs)))
        self.multi_hint.set_visible(len(m.jobs) > 1)
        self._rebuild_rows()
        self.panel.refresh()
        self._refresh_footer()

    def _hooks(self) -> RowHooks:
        return RowHooks(
            self._select, self._remove, desktop.reveal, lambda p: desktop.open_paths([p])
        )

    def _rebuild_rows(self) -> None:
        clear(self.rows)
        hooks = self._hooks()
        for job in self.model.jobs:
            self.rows.append(job_row(job, job.uid in self.model.selection, hooks))

    def _refresh_footer(self) -> None:
        m = self.model
        self.mode_same.set_active(m.output_mode == "same")
        self.mode_folder.set_active(m.output_mode == "folder")
        self.folder_button.set_visible(m.output_mode == "folder")
        self.folder_label.set_label(m.output_folder.name if m.output_folder else "選擇…")
        self.folder_button.set_tooltip_text(
            str(m.output_folder) if m.output_folder else "選擇輸出資料夾"
        )
        prog = m.overall_progress()
        self.progress.set_visible(prog is not None)
        self.progress.set_fraction(prog or 0.0)
        self.stop.set_visible(m.running)
        self.convert.set_visible(not m.running)
        ready = bool(m.ready_jobs())
        self.convert.set_sensitive(ready)
        self.convert_label.set_label(m.convert_title())
        self.convert.set_tooltip_text("開始轉換（Ctrl+Enter）" if ready else "先在右側選擇輸出格式")

    def _drag_hover(self, on: bool) -> None:
        for btn, idle, active in (
            (self.big_drop, "把檔案或資料夾拖到這裡", "放開以加入檔案"),
            (self.small_drop, "加入更多檔案", "放開以加入"),
        ):
            btn.qs_title.set_label(active if on else idle)
            (btn.add_css_class if on else btn.remove_css_class)("active")

    # ------------------------------------------------------------ 檔案

    def add_files(self, paths: Sequence[Path]) -> None:
        for job in self.model.add(paths):
            self._analyze(job.uid, job.path)
        self.refresh()

    def _analyze(self, uid: int, path: Path) -> None:
        def work() -> None:
            try:
                analysis = cmod.analyze_path(path)
            except Exception:  # noqa: BLE001 — 分析失敗也要結束「分析中」
                LOG.exception("分析檔案失敗：%s", path)
                analysis = cmod.Analysis((), (), "", None)
            to_main(self._analyzed, uid, analysis)

        threading.Thread(target=work, daemon=True).start()

    def _analyzed(self, uid: int, analysis: cmod.Analysis) -> None:
        self.model.apply_analysis(uid, analysis)
        self.refresh()

    def _select(self, uid: int, extend: bool) -> None:
        self.model.select(uid, extend)
        self.refresh()

    def _remove(self, uid: int) -> None:
        self.model.remove([uid])
        self.refresh()

    def _choose(self, fid: str) -> None:
        self.model.choose(fid)
        self.refresh()

    def _close_banner(self) -> None:
        self.model.banner = None
        self.refresh()

    def _set_mode(self, mode: str) -> None:
        if mode == self.model.output_mode:
            return
        self.model.set_output_mode(mode)
        if mode == "folder" and self.model.output_folder is None:
            self.choose_folder()
        self._refresh_footer()

    def _dialog_paths(self, dlg: Gtk.FileDialog, res: Gio.AsyncResult, finish: str) -> list[Path]:
        try:
            model = getattr(dlg, finish)(res)
        except GLib.Error:
            return []
        return [
            Path(model.get_item(i).get_path())
            for i in range(model.get_n_items())
            if model.get_item(i).get_path()
        ]

    def pick_files(self) -> None:
        dlg = Gtk.FileDialog(title="選擇要轉換的檔案或資料夾", accept_label="加入", modal=True)
        dlg.open_multiple(
            self.get_root(),
            None,
            lambda d, r: self.add_files(self._dialog_paths(d, r, "open_multiple_finish")),
        )

    def pick_folders(self) -> None:
        dlg = Gtk.FileDialog(title="選擇要轉換的檔案或資料夾", accept_label="加入", modal=True)
        dlg.select_multiple_folders(
            self.get_root(),
            None,
            lambda d, r: self.add_files(self._dialog_paths(d, r, "select_multiple_folders_finish")),
        )

    def choose_folder(self) -> None:
        dlg = Gtk.FileDialog(title="選擇轉換後檔案的輸出位置", accept_label="選擇", modal=True)
        if self.model.output_folder is not None:
            dlg.set_initial_folder(Gio.File.new_for_path(str(self.model.output_folder)))
        dlg.select_folder(self.get_root(), None, self._folder_chosen)

    def _folder_chosen(self, dlg: Gtk.FileDialog, res: Gio.AsyncResult) -> None:
        try:
            f = dlg.select_folder_finish(res)
        except GLib.Error:
            self._refresh_footer()
            return
        if f is not None and f.get_path():
            self.model.set_output_folder(Path(f.get_path()))
        self._refresh_footer()

    # ------------------------------------------------------------ 轉換與合併

    def convert_all(self) -> None:
        if self.model.running:
            return
        if self.model.needs_folder():
            self.choose_folder()
            return
        if self.model.begin_convert():
            self._next()

    def _next(self) -> None:
        uid = self.model.next_queued()
        job = self.model.job(uid) if uid is not None else None
        if job is None or job.target_id is None:
            self.model.end_convert()
            self.runner, self._uid = None, None
            self.refresh()
            return
        self._uid = uid
        self.model.mark_running(uid)
        spec = JobSpec(
            "convert",
            job.target_id,
            [str(job.path)],
            dict(self.model.options),
            self.model.output_folder_spec(),
        )
        self.runner = JobRunner(
            spec, lambda f, _m: self._progress(uid, f), lambda r: self._finished(uid, r)
        )
        self.refresh()
        self.runner.start()

    def _progress(self, uid: int, fraction: float) -> None:
        self.model.set_progress(uid, fraction)
        self._rebuild_rows()
        self._refresh_footer()

    def _finished(self, uid: int, result: dict[str, Any]) -> None:
        self.model.finish(uid, result)
        self._next()

    def cancel(self) -> None:
        self.model.cancel_run()
        if self.runner is not None:
            self.runner.cancel()

    def merge(self) -> None:
        jobs = self.model.mergeable_jobs()
        if len(jobs) < 2 or self.model.running:
            return
        if self.model.needs_folder():
            self.choose_folder()
            return
        self.model.running = True
        self.model.banner = None
        spec = JobSpec(
            "merge-pdf",
            "merge",
            [str(j.path) for j in jobs],
            dict(self.model.options),
            self.model.output_folder_spec(),
        )
        self.runner = JobRunner(spec, lambda _f, _m: None, lambda r: self._merged(r, len(jobs)))
        self.refresh()
        self.runner.start()

    def _merged(self, result: dict[str, Any], count: int) -> None:
        self.model.running = False
        self.runner = None
        if not result.get("cancelled"):
            self.model.merge_finished(result, count)
        self.refresh()
