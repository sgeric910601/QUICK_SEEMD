"""預覽視窗的開關狀態（照 GNOME 內建預覽 Sushi 的行為）。

- 同一個檔案、要求「已顯示就關閉」→ 關閉
- 同一個檔案、不要求關閉 → 把視窗叫到前面
- 不同檔案 → 換內容（視窗沒開就開）

收到請求的瞬間就記下目前的網址；每次換內容或關閉都把 generation 加一，
背景執行緒準備好的內容只有在 generation 還相同時才顯示（連按兩次空白鍵也不會錯）。
"""

from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum


class Decision(Enum):
    SHOW = "show"  # 顯示新內容（開視窗或換內容）
    PRESENT = "present"  # 同一個檔案：把視窗叫到前面
    CLOSE = "close"  # 關閉視窗


@dataclass(frozen=True)
class PreviewState:
    current_uri: str | None = None
    generation: int = 0

    @property
    def is_open(self) -> bool:
        return self.current_uri is not None

    def is_current(self, generation: int) -> bool:
        """這一代的內容還需要顯示嗎？"""
        return self.is_open and generation == self.generation


def on_show_file(
    state: PreviewState, uri: str, close_if_shown: bool
) -> tuple[PreviewState, Decision]:
    """處理 ShowFile 請求，回傳新的狀態與要做的事。"""
    if state.current_uri == uri:
        if close_if_shown:
            return on_close(state), Decision.CLOSE
        return state, Decision.PRESENT
    return replace(state, current_uri=uri, generation=state.generation + 1), Decision.SHOW


def on_close(state: PreviewState) -> PreviewState:
    """視窗關閉（使用者按鍵、Close() 或同一個檔案再按一次）。"""
    return replace(state, current_uri=None, generation=state.generation + 1)
