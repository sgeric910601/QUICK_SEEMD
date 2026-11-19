"""整理 PDF 的頁面清單（對應原版 PTOrganizeSession），不需要畫面。

和 PDF 工具的合約：選項 `pages` 的值是 JSON 字串，內容是清單
`[{"src": 原檔頁索引（0 起算）或 null（空白頁）, "rotate": 0/90/180/270}, …]`；
rotate 是在原本頁面旋轉之外「再」轉的角度。還沒讀到 PDF 時值是空字串。
"""

from __future__ import annotations

import json
from collections.abc import Iterable
from dataclasses import dataclass, replace

DEFAULT_PAGE_SIZE = (595.0, 842.0)


@dataclass(frozen=True)
class OrgPage:
    uid: int
    src: int | None
    rotate: int = 0


class OrganizeModel:
    """頁面順序、旋轉、空白頁與選取。所有修改都換成新的 tuple（不就地改）。"""

    def __init__(self) -> None:
        self.pages: tuple[OrgPage, ...] = ()
        self.selection: frozenset[int] = frozenset()
        self.original_count = 0
        self.loaded = False
        self._next = 1

    def _new(self, src: int | None, rotate: int = 0) -> OrgPage:
        page = OrgPage(self._next, src, rotate)
        self._next += 1
        return page

    def load(self, count: int) -> None:
        self.original_count = max(int(count), 0)
        self.loaded = True
        self.reset()

    def unload(self) -> None:
        self.pages, self.selection, self.original_count, self.loaded = (), frozenset(), 0, False

    def reset(self) -> None:
        self.pages = tuple(self._new(i) for i in range(self.original_count))
        self.selection = frozenset()

    # ------------------------------------------------------------ 編輯

    def rotate(self, uids: Iterable[int], degrees: int) -> None:
        ids = set(uids)
        self.pages = tuple(
            replace(p, rotate=((p.rotate + degrees) % 360 + 360) % 360) if p.uid in ids else p
            for p in self.pages
        )

    def delete(self, uids: Iterable[int]) -> None:
        ids = set(uids)
        self.pages = tuple(p for p in self.pages if p.uid not in ids)
        self.selection = self.selection - ids

    def insert_blank(self, after: int | None) -> None:
        index = len(self.pages)
        if after is not None:
            for i, p in enumerate(self.pages):
                if p.uid == after:
                    index = i + 1
                    break
        pages = list(self.pages)
        pages.insert(index, self._new(None))
        self.pages = tuple(pages)

    def move(self, uid: int, target: int) -> None:
        order = [p.uid for p in self.pages]
        if uid == target or uid not in order or target not in order:
            return
        src, dst = order.index(uid), order.index(target)
        pages = list(self.pages)
        item = pages.pop(src)
        pages.insert(dst, item)
        self.pages = tuple(pages)

    def reverse(self) -> None:
        self.pages = tuple(reversed(self.pages))

    # ------------------------------------------------------------ 選取

    def tap(self, uid: int, ctrl: bool = False, shift: bool = False) -> None:
        order = [p.uid for p in self.pages]
        if ctrl:
            self.selection = self.selection ^ {uid}
            return
        anchor = next((i for i, u in enumerate(order) if u in self.selection), None)
        if shift and anchor is not None and uid in order:
            target = order.index(uid)
            lo, hi = min(anchor, target), max(anchor, target)
            self.selection = frozenset(order[lo : hi + 1])
            return
        self.selection = frozenset() if self.selection == {uid} else frozenset({uid})

    def clear_selection(self) -> None:
        self.selection = frozenset()

    # ------------------------------------------------------------ 狀態

    @property
    def is_modified(self) -> bool:
        if len(self.pages) != self.original_count:
            return True
        return any(p.src != i or p.rotate % 360 != 0 for i, p in enumerate(self.pages))

    def to_value(self) -> str:
        if not self.loaded:
            return ""
        return json.dumps(
            [{"src": p.src, "rotate": p.rotate} for p in self.pages], separators=(",", ":")
        )

    def validate(self) -> str | None:
        if self.loaded and not self.pages:
            return "至少要保留一頁"
        return None


# ---------------------------------------------------------------- 文字與尺寸


def cell_label(page: OrgPage, number: int) -> tuple[str, str | None]:
    """縮圖下方：頁碼，以及「原第 N 頁」（移動過）或「空白頁」。"""
    if page.src is None:
        return str(number), "空白頁"
    if page.src + 1 != number:
        return str(number), f"原第 {page.src + 1} 頁"
    return str(number), None


def cell_tooltip(page: OrgPage) -> str:
    return "空白頁" if page.src is None else f"原檔第 {page.src + 1} 頁"


def empty_text(original_count: int) -> str:
    return "所有頁面都已刪除" if original_count > 0 else "無法讀取這份 PDF 的頁面"


def selection_title(count: int) -> str:
    return f"已選取 {count} 頁"


def fit_size(
    natural: tuple[float, float], rotate: int, box: tuple[float, float]
) -> tuple[float, float]:
    """旋轉後的頁面縮放進方框的大小（寬, 高）。"""
    w, h = natural
    if rotate % 180 != 0:
        w, h = h, w
    scale = min(box[0] / max(w, 1.0), box[1] / max(h, 1.0))
    return w * scale, h * scale
