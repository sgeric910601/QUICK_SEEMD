"""影音工具共用的選項元件與選項值讀取。"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from pathlib import Path
from typing import Any

from ... import errors
from ...options import Choice, Info, Kind, Option, Values
from .probe import MediaInfo, infos_from, inspect_media

MOVIE_TYPES: tuple[str, ...] = ("video/*",)
AUDIO_TYPES: tuple[str, ...] = ("audio/*",)
REQUIRES: tuple[str, ...] = ("ffmpeg", "ffprobe")

# 解析度（短邊）：值 0 代表原始
RESOLUTIONS: tuple[tuple[int, str], ...] = (
    (0, "原始"),
    (2160, "4K"),
    (1080, "1080p"),
    (720, "720p"),
    (480, "480p"),
)
RESOLUTION_TITLES: dict[int, str] = dict(RESOLUTIONS)


def resolution_choices() -> tuple[Choice, ...]:
    return tuple(Choice(v, t) for v, t in RESOLUTIONS)


def source_note() -> Option:
    """選項面板上方的來源摘要（解析度、長度、編碼、大小）。"""

    def text(values: Values, info: Info) -> tuple[str, bool] | None:
        summary = info.get("summary")
        return (str(summary), False) if summary else None

    return Option(key="_source", kind=Kind.NOTE, note=text)


def inspect(files: Sequence[Path]) -> Info:
    return inspect_media(list(files))


def single_info(info: Info) -> MediaInfo | None:
    """只有一個檔案而且讀得到資訊時，回傳它的資訊。"""
    if info.get("count") != 1:
        return None
    items = infos_from(info)
    return items[0] if items else None


# ---------------------------------------------------------------- 選項值（邊界檢查）


def choice(values: Values, key: str, allowed: Iterable[Any]) -> Any:
    """選項值必須是合法選項之一；否則丟出中文錯誤。"""
    value = values.get(key)
    for a in allowed:
        if value == a and type(value) is not bool:
            return a
    raise errors.QSError(f"選項「{key}」的值不正確：{value!r}")


def flag(values: Values, key: str, default: bool) -> bool:
    value = values.get(key, default)
    return value if isinstance(value, bool) else default


def number(values: Values, key: str, default: float) -> float:
    value = values.get(key, default)
    if isinstance(value, bool):
        return default
    try:
        f = float(value)
    except (TypeError, ValueError) as exc:
        raise errors.QSError(f"選項「{key}」的值不正確：{value!r}") from exc
    if f != f or f in (float("inf"), float("-inf")):
        raise errors.QSError(f"選項「{key}」的值不正確：{value!r}")
    return f


def text(values: Values, key: str) -> str:
    value = values.get(key)
    return value if isinstance(value, str) else ""
