"""拖曳輪盤的純邏輯：拖曳中的檔案清單、輪盤內容、位置與命中判斷。

輪盤內容本身由 core.wheel.catalog 決定；這裡負責接上工具清單（最少檔案數、顏色）
與萬用轉換的格式，並把這個版本沒有的工具對應的動作排除（不然放上去只會失敗）。
"""

from __future__ import annotations

import logging
import math
import os
from collections.abc import Mapping, Sequence
from pathlib import Path
from urllib.parse import unquote_to_bytes, urlsplit

from ..core import registry
from ..core import settings as qs_settings
from ..core import wheel as qs_wheel
from ..core.filetypes import file_ref
from ..core.model import RGB
from .queue import ACCENT

log = logging.getLogger(__name__)

SIZE = qs_wheel.WINDOW_SIZE
CENTER = SIZE / 2
MAX_SAMPLE_FILES = 20


def parse_uri_list(data: bytes | str) -> list[Path]:
    """text/uri-list → 本機檔案路徑（略過註解、非 file 網址、別台主機與不合法的路徑）。"""
    if isinstance(data, bytes):
        try:
            data = data.decode("utf-8")
        except UnicodeDecodeError:
            return []
    out: list[Path] = []
    for raw in data.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        parts = urlsplit(line)
        if parts.scheme != "file" or parts.netloc not in ("", "localhost") or not parts.path:
            continue
        raw_path = unquote_to_bytes(parts.path)
        if b"\0" in raw_path:
            continue
        path = os.fsdecode(raw_path)
        if os.path.isabs(path):
            out.append(Path(os.path.normpath(path)))
    return out


def owner_is_ours(owner_xid: int, own_xid: int | None, resource_mask: int) -> bool:
    """XdndSelection 的擁有者是不是我們自己的視窗（同一條 X 連線配發的資源代碼）。"""
    if not owner_xid or own_xid is None:
        return False
    return (owner_xid & ~resource_mask) == (own_xid & ~resource_mask)


def device_to_logical(x: int, y: int, scale: int) -> tuple[int, int]:
    """X 的裝置像素 → GTK 的邏輯像素（高解析度螢幕縮放）。"""
    if scale <= 1:
        return x, y
    return x // scale, y // scale


def ring_origin(
    x: int, y: int, workarea: tuple[int, int, int, int], size: int = SIZE
) -> tuple[int, int]:
    """輪盤視窗左上角：以游標為中心，並限制在螢幕可用範圍內。"""
    wx, wy, ww, wh = workarea
    ox = min(max(x - size // 2, wx), wx + ww - size)
    oy = min(max(y - size // 2, wy), wy + wh - size)
    return ox, oy


def hit_at(x: float, y: float, count: int) -> int | str | None:
    """視窗內座標 → 格子編號、"center" 或 None。"""
    return qs_wheel.hit_test(x - CENTER, y - CENTER, count)


def label_center(index: int, count: int) -> tuple[float, float]:
    """第 index 格標籤的中心點（視窗座標）。"""
    angle = math.radians(index * 360.0 / count)
    r = qs_wheel.LABEL_RADIUS
    return CENTER + r * math.sin(angle), CENTER - r * math.cos(angle)


def _tint_for_tool(tool_id: str) -> RGB:
    tool = registry.tool(tool_id)
    return tool.tint_rgb if tool is not None else ACCENT


def _targets(paths: Sequence[Path]):
    def fetch() -> list[qs_wheel.TargetLike]:
        try:
            return qs_wheel.converter_targets([str(p) for p in paths])
        except Exception as exc:  # noqa: BLE001 — 轉換器出錯時輪盤照樣能開，只是少了格式
            log.warning("讀不到可轉換的格式：%s", exc)
            return []

    return fetch


def build_catalog(mode: str, paths: Sequence[Path]) -> list[qs_wheel.WheelItem]:
    """這個模式下輪盤要列出的格子。"""
    refs = [file_ref(p) for p in paths[:MAX_SAMPLE_FILES]]
    return qs_wheel.catalog(
        mode,
        refs,
        minimum_files=registry.minimum_files(),
        tint_for_tool=_tint_for_tool,
        targets=_targets(paths),
        registered=set(registry.by_id()),  # 這個版本沒有的工具，它的動作不放上輪盤
    )


def key_labels() -> dict[str, str]:
    return dict(qs_settings.WHEEL_KEYS)


def hint(mode: str, keys: Mapping[str, str]) -> str:
    return qs_wheel.hint_line(mode, dict(keys), key_labels())


def center_title(mode: str, file_count: int) -> str:
    return qs_wheel.center_title(mode, file_count)
