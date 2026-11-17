"""「右鍵、拖曳與空白鍵預覽」頁（對應原版 IntegrationView）：系統整合的狀態、開關與說明。"""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
gi.require_version("Adw", "1")
from gi.repository import Adw, Gtk  # noqa: E402

from ..core import settings  # noqa: E402
from ..core.model import TINTS  # noqa: E402
from . import css, dbus, integration  # noqa: E402
from .prefs_rows import WheelKeys, autostart_row, switch_row  # noqa: E402
from .widgets import hbox, icon, label, secondary, sf_icon, vbox  # noqa: E402

TITLE = "在任何地方使用 QUICK_SEEMD"
SUBTITLE = "不用打開主程式：右鍵一鍵轉換、拖曳時點一下 Shift 叫出轉換輪盤、按空白鍵預覽。"
RIGHT_CLICK = (
    "在 Nemo 對檔案按右鍵 →「QUICK_SEEMD」，依檔案類型列出可以直接轉換的格式與工具"
    "（例如 Word 轉 PDF、壓縮圖片、影片轉 GIF），點一下就完成，結果存在原檔旁邊。"
)
WHEEL = (
    "從 Nemo、桌面或任何程式拖著檔案時，點一下按鍵叫出輪盤：轉換輪盤（格式）、"
    "工具輪盤（壓縮、去背、拆分…）、動作輪盤（用其他程式開啟、浮動暫存、拷貝、移動、拷貝路徑、終端機）。"
    "放開按鍵輪盤仍會留著，拖曳中點另一個鍵可切換，Esc 取消。"
    "把檔案放到想要的項目上就開始處理；放在中間則打開 QUICK_SEEMD。"
)
PREVIEW = (
    "在 Nemo 選取檔案按空白鍵：程式碼上色、Markdown 排版、JSON 自動排版、壓縮檔內容、"
    "SQLite 資料表、二進位檔資訊……都能直接看。"
)
BACKGROUND = "關閉視窗後 QUICK_SEEMD 會留在系統匣待命，右鍵轉換與拖曳輪盤才能隨時使用。"


class _Card(Gtk.Box):
    def __init__(self, symbol: str, tint: tuple, title: str, detail: str) -> None:
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=16)
        self.add_css_class("qs-int-card")
        tile = Gtk.Box(valign=Gtk.Align.START, hexpand=False)
        tile.add_css_class("qs-int-tile")
        tile.add_css_class(css.tint_class(tint))
        img = sf_icon(symbol, 22)
        img.set_hexpand(True)
        tile.append(img)
        self.append(tile)
        self.status_icon = icon("emblem-ok-symbolic", 16)
        self.status = label("", "qs-semibold")
        head = hbox(8, label(title, "title-3"), self.status_icon, self.status)
        self.body = vbox(8, head, label(detail, "qs-dim", wrap=True))
        self.body.set_hexpand(True)
        self.append(self.body)

    def set_status(self, text: str | None, ok: bool | None) -> None:
        self.status.set_visible(text is not None)
        self.status_icon.set_visible(text is not None)
        if text is None:
            return
        self.status.set_label(text)
        name = (
            "emblem-ok-symbolic"
            if ok
            else ("content-loading-symbolic" if ok is None else "dialog-warning-symbolic")
        )
        self.status_icon.set_from_icon_name(name)
        for w in (self.status, self.status_icon):
            for c in ("qs-success", "qs-warning", "qs-dim"):
                w.remove_css_class(c)
            w.add_css_class("qs-success" if ok else ("qs-dim" if ok is None else "qs-warning"))


def _list(*rows: Gtk.Widget) -> Gtk.ListBox:
    lb = Gtk.ListBox(selection_mode=Gtk.SelectionMode.NONE)
    lb.add_css_class("boxed-list")
    for r in rows:
        lb.append(r)
    return lb


class IntegrationPage(Gtk.Box):
    def __init__(self, toast: Callable[[str], None]) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL, hexpand=True, vexpand=True)
        self.toast = toast
        toolbar = Adw.ToolbarView()
        bar = Adw.HeaderBar()
        bar.set_title_widget(Gtk.Box())
        toolbar.add_top_bar(bar)
        toolbar.set_top_bar_style(Adw.ToolbarStyle.FLAT)
        body = vbox(18, margin_top=12, margin_bottom=28, margin_start=28, margin_end=28)
        body.append(
            vbox(6, label(TITLE, "qs-int-title"), label(SUBTITLE, "qs-subtitle", wrap=True))
        )
        self.nemo = _Card("cursorarrow.click.2", TINTS["pdf"], "右鍵一鍵轉換", RIGHT_CLICK)
        self.nemo.body.append(self._recheck())
        body.append(self.nemo)
        body.append(self._wheel_card())
        self.preview = _Card("doc.viewfinder", TINTS["slate"], "空白鍵預覽", PREVIEW)
        self.preview.body.append(self._recheck())
        body.append(self.preview)
        body.append(self._background_card())
        scroll = Gtk.ScrolledWindow(hscrollbar_policy=Gtk.PolicyType.NEVER, vexpand=True)
        scroll.set_child(Adw.Clamp(maximum_size=980, child=body))
        bg = vbox(0, scroll)
        bg.add_css_class("qs-bg")
        bg.add_css_class(css.tint_class(TINTS["slate"]))
        toolbar.set_content(bg)
        self.append(toolbar)
        self.refresh()

    def _recheck(self) -> Gtk.Widget:
        btn = secondary("重新檢查", "view-refresh-symbolic", on_click=self.refresh)
        btn.set_halign(Gtk.Align.START)
        return btn

    def _wheel_card(self) -> Gtk.Widget:
        self.wheel = _Card("arrow.triangle.2.circlepath", TINTS["image"], "拖曳轉換輪盤", WHEEL)
        self.keys = WheelKeys(self.toast)
        toggle = switch_row(
            "啟用拖曳轉換輪盤", "wheel.enabled", toast=self.toast, on_change=self._wheel_changed
        )
        self.wheel.body.append(_list(toggle, *self.keys.all_rows()))
        self._wheel_changed(bool(settings.load()["wheel.enabled"]))
        return self.wheel

    def _wheel_changed(self, on: bool) -> None:
        self.keys.set_visible(on)
        self.wheel.set_status("已開啟" if on else "已關閉", on)

    def _background_card(self) -> Gtk.Widget:
        self.background = _Card("bolt", TINTS["teal"], "系統匣與背景執行", BACKGROUND)
        rows = (
            switch_row("在系統匣顯示圖示", "tray.show", toast=self.toast),
            switch_row("關閉視窗後繼續在背景執行", "background.enabled", toast=self.toast),
            autostart_row(self.toast),
        )
        self.background.body.append(_list(*rows))
        return self.background

    def refresh(self) -> None:
        self.nemo.set_status(*integration.nemo_status(integration.nemo_extension_installed()))
        self.preview.set_status(
            *integration.preview_status(integration.preview_service_installed())
        )
        self.background.set_status(*integration.agent_status(None))
        dbus.ping_agent(self._agent, autostart=False)

    def _agent(self, alive: bool) -> None:
        self.background.set_status(*integration.agent_status(alive))
