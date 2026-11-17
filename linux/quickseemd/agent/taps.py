"""拖曳中「點一下」修飾鍵的判斷（純邏輯，不碰 X）。

原版在拖曳時只看「剛按下」的瞬間；Linux 上 Shift／Ctrl 會改變 Nemo 的拖放動作，
所以改成「快速點一下」：300 ms 內按下又放開、期間沒有牽涉其他修飾鍵，才算數。

狀態來源有兩個：每 40 ms 輪詢一次的修飾鍵遮罩（sample），以及 XInput 2.2 的原始按鍵事件
（press／release）。兩個來源可能互相落後，所以每個鍵記住最後一次改變的時間，
比它還舊的樣本一律忽略，同一次按壓也只會算一次。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from ..core import settings as qs_settings

MODIFIERS: tuple[str, ...] = ("shift", "control", "alt", "super")
TAP_MAX_SECONDS = 0.30
_EPSILON = 1e-6  # 浮點誤差：剛好 300 ms 也算

# X 的修飾鍵遮罩位元（Xlib.X.ShiftMask 等；GNOME 下 Alt＝Mod1、Super＝Mod4）
MASK_BITS: dict[str, int] = {"shift": 1 << 0, "control": 1 << 2, "alt": 1 << 3, "super": 1 << 6}

_KEYSYM_NAMES: dict[str, str] = {
    "Shift_L": "shift",
    "Shift_R": "shift",
    "Control_L": "control",
    "Control_R": "control",
    "Alt_L": "alt",
    "Alt_R": "alt",
    "Meta_L": "alt",
    "Meta_R": "alt",
    "Super_L": "super",
    "Super_R": "super",
    "Escape": "escape",
}

WHEEL_MODES: tuple[str, ...] = ("convert", "tools", "actions")
_VALID_KEYS = frozenset(k for k, _label in qs_settings.WHEEL_KEYS)


@dataclass(frozen=True)
class _Press:
    since: float
    clean: bool


def mask_to_keys(mask: int) -> frozenset[str]:
    """修飾鍵遮罩 → 目前按著的鍵名稱。"""
    return frozenset(name for name, bit in MASK_BITS.items() if mask & bit)


def key_for_keysym_name(name: str) -> str | None:
    """X 的 keysym 名稱（Shift_L、Escape…）→ 我們的鍵名稱；不相關的鍵回傳 None。"""
    return _KEYSYM_NAMES.get(name)


def wheel_keys(values: Mapping[str, object]) -> dict[str, str]:
    """從設定讀出三種輪盤各用哪個鍵（不認得的值用預設值）。"""
    out: dict[str, str] = {}
    for mode in WHEEL_MODES:
        key = f"wheel.key.{mode}"
        value = values.get(key)
        out[mode] = (
            value if isinstance(value, str) and value in _VALID_KEYS else qs_settings.DEFAULTS[key]
        )
    return out


def mode_for_key(key: str, keys: Mapping[str, str]) -> str | None:
    """點了這個鍵要開哪個輪盤：依原版順序（轉換、工具、動作）取第一個符合的。"""
    for mode in WHEEL_MODES:
        if keys.get(mode) == key:
            return mode
    return None


class TapDetector:
    """一次拖曳期間的修飾鍵狀態機。只在追蹤執行緒裡使用（不需要鎖）。"""

    def __init__(self, tap_max: float = TAP_MAX_SECONDS) -> None:
        self.tap_max = tap_max
        self._down: dict[str, _Press] = {}
        self._last_change: dict[str, float] = {}

    def reset(self, held: Iterable[str], t: float) -> None:
        """開始追蹤：當下已經按著的鍵不算（例如按著 Shift 開始拖曳）。"""
        self._down = {k: _Press(t, clean=False) for k in held if k in MODIFIERS}
        self._last_change = {k: t for k in MODIFIERS}

    def press(self, key: str, t: float) -> None:
        if key not in MODIFIERS or key in self._down or t < self._last_change.get(key, 0.0):
            return
        others_down = bool(self._down)
        # 其他鍵正按著：大家都不算點一下
        self._down = {k: _Press(p.since, clean=False) for k, p in self._down.items()}
        self._down[key] = _Press(t, clean=not others_down)
        self._last_change[key] = t

    def release(self, key: str, t: float) -> str | None:
        """放開；是「點一下」就回傳鍵名稱。"""
        if key not in self._down or t < self._last_change.get(key, 0.0):
            return None
        press = self._down.pop(key)
        self._last_change[key] = t
        if press.clean and not self._down and (t - press.since) <= self.tap_max + _EPSILON:
            return key
        return None

    def sample(self, held: Iterable[str], t: float) -> list[str]:
        """輪詢到的按鍵狀態；回傳這次偵測到的「點一下」。先處理按下再處理放開（保守）。"""
        held_set = {k for k in held if k in MODIFIERS}
        for key in MODIFIERS:
            if key in held_set and key not in self._down:
                self.press(key, t)
        taps: list[str] = []
        for key in MODIFIERS:
            if key not in held_set and key in self._down:
                tapped = self.release(key, t)
                if tapped:
                    taps.append(tapped)
        return taps
