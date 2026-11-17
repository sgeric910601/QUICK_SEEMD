"""圖片與 PDF 的載入（GdkPixbuf、Poppler；不碰 GTK，可以在背景執行緒呼叫）。

載入失敗一律丟 QSError（中文訊息），呼叫端只要顯示錯誤頁。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import gi

gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Poppler", "0.18")
from gi.repository import GdkPixbuf, Gio, GLib, Poppler  # noqa: E402

from ..core import errors  # noqa: E402

MAX_STATIC_PIXELS = 60_000_000  # 超過就先縮小再載入，避免吃光記憶體
SCALED_EDGE = 6000


@dataclass(frozen=True)
class LoadedImage:
    pixbuf: GdkPixbuf.Pixbuf | None  # 靜態圖（已套用 EXIF 方向）
    animation: GdkPixbuf.PixbufAnimation | None  # 動圖
    width: int  # 顯示用的寬高（已轉正）
    height: int
    source_size: tuple[int, int]  # 原始檔案的寬高


def _oriented(pixbuf: GdkPixbuf.Pixbuf) -> GdkPixbuf.Pixbuf:
    return pixbuf.apply_embedded_orientation() or pixbuf


def _load_large(path: Path, size: tuple[int, int]) -> LoadedImage:
    pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(path), SCALED_EDGE, SCALED_EDGE, True)
    pb = _oriented(pb)
    return LoadedImage(pb, None, pb.get_width(), pb.get_height(), size)


def load_image(path: Path) -> LoadedImage:
    try:
        _fmt, width, height = GdkPixbuf.Pixbuf.get_file_info(str(path))
        if _fmt is not None and width * height > MAX_STATIC_PIXELS:
            return _load_large(path, (width, height))
        anim = GdkPixbuf.PixbufAnimation.new_from_file(str(path))
    except GLib.Error as exc:
        raise errors.unreadable(exc.message) from exc
    if anim.is_static_image():
        pb = _oriented(anim.get_static_image())
        size = (pb.get_width(), pb.get_height())
        return LoadedImage(pb, None, size[0], size[1], size)
    size = (anim.get_width(), anim.get_height())
    return LoadedImage(None, anim, size[0], size[1], size)


def load_pdf(path: Path) -> Poppler.Document:
    uri = Gio.File.new_for_path(str(path)).get_uri()
    try:
        doc = Poppler.Document.new_from_file(uri, None)
    except GLib.Error as exc:
        if exc.matches(Poppler.Error.quark(), Poppler.Error.ENCRYPTED):
            raise errors.QSError("這份 PDF 受密碼保護，無法預覽。") from exc
        raise errors.unreadable(exc.message) from exc
    if doc.get_n_pages() < 1:
        raise errors.QSError("這份 PDF 沒有任何頁面。")
    return doc
