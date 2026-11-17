"""圖片與 PDF 的載入（GdkPixbuf、Poppler；不碰 GTK，可以在背景執行緒呼叫）。

載入失敗一律丟 QSError（中文訊息），呼叫端只要顯示錯誤頁。
圖片先只讀檔頭拿尺寸；超過 MAX_PIXELS 時絕不解碼全尺寸：
能邊解碼邊縮小的格式（JPEG、SVG）縮成 SCALED_EDGE，其他格式顯示「圖片太大」。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import gi

gi.require_version("GdkPixbuf", "2.0")
gi.require_version("Poppler", "0.18")
from gi.repository import GdkPixbuf, Gio, GLib, Poppler  # noqa: E402

from ..core import errors  # noqa: E402

MAX_PIXELS = 64_000_000  # 超過這個像素數就不解碼全尺寸
SCALED_EDGE = 4096  # 超大圖片縮小後的最長邊
# 解碼時就能直接縮小的格式（JPEG 用 DCT 縮放、SVG 是向量）；其他格式縮小前一定要先解出全尺寸
NATIVE_SCALE_FORMATS = frozenset({"jpeg", "svg"})


@dataclass(frozen=True)
class LoadedImage:
    pixbuf: GdkPixbuf.Pixbuf | None  # 靜態圖（已套用 EXIF 方向）
    animation: GdkPixbuf.PixbufAnimation | None  # 動圖
    width: int  # 顯示用的寬高（已轉正）
    height: int
    source_size: tuple[int, int]  # 原始檔案的寬高


def _oriented(pixbuf: GdkPixbuf.Pixbuf) -> GdkPixbuf.Pixbuf:
    return pixbuf.apply_embedded_orientation() or pixbuf


def too_big(width: int, height: int) -> errors.QSError:
    return errors.QSError(
        f"圖片太大（{width} × {height} 像素），為了不佔用太多記憶體，不顯示內容。"
    )


def _probe(path: Path) -> tuple[str, int, int]:
    """只讀檔頭：（格式名稱, 寬, 高）；不認得的格式丟 QSError。"""
    fmt, width, height = GdkPixbuf.Pixbuf.get_file_info(str(path))
    if fmt is None:
        raise errors.unreadable("不支援的圖片格式")
    return fmt.get_name(), width, height


def _load_scaled(path: Path, size: tuple[int, int]) -> LoadedImage:
    """邊解碼邊縮小（只用在 NATIVE_SCALE_FORMATS），不會配置全尺寸的影像。"""
    pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(path), SCALED_EDGE, SCALED_EDGE, True)
    pb = _oriented(pb)
    return LoadedImage(pb, None, pb.get_width(), pb.get_height(), size)


def _decode(path: Path) -> LoadedImage:
    anim = GdkPixbuf.PixbufAnimation.new_from_file(str(path))
    if anim.is_static_image():
        pb = _oriented(anim.get_static_image())
        size = (pb.get_width(), pb.get_height())
        return LoadedImage(pb, None, size[0], size[1], size)
    size = (anim.get_width(), anim.get_height())
    return LoadedImage(None, anim, size[0], size[1], size)


def load_image(path: Path) -> LoadedImage:
    try:
        name, width, height = _probe(path)
        if width * height <= MAX_PIXELS:
            return _decode(path)
        if name not in NATIVE_SCALE_FORMATS:
            raise too_big(width, height)
        return _load_scaled(path, (width, height))
    except GLib.Error as exc:
        raise errors.unreadable(exc.message) from exc


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
