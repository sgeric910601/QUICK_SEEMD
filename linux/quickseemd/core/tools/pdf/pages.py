"""合併、拆分、刪除頁面（對應原版 PTPages.swift）。"""

from __future__ import annotations

import math
import os
from pathlib import Path

import pikepdf

from ... import errors, naming, pagerange
from ...model import PDF, ToolContext, ToolDefinition, per_file
from ...options import Choice, Info, Kind, Option, Values, note
from . import assemble, render
from .common import (
    work_dir,
    INPUT_LABEL,
    PDF_TYPES,
    as_bool,
    as_int,
    inspect_pdfs,
    make_validator,
    open_pdf,
    padded,
    page_count_of,
    page_list,
    read_info,
    visual_size,
)
from .outline import file_entry, read_outline

# ================================================================ 合併


def _merge_note(values: Values, info: Info) -> tuple[str, bool] | None:
    total = int(info.get("total_pages") or 0)
    if total <= 0:
        return None
    return f"合併後共 {total} 頁。在左側拖曳檔案卡片即可調整順序。", False


def _merge_name(files: list[Path], name: str, ctx: ToolContext) -> str:
    trimmed = str(name or "").strip()
    if not trimmed:
        return ctx.output_name(files[0], "已合併", "pdf")
    out = naming.sanitize(trimmed)
    return out if out.lower().endswith(".pdf") else out + ".pdf"


def _merge_one(
    file: Path, doc: pikepdf.Pdf, entries: list[assemble.Entry], bookmarks: bool
) -> tuple:
    """把一個檔案的頁面加進 entries，回傳它的書籤節點。"""
    start = len(entries)
    entries.extend(assemble.Entry(doc, i) for i in range(len(doc.pages)))
    nested = read_outline(doc, lambda i: start + i)
    if bookmarks:
        return (file_entry(naming.base_name(file), start, doc.pages[0], nested),)
    return nested


def run_merge(files: list[Path], values: Values, ctx: ToolContext) -> list[Path]:
    if len(files) < 2:
        raise errors.QSError("合併需要至少 2 個 PDF 檔案。")
    bookmarks = as_bool(values.get("bookmarks"), True)
    duplex = as_bool(values.get("duplex"), False)
    entries: list[assemble.Entry] = []
    nodes: tuple = ()
    docs: list[pikepdf.Pdf] = []
    try:
        for fi, file in enumerate(files):
            ctx.check_cancelled()
            ctx.progress(fi / len(files) * 0.3, f"讀取「{file.name}」（{fi + 1}/{len(files)}）…")
            doc = open_pdf(file)
            docs.append(doc)
            nodes += _merge_one(file, doc, entries, bookmarks)
            # 雙面列印：頁數為奇數時補一張空白頁，讓下一份從正面開始
            if duplex and fi < len(files) - 1 and len(doc.pages) % 2 == 1:
                size = visual_size(doc.pages[-1])
                entries.append(assemble.Entry(doc, None, blank_size=size))
        out_name = _merge_name(files, str(values.get("name") or ""), ctx)
        ctx.progress(0.3, f"寫入合併後的 PDF（共 {len(entries)} 頁）…")
        tmp = work_dir(ctx, files[0]) / "merged.pdf"
        assemble.write(
            entries,
            nodes,
            {"Title": out_name[:-4]},
            tmp,
            lambda f: ctx.progress(0.3 + 0.65 * f),
            ctx.check_cancelled,
        )
    finally:
        for d in docs:
            d.close()
    return [ctx.deliver(tmp, out_name, files[0])]


MERGE = ToolDefinition(
    id="pdf.merge",
    category=PDF,
    title="合併 PDF",
    subtitle="依照你排好的順序，把多個 PDF 合併成一份。",
    icon="rectangle.stack.badge.plus",
    run=run_merge,
    input_types=PDF_TYPES,
    input_label=INPUT_LABEL,
    minimum_files=2,
    reorderable=True,
    action_title="合併 PDF",
    keywords=("merge", "combine", "join", "append", "合併", "結合", "串接"),
    badge="PDF",
    options=(
        note("", key="_merge_note", note=_merge_note, group_icon="doc.on.doc"),
        Option(
            "bookmarks",
            Kind.TOGGLE,
            "每個檔案加一個書籤",
            True,
            caption="以檔名當書籤，方便在閱讀器中跳到各份文件；原有書籤會放在底下。",
            group="書籤與版面",
            group_icon="bookmark",
        ),
        Option(
            "duplex",
            Kind.TOGGLE,
            "雙面列印對齊",
            False,
            caption="頁數為奇數的檔案後面補一張空白頁，讓每份都從正面開始。",
            group="書籤與版面",
            group_icon="bookmark",
        ),
        Option(
            "name",
            Kind.TEXT,
            "檔名（可留白）",
            "",
            placeholder="檔名-已合併",
            group="輸出檔名",
            group_icon="pencil.line",
        ),
    ),
    presets={"pdf.merge-pdfs": {"bookmarks": True, "duplex": False, "name": ""}},
    inspect=inspect_pdfs,
    validate=make_validator(),
)

# ================================================================ 拆分

SPLIT_MODES = ("ranges", "every_n", "every_page", "extract")


def split_label(group: list[int], total: int) -> str:
    """檔名用的頁碼說明：「第03頁」「第01-05頁」「第02頁等3頁」。"""
    if not group:
        return "空白"
    first, last = group[0], group[-1]
    if len(group) == 1:
        return f"第{padded(first, total)}頁"
    if group == list(range(first, max(first, last) + 1)):
        return f"第{padded(first, total)}-{padded(last, total)}頁"
    return f"第{padded(first, total)}頁等{len(group)}頁"


def _auto_ranges(n: int) -> str:
    half = (n + 1) // 2
    if half == 1:
        return "1, 2"
    tail = "" if half + 1 == n else f"-{n}"
    return f"1-{half}, {half + 1}{tail}"


def _split_prefill(files: list[Path], info: Info) -> Values:
    n = page_count_of(info)
    return {"ranges": _auto_ranges(n)} if n is not None and n >= 2 else {}


def _split_validate(files: list[Path], values: Values, info: Info) -> str | None:
    mode = values.get("mode")
    n = page_count_of(info)
    if mode == "ranges":
        text = str(values.get("ranges") or "")
        if not text.strip():
            return "請輸入要拆分的範圍，例如 1-3, 4-6"
        if n is not None and pagerange.parse_groups(text, n) is None:
            return f"範圍不正確（共 {n} 頁）"
    elif mode == "extract":
        text = str(values.get("extract") or "")
        if not text.strip():
            return "請輸入要擷取的頁面"
        if n is not None and pagerange.parse(text, n) is None:
            return f"頁碼範圍不正確（共 {n} 頁）"
    elif mode == "every_n" and as_int(values.get("chunk"), 2) < 1:
        return "每個檔案至少 1 頁"
    return None


def _split_preview(values: Values, info: Info) -> tuple[str, bool] | None:
    n = page_count_of(info)
    if n is None:
        return None
    mode = values.get("mode")
    if mode == "ranges":
        groups = pagerange.parse_groups(str(values.get("ranges") or ""), n)
        return (f"會產生 {len(groups)} 個 PDF", False) if groups else None
    if mode == "every_n":
        size = max(1, as_int(values.get("chunk"), 2))
        return f"共 {n} 頁，會產生 {math.ceil(n / size)} 個 PDF", False
    if mode == "every_page":
        return f"共 {n} 頁，會產生 {n} 個 PDF", False
    text = str(values.get("extract") or "")
    pages = pagerange.parse(text, n)
    if pages is None or not text:
        return None
    if as_bool(values.get("separate"), False):
        return f"會產生 {len(pages)} 個 PDF", False
    return f"擷取 {len(pages)} 頁成一個 PDF", False


def _split_groups(file: Path, values: Values, n: int) -> tuple[list[list[int]], bool]:
    """（頁碼分組, 是否為「合併擷取」）。"""
    mode = values.get("mode")
    if mode == "ranges":
        text = str(values.get("ranges") or "")
        groups = pagerange.parse_groups(text, n)
        if groups is None:
            raise errors.QSError(f"拆分範圍「{text}」不正確：「{file.name}」共 {n} 頁。")
        return groups, False
    if mode == "every_n":
        size = max(1, as_int(values.get("chunk"), 2))
        return [list(range(s, min(s + size - 1, n) + 1)) for s in range(1, n + 1, size)], False
    if mode == "extract":
        pages = page_list(str(values.get("extract") or ""), n, file)
        if as_bool(values.get("separate"), False):
            return [[p] for p in pages], False
        return [pages], True
    return [[p] for p in range(1, n + 1)], False


def _write_group(
    doc: pikepdf.Pdf, group: list[int], label: str, base: str, work: Path, info: dict
) -> Path:
    index_map: dict[int, int] = {}
    for i, p in enumerate(group):
        index_map.setdefault(p - 1, i)
    entries = [assemble.Entry(doc, p - 1) for p in group]
    nodes = read_outline(doc, index_map.get)
    out = work / naming.join_name(naming.sanitize(f"{base}-{label}"), "pdf")
    assemble.write(entries, nodes, info, out)
    return out


def _deliver_parts(
    parts: list[Path], file: Path, base: str, work: Path, folder: bool, ctx: ToolContext
) -> list[Path]:
    if len(parts) == 1:
        return [ctx.deliver(parts[0], parts[0].name, file)]
    if folder:
        target = work / naming.sanitize(f"{base}-已拆分")
        target.mkdir()
        for p in parts:
            os.rename(p, target / p.name)
        ctx.progress(1, f"已拆成 {len(parts)} 個檔案")
        return [ctx.deliver(target, target.name, file)]
    outs = [ctx.deliver(p, p.name, file) for p in parts]
    ctx.progress(1, f"已拆成 {len(parts)} 個檔案")
    return outs


def _split_file(file: Path, values: Values, ctx: ToolContext) -> list[Path]:
    with open_pdf(file) as doc:
        n = len(doc.pages)
        groups, single = _split_groups(file, values, n)
        if len(groups) == 1 and len(groups[0]) == n and not single:
            raise errors.QSError(f"「{file.name}」只有 {n} 頁，拆分後還是同一份檔案。")
        base = naming.base_name(file)
        work = work_dir(ctx, file) / "parts"
        work.mkdir()
        info = read_info(doc)
        parts: list[Path] = []
        for gi, group in enumerate(groups):
            ctx.check_cancelled()
            ctx.progress(gi / len(groups) * 0.9, f"產生第 {gi + 1} / {len(groups)} 個檔案…")
            label = "已擷取" if single else split_label(group, n)
            parts.append(_write_group(doc, group, label, base, work, info))
    return _deliver_parts(parts, file, base, work, as_bool(values.get("folder"), True), ctx)


def run_split(files: list[Path], values: Values, ctx: ToolContext) -> list[Path]:
    return per_file(files, ctx, lambda f, sub: _split_file(f, values, sub), skip_errors=False)


def _is_mode(*modes: str):
    return lambda v, i: v.get("mode") in modes


SPLIT = ToolDefinition(
    id="pdf.split",
    category=PDF,
    title="拆分 PDF",
    subtitle="依範圍、固定頁數或每一頁拆成多個 PDF，也能只擷取需要的頁面。",
    icon="scissors",
    run=run_split,
    input_types=PDF_TYPES,
    input_label=INPUT_LABEL,
    action_title="拆分 PDF",
    keywords=("split", "extract", "separate", "burst", "拆分", "分割", "擷取", "抽頁"),
    badge="PDF",
    options=(
        Option(
            "mode",
            Kind.CARDS,
            "拆分方式",
            "ranges",
            choices=(
                Choice(
                    "ranges",
                    "依範圍拆分",
                    "每個範圍存成一個檔案",
                    "text.line.first.and.arrowtriangle.forward",
                ),
                Choice("every_n", "每 N 頁一個檔案", "固定頁數平均切開", "square.split.2x1"),
                Choice("every_page", "每一頁一個檔案", "整份拆成單頁 PDF", "square.grid.3x3"),
                Choice("extract", "擷取指定頁面", "只取出需要的頁面", "doc.on.clipboard"),
            ),
            group="拆分方式",
            group_icon="scissors",
        ),
        Option(
            "ranges",
            Kind.TEXT,
            "範圍",
            "",
            caption="以逗號分隔，每段各存成一個 PDF",
            placeholder="例如 1-3, 4-6, 7-",
            group="設定",
            group_icon="slider.horizontal.3",
            visible=_is_mode("ranges"),
        ),
        Option(
            "chunk",
            Kind.NUMBER,
            "每個檔案的頁數",
            2,
            minimum=1,
            maximum=9999,
            dynamic_range=lambda v, i: (1, max(1, page_count_of(i) or 9999)),
            group="設定",
            group_icon="slider.horizontal.3",
            visible=_is_mode("every_n"),
        ),
        Option(
            "extract",
            Kind.PAGE_RANGE,
            "要擷取的頁面",
            "",
            placeholder=pagerange.PLACEHOLDER,
            group="設定",
            group_icon="slider.horizontal.3",
            visible=_is_mode("extract"),
            note=lambda v, i: pagerange.caption(str(v.get("extract") or ""), page_count_of(i)),
        ),
        Option(
            "separate",
            Kind.TOGGLE,
            "每頁各存成一個檔案",
            False,
            caption="關閉時合併成一個 PDF",
            group="設定",
            group_icon="slider.horizontal.3",
            visible=_is_mode("extract"),
        ),
        Option(
            "folder",
            Kind.TOGGLE,
            "放進新資料夾",
            True,
            caption="以原檔名建立資料夾收納拆分結果",
            group="設定",
            group_icon="slider.horizontal.3",
            visible=lambda v, i: v.get("mode") != "extract" or as_bool(v.get("separate"), False),
        ),
        note("", key="_split_preview", note=_split_preview, group="設定"),
    ),
    presets={"pdf.split-pages": {"mode": "every_page", "folder": True}},
    inspect=inspect_pdfs,
    prefill=_split_prefill,
    validate=make_validator(_split_validate),
)

# ================================================================ 刪除頁面


def _delete_validate(files: list[Path], values: Values, info: Info) -> str | None:
    if values.get("mode", "custom") != "custom":
        return None
    text = str(values.get("pages") or "")
    if not text.strip():
        return "請輸入要刪除的頁碼"
    n = page_count_of(info)
    if n is not None:
        pages = pagerange.parse(text, n)
        if pages is None:
            return f"頁碼範圍不正確（共 {n} 頁）"
        if len(set(pages)) >= n:
            return "不能刪除全部的頁面"
    return None


def _delete_caption(values: Values, info: Info) -> tuple[str, bool] | None:
    n = page_count_of(info)
    if n is None:
        return None
    text = str(values.get("pages") or "")
    if text and pagerange.parse(text, n) is None:
        return f"範圍不正確（共 {n} 頁）", True
    return f"共 {n} 頁", False


def _blank_pages(file: Path, doc: pikepdf.Pdf, sub: ToolContext) -> set[int]:
    n = len(doc.pages)
    texts = render.all_page_texts(sub, file, n)
    work = work_dir(sub, file)
    remove: set[int] = set()
    for i, page in enumerate(doc.pages):
        sub.check_cancelled()
        sub.progress(i / n * 0.8, f"檢查第 {i + 1} / {n} 頁是否空白…")
        if render.is_blank_page(sub, file, page, i + 1, texts[i], work):
            remove.add(i + 1)
    return remove


def _pages_to_remove(file: Path, doc: pikepdf.Pdf, mode: str, text: str, sub: ToolContext) -> set:
    n = len(doc.pages)
    if mode == "even":
        return set(range(2, n + 1, 2))
    if mode == "odd":
        return set(range(1, n + 1, 2))
    if mode == "blank":
        return _blank_pages(file, doc, sub)
    if not text.strip():
        raise errors.QSError("請輸入要刪除的頁碼。")
    return set(page_list(text, n, file))


def _delete_file(file: Path, values: Values, sub: ToolContext, multiple: bool) -> list[Path]:
    sub.progress(0, f"分析「{file.name}」…")
    with open_pdf(file) as doc:
        n = len(doc.pages)
        remove = _pages_to_remove(
            file, doc, str(values.get("mode") or "custom"), str(values.get("pages") or ""), sub
        )
        if not remove:
            if not multiple:
                raise errors.QSError(f"「{file.name}」沒有需要刪除的頁面（沒有偵測到空白頁）。")
            message = f"「{file.name}」沒有需要刪除的頁面，略過"
            sub.note_skip(message)
            sub.progress(1, message)
            return []
        if len(remove) >= n:
            raise errors.QSError(f"「{file.name}」共 {n} 頁，不能把全部頁面都刪除。")
        kept = [i for i in range(n) if i + 1 not in remove]
        index_map = {p: i for i, p in enumerate(kept)}
        tmp = work_dir(sub, file) / "deleted.pdf"
        assemble.write(
            [assemble.Entry(doc, i) for i in kept],
            read_outline(doc, index_map.get),
            read_info(doc),
            tmp,
            lambda f: sub.progress(0.8 + 0.18 * f),
            sub.check_cancelled,
        )
    out = sub.deliver(tmp, sub.output_name(file, "已刪頁", "pdf"), file)
    sub.progress(1, f"已刪除 {len(remove)} 頁，剩下 {n - len(remove)} 頁")
    return [out]


def run_delete(files: list[Path], values: Values, ctx: ToolContext) -> list[Path]:
    outputs: list[Path] = []
    for fi, file in enumerate(files):
        ctx.check_cancelled()
        sub = ctx.segment(fi, len(files))
        outputs.extend(_delete_file(file, values, sub, len(files) > 1))
    return outputs


DELETE_PAGES = ToolDefinition(
    id="pdf.delete-pages",
    category=PDF,
    title="刪除頁面",
    subtitle="移除不需要的頁面，例如指定頁碼、奇數或偶數頁、空白頁。",
    icon="doc.badge.ellipsis",
    run=run_delete,
    input_types=PDF_TYPES,
    input_label=INPUT_LABEL,
    action_title="刪除頁面",
    keywords=("delete", "remove pages", "blank pages", "刪頁", "移除頁面", "空白頁"),
    badge="PDF",
    options=(
        Option(
            "mode",
            Kind.CARDS,
            "要刪除哪些頁面",
            "custom",
            choices=(
                Choice("custom", "指定頁碼", "輸入要移除的頁碼或範圍", "number"),
                Choice("blank", "所有空白頁", "自動找出沒有內容的頁面", "doc"),
                Choice("even", "偶數頁", "移除第 2、4、6… 頁", "2.square"),
                Choice("odd", "奇數頁", "移除第 1、3、5… 頁", "1.square"),
            ),
            group="要刪除哪些頁面",
            group_icon="trash",
        ),
        Option(
            "pages",
            Kind.TEXT,
            "要刪除的頁面",
            "",
            placeholder="例如 2, 5-7",
            group="頁碼",
            group_icon="doc.on.doc",
            visible=lambda v, i: v.get("mode", "custom") == "custom",
            note=_delete_caption,
        ),
        note("原始檔案不會被修改，結果會另存成「檔名-已刪頁.pdf」。"),
    ),
    inspect=inspect_pdfs,
    validate=make_validator(_delete_validate),
)
