"""組合頁面寫出新 PDF（對應原版 PTAssemble／PTPDFWriter）。

合併、拆分、刪頁、整理都走這裡：頁面以 qpdf 物件層級複製，內容串流與字型原封不動，
文字層不會壞。另外照原版處理：
- 指向不在新文件中的頁面的連結會被移除；具名目的地先解析成實際頁面
- 書籤依新的頁面重建
- 全部來自同一份文件時，沿用目錄（Catalog）中適用的項目
- 文件資訊寫成 5 個欄位＋Producer＋日期
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

import pikepdf
from pikepdf import Name

from ... import errors
from .common import A4, rotation, save_pdf, verify_pages, write_info
from .outline import Destinations, OutlineNode, explicit_dest, write_outline

log = logging.getLogger(__name__)

# 輸出包含來源的全部頁面（順序相同）時可沿用的目錄項目
WHOLE_DOCUMENT_KEYS: tuple[str, ...] = (
    "StructTreeRoot",
    "MarkInfo",
    "Lang",
    "AcroForm",
    "Names",
    "OCProperties",
    "PageLabels",
    "ViewerPreferences",
    "PageLayout",
)
# 只取部分頁面時仍適用的目錄項目
PARTIAL_DOCUMENT_KEYS: tuple[str, ...] = ("Lang", "OCProperties", "ViewerPreferences")
# 原版複製頁面時略過的鍵（縮圖、文章串、應用程式私有資料）
DROPPED_PAGE_KEYS: tuple[str, ...] = ("/B", "/Thumb", "/PieceInfo")


@dataclass(frozen=True)
class Entry:
    """新文件中的一頁：來源文件的第幾頁（None＝空白頁）。"""

    doc: pikepdf.Pdf
    index: int | None
    rotation: int | None = None  # 絕對角度；None＝沿用原頁
    blank_size: tuple[float, float] | None = None


Progress = Callable[[float], None]


def prepare_links(pdf: pikepdf.Pdf) -> None:
    """把連結的具名目的地換成實際的頁面陣列（只改記憶體中的來源，不會存回原檔）。

    無法解析的具名連結標記成「指向外部」，寫出時移除（原版 linkPointsOutside）。
    """
    dests: Destinations | None = None
    for page in pdf.pages:
        annots = page.obj.get("/Annots")
        if not isinstance(annots, pikepdf.Array):
            continue
        for annot in annots:
            if isinstance(annot, pikepdf.Dictionary) and annot.get("/Subtype") == Name.Link:
                dests = dests or Destinations.of(pdf)  # 名稱樹每份文件只攤平一次
                _resolve_link(dests, annot)


def _resolve_link(dests: Destinations, annot: pikepdf.Dictionary) -> None:
    named_dest = isinstance(annot.get("/Dest"), (pikepdf.Name, pikepdf.String))
    action = annot.get("/A")
    named_action = (
        isinstance(action, pikepdf.Dictionary)
        and action.get("/S") == Name.GoTo
        and isinstance(action.get("/D"), (pikepdf.Name, pikepdf.String))
    )
    if not (named_dest or named_action):
        return
    arr = explicit_dest(dests, annot)
    if arr is None:
        annot["/QSDrop"] = True
        return
    copy = pikepdf.Array(list(arr))
    if named_dest:
        annot.Dest = copy
    else:
        action.D = copy


def _link_outside(annot: pikepdf.Dictionary, pages: set[tuple[int, int]]) -> bool:
    if annot.get("/Subtype") != Name.Link:
        return False
    if annot.get("/QSDrop") is not None:
        return True
    target = annot.get("/Dest")
    if target is None:
        action = annot.get("/A")
        if isinstance(action, pikepdf.Dictionary) and action.get("/S") == Name.GoTo:
            target = action.get("/D")
    if target is None:
        return False
    if isinstance(target, pikepdf.Dictionary):
        target = target.get("/D")
    if not isinstance(target, pikepdf.Array) or len(target) == 0:
        return target is not None and not isinstance(target, pikepdf.Array)
    first = target[0]
    if first is None:
        return True  # qpdf 把沒複製的頁面換成 null
    if isinstance(first, pikepdf.Dictionary):
        return not first.is_indirect or first.objgen not in pages
    return False


def clean_annotations(pdf: pikepdf.Pdf) -> None:
    """移除指向新文件以外頁面的連結。"""
    pages = {p.obj.objgen for p in pdf.pages}
    for page in pdf.pages:
        annots = page.obj.get("/Annots")
        if not isinstance(annots, pikepdf.Array):
            continue
        kept = [
            a for a in annots if not (isinstance(a, pikepdf.Dictionary) and _link_outside(a, pages))
        ]
        if len(kept) != len(annots):
            if kept:
                page.obj.Annots = pikepdf.Array(kept)
            else:
                del page.obj["/Annots"]


def _blank(dst: pikepdf.Pdf, size: tuple[float, float]) -> pikepdf.Page:
    w, h = max(size[0], 1.0), max(size[1], 1.0)
    page = pikepdf.Dictionary(
        Type=Name.Page,
        MediaBox=pikepdf.Array([0, 0, round(w, 4), round(h, 4)]),
        Resources=pikepdf.Dictionary(),
        Contents=dst.make_stream(b""),
    )
    return pikepdf.Page(dst.make_indirect(page))


def _add_entry(dst: pikepdf.Pdf, entry: Entry) -> None:
    if entry.index is None:
        dst.pages.append(_blank(dst, entry.blank_size or A4))
        return
    src_page = entry.doc.pages[entry.index]
    dst.pages.append(src_page)
    page = dst.pages[-1].obj
    for key in DROPPED_PAGE_KEYS:
        if key in page:
            del page[key]
    if entry.rotation is not None:
        r = entry.rotation % 360
        page.Rotate = r - r % 90
    else:
        page.Rotate = rotation(src_page)


def _single_source(entries: Sequence[Entry]) -> pikepdf.Pdf | None:
    first = entries[0].doc if entries else None
    if first is None or any(e.doc is not first for e in entries):
        return None
    return first


def _copy_catalog(dst: pikepdf.Pdf, entries: Sequence[Entry]) -> None:
    src = _single_source(entries)
    if src is None:
        return
    whole = len(entries) == len(src.pages) and all(e.index == i for i, e in enumerate(entries))
    keys = WHOLE_DOCUMENT_KEYS if whole else PARTIAL_DOCUMENT_KEYS
    for key in keys:
        value = src.Root.get("/" + key)
        if value is None:
            continue
        try:
            dst.Root["/" + key] = _foreign(dst, src, value)
        except (pikepdf.PdfError, ValueError, TypeError) as exc:
            log.warning("目錄項目 %s 無法沿用：%s", key, exc)


def _foreign(dst: pikepdf.Pdf, src: pikepdf.Pdf, value: pikepdf.Object) -> pikepdf.Object:
    """把來源目錄中的項目複製到新文件（直接物件先包成間接物件，參照才會對應到新頁面）。"""
    if isinstance(value, pikepdf.Name):
        return pikepdf.Name(str(value))
    if isinstance(value, pikepdf.String):
        return pikepdf.String(bytes(value))
    if not isinstance(value, (pikepdf.Dictionary, pikepdf.Array, pikepdf.Stream)):
        return value
    obj = value if value.is_indirect else src.make_indirect(value)
    return dst.copy_foreign(obj)


def build(
    entries: Sequence[Entry],
    outline: tuple[OutlineNode, ...],
    info: dict[str, str],
    progress: Progress | None = None,
    check: Callable[[], None] | None = None,
) -> pikepdf.Pdf:
    """組出新的 PDF（還沒寫出）。"""
    dst = pikepdf.new()
    for doc in {id(e.doc): e.doc for e in entries if e.index is not None}.values():
        prepare_links(doc)
    n = max(len(entries), 1)
    for i, entry in enumerate(entries):
        if check is not None and i % 16 == 0:
            check()
        _add_entry(dst, entry)
        if progress is not None:
            progress((i + 1) / n)
    clean_annotations(dst)
    _copy_catalog(dst, entries)
    write_outline(dst, outline)
    write_info(dst, info)
    return dst


def write(
    entries: Sequence[Entry],
    outline: tuple[OutlineNode, ...],
    info: dict[str, str],
    out: Path,
    progress: Progress | None = None,
    check: Callable[[], None] | None = None,
) -> None:
    """組合並寫出，再讀回確認頁數。"""
    if not entries:
        raise errors.QSError("至少要保留一頁。")
    dst = build(entries, outline, info, progress, check)
    try:
        save_pdf(dst, out)
    finally:
        dst.close()
    verify_pages(out, len(entries))
