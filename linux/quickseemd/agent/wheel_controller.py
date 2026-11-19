"""拖曳偵測（x11_drag，在追蹤執行緒）與輪盤視窗（主執行緒）之間的協調。

- 每次拖曳只讀一次檔案清單；每種輪盤的內容在這次拖曳裡只算一次。
- 點一下按鍵：輪盤沒開就在游標處打開，已開就切換成那種輪盤（原版行為）。
- Esc 收起輪盤；放開滑鼠交給輪盤視窗決定何時收起（等拖放完成）。
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from ..core.wheel import WheelItem
from . import wheel_model
from .taps import mode_for_key, wheel_keys

log = logging.getLogger(__name__)

Catalog = Callable[[str, Sequence[Path]], Sequence[WheelItem]]
Dispatch = Callable[[Callable[[], None]], None]
FetchFiles = Callable[[], "list[Path] | None"]


@dataclass(frozen=True)
class WheelState:
    mode: str
    files: tuple[Path, ...]
    items: tuple[WheelItem, ...]
    center_title: str
    hint: str


class WheelUI(Protocol):
    def present(self, state: WheelState, x: int, y: int) -> None: ...

    def hide(self) -> None: ...

    def drag_released(self) -> None: ...


class WheelController:
    def __init__(
        self,
        ui: WheelUI,
        settings_getter: Callable[[], Mapping[str, object]],
        catalog: Catalog = wheel_model.build_catalog,
        *,
        dispatch: Dispatch,
    ) -> None:
        self._ui = ui
        self._settings = settings_getter
        self._catalog = catalog
        self._dispatch = dispatch
        self._files: tuple[Path, ...] | None = None
        self._cache: dict[str, tuple[WheelItem, ...]] = {}

    # 以下在追蹤執行緒呼叫

    def drag_began(self) -> None:
        self._files = None
        self._cache = {}

    def key_tapped(self, key: str, x: int, y: int, fetch: FetchFiles) -> None:
        keys = wheel_keys(self._settings())
        mode = mode_for_key(key, keys)
        if mode is None:
            return
        if self._files is None:
            self._files = tuple(fetch() or ())
        state = WheelState(
            mode,
            self._files,
            self._items_for(mode),
            wheel_model.center_title(mode, len(self._files)),
            wheel_model.hint(mode, keys),
        )
        log.info("點了 %s：開啟%s輪盤（%d 個檔案）", key, mode, len(self._files))
        self._dispatch(lambda: self._ui.present(state, x, y))

    def escape_pressed(self) -> None:
        self._dispatch(self._ui.hide)

    def drag_ended(self) -> None:
        self._dispatch(self._ui.drag_released)

    def _items_for(self, mode: str) -> tuple[WheelItem, ...]:
        if mode not in self._cache:
            try:
                self._cache[mode] = tuple(self._catalog(mode, self._files or ()))
            except Exception as exc:  # noqa: BLE001 — 內容算不出來也要能開輪盤（中間可放下）
                log.error("算不出輪盤內容：%s", exc)
                self._cache[mode] = ()
        return self._cache[mode]
