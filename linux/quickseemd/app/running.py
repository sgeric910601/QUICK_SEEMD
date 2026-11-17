"""執行中的遮罩（對應原版 runningOverlay）與失敗頁（「無法完成」）。"""

from __future__ import annotations

from collections.abc import Callable

import gi

gi.require_version("Gtk", "4.0")
from gi.repository import Gtk  # noqa: E402

from ..core.model import ToolDefinition  # noqa: E402
from . import css  # noqa: E402
from .widgets import icon, label, primary, secondary, tool_tile, vbox  # noqa: E402

BAR_WIDTH = 320


class RunningOverlay(Gtk.Box):
    """16% 暗色遮罩＋卡片：圖示、百分比、進度條、訊息、取消。"""

    def __init__(self, tool: ToolDefinition, on_cancel: Callable[[], None]) -> None:
        super().__init__(hexpand=True, vexpand=True)
        self.add_css_class("qs-dimmer")
        card = vbox(14, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, hexpand=True)
        card.add_css_class("qs-run-card")
        card.add_css_class(css.tint_class(tool.tint_rgb))
        card.append(tool_tile(tool.icon, tool.tint_rgb, 52, tool.badge))
        self.pct = label("0%", "qs-pct", xalign=0.5)
        card.append(self.pct)
        self.bar = Gtk.ProgressBar()
        self.bar.set_size_request(BAR_WIDTH, -1)
        card.append(self.bar)
        self.message = label("準備中…", "qs-dim", xalign=0.5, wrap=True, lines=2, ellipsize="end")
        self.message.set_size_request(BAR_WIDTH, -1)
        self.message.set_max_width_chars(30)
        card.append(self.message)
        self.cancel_button = secondary("取消", on_click=on_cancel)
        self.cancel_button.set_halign(Gtk.Align.CENTER)
        card.append(self.cancel_button)
        self.append(card)
        self.set_visible(False)

    def start(self) -> None:
        self.update(0.0, "準備中…")
        self.cancel_button.set_sensitive(True)
        self.set_visible(True)

    def update(self, fraction: float, message: str | None) -> None:
        f = min(max(fraction, 0.0), 1.0)
        self.pct.set_label(f"{int(f * 100)}%")
        self.bar.set_fraction(f)
        if message is not None:
            self.message.set_label(message or "處理中…")


def failure_page(tool: ToolDefinition, reason: str, on_back: Callable[[], None]) -> Gtk.Widget:
    box = vbox(16, halign=Gtk.Align.CENTER, valign=Gtk.Align.CENTER, hexpand=True, vexpand=True)
    box.add_css_class(css.tint_class(tool.tint_rgb))
    box.append(icon("dialog-error-symbolic", 56, "qs-error-icon"))
    box.append(label("無法完成", "qs-fail-title", xalign=0.5))
    msg = label(reason, "qs-subtitle", xalign=0.5, wrap=True, selectable=True)
    msg.set_justify(Gtk.Justification.CENTER)
    msg.set_max_width_chars(48)
    box.append(msg)
    back = primary("返回修改", "go-previous-symbolic", on_click=on_back)
    back.set_size_request(200, -1)
    back.set_halign(Gtk.Align.CENTER)
    back.set_margin_top(8)
    box.append(back)
    return box
