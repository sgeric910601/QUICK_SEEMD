"""壓縮檔清單 → 樹狀檢視（對應原版 QSArchiveView）。

資料夾在前、自然排序（不分大小寫、數字照數值）；項目不超過 400 個時前三層展開，否則只展開第一層。
每種副檔名一個圖示（最多 80 種，之後用通用圖示）；🔒 加密、↪︎ 符號連結。
Linux 版補充：樹的節點最多 MAX_NODES 個、深度最多 MAX_DEPTH 層（更深的部分併成一個名稱），
建樹、加總與輸出都不用遞迴，惡意的超深路徑不會拖垮預覽。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable

from . import Attachment, fmt, icons
from .archive import MAX_ENTRIES, Entry, Listing
from .page import Chip, FileInfo, Notice, Page, escape

OPEN_ALL_LIMIT = 400
MAX_ICONS = 80
MAX_NODES = 100_000
MAX_DEPTH = 64
ICON_SIZE = 36
PARTIAL_NOTICE = "壓縮檔太大，只列出前面的部分"
_DIGITS = re.compile(r"(\d+)")
HEADER = (
    '<div class="arc"><div class="hdr"><span>名稱</span><span class="num">原始大小</span>'
    '<span class="num">壓縮後</span><span>修改日期</span></div>'
)


class _Node:
    __slots__ = ("name", "is_dir", "entry", "children", "size", "compressed", "has_compressed")

    def __init__(self, name: str, is_dir: bool) -> None:
        self.name = name
        self.is_dir = is_dir
        self.entry: Entry | None = None
        self.children: dict[str, _Node] = {}
        self.size = 0
        self.compressed = 0
        self.has_compressed = False


class _Tree:
    def __init__(self) -> None:
        self.root = _Node("", True)
        self.nodes = 0
        self.folders = 0
        self.files = 0
        self.total_size = 0
        self.total_compressed = 0
        self.encrypted = False
        self.capped = False

    def add(self, e: Entry) -> None:
        parts = [p for p in e.path.split("/") if p]
        if not parts:
            return
        if len(parts) > MAX_DEPTH:
            parts = parts[: MAX_DEPTH - 1] + ["/".join(parts[MAX_DEPTH - 1 :])]
        node = self.root
        for k, part in enumerate(parts):
            last = k == len(parts) - 1
            child = node.children.get(part)
            if child is None:
                if self.nodes >= MAX_NODES:
                    self.capped = True
                    return
                child = _Node(part, (not last) or e.is_dir)
                self.nodes += 1
                self.folders += child.is_dir
                node.children[part] = child
            node = child
            if last:
                node.entry = e
                self.files += not e.is_dir
        if not e.is_dir:
            self.total_size += e.size or 0
            self.total_compressed += e.compressed_size or 0
        self.encrypted = self.encrypted or e.encrypted

    def aggregate(self) -> None:
        """資料夾大小＝底下所有檔案的總和（後序走訪，不用遞迴）。"""
        order: list[_Node] = []
        stack = [self.root]
        while stack:
            n = stack.pop()
            order.append(n)
            if n.is_dir:
                stack.extend(n.children.values())
        for n in reversed(order):
            if not n.is_dir:
                n.size = (n.entry.size if n.entry else 0) or 0
                n.compressed = (n.entry.compressed_size if n.entry else 0) or 0
                n.has_compressed = n.entry is not None and n.entry.compressed_size is not None
                continue
            for c in n.children.values():
                n.size += c.size
                n.compressed += c.compressed
                n.has_compressed = n.has_compressed or c.has_compressed


def natural_key(name: str) -> tuple:
    """自然排序：不分大小寫，數字依數值（不轉成整數，超長的數字也安全）。"""
    parts = _DIGITS.split(name.casefold())
    key = []
    for i, p in enumerate(parts):
        if not p:
            continue
        if i % 2:
            digits = p.lstrip("0") or "0"
            key.append((0, len(digits), digits))
        else:
            key.append((1, 0, p))
    return tuple(key)


def _sorted(n: _Node) -> list[_Node]:
    return sorted(n.children.values(), key=lambda c: (not c.is_dir, natural_key(c.name), c.name))


class _Icons:
    """每種副檔名一個附件 ico-N；超過 80 種後用 ico-fallback。"""

    def __init__(self) -> None:
        self.keys: dict[str, str] = {}
        self.attachments: dict[str, Attachment] = {}

    def key(self, n: _Node) -> str:
        ext = "/folder" if n.is_dir else _ext(n.name)
        found = self.keys.get(ext)
        if found is not None:
            return found
        if len(self.keys) < MAX_ICONS:
            name = f"ico-{len(self.keys)}"
            icon = (
                icons.for_mime(icons.FOLDER_MIME, ICON_SIZE)
                if n.is_dir
                else icons.for_extension(ext, ICON_SIZE)
            )
            self.attachments[name] = Attachment(name, icon.data, icon.mime)
            self.keys[ext] = name
            return name
        if "/fallback" not in self.keys:
            icon = icons.for_mime(None, ICON_SIZE)
            self.attachments["ico-fallback"] = Attachment("ico-fallback", icon.data, icon.mime)
            self.keys["/fallback"] = "ico-fallback"
        return "ico-fallback"


def _ext(name: str) -> str:
    dot = name.rfind(".")
    return name[dot + 1 :].lower() if dot > 0 else ""


def _cells(n: _Node, depth: int, icon_key: str) -> str:
    s = [
        f'<span class="nm" style="padding-left:{12 + depth * 18}px"><span class="tw"></span>',
        f'<img src="cid:{icon_key}" alt="">{escape(n.name)}',
    ]
    if n.entry is not None and n.entry.encrypted:
        s.append(' <span class="enc">🔒</span>')
    if n.entry is not None and n.entry.is_link:
        s.append(' <span class="enc">↪︎</span>')
    s.append("</span>")
    s.append(
        f'<span class="num">{"—" if n.is_dir and n.size == 0 else fmt.bytes_label(n.size)}</span>'
    )
    s.append(
        f'<span class="num">{fmt.bytes_label(n.compressed) if n.has_compressed else "—"}</span>'
    )
    s.append(f'<span class="dt">{fmt.date(n.entry.modified if n.entry else None)}</span>')
    return "".join(s)


def _emit(root: _Node, open_all: bool, icon: Callable[[_Node], str]) -> list[str]:
    out: list[str] = [HEADER]
    stack: list[tuple[Iterable[_Node], int]] = [(iter(_sorted(root)), 0)]
    while stack:
        it, depth = stack[-1]
        child = next(it, None)  # type: ignore[call-overload]
        if child is None:
            stack.pop()
            if stack:
                out.append("</details>")
            continue
        if child.is_dir:
            is_open = depth < 3 if open_all else depth == 0
            out.append(
                f"<details{' open' if is_open else ''}><summary class=\"row\">"
                f"{_cells(child, depth, icon(child))}</summary>"
            )
            stack.append((iter(_sorted(child)), depth + 1))
        else:
            out.append(f'<div class="row">{_cells(child, depth, icon(child))}</div>')
    out.append("</div>")
    return out


def _chips(tree: _Tree, listing: Listing, extra: Iterable[Chip]) -> list[Chip]:
    chips = [
        Chip(f"{fmt.number(tree.folders)} 個資料夾、{fmt.number(tree.files)} 個檔案"),
        Chip(f"原始 {fmt.bytes_label(tree.total_size)}"),
    ]
    if tree.total_compressed > 0 and tree.total_size > 0:
        ratio = int((1 - tree.total_compressed / tree.total_size) * 100)
        chips.append(Chip(f"壓縮率 {max(0, ratio)}%"))
    chips.extend(extra)
    if tree.encrypted:
        chips.append(Chip("含加密檔案", "warn"))
    return chips


def render(listing: Listing, info: FileInfo, extra_chips: Iterable[Chip] = ()) -> Page:
    """壓縮檔頁面（info 只用來決定頁首，內容全來自 listing）。"""
    tree = _Tree()
    for e in listing.entries:
        tree.add(e)
        if tree.capped:
            break
    tree.aggregate()
    page = Page(kind=f"{listing.format} 壓縮檔", size=(860, 620))
    page.chips = _chips(tree, listing, extra_chips)
    if listing.truncated:
        page.notices.append(Notice(f"項目太多，只列出前 {fmt.number(MAX_ENTRIES)} 個", warn=True))
    if listing.partial:
        page.notices.append(Notice(PARTIAL_NOTICE, warn=True))
    if tree.capped:
        page.notices.append(Notice("路徑太多或太深，只列出部分項目", warn=True))
    if listing.hidden_mac_metadata > 0:
        page.chips.append(Chip(f"已隱藏 {listing.hidden_mac_metadata} 個 macOS 中繼資料"))
    if listing.comment:
        page.notices.append(Notice(f"註解：{listing.comment}"))
    if not listing.entries:
        page.body = '<div class="empty">壓縮檔是空的</div>'
        return page
    icon_map = _Icons()
    page.body = "".join(_emit(tree.root, len(listing.entries) <= OPEN_ALL_LIMIT, icon_map.key))
    page.attachments = icon_map.attachments
    return page
