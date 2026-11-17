"""整理 PDF（對應原版 PTOrganize.swift）與旋轉 PDF（PDFToolCatalog.swift）。

整理的頁面安排由主程式的縮圖格線（custom_view="pdf-organize"）產生，存在隱藏的 pages 選項：
JSON 陣列，每一項 {"src": 原檔頁索引（0 起算，null＝空白頁）, "rotate": 額外旋轉 0/90/180/270}；
空字串＝維持原本順序。
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import pikepdf

from ... import errors
from ...model import PDF, ToolContext, ToolDefinition, per_file
from ...options import Choice, Info, Kind, Option, Values, note
from . import assemble
from .common import (
    INPUT_LABEL,
    PDF_TYPES,
    as_int,
    inspect_pdfs,
    make_validator,
    open_pdf,
    page_list,
    page_range_option,
    range_caption,
    range_reason,
    read_info,
    rotation,
    save_pdf,
    visual_size,
)
from .outline import read_outline

BAD_PLAN = "頁面安排的資料格式不正確。"


@dataclass(frozen=True)
class PlanItem:
    """整理後的一頁：原檔第幾頁（None＝空白頁）與額外旋轉角度。"""

    src: int | None
    rotate: int = 0


def parse_plan(text: str) -> list[PlanItem] | None:
    """解析頁面安排；空字串回傳 None（＝不變）。格式不對時丟出錯誤。"""
    if not str(text or "").strip():
        return None
    try:
        raw = json.loads(text)
    except (TypeError, ValueError) as exc:
        raise errors.QSError(BAD_PLAN) from exc
    if not isinstance(raw, list):
        raise errors.QSError(BAD_PLAN)
    items: list[PlanItem] = []
    for entry in raw:
        if not isinstance(entry, dict):
            raise errors.QSError(BAD_PLAN)
        src = entry.get("src")
        if src is not None and (isinstance(src, bool) or not isinstance(src, int)):
            raise errors.QSError(BAD_PLAN)
        rot = as_int(entry.get("rotate", 0), 0)
        items.append(PlanItem(src, ((rot % 360) + 360) % 360))
    return items


def _organize_validate(files: list[Path], values: Values, info: Info) -> str | None:
    try:
        plan = parse_plan(str(values.get("pages") or ""))
    except errors.QSError as exc:
        return str(exc)
    if files and plan is not None and not plan:
        return "至少要保留一頁"
    return None


def _entries(file: Path, doc: pikepdf.Pdf, plan: list[PlanItem]) -> tuple[list, dict[int, int]]:
    entries: list[assemble.Entry] = []
    index_map: dict[int, int] = {}
    reference = visual_size(doc.pages[0])
    n = len(doc.pages)
    for i, item in enumerate(plan):
        if item.src is None:
            entries.append(assemble.Entry(doc, None, blank_size=reference))
            continue
        if not 0 <= item.src < n:
            raise errors.QSError(
                f"「{file.name}」已經變更（找不到第 {item.src + 1} 頁），請重新選擇檔案。"
            )
        page = doc.pages[item.src]
        entries.append(assemble.Entry(doc, item.src, rotation=(rotation(page) + item.rotate) % 360))
        reference = visual_size(page)
        index_map.setdefault(item.src, i)
    return entries, index_map


def run_organize(files: list[Path], values: Values, ctx: ToolContext) -> list[Path]:
    if not files:
        raise errors.QSError("請選擇一個 PDF 檔案。")
    file = files[0]
    plan = parse_plan(str(values.get("pages") or ""))
    with open_pdf(file) as doc:
        items = plan if plan is not None else [PlanItem(i) for i in range(len(doc.pages))]
        if not items:
            raise errors.QSError("至少要保留一頁。")
        entries, index_map = _entries(file, doc, items)
        ctx.progress(0.2, f"寫出 PDF（共 {len(entries)} 頁）…")
        tmp = ctx.make_work_dir(file) / "organized.pdf"
        assemble.write(
            entries,
            read_outline(doc, index_map.get),
            read_info(doc),
            tmp,
            lambda f: ctx.progress(0.2 + 0.75 * f),
            ctx.check_cancelled,
        )
    return [ctx.deliver(tmp, ctx.output_name(file, "已整理", "pdf"), file)]


ORGANIZE = ToolDefinition(
    id="pdf.organize",
    category=PDF,
    title="整理 PDF",
    subtitle="拖曳頁面縮圖調整順序，也可以旋轉、刪除頁面或插入空白頁。",
    icon="square.grid.3x3.square",
    run=run_organize,
    input_types=PDF_TYPES,
    input_label=INPUT_LABEL,
    allows_multiple=False,
    action_title="儲存整理後的 PDF",
    keywords=(
        "organize",
        "reorder",
        "sort pages",
        "move pages",
        "insert blank",
        "整理",
        "排序",
        "頁面順序",
        "重新排列",
    ),
    badge="PDF",
    options=(
        Option("pages", Kind.TEXT, "頁面安排", "", visible=lambda v, i: False),
        note(
            "拖曳縮圖可以調整順序；把滑鼠移到頁面上可旋轉、刪除或在後面插入空白頁。"
            "按住 Ctrl 或 Shift 點選可一次選取多頁。"
        ),
    ),
    inspect=inspect_pdfs,
    validate=make_validator(_organize_validate),
    custom_view="pdf-organize",
)

# ================================================================ 旋轉


def _rotate_file(file: Path, values: Values, ctx: ToolContext) -> list[Path]:
    angle = as_int(values.get("angle"), 90)
    with open_pdf(file) as doc:
        pages = page_list(str(values.get("pages") or ""), len(doc.pages), file)
        for i, n in enumerate(pages):
            ctx.check_cancelled()
            page = doc.pages[n - 1]
            page.obj.Rotate = (rotation(page) + angle) % 360
            ctx.progress((i + 1) / len(pages) * 0.8)
        tmp = ctx.make_work_dir(file) / "rotated.pdf"
        save_pdf(doc, tmp)
    return [ctx.deliver(tmp, ctx.output_name(file, "已旋轉", "pdf"), file)]


def run_rotate(files: list[Path], values: Values, ctx: ToolContext) -> list[Path]:
    return per_file(files, ctx, lambda f, sub: _rotate_file(f, values, sub), skip_errors=False)


ROTATE = ToolDefinition(
    id="pdf.rotate",
    category=PDF,
    title="旋轉 PDF",
    subtitle="把整份或指定的頁面轉 90° 或 180°，可一次處理多個檔案。",
    icon="rotate.right",
    run=run_rotate,
    input_types=PDF_TYPES,
    input_label=INPUT_LABEL,
    action_title="旋轉 PDF",
    keywords=("rotate", "turn", "轉向", "方向", "橫向", "直向"),
    badge="PDF",
    options=(
        Option(
            "angle",
            Kind.CARDS,
            "旋轉方向",
            90,
            choices=(
                Choice(90, "向右轉 90°", "順時針", "rotate.right"),
                Choice(270, "向左轉 90°", "逆時針", "rotate.left"),
                Choice(180, "轉 180°", "上下顛倒", "arrow.up.arrow.down"),
            ),
            group="旋轉方向",
            group_icon="arrow.clockwise",
        ),
        page_range_option("pages", "要旋轉的頁面", note=range_caption("pages")),
    ),
    presets={"pdf.rotate-right": {"angle": 90, "pages": ""}},
    inspect=inspect_pdfs,
    validate=make_validator(lambda f, v, i: range_reason(str(v.get("pages") or ""), i)),
)
