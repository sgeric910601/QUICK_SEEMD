"""系統匣選單的內容（純邏輯；對應原版選單列圖示的選單，PR1 不含更新項目）。"""

from __future__ import annotations

from dataclasses import dataclass

from ..core import registry
from ..core.model import CATEGORIES

TOOLTIP = "QUICK_SEEMD"


@dataclass(frozen=True)
class MenuEntry:
    kind: str  # item、check、separator、header（不能點的分組標題）、submenu
    label: str = ""
    action: str = (
        ""  # open-main、pick-files、tool:<代碼>、toggle-wheel、toggle-autostart、settings、quit
    )
    active: bool = False
    children: tuple[MenuEntry, ...] = ()


SEPARATOR = MenuEntry("separator")


def escape_mnemonic(label: str) -> str:
    """選單把「_」當成快速鍵記號：寫成「__」才會顯示一個底線。"""
    return label.replace("_", "__")


def _favorites() -> tuple[MenuEntry, ...]:
    out = []
    for tool_id in registry.FAVORITES:
        tool = registry.tool(tool_id)
        if tool is not None:
            out.append(MenuEntry("item", tool.title, f"tool:{tool.id}"))
    return tuple(out)


def _all_tools() -> tuple[MenuEntry, ...]:
    out: list[MenuEntry] = []
    for cat in CATEGORIES:
        tools = registry.tools_in(cat)
        if not tools:
            continue
        out.append(MenuEntry("header", cat.title))
        out.extend(MenuEntry("item", t.title, f"tool:{t.id}") for t in tools)
    return tuple(out)


def build_menu(*, wheel_enabled: bool, autostart_on: bool) -> tuple[MenuEntry, ...]:
    return (
        MenuEntry("item", "開啟 QUICK_SEEMD 工具箱", "open-main"),
        MenuEntry("item", "選擇檔案…", "pick-files"),
        SEPARATOR,
        MenuEntry("submenu", "常用工具", children=_favorites()),
        MenuEntry("submenu", "所有工具", children=_all_tools()),
        SEPARATOR,
        MenuEntry("check", "拖曳轉換輪盤", "toggle-wheel", active=wheel_enabled),
        MenuEntry("check", "登入時自動啟動", "toggle-autostart", active=autostart_on),
        MenuEntry("item", "設定…", "settings"),
        SEPARATOR,
        MenuEntry("item", "結束 QUICK_SEEMD", "quit"),
    )
