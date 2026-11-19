"""書籤（大綱）的讀取與重建（對應原版 PTOutline 與 PTPDFWriter.emitOutline）。

讀取時把來源的書籤轉成與文件無關的節點（頁面用索引），寫出時再依新文件的頁面建立。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import pikepdf
from pikepdf import Name

from .common import crop_box

log = logging.getLogger(__name__)

MAX_DEPTH = 16  # 書籤最多幾層（原版上限）
OUTLINE_BUDGET = 100_000  # 一份文件最多讀幾個書籤
NAME_TREE_BUDGET = 10_000  # 名稱樹最多走幾個節點
MAX_TREE_DEPTH = 32

PageMap = Callable[[int], "int | None"]


@dataclass(frozen=True)
class OutlineNode:
    """書籤節點：page 是新文件的頁面索引（None＝沒有目的地），point 是頁面座標。"""

    title: str
    page: int | None = None
    point: tuple[float, float] | None = None
    open: bool = False
    children: tuple[OutlineNode, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------- 目的地解析


def _dest_array(value: Any) -> pikepdf.Array | None:
    if isinstance(value, pikepdf.Array):
        return value
    if isinstance(value, pikepdf.Dictionary):
        d = value.get("/D")
        return d if isinstance(d, pikepdf.Array) else None
    return None


def _flatten_name_tree(tree: Any) -> dict[bytes, Any]:
    """把名稱樹攤平成字典（只走一次）。

    檔案內容不可信：記錄走過的物件（/Kids 互相參照或繞圈時不會重複走），
    並限制節點總數與深度，避免惡意檔案讓查詢爆量。
    """
    out: dict[bytes, Any] = {}
    seen: set[tuple[int, int]] = set()
    stack: list[tuple[Any, int]] = [(tree, 0)]
    budget = NAME_TREE_BUDGET
    while stack and budget > 0:
        node, depth = stack.pop()
        if not isinstance(node, pikepdf.Dictionary) or depth > MAX_TREE_DEPTH:
            continue
        if node.is_indirect:
            if node.objgen in seen:
                continue
            seen.add(node.objgen)
        budget -= 1
        names = node.get("/Names")
        if isinstance(names, pikepdf.Array):
            for i in range(0, min(len(names), 2 * NAME_TREE_BUDGET) - 1, 2):
                k = names[i]
                if isinstance(k, pikepdf.String) and bytes(k) not in out:
                    out[bytes(k)] = names[i + 1]
        kids = node.get("/Kids")
        if isinstance(kids, pikepdf.Array):
            stack.extend((kid, depth + 1) for kid in list(kids)[:NAME_TREE_BUDGET])
    return out


@dataclass(frozen=True)
class Destinations:
    """一份文件的具名目的地（名稱樹攤平一次，之後每個連結、書籤直接查表）。"""

    names: dict[bytes, Any]
    legacy: Any  # 舊式的 /Root /Dests 字典

    @classmethod
    def of(cls, pdf: pikepdf.Pdf) -> Destinations:
        root = pdf.Root
        names = root.get("/Names")
        tree = names.get("/Dests") if isinstance(names, pikepdf.Dictionary) else None
        legacy = root.get("/Dests")
        return cls(
            _flatten_name_tree(tree), legacy if isinstance(legacy, pikepdf.Dictionary) else None
        )

    def resolve(self, target: Any) -> pikepdf.Array | None:
        """具名目的地（名稱或字串）→ 實際的目的地陣列；找不到回傳 None。"""
        if isinstance(target, pikepdf.Name):
            if self.legacy is not None and str(target) in self.legacy:
                found = _dest_array(self.legacy[str(target)])
                if found is not None:
                    return found
            key = str(target)[1:].encode("utf-8")
        elif isinstance(target, pikepdf.String):
            key = bytes(target)
        else:
            return None
        return _dest_array(self.names.get(key))


def explicit_dest(dests: Destinations, item: pikepdf.Dictionary) -> pikepdf.Array | None:
    """書籤或連結的目的地陣列（/Dest 或 GoTo 動作的 /D；具名的會解析）。"""
    target = item.get("/Dest")
    if target is None:
        action = item.get("/A")
        if isinstance(action, pikepdf.Dictionary) and action.get("/S") == Name.GoTo:
            target = action.get("/D")
    if target is None:
        return None
    if isinstance(target, (pikepdf.Name, pikepdf.String)):
        return dests.resolve(target)
    return _dest_array(target)


def _number(value: Any) -> float | None:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def dest_point(arr: pikepdf.Array, page: pikepdf.Dictionary) -> tuple[float, float]:
    """目的地的點（未指定的座標用頁面左上角，與原版相同）。"""
    x0, _y0, _x1, y1 = crop_box(page)
    kind = str(arr[1]) if len(arr) > 1 else "/Fit"
    x = y = None
    if kind == "/XYZ":
        x = _number(arr[2]) if len(arr) > 2 else None
        y = _number(arr[3]) if len(arr) > 3 else None
    elif kind in ("/FitH", "/FitBH"):
        y = _number(arr[2]) if len(arr) > 2 else None
    elif kind in ("/FitV", "/FitBV"):
        x = _number(arr[2]) if len(arr) > 2 else None
    elif kind == "/FitR" and len(arr) > 5:
        x, y = _number(arr[2]), _number(arr[5])
    return (x0 if x is None else x, y1 if y is None else y)


def page_index_map(pdf: pikepdf.Pdf) -> dict[tuple[int, int], int]:
    """頁面物件編號 → 頁面索引。"""
    return {p.obj.objgen: i for i, p in enumerate(pdf.pages) if p.obj.is_indirect}


# ---------------------------------------------------------------- 讀取


@dataclass
class _Walk:
    """走訪書籤的狀態：走過的物件與剩下的節點預算（防止繞圈與爆量）。"""

    index: dict[tuple[int, int], int]
    mapping: PageMap
    dests: Destinations
    seen: set[tuple[int, int]] = field(default_factory=set)
    budget: int = OUTLINE_BUDGET


def read_outline(pdf: pikepdf.Pdf, mapping: PageMap) -> tuple[OutlineNode, ...]:
    """來源的書籤；mapping：來源頁索引 → 新頁索引（None＝不在新文件中，該書籤略過）。"""
    try:
        root = pdf.Root.get("/Outlines")
        if not isinstance(root, pikepdf.Dictionary):
            return ()
        walk = _Walk(page_index_map(pdf), mapping, Destinations.of(pdf))
        return _children(walk, root, 0)
    except (pikepdf.PdfError, ValueError, TypeError, KeyError) as exc:
        log.warning("書籤讀取失敗，略過：%s", exc)
        return ()


def _children(walk: _Walk, parent: pikepdf.Dictionary, depth: int) -> tuple[OutlineNode, ...]:
    if depth >= MAX_DEPTH:
        return ()
    out: list[OutlineNode] = []
    item = parent.get("/First")
    while isinstance(item, pikepdf.Dictionary) and walk.budget > 0:
        if item.is_indirect:
            if item.objgen in walk.seen:
                break
            walk.seen.add(item.objgen)
        walk.budget -= 1
        node = _node(walk, item, depth)
        if node is not None:
            out.append(node)
        item = item.get("/Next")
    return tuple(out)


def _node(walk: _Walk, item: pikepdf.Dictionary, depth: int) -> OutlineNode | None:
    kids = _children(walk, item, depth + 1)
    title = str(item.get("/Title", ""))
    count = _number(item.get("/Count", 0)) or 0
    page = point = None
    arr = explicit_dest(walk.dests, item)
    if arr is not None and len(arr) > 0 and isinstance(arr[0], pikepdf.Dictionary):
        src = walk.index.get(arr[0].objgen) if arr[0].is_indirect else None
        if src is not None:
            page = walk.mapping(src)
            if page is not None:
                point = dest_point(arr, arr[0])
    if page is None and not kids:
        return None
    return OutlineNode(title, page, point, count > 0, kids)


# ---------------------------------------------------------------- 寫出


def file_entry(
    title: str, page: int, source_page: pikepdf.Page, children: tuple[OutlineNode, ...] = ()
) -> OutlineNode:
    """指向某頁頂端的書籤（合併時每個檔案一個）。"""
    x0, _y0, _x1, y1 = crop_box(source_page)
    return OutlineNode(title, page, (x0, y1), False, tuple(children))


def write_outline(pdf: pikepdf.Pdf, nodes: tuple[OutlineNode, ...]) -> None:
    """把節點寫成新文件的書籤（取代原有的）。"""
    if "/Outlines" in pdf.Root:
        del pdf.Root["/Outlines"]
    if not nodes:
        return
    pages = [p.obj for p in pdf.pages]
    root = pdf.make_indirect(pikepdf.Dictionary(Type=Name.Outlines))
    first, last, visible = _emit_level(pdf, nodes, root, pages)
    root.First, root.Last, root.Count = first, last, visible
    pdf.Root.Outlines = root
    pdf.Root.PageMode = Name.UseOutlines


def _emit_level(
    pdf: pikepdf.Pdf,
    nodes: tuple[OutlineNode, ...],
    parent: pikepdf.Dictionary,
    pages: list[pikepdf.Dictionary],
) -> tuple[pikepdf.Dictionary, pikepdf.Dictionary, int]:
    items = [pdf.make_indirect(pikepdf.Dictionary()) for _ in nodes]
    visible = 0
    for i, (node, item) in enumerate(zip(nodes, items, strict=True)):
        item.Title = pikepdf.String(node.title)
        item.Parent = parent
        if i > 0:
            item.Prev = items[i - 1]
        if i < len(items) - 1:
            item.Next = items[i + 1]
        if node.page is not None and 0 <= node.page < len(pages):
            target = pages[node.page]
            if node.point is not None:
                item.Dest = pikepdf.Array([target, Name.XYZ, node.point[0], node.point[1], None])
            else:
                item.Dest = pikepdf.Array([target, Name.Fit])
        if node.children:
            first, last, kid_visible = _emit_level(pdf, node.children, item, pages)
            item.First, item.Last = first, last
            item.Count = kid_visible if node.open else -len(node.children)
            if node.open:
                visible += kid_visible
        visible += 1
    return items[0], items[-1], visible
