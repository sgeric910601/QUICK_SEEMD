"""宣告式的工具選項（對應原版 ToolOptionControls）。

工具只描述「有哪些選項」，主程式（GTK4）依種類自動畫出控制項；
選項值是單純的 dict（可轉成 JSON），交給工作程序執行。

動態行為用函式描述，參數一律是 (values, info)：
- values：目前的選項值 dict
- info：ToolDefinition.inspect 讀到的檔案資訊 dict（例如頁數、像素大小），還沒讀到時是 {}
"""

from __future__ import annotations

import zlib
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from typing import Any

Values = dict[str, Any]
Info = dict[str, Any]


class Kind(str, Enum):
    """控制項種類。"""

    CARDS = "cards"  # 直排卡片單選（ChoiceCards）
    CHIPS = "chips"  # 膠囊單選（ChoiceChips）
    SEGMENTED = "segmented"  # 分段按鈕（2–5 個選項）
    SLIDER = "slider"  # 滑桿（minimum/maximum/step，display 格式化顯示值）
    TOGGLE = "toggle"  # 開關（label＋caption）
    TEXT = "text"  # 文字輸入
    PASSWORD = "password"  # 密碼輸入
    NUMBER = "number"  # 整數輸入＋加減按鈕
    PAGE_RANGE = "page_range"  # 頁碼範圍（info["page_count"] 用來顯示說明與檢查）
    COLOR = "color"  # 色點單選；choice.value 是 "#rrggbb" 或 "custom"（自訂色存在 custom_key）
    GRID = "grid"  # 位置選擇格（columns 欄，choices 依列優先排列；值為 None 的格子不能選）
    FILE = "file"  # 選一個檔案（例如浮水印標誌），值是路徑字串
    TIME = "time"  # 時間輸入（「1:05.5」或「65」），值是字串
    NOTE = "note"  # 說明框（文字來自 label 或 note 函式）
    CHECKS = "checks"  # 多選勾選（值是 list，例如校驗碼演算法）


@dataclass(frozen=True)
class Choice:
    value: Any
    title: str
    subtitle: str | None = None
    icon: str | None = None  # 原版 SF Symbol 名稱，介面層用 icons.icon_name 轉換
    badge: str | None = None


@dataclass(frozen=True)
class Option:
    key: str
    kind: Kind
    label: str = ""
    default: Any = None
    choices: tuple[Choice, ...] = ()
    caption: str | None = None  # 控制項下方的固定說明（開關的副標）
    placeholder: str | None = None
    minimum: float | None = None
    maximum: float | None = None
    step: float = 1
    display: Callable[[Any], str] | None = None  # 滑桿數值的顯示方式
    columns: int = 3  # GRID 用
    custom_key: str | None = None  # COLOR 選「自訂」時，自訂色存在這個鍵
    file_mime: str | None = None  # FILE 可選的檔案類型（例如 "image/*"）
    group: str | None = None  # 段落標題（同一 group 連續的選項畫在同一段）
    group_icon: str | None = None
    visible: Callable[[Values, Info], bool] | None = None
    enabled: Callable[[Values, Info], bool] | None = None
    # 動態說明：回傳 (文字, 是否警告) 或 None；NOTE 種類用它產生內容
    note: Callable[[Values, Info], tuple[str, bool] | None] | None = None
    # 動態選項（例如依外部工具是否存在而增減的格式）；回傳完整 choices
    dynamic_choices: Callable[[Values, Info], tuple[Choice, ...]] | None = None
    # 動態範圍（滑桿／數字）：回傳 (最小, 最大)，例如依影片長度決定 GIF 起點上限
    dynamic_range: Callable[[Values, Info], tuple[float, float]] | None = None
    # 文字／數字欄位旁的快速填入按鈕（例如 10／25／50 MB），按下就把 value 填入
    suggestions: tuple[Choice, ...] = ()

    def is_visible(self, values: Values, info: Info) -> bool:
        return self.visible is None or bool(self.visible(values, info))

    def is_enabled(self, values: Values, info: Info) -> bool:
        return self.enabled is None or bool(self.enabled(values, info))

    def current_choices(self, values: Values, info: Info) -> tuple[Choice, ...]:
        if self.dynamic_choices is not None:
            return self.dynamic_choices(values, info)
        return self.choices

    def current_range(self, values: Values, info: Info) -> tuple[float | None, float | None]:
        """目前的（最小, 最大）；有 dynamic_range 時以它為準。"""
        if self.dynamic_range is not None:
            lo, hi = self.dynamic_range(values, info)
            return float(lo), float(max(lo, hi))
        return self.minimum, self.maximum


def defaults(options: tuple[Option, ...]) -> Values:
    """所有選項的預設值（NOTE 不算）。"""
    out: Values = {}
    for opt in options:
        if opt.kind is Kind.NOTE:
            continue
        out[opt.key] = opt.default
        if opt.custom_key:
            out.setdefault(opt.custom_key, None)
    return out


def note(text: str, *, key: str = "", group: str | None = None, **kw: Any) -> Option:
    """固定文字的說明框（鍵用文字的 CRC 產生，每次執行都一樣）。"""
    return Option(
        key=key or f"_note_{zlib.crc32(text.encode()):08x}",
        kind=Kind.NOTE,
        label=text,
        group=group,
        **kw,
    )
