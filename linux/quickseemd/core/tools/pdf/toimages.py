"""PDF 轉圖片：每頁一張，或擷取內嵌圖片（對應原版 PTImages.swift 前半）。"""

from __future__ import annotations

import io
import logging
from pathlib import Path

import pikepdf
from pikepdf import Name
from PIL import Image

from ... import backends, errors, naming
from ...model import TINTS, PDF, ToolContext, ToolDefinition, per_file
from ...options import Choice, Info, Kind, Option, Values, note
from . import render
from .common import (
    INPUT_LABEL,
    PDF_TYPES,
    as_float,
    as_int,
    inspect_pdfs,
    make_validator,
    open_pdf,
    padded,
    page_list,
    page_range_option,
    range_caption,
    range_reason,
)

log = logging.getLogger(__name__)

FORMAT_NAMES = {"jpg": "JPG", "png": "PNG", "heic": "HEIC", "tiff": "TIFF"}
MIN_EMBEDDED_SIDE = 8
MAX_FORM_DEPTH = 8
WALK_BUDGET = 50_000  # 擷取內嵌圖片時最多走幾個 XObject

# ---------------------------------------------------------------- 每頁一張


PIL_FORMATS = {"jpg": "JPEG", "png": "PNG", "tiff": "TIFF"}


def _render_one(
    ctx: ToolContext,
    file: Path,
    page: pikepdf.Page,
    n: int,
    values: Values,
    out: Path,
    scratch: Path,
) -> Path:
    """畫出一頁（白底）並存成指定格式（out 含副檔名），寫入解析度資訊。"""
    fmt = str(values.get("format") or "jpg")
    want = as_float(values.get("dpi"), 150)
    points = render.page_points(page)
    dpi = render.effective_dpi(points, want)
    quality = as_int(values.get("quality"), 85)
    png = render.render_page(ctx, file, n, dpi, scratch / f"page-{n}", points=points)
    if fmt == "heic":
        ctx.run_process(
            [backends.require("heif-enc"), "-q", str(quality), "-o", str(out), str(png)],
            "heif-enc",
        )
        return out
    with Image.open(png) as im:
        rgb = im.convert("RGB")
    options: dict = {"dpi": (want, want)}
    if fmt == "jpg":
        options["quality"] = quality
    elif fmt == "tiff":
        options["compression"] = "tiff_lzw"  # 無損壓縮
    rgb.save(out, PIL_FORMATS[fmt], **options)
    return out


def _pages_mode(file: Path, values: Values, ctx: ToolContext) -> list[Path]:
    fmt = str(values.get("format") or "jpg")
    fmt = fmt if fmt in FORMAT_NAMES else "jpg"
    with open_pdf(file) as doc:
        total = len(doc.pages)
        pages = page_list(str(values.get("pages") or ""), total, file)
        base = naming.base_name(file)
        work = ctx.make_work_dir(file)
        single = len(pages) == 1
        folder = work if single else work / naming.sanitize(f"{base}-{FORMAT_NAMES[fmt]}")
        folder.mkdir(exist_ok=True)
        scratch = work / "render"
        scratch.mkdir()
        produced: list[Path] = []
        for i, n in enumerate(pages):
            ctx.check_cancelled()
            ctx.progress(i / len(pages), f"轉換第 {n} 頁（{i + 1}/{len(pages)}）…")
            name = f"{base}-第{n}頁" if single else f"{base}-{padded(n, total)}"
            out = folder / naming.join_name(naming.sanitize(name), fmt)
            page = doc.pages[n - 1]
            vals = {**values, "format": fmt}
            produced.append(_render_one(ctx, file, page, n, vals, out, scratch))
    if single:
        return [ctx.deliver(produced[0], produced[0].name, file)]
    ctx.progress(1, f"已轉出 {len(pages)} 張 {FORMAT_NAMES[fmt]} 圖片")
    return [ctx.deliver(folder, folder.name, file)]


# ---------------------------------------------------------------- 擷取內嵌圖片


def _collect(resources: object, out: list, seen: set, depth: int, budget: list[int]) -> None:
    """頁面（含 Form XObject）裡的圖片；跨頁去除重複，記錄走過的物件並限制數量。"""
    if depth >= MAX_FORM_DEPTH or not isinstance(resources, pikepdf.Dictionary):
        return
    xobjs = resources.get("/XObject")
    if not isinstance(xobjs, pikepdf.Dictionary):
        return
    for _key, obj in xobjs.items():
        budget[0] -= 1
        if budget[0] <= 0:
            return
        if not isinstance(obj, pikepdf.Stream) or not obj.is_indirect or obj.objgen in seen:
            continue
        seen.add(obj.objgen)
        subtype = obj.get("/Subtype")
        if subtype == Name.Image:
            out.append(obj)
        elif subtype == Name.Form:
            _collect(obj.get("/Resources"), out, seen, depth + 1, budget)


def _filters(obj: pikepdf.Stream) -> list[str]:
    f = obj.get("/Filter")
    if isinstance(f, pikepdf.Array):
        return [str(x) for x in f]
    return [str(f)] if f is not None else []


def _encoded(obj: pikepdf.Stream) -> tuple[bytes, str] | None:
    """圖片 → (檔案內容, 副檔名)。JPEG／JPEG 2000 原封不動取出，其他存成 PNG。"""
    try:
        w, h = int(obj.get("/Width", 0)), int(obj.get("/Height", 0))
    except (TypeError, ValueError):
        return None
    if w < MIN_EMBEDDED_SIDE or h < MIN_EMBEDDED_SIDE:
        return None
    filters = _filters(obj)
    if filters[-1:] in (["/DCTDecode"], ["/JPXDecode"]):
        ext = "jpg" if filters[-1] == "/DCTDecode" else "jp2"
        if len(filters) == 1:
            return obj.read_raw_bytes(), ext
        return obj.read_bytes(pikepdf.StreamDecodeLevel.generalized), ext
    return _as_png(obj)


def _soft_mask(obj: pikepdf.Stream, size: tuple[int, int]) -> Image.Image | None:
    smask = obj.get("/SMask")
    if not isinstance(smask, pikepdf.Stream):
        return None
    try:
        mask = pikepdf.PdfImage(smask).as_pil_image().convert("L")
    except Exception as exc:  # noqa: BLE001 — 透明遮罩讀不到就當成不透明
        log.info("透明遮罩無法解碼：%s", exc)
        return None
    return mask if mask.size == size else mask.resize(size, Image.BICUBIC)


def _as_png(obj: pikepdf.Stream) -> tuple[bytes, str] | None:
    try:
        image = pikepdf.PdfImage(obj).as_pil_image()
    except Exception as exc:  # noqa: BLE001 — 無法解碼的圖片略過（原版同樣略過）
        log.info("內嵌圖片無法解碼，略過：%s", exc)
        return None
    if image.mode not in ("L", "RGB", "RGBA", "LA"):
        image = image.convert("RGB")
    mask = _soft_mask(obj, image.size)
    if mask is not None:
        image = image.convert("RGBA")
        image.putalpha(mask)
    buf = io.BytesIO()
    image.save(buf, "PNG")
    return buf.getvalue(), "png"


def _embedded_mode(file: Path, values: Values, ctx: ToolContext) -> list[Path]:
    with open_pdf(file) as doc:
        total = len(doc.pages)
        pages = page_list(str(values.get("pages") or ""), total, file)
        base = naming.base_name(file)
        folder = ctx.make_work_dir(file) / naming.sanitize(f"{base}-內嵌圖片")
        folder.mkdir()
        seen: set = set()
        budget = [WALK_BUDGET]
        count = 0
        for i, n in enumerate(pages):
            ctx.check_cancelled()
            ctx.progress(i / len(pages), f"搜尋第 {n} 頁的圖片…")
            found: list[pikepdf.Stream] = []
            _collect(doc.pages[n - 1].obj.get("/Resources"), found, seen, 0, budget)
            k = 0
            for obj in found:
                encoded = _encoded(obj)
                if encoded is None:
                    continue
                k += 1
                name = naming.sanitize(f"{base}-第{padded(n, total)}頁-{k}")
                (folder / naming.join_name(name, encoded[1])).write_bytes(encoded[0])
                count += 1
    if count == 0:
        raise errors.empty_content(f"「{file.name}」的指定頁面中沒有內嵌圖片。")
    ctx.progress(1, f"已取出 {count} 張圖片")
    return [ctx.deliver(folder, folder.name, file)]


def _to_images_file(file: Path, values: Values, ctx: ToolContext) -> list[Path]:
    if values.get("mode") == "embedded":
        return _embedded_mode(file, values, ctx)
    return _pages_mode(file, values, ctx)


def run_to_images(files: list[Path], values: Values, ctx: ToolContext) -> list[Path]:
    return per_file(files, ctx, lambda f, sub: _to_images_file(f, values, sub), skip_errors=False)


def _dpi_hint(values: Values, info: Info) -> tuple[str, bool]:
    dpi = as_int(values.get("dpi"), 150)
    if dpi == 72:
        return "螢幕瀏覽、檔案最小", False
    if dpi == 300:
        return "列印品質，檔案較大", False
    return "清晰且大小適中", False


def _pages(values: Values, info: Info) -> bool:
    return values.get("mode", "pages") == "pages"


TO_IMAGES = ToolDefinition(
    id="pdf.to-images",
    category=PDF,
    title="PDF 轉圖片",
    subtitle="把每一頁轉成 JPG、PNG 等圖片，或取出 PDF 裡的原始圖片。",
    icon="photo.on.rectangle.angled",
    run=run_to_images,
    input_types=PDF_TYPES,
    input_label=INPUT_LABEL,
    action_title="轉成圖片",
    keywords=(
        "pdf to jpg",
        "pdf to png",
        "pdf to image",
        "extract images",
        "轉圖",
        "截圖",
        "圖片",
        "JPG",
        "PNG",
    ),
    tint=TINTS["image"],
    badge="JPG",
    options=(
        Option(
            "mode",
            Kind.CARDS,
            "轉換方式",
            "pages",
            choices=(
                Choice("pages", "每頁轉成一張圖片", "整頁（含文字）輸出成圖片", "doc.richtext"),
                Choice("embedded", "擷取內嵌圖片", "取出 PDF 中的原始相片與圖片", "photo.stack"),
            ),
            group="轉換方式",
            group_icon="photo.on.rectangle",
        ),
        Option(
            "format",
            Kind.CHIPS,
            "圖片格式",
            "jpg",
            choices=(
                Choice("jpg", "JPG", "檔案小，適合分享"),
                Choice("png", "PNG", "無損，文字邊緣最清楚"),
                Choice("heic", "HEIC", "比 JPG 更小"),
                Choice("tiff", "TIFF", "無損，適合印刷"),
            ),
            group="圖片格式",
            group_icon="photo",
            visible=_pages,
        ),
        Option(
            "dpi",
            Kind.SEGMENTED,
            "解析度",
            150,
            choices=(Choice(72, "72 dpi"), Choice(150, "150 dpi"), Choice(300, "300 dpi")),
            group="解析度",
            group_icon="square.resize",
            visible=_pages,
            note=_dpi_hint,
        ),
        Option(
            "quality",
            Kind.SLIDER,
            "畫質",
            85,
            minimum=40,
            maximum=100,
            step=5,
            display=lambda v: f"{int(as_float(v, 0))}%",
            group="解析度",
            group_icon="square.resize",
            visible=lambda v, i: _pages(v, i) and v.get("format", "jpg") in ("jpg", "heic"),
        ),
        note(
            "JPEG 圖片會原封不動取出（不重新壓縮），其他圖片存成 PNG。",
            visible=lambda v, i: not _pages(v, i),
        ),
        page_range_option("pages", "要轉換的頁面", note=range_caption("pages")),
    ),
    presets={"pdf.to-jpg": {"mode": "pages", "format": "jpg"}},
    inspect=inspect_pdfs,
    validate=make_validator(lambda f, v, i: range_reason(str(v.get("pages") or ""), i)),
)
