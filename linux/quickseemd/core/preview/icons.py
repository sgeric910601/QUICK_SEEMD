"""檔案類型圖示（取代原版向 Finder 要的檔案圖示）。

不需要畫面連線：依 MIME 向 Gio 要圖示名稱，再直接到系統圖示主題資料夾找 PNG／SVG。
找不到時用內建的通用文件圖示（SVG）。結果依（圖示名稱, 大小）快取。
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

ICON_ROOTS: tuple[Path, ...] = (Path("/usr/share/icons"), Path("/usr/local/share/icons"))
THEMES: tuple[str, ...] = ("Yaru", "Adwaita", "hicolor")
CONTEXTS: tuple[str, ...] = ("mimetypes", "places", "devices", "apps", "legacy")
_SMALL_DIRS: tuple[str, ...] = ("32x32@2x", "48x48", "64x64", "scalable", "32x32", "256x256")
_BIG_DIRS: tuple[str, ...] = ("256x256", "scalable", "512x512", "128x128", "64x64", "48x48")
_BIG_THRESHOLD = 96
_MAX_ICON_BYTES = 2 * 1024 * 1024
FOLDER_MIME = "inode/directory"

GENERIC_SVG = (
    b'<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
    b'<path d="M14 4h26l14 14v40a2 2 0 0 1-2 2H14a2 2 0 0 1-2-2V6a2 2 0 0 1 2-2z" '
    b'fill="#f4f4f6" stroke="#9a9aa2" stroke-width="2"/>'
    b'<path d="M40 4v12a2 2 0 0 0 2 2h12" fill="#e2e2e8" stroke="#9a9aa2" stroke-width="2"/>'
    b'<path d="M20 30h24M20 38h24M20 46h16" stroke="#b8b8c0" stroke-width="3" '
    b'stroke-linecap="round"/></svg>'
)


@dataclass(frozen=True)
class Icon:
    data: bytes
    mime: str


_cache: dict[tuple[tuple[str, ...], int], Icon | None] = {}
_lock = threading.Lock()


def _gio():
    try:
        from gi.repository import Gio  # noqa: PLC0415

        return Gio
    except (ImportError, ValueError):
        return None


def icon_names(mime: str | None) -> tuple[str, ...]:
    """這個 MIME 的圖示名稱（由專用到通用，去掉單色版）。"""
    gio = _gio()
    if gio is None or not mime:
        return ()
    try:
        icon = gio.content_type_get_icon(mime)
        names = tuple(icon.get_names()) if hasattr(icon, "get_names") else ()
    except Exception:  # noqa: BLE001 — 取不到就用通用圖示
        log.debug("取得圖示名稱失敗：%s", mime, exc_info=True)
        return ()
    return tuple(n for n in names if not n.endswith("-symbolic"))


def _find_file(name: str, size: int) -> Path | None:
    dirs = _BIG_DIRS if size >= _BIG_THRESHOLD else _SMALL_DIRS
    for root in ICON_ROOTS:
        for theme in THEMES:
            base = root / theme
            if not base.is_dir():
                continue
            for size_dir in dirs:
                for ctx in CONTEXTS:
                    for suffix in (".png", ".svg"):
                        cand = base / size_dir / ctx / (name + suffix)
                        if cand.is_file():
                            return cand
    return None


def _load(names: tuple[str, ...], size: int) -> Icon | None:
    for name in names:
        path = _find_file(name, size)
        if path is None:
            continue
        try:
            if path.stat().st_size > _MAX_ICON_BYTES:
                continue
            data = path.read_bytes()
        except OSError:
            log.debug("讀不到圖示：%s", path, exc_info=True)
            continue
        return Icon(data, "image/svg+xml" if path.suffix == ".svg" else "image/png")
    return None


def for_mime(mime: str | None, size: int) -> Icon:
    """MIME 對應的主題圖示；找不到就回傳內建的通用圖示。"""
    names = icon_names(mime)
    key = (names, size)
    with _lock:
        if key in _cache:
            found = _cache[key]
            return found if found is not None else Icon(GENERIC_SVG, "image/svg+xml")
    found = _load(names, size) if names else None
    with _lock:
        _cache[key] = found
    return found if found is not None else Icon(GENERIC_SVG, "image/svg+xml")


def for_extension(ext: str, size: int) -> Icon:
    """依副檔名猜類型再取圖示（壓縮檔清單用）。"""
    gio = _gio()
    mime = None
    if gio is not None:
        try:
            guessed, _uncertain = gio.content_type_guess(f"x.{ext}" if ext else "x", None)
            mime = gio.content_type_get_mime_type(guessed) or guessed
        except Exception:  # noqa: BLE001 — 猜不到就用通用圖示
            log.debug("猜不出類型：%s", ext, exc_info=True)
    return for_mime(mime, size)
