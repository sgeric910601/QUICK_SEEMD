"""PDF 的顯示（Poppler，GTK3）：所有頁面直排在可捲動的區域，寬度跟著視窗。

每一頁是「高度依寬度決定」的元件，只有捲到畫面裡才會畫；
畫好的頁面圖暫存在小小的 LRU 快取裡，捲動時不用每次重畫。
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable

import cairo
import gi

gi.require_version("Gtk", "3.0")
gi.require_version("Poppler", "0.18")
from gi.repository import Gtk, Poppler  # noqa: E402

PT_TO_PX = 96 / 72
PAGE_SPACING = 12
PAGE_MARGIN = 16
MIN_PAGE_WIDTH = 120
MAX_PAGES = 2000
CACHE_PAGES = 12


def pdf_window_size(page_size_pt: tuple[float, float]) -> tuple[int, int]:
    """PDF 視窗想要的大小：第一頁 96 dpi 的寬度加邊界，高度盡量高（之後再限制在螢幕內）。"""
    width = round(page_size_pt[0] * PT_TO_PX) + 2 * PAGE_MARGIN + 16
    height = round(page_size_pt[1] * PT_TO_PX) + 2 * PAGE_MARGIN
    return width, height


def page_height_for_width(page_size_pt: tuple[float, float], width: int) -> int:
    pw, ph = page_size_pt
    if pw <= 0:
        return max(1, width)
    return max(1, round(width * ph / pw))


class PageCache:
    """（頁碼, 寬, 高）→ 畫好的頁面圖。"""

    def __init__(self, limit: int = CACHE_PAGES) -> None:
        self._limit = limit
        self._items: OrderedDict[tuple[int, int, int], cairo.ImageSurface] = OrderedDict()

    def get(
        self, key: tuple[int, int, int], render: Callable[[], cairo.ImageSurface]
    ) -> cairo.ImageSurface:
        if key in self._items:
            self._items.move_to_end(key)
            return self._items[key]
        surface = render()
        self._items[key] = surface
        while len(self._items) > self._limit:
            self._items.popitem(last=False)
        return surface

    def __len__(self) -> int:
        return len(self._items)


class PdfPage(Gtk.DrawingArea):
    def __init__(self, page: Poppler.Page, cache: PageCache) -> None:
        super().__init__()
        self._page = page
        self._index = page.get_index()
        self._size = page.get_size()
        self._cache = cache
        self.set_hexpand(True)

    def do_get_request_mode(self) -> Gtk.SizeRequestMode:
        return Gtk.SizeRequestMode.HEIGHT_FOR_WIDTH

    def do_get_preferred_width(self) -> tuple[int, int]:
        return MIN_PAGE_WIDTH, max(MIN_PAGE_WIDTH, round(self._size[0] * PT_TO_PX))

    def do_get_preferred_height_for_width(self, width: int) -> tuple[int, int]:
        height = page_height_for_width(self._size, width)
        return height, height

    def do_get_preferred_height(self) -> tuple[int, int]:
        height = round(self._size[1] * PT_TO_PX)
        return height, height

    def _render(self, width: int, height: int) -> cairo.ImageSurface:
        surface = cairo.ImageSurface(cairo.FORMAT_RGB24, width, height)
        cr = cairo.Context(surface)
        cr.set_source_rgb(1, 1, 1)
        cr.paint()
        cr.scale(width / max(self._size[0], 1), height / max(self._size[1], 1))
        self._page.render(cr)
        surface.flush()
        return surface

    def do_draw(self, cr) -> bool:  # noqa: ANN001 — cairo.Context
        width = self.get_allocated_width()
        height = self.get_allocated_height()
        if width <= 1 or height <= 1:
            return False
        key = (self._index, width, height)
        surface = self._cache.get(key, lambda: self._render(width, height))
        cr.set_source_surface(surface, 0, 0)
        cr.paint()
        cr.set_source_rgba(0, 0, 0, 0.25)
        cr.set_line_width(1)
        cr.rectangle(0.5, 0.5, width - 1, height - 1)
        cr.stroke()
        return False


class PdfView(Gtk.ScrolledWindow):
    def __init__(self, document: Poppler.Document) -> None:
        super().__init__()
        self.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._document = document  # 保留參照，頁面物件需要文件活著
        self._cache = PageCache()
        box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=PAGE_SPACING)
        box.set_margin_start(PAGE_MARGIN)
        box.set_margin_end(PAGE_MARGIN)
        box.set_margin_top(PAGE_MARGIN)
        box.set_margin_bottom(PAGE_MARGIN)
        total = document.get_n_pages()
        for index in range(min(total, MAX_PAGES)):
            box.pack_start(PdfPage(document.get_page(index), self._cache), False, False, 0)
        if total > MAX_PAGES:
            box.pack_start(Gtk.Label(label=f"只顯示前 {MAX_PAGES} 頁"), False, False, 0)
        self.add(box)
