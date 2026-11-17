"""選項面板的狀態與數值轉換（不需要畫面）。

OptionsState 保存目前的選項值與 inspect 讀到的檔案資訊；介面訂閱變動後重新評估
每個選項的顯示／啟用／說明／動態選項。工具提供的函式出錯時記錄下來並用安全的預設值，
不讓整個面板壞掉。
"""

from __future__ import annotations

import copy
import logging
import math
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

from ..core import pagerange
from ..core.model import ToolDefinition
from ..core.options import Choice, Info, Kind, Option, Values

LOG = logging.getLogger(__name__)
NUMBER_DEFAULT_RANGE = (0.0, 1_000_000.0)
_NUM = re.compile(r"^(\d+\.?\d*|\.\d+)$")
_RATIO = re.compile(r"^r?(\d+(?:\.\d+)?)\s*[:x/×]\s*(\d+(?:\.\d+)?)$")

Listener = Callable[["str | None"], None]


@dataclass(frozen=True)
class GroupSpec:
    """面板上的一段：標題（可以沒有）、圖示與選項。"""

    title: str | None
    icon: str | None
    options: tuple[Option, ...]


def group_options(options: Sequence[Option]) -> list[GroupSpec]:
    """連續同一個 group 的選項畫成一段；沒有 group 的選項接在前一段後面。"""
    groups: list[GroupSpec] = []
    for opt in options:
        if groups and (opt.group is None or opt.group == groups[-1].title):
            last = groups[-1]
            groups[-1] = GroupSpec(last.title, last.icon, (*last.options, opt))
        else:
            groups.append(GroupSpec(opt.group, opt.group_icon, (opt,)))
    return groups


def _safe(func: Callable[[], Any], fallback: Any, what: str) -> Any:
    try:
        return func()
    except Exception:  # noqa: BLE001 — 工具的函式出錯不能讓整個面板壞掉
        LOG.exception("選項函式出錯：%s", what)
        return fallback


class OptionsState:
    """一個工具頁的選項值（每次打開工具都從預設值開始，原版不記住選項）。"""

    def __init__(self, tool: ToolDefinition) -> None:
        self.tool = tool
        self.values: Values = tool.defaults()
        self.info: Info = {}
        self._listeners: list[Listener] = []

    def subscribe(self, callback: Listener) -> Callable[[], None]:
        self._listeners.append(callback)
        return lambda: self._listeners.remove(callback)

    def _notify(self, key: str | None) -> None:
        for cb in list(self._listeners):
            cb(key)

    def set_value(self, key: str, value: Any) -> None:
        """使用者改了一個值；工具有 adjust 時一併套用它回傳的連動（連動本身不再觸發 adjust）。"""
        if key in self.values and self.values[key] == value:
            return
        values = {**self.values, key: value}
        adjust = self.tool.adjust
        if adjust is not None:
            changes = _safe(lambda: adjust(dict(values), key, self.info), None, "adjust")
            if isinstance(changes, dict):
                values.update({k: v for k, v in changes.items() if k in values})
        self.values = values
        self._notify(key)

    def merge(self, changes: Values) -> None:
        """工具的 prefill 結果：只接受已知的選項鍵。"""
        known = {k: v for k, v in changes.items() if k in self.values}
        if known:
            self.values = {**self.values, **known}
        self._notify(None)

    def set_info(self, info: Info) -> None:
        self.info = dict(info)
        self._notify(None)

    def snapshot(self) -> Values:
        return copy.deepcopy(self.values)

    # ------------------------------------------------------------ 評估

    def visible(self, opt: Option) -> bool:
        if not _safe(lambda: opt.is_visible(self.values, self.info), True, opt.key):
            return False
        if opt.kind is Kind.NOTE:
            return self.note(opt) is not None
        return True

    def enabled(self, opt: Option) -> bool:
        return bool(_safe(lambda: opt.is_enabled(self.values, self.info), True, opt.key))

    def choices(self, opt: Option) -> tuple[Choice, ...]:
        return tuple(
            _safe(lambda: opt.current_choices(self.values, self.info), opt.choices, opt.key)
        )

    def range(self, opt: Option) -> tuple[float, float]:
        """滑桿／數字目前的範圍（工具可以依檔案資訊動態決定）。"""
        raw = _safe(lambda: opt.current_range(self.values, self.info), None, opt.key)
        if raw is None:
            return number_range(opt)
        return number_range(opt, raw)

    def note(self, opt: Option) -> tuple[str, bool] | None:
        """選項下方的說明（文字, 是否警告）；NOTE 種類就是框裡的內容。"""
        dynamic = None
        if opt.note is not None:
            dynamic = _safe(lambda: opt.note(self.values, self.info), None, opt.key)
        if dynamic is not None:
            return (str(dynamic[0]), bool(dynamic[1]))
        if opt.kind is Kind.PAGE_RANGE:
            text = str(self.values.get(opt.key) or "")
            return pagerange.caption(text, self.info.get("page_count"))
        if opt.kind is Kind.NOTE and opt.note is None and opt.label:
            return (opt.label, False)
        return None

    def placeholder(self, opt: Option, fallback: str = "") -> str:
        """欄位的提示文字：工具有 dynamic_placeholder 就用它（例如「報告-已合併」），否則用 placeholder。"""
        static = opt.placeholder or fallback
        func = opt.dynamic_placeholder
        if func is None:
            return static
        text = _safe(lambda: func(self.values, self.info), None, opt.key)
        return str(text) if text else static

    def action_title(self) -> str:
        return str(
            _safe(lambda: self.tool.title_for_action(self.values, self.info), "開始", "action")
        )


# ---------------------------------------------------------------- 數值


def digits_for(opt: Option) -> int:
    step = float(opt.step or 1)
    if step.is_integer():
        return 0
    text = f"{step:.6f}".rstrip("0")
    return len(text.split(".")[1]) if "." in text else 0


def number_range(
    opt: Option, current: tuple[float | None, float | None] | None = None
) -> tuple[float, float]:
    lo_raw, hi_raw = current if current is not None else (opt.minimum, opt.maximum)
    lo = float(lo_raw) if lo_raw is not None else NUMBER_DEFAULT_RANGE[0]
    hi = float(hi_raw) if hi_raw is not None else NUMBER_DEFAULT_RANGE[1]
    return lo, max(lo, hi)


def _is_int_option(opt: Option) -> bool:
    return float(opt.step or 1).is_integer() and not isinstance(opt.default, float)


def coerce_number(opt: Option, raw: float, rng: tuple[float, float] | None = None) -> int | float:
    """把控制項的數值對齊到 step、限制在範圍內；整數選項回傳 int。"""
    lo, hi = rng if rng is not None else number_range(opt)
    step = float(opt.step or 1)
    value = min(max(float(raw), lo), hi)
    value = lo + math.floor((value - lo) / step + 0.5) * step
    value = min(max(value, lo), hi)
    if _is_int_option(opt):
        return int(round(value))
    return round(value, digits_for(opt))


def slider_text(opt: Option, value: Any) -> str:
    if opt.display is not None:
        return str(opt.display(value))
    if _is_int_option(opt):
        return str(int(round(float(value))))
    return f"{float(value):.{digits_for(opt)}f}"


# ---------------------------------------------------------------- 時間（對應原版 MTTime）


def parse_time(text: str) -> float | None:
    """解析「75」「75.5」「1:15」「1:15.5」「0:01:15」；不正確回傳 None。"""
    t = text.strip().replace("：", ":").replace("。", ".").replace("s", "").replace("秒", "")
    if not t:
        return None
    parts = t.split(":")
    if len(parts) > 3:
        return None
    total = 0.0
    for i, part in enumerate(parts):
        if not _NUM.match(part):
            return None
        v = float(part)
        if i < len(parts) - 1 and not v.is_integer():
            return None
        if i > 0 and v >= 60:
            return None
        total = total * 60 + v
    return total


def format_time(seconds: float, decimals: int = 1) -> str:
    """65.25 → "1:05.3"；3725 → "1:02:05.0"。"""
    s = max(0.0, seconds if math.isfinite(seconds) else 0.0)
    factor = 10**decimals
    rounded = math.floor(s * factor + 0.5) / factor
    whole = int(rounded)
    h, m = whole // 3600, (whole % 3600) // 60
    sec = rounded - (h * 3600 + m * 60)
    sec_text = f"{sec:0{decimals + 3}.{decimals}f}" if decimals > 0 else f"{int(sec):02d}"
    return f"{h}:{m:02d}:{sec_text}" if h > 0 else f"{m}:{sec_text}"


def time_ok(text: str) -> bool:
    return not text.strip() or parse_time(text) is not None


# ---------------------------------------------------------------- 顏色、格子、勾選


def hex_to_rgb(value: str) -> tuple[float, float, float] | None:
    m = re.fullmatch(r"#?([0-9a-fA-F]{6})", (value or "").strip())
    if not m:
        return None
    raw = m.group(1)
    return tuple(int(raw[i : i + 2], 16) / 255.0 for i in (0, 2, 4))  # type: ignore[return-value]


def rgb_to_hex(r: float, g: float, b: float) -> str:
    def part(x: float) -> str:
        return f"{int(round(min(max(float(x), 0.0), 1.0) * 255)):02x}"

    return f"#{part(r)}{part(g)}{part(b)}"


def grid_rows(opt: Option, choices: Sequence[Choice]) -> list[list[Choice]]:
    cols = max(int(opt.columns or 1), 1)
    return [list(choices[i : i + cols]) for i in range(0, len(choices), cols)]


def toggle_check(current: Sequence[Any] | None, value: Any, on: bool) -> list[Any]:
    items = list(current or [])
    if on and value not in items:
        return [*items, value]
    if not on:
        return [v for v in items if v != value]
    return items


def choices_signature(choices: Sequence[Choice]) -> tuple:
    return tuple((repr(c.value), c.title, c.subtitle, c.icon, c.badge) for c in choices)


# ---------------------------------------------------------------- 裁剪比例


def _ratio_from_text(text: str) -> float | None:
    s = text.strip().lower()
    if s in ("", "free", "自由", "none"):
        return None
    if s in ("square", "正方形"):
        return 1.0
    if s.startswith("a4"):
        rest = s[2:].strip(" -_")
        landscape = rest.startswith(("l", "橫")) or "橫" in s
        return 297 / 210 if landscape else 210 / 297
    m = _RATIO.match(s)
    if m:
        a, b = float(m.group(1)), float(m.group(2))
        return a / b if a > 0 and b > 0 else None
    return None


def parse_ratio(value: Any, title: str | None) -> float | None:
    """裁剪比例選項的值 → 寬 ÷ 高；「自由」回傳 None。值看不懂時改用選項標題判斷。"""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value) if value > 0 else None
    if isinstance(value, str):
        if value.strip().lower() in ("free", "自由", ""):
            return None
        hit = _ratio_from_text(value)
        if hit is not None:
            return hit
    if title:
        return _ratio_from_text(title)
    return None
