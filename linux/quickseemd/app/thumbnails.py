"""檔案縮圖（對應原版 ToolThumbnails）：背景執行緒產生，結果以 GdkPixbuf 交回主執行緒。

來源順序：系統縮圖快取（thumbnail::path）→ 圖片直接縮小 → PDF 第一頁（Poppler）
→ 影片畫面（ffmpeg）→ 沒有（介面改顯示檔案類型圖示）。快取鍵 path|mtime|寬x高。
"""

from __future__ import annotations

import io
import logging
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import gi

gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, Gio, GLib  # noqa: E402

from ..core import backends, filetypes, naming, process  # noqa: E402
from .mainloop import to_main  # noqa: E402

LOG = logging.getLogger(__name__)
CACHE_LIMIT = 400
FRAME_TIMEOUT = 20.0

Callback = Callable[["GdkPixbuf.Pixbuf | None"], None]


def _from_file(path: Path, w: int, h: int) -> GdkPixbuf.Pixbuf | None:
    try:
        pb = GdkPixbuf.Pixbuf.new_from_file_at_scale(str(path), w, h, True)
    except GLib.Error:
        return None
    return pb.apply_embedded_orientation() or pb


def _from_bytes(data: bytes, w: int, h: int) -> GdkPixbuf.Pixbuf | None:
    loader = GdkPixbuf.PixbufLoader()
    try:
        loader.write(data)
        loader.close()
    except GLib.Error:
        return None
    pb = loader.get_pixbuf()
    if pb is None:
        return None
    scale = min(w / max(pb.get_width(), 1), h / max(pb.get_height(), 1), 1.0)
    if scale >= 1.0:
        return pb
    nw, nh = max(int(pb.get_width() * scale), 1), max(int(pb.get_height() * scale), 1)
    return pb.scale_simple(nw, nh, GdkPixbuf.InterpType.BILINEAR)


def _system_thumbnail(path: Path, w: int, h: int) -> GdkPixbuf.Pixbuf | None:
    try:
        info = Gio.File.new_for_path(str(path)).query_info(
            "thumbnail::path", Gio.FileQueryInfoFlags.NONE, None
        )
    except GLib.Error:
        return None
    thumb = info.get_attribute_byte_string("thumbnail::path")
    return _from_file(Path(thumb), w, h) if thumb and Path(thumb).is_file() else None


def render_pdf_page(path: Path, index: int, max_side: int) -> bytes | None:
    """PDF 某一頁畫成 PNG（白底）；整理 PDF 的縮圖也用它。"""
    import cairo  # noqa: PLC0415

    gi.require_version("Poppler", "0.18")
    from gi.repository import Poppler  # noqa: PLC0415

    try:
        doc = Poppler.Document.new_from_file(path.as_uri(), None)
        page = doc.get_page(index)
    except GLib.Error:
        return None
    if page is None:
        return None
    pw, ph = page.get_size()
    scale = max_side / max(pw, ph, 1.0)
    surf = cairo.ImageSurface(cairo.FORMAT_RGB24, max(int(pw * scale), 1), max(int(ph * scale), 1))
    cr = cairo.Context(surf)
    cr.set_source_rgb(1, 1, 1)
    cr.paint()
    cr.scale(scale, scale)
    page.render(cr)
    buf = io.BytesIO()
    surf.write_to_png(buf)
    return buf.getvalue()


def pdf_page_count(path: Path) -> int:
    gi.require_version("Poppler", "0.18")
    from gi.repository import Poppler  # noqa: PLC0415

    try:
        return int(Poppler.Document.new_from_file(path.as_uri(), None).get_n_pages())
    except GLib.Error:
        return 0


def video_frame(path: Path, seconds: float, height: int) -> bytes | None:
    """用 ffmpeg 取一張畫面（PNG）；暫存檔放在自己的快取資料夾，用完就清掉。"""
    ffmpeg = backends.find("ffmpeg")
    if ffmpeg is None:
        return None
    cache = backends.cache_dir()
    cache.mkdir(parents=True, exist_ok=True)
    work = naming.make_work_dir(cache)
    try:
        out = work / "frame.png"
        args = [ffmpeg, "-hide_banner", "-loglevel", "error", "-nostdin", "-n"]
        args += ["-ss", f"{max(seconds, 0):.3f}", "-i", str(path.resolve()), "-frames:v", "1"]
        args += ["-vf", f"scale=-2:{int(height)}", str(out)]
        res = process.run(args, timeout=FRAME_TIMEOUT)
        if res.returncode != 0 or not out.is_file():
            return None
        return out.read_bytes()
    except (OSError, ValueError) as exc:
        LOG.info("取不到影片畫面：%s", exc)
        return None
    finally:
        naming.remove_work_dir(work)


def _make(path: Path, w: int, h: int) -> GdkPixbuf.Pixbuf | None:
    hit = _system_thumbnail(path, w, h)
    if hit is not None:
        return hit
    mime = filetypes.mime_of(path)
    if mime.startswith("image/"):
        return _from_file(path, w, h)
    if mime == "application/pdf":
        data = render_pdf_page(path, 0, max(w, h))
        return _from_bytes(data, w, h) if data else None
    if mime.startswith("video/"):
        data = video_frame(path, 1.0, h) or video_frame(path, 0.0, h)
        return _from_bytes(data, w, h) if data else None
    return None


class ThumbnailLoader:
    """有快取的縮圖產生器；callback 一律在主執行緒呼叫。"""

    def __init__(self, workers: int = 2) -> None:
        self._pool = ThreadPoolExecutor(max_workers=workers, thread_name_prefix="qs-thumb")
        self._cache: dict[str, GdkPixbuf.Pixbuf | None] = {}
        self._lock = threading.Lock()

    @staticmethod
    def key(path: Path, w: int, h: int) -> str:
        try:
            mtime = path.stat().st_mtime
        except OSError:
            mtime = 0
        return f"{path}|{mtime}|{w}x{h}"

    def request(self, path: Path, w: int, h: int, callback: Callback) -> None:
        key = self.key(Path(path), w, h)
        with self._lock:
            if key in self._cache:
                hit = self._cache[key]
                to_main(callback, hit)
                return
        self._pool.submit(self._work, Path(path), w, h, key, callback)

    def _work(self, path: Path, w: int, h: int, key: str, callback: Callback) -> None:
        try:
            pb = _make(path, w, h)
        except Exception:  # noqa: BLE001 — 縮圖失敗只顯示圖示
            LOG.exception("產生縮圖失敗：%s", path)
            pb = None
        with self._lock:
            if len(self._cache) > CACHE_LIMIT:
                self._cache.clear()
            self._cache[key] = pb
        to_main(callback, pb)


_LOADER: ThumbnailLoader | None = None


def loader() -> ThumbnailLoader:
    global _LOADER  # noqa: PLW0603 — 整個程式共用一個
    if _LOADER is None:
        _LOADER = ThumbnailLoader()
    return _LOADER
