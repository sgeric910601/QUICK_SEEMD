"""剪輯範圍（對應原版 MTTrimSession），不需要畫面。

和影音工具的合約：選項 `start`／`end` 是時間字串（例如 "1:05.50"；end 空字串＝到結尾）。
保留或刪除選取範圍讀選項 `keep`（或 `mode`）的值："remove"／"delete" 代表刪除，其他代表保留。
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

import numpy as np

from ..options_model import format_time, parse_time

MIN_LENGTH = 0.1
EDGE = 0.05
REMOVE_VALUES = ("remove", "delete")


def keep_mode(values: dict[str, Any]) -> str:
    for key in ("keep", "mode"):
        if str(values.get(key, "")).lower() in REMOVE_VALUES:
            return "remove"
    return "selection"


def length_title(keep: str) -> str:
    return "刪除長度" if keep == "remove" else "保留長度"


class TrimModel:
    """開始／結束時間與長度；所有限制照原版 setStart／setEnd。"""

    def __init__(self) -> None:
        self.duration: float | None = None
        self.start = 0.0
        self.end: float | None = None

    @property
    def effective_end(self) -> float:
        if self.end is not None:
            return self.end
        return self.duration or 0.0

    def load(self, duration: float) -> None:
        self.duration = max(float(duration), 0.0)
        self.start = 0.0
        self.end = self.duration

    def set_start(self, t: float) -> None:
        limit = self.effective_end - MIN_LENGTH
        self.start = max(0.0, min(float(t), limit))

    def set_end(self, t: float) -> None:
        d = self.duration if self.duration is not None else float(t)
        self.end = min(d, max(float(t), self.start + MIN_LENGTH))

    def from_values(self, values: dict[str, Any]) -> None:
        """由選項文字同步回來（使用者在欄位輸入）；不合理的範圍留給 validate 說明。"""
        start = parse_time(str(values.get("start") or "")) or 0.0
        end = parse_time(str(values.get("end") or ""))
        d = self.duration
        if d is not None:
            start = min(start, max(d - MIN_LENGTH, 0.0))
            end = d if end is None else min(end, d)
        self.start, self.end = max(start, 0.0), end

    def values(self) -> dict[str, str]:
        end = "" if self.end is None else format_time(self.end, decimals=2)
        return {"start": format_time(self.start, decimals=2), "end": end}

    def summary(self, keep: str) -> tuple[str, str]:
        length = max(0.0, self.effective_end - self.start)
        total = self.duration or 0.0
        result = length if keep != "remove" else max(0.0, total - length)
        return (
            f"選取 {format_time(self.start)} – {format_time(self.effective_end)}",
            f"輸出長度約 {format_time(result)}（在左側拖曳時間軸的把手調整）",
        )

    def validate(self, keep: str) -> str | None:
        if self.end is not None and self.end - self.start < MIN_LENGTH:
            return "結束時間必須比開始時間晚至少 0.1 秒"
        d = self.duration
        if d is not None:
            if self.start >= d:
                return f"開始時間超過檔案長度（{format_time(d)}）"
            if keep == "remove" and self.start <= EDGE and self.effective_end >= d - EDGE:
                return "不能刪除整段內容"
        return None


def compute_peaks(samples: np.ndarray, count: int) -> list[float]:
    """把音訊樣本分成 count 段，每段取最大振幅，再以整體最大值正規化成 0…1。"""
    if count <= 0:
        return []
    if samples.size == 0:
        return [0.0] * count
    data = np.abs(samples.astype(np.float64))
    chunks = np.array_split(data, count)
    peaks = np.array([c.max() if c.size else 0.0 for c in chunks])
    top = float(peaks.max())
    if top <= 0:
        return [0.0] * count
    return [float(p / top) for p in peaks]


def strip_times(duration: float, count: int) -> Sequence[float]:
    """縮圖帶每一格取畫面的時間點（每段的中間）。"""
    if duration <= 0 or count <= 0:
        return []
    return [(i + 0.5) * duration / count for i in range(count)]
