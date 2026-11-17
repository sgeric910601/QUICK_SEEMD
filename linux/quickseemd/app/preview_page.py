"""預覽測試頁（對應原版 PreviewTestView）：選一個檔案，叫空白鍵預覽視窗顯示它。

和 Nemo 按空白鍵走同一條路：D-Bus org.nemo.Preview.ShowFile(uri, 這個視窗的代碼, False)。
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gio, GLib, Gtk  # noqa: E402

from ..core.model import TINTS  # noqa: E402
from . import css, dbus, desktop  # noqa: E402
from .widgets import (  # noqa: E402
    add_file_drop,
    drop_overlay,
    hbox,
    icon,
    label,
    link,
    mime_icon,
    secondary,
    vbox,
)

EMPTY_TITLE = "把任何檔案拖到這裡"
EMPTY_TEXT = (
    "程式碼、Markdown、JSON、CSV、壓縮檔、SQLite、二進位檔…\n"
    "這裡看到的，就是在 Nemo 按空白鍵會看到的預覽。"
)


def window_xid(widget: Gtk.Widget) -> int:
    """這個視窗在 X11 上的代碼（預覽視窗會掛在它上面）；不是 X11 時回傳 0。"""
    native = widget.get_native()
    surface = native.get_surface() if native is not None else None
    if surface is None:
        return 0
    try:
        gi.require_version("GdkX11", "4.0")
        from gi.repository import GdkX11  # noqa: PLC0415
    except (ImportError, ValueError):
        return 0
    if isinstance(surface, GdkX11.X11Surface):
        return int(surface.get_xid())
    return 0


def preview_error_text(error: str) -> str:
    """D-Bus 的錯誤訊息改成看得懂的中文。"""
    if "ServiceUnknown" in error or "not provided by any" in error:
        return "預覽服務尚未安裝（請重新執行安裝程式）。"
    if "NoReply" in error or "Timeout" in error:
        return "預覽服務沒有回應。"
    return error


class PreviewPage(Gtk.Box):
    def __init__(self, toast: Callable[[str], None]) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, hexpand=True, vexpand=True)
        self.toast = toast
        self.path: Path | None = None
        toolbar = Adw.ToolbarView()
        bar = Adw.HeaderBar()
        self.file_icon = Gtk.Box()
        self.title = label("預覽測試", "heading", ellipsize="middle")
        bar.set_title_widget(hbox(8, self.file_icon, self.title))
        choose = Gtk.Button()
        choose.add_css_class("suggested-action")
        choose.set_child(hbox(6, icon("document-open-symbolic"), label("選擇檔案…")))
        choose.connect("clicked", lambda _b: self.choose())
        bar.pack_end(choose)
        self.reveal = link("在資料夾中顯示", "folder-symbolic", on_click=self._reveal)
        self.reveal.set_visible(False)
        bar.pack_end(self.reveal)
        toolbar.add_top_bar(bar)
        toolbar.set_content(self._body())
        self.append(toolbar)

    def _body(self) -> Gtk.Widget:
        box = vbox(12, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, vexpand=True)
        box.append(icon("document-open-symbolic", 54, "qs-dim"))
        box.append(label(EMPTY_TITLE, "title-2", xalign=0.5))
        text = label(EMPTY_TEXT, "qs-dim", xalign=0.5, wrap=True)
        text.set_justify(Gtk.Justification.CENTER)
        box.append(text)
        self.status = label("", "qs-small", xalign=0.5, wrap=True, selectable=True)
        self.status.set_visible(False)
        box.append(self.status)
        self.again = secondary("再預覽一次", "view-refresh-symbolic", on_click=self.show_preview)
        self.again.set_halign(Gtk.Align.CENTER)
        self.again.set_visible(False)
        box.append(self.again)
        over = Gtk.Overlay(hexpand=True, vexpand=True)
        holder = vbox(0, box)
        holder.add_css_class("qs-bg")
        holder.add_css_class(css.tint_class(TINTS["slate"]))
        over.set_child(holder)
        hint = drop_overlay()
        over.add_overlay(hint)
        add_file_drop(over, lambda files: self.load(files[0]), hint.set_visible)
        return over

    def choose(self) -> None:
        dlg = Gtk.FileDialog(title="預覽測試", accept_label="預覽", modal=True)
        dlg.open(self.get_root(), None, self._chosen)

    def _chosen(self, dlg: Gtk.FileDialog, res: Gio.AsyncResult) -> None:
        try:
            f = dlg.open_finish(res)
        except GLib.Error:
            return
        if f is not None and f.get_path():
            self.load(Path(f.get_path()))

    def load(self, path: Path) -> None:
        """只用第一個檔案（原版行為）。"""
        self.path = path
        self.title.set_label(path.name)
        child = self.file_icon.get_first_child()
        if child is not None:
            self.file_icon.remove(child)
        self.file_icon.append(mime_icon(path, 22))
        self.reveal.set_visible(True)
        self.show_preview()

    def show_preview(self) -> None:
        if self.path is None:
            return
        self._set_status(f"正在開啟「{self.path.name}」的預覽…", None)
        dbus.preview_show_file(self.path.as_uri(), window_xid(self), self._shown)

    def _shown(self, error: str | None) -> None:
        if error:
            self._set_status(f"無法開啟預覽：{preview_error_text(error)}", True)
        elif self.path is not None:
            self._set_status(f"已在預覽視窗開啟「{self.path.name}」", False)
        self.again.set_visible(self.path is not None)

    def _set_status(self, text: str, warn: bool | None) -> None:
        self.status.set_visible(True)
        self.status.set_label(text)
        (self.status.add_css_class if warn else self.status.remove_css_class)("qs-warning")

    def _reveal(self) -> None:
        if self.path is not None:
            desktop.reveal([self.path])
