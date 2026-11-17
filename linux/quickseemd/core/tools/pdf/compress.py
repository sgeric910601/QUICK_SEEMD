"""壓縮 PDF（對應原版 PTCompress.swift 與 PTPDFWriter 的圖片重新壓縮）。

只重新壓縮、縮小圖片；字型、文字與向量內容原封不動。步驟：
1. 掃描每頁內容（q／Q／cm／Do，含 Form XObject），記錄每張圖片在頁面上最大的顯示尺寸
2. 依程度把圖片縮到「顯示尺寸 × 目標 dpi」並重新存成 JPEG；透明遮罩保持無損
3. 新資料要小於原本的一定比例才替換
4. 整份檔案沒有變小時，輸出原檔的複本
備援：上述方式遇到特殊 PDF 失敗時改用 Ghostscript。
"""

from __future__ import annotations

import io
import logging
import math
import shutil
import zlib
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import pikepdf
from pikepdf import Name
from PIL import Image

from ... import backends, errors, process
from ...model import PDF, ToolContext, ToolDefinition, per_file
from ...options import Choice, Info, Kind, Option, Values, note
from ...result import format_size
from .common import (
    work_dir,
    INPUT_LABEL,
    PDF_TYPES,
    inspect_pdfs,
    make_validator,
    open_pdf,
    read_info,
    verify_pages,
    write_info,
)
from .overlay import Matrix, concat

log = logging.getLogger(__name__)

IDENTITY: Matrix = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
MAX_FORM_DEPTH = 12
SCAN_BUDGET = 2_000_000  # 掃描時最多處理幾個指令（Form 重複引用、互相引用的惡意檔案不會爆量）
MIN_IMAGE_SIDE = 16
MAX_DECODE_PIXELS = 100_000_000  # 超過就不解碼（避免吃光記憶體），保留原圖


@dataclass(frozen=True)
class Level:
    """壓縮程度：JPEG 品質、目標 dpi、最長邊像素上限、替換門檻。"""

    quality: float
    dpi: float
    max_pixels: int
    min_saving: float


LEVELS: dict[str, Level] = {
    "extreme": Level(0.40, 96, 1600, 0.95),
    "recommended": Level(0.65, 150, 2400, 0.95),
    "low": Level(0.82, 220, 4000, 0.90),
}

ObjKey = tuple[int, int]


# ---------------------------------------------------------------- 顯示尺寸掃描


@dataclass
class _Scan:
    sizes: dict[ObjKey, tuple[float, float]] = field(default_factory=dict)
    masks: set[ObjKey] = field(default_factory=set)
    parsed: dict[ObjKey, list] = field(default_factory=dict)
    budget: int = SCAN_BUDGET


def _record(state: _Scan, obj: pikepdf.Object, size: tuple[float, float]) -> None:
    if not obj.is_indirect:
        return
    old = state.sizes.get(obj.objgen, (0.0, 0.0))
    state.sizes[obj.objgen] = (max(old[0], size[0]), max(old[1], size[1]))


def _matrix(value: object) -> Matrix | None:
    try:
        nums = [float(v) for v in value]  # type: ignore[union-attr]
    except (TypeError, ValueError):
        return None
    return tuple(nums) if len(nums) == 6 else None  # type: ignore[return-value]


def _instructions(state: _Scan, source: pikepdf.Object | pikepdf.Page) -> list:
    """解析內容串流（只取 q Q cm Do）；Form 依物件編號快取，只解析一次。"""
    key = source.obj.objgen if isinstance(source, pikepdf.Page) else source.objgen
    if key in state.parsed and key != (0, 0):
        return state.parsed[key]
    try:
        ops = pikepdf.parse_content_stream(source, "q Q cm Do")
    except (pikepdf.PdfError, ValueError, TypeError) as exc:
        log.info("內容串流無法解析，略過：%s", exc)
        ops = []
    state.parsed[key] = ops
    return ops


def _do(
    state: _Scan, resources: object, name: object, ctm: Matrix, path: tuple, depth: int
) -> None:
    xobjs = resources.get("/XObject") if isinstance(resources, pikepdf.Dictionary) else None
    obj = xobjs.get(str(name)) if isinstance(xobjs, pikepdf.Dictionary) else None
    if not isinstance(obj, pikepdf.Stream):
        return
    subtype = obj.get("/Subtype")
    if subtype == Name.Image:
        size = (math.hypot(ctm[0], ctm[1]), math.hypot(ctm[2], ctm[3]))
        _record(state, obj, size)
        smask = obj.get("/SMask")
        if isinstance(smask, pikepdf.Stream) and smask.is_indirect:
            state.masks.add(smask.objgen)
            _record(state, smask, size)
    elif subtype == Name.Form and depth < MAX_FORM_DEPTH and obj.is_indirect:
        if obj.objgen in path:  # 自己畫自己（繞圈）：不再往下
            return
        m = _matrix(obj.get("/Matrix")) or IDENTITY
        res = obj.get("/Resources")
        _scan_stream(
            state,
            obj,
            res if isinstance(res, pikepdf.Dictionary) else resources,
            concat(m, ctm),
            path + (obj.objgen,),
            depth + 1,
        )


def _scan_stream(
    state: _Scan, source: object, resources: object, ctm: Matrix, path: tuple, depth: int
) -> None:
    stack: list[Matrix] = [ctm]
    ops = _instructions(state, source)
    state.budget -= len(ops)
    if state.budget <= 0:
        return
    for operands, operator in ops:
        op = str(operator)
        if op == "q":
            stack.append(stack[-1])
        elif op == "Q":
            if len(stack) > 1:
                stack.pop()
        elif op == "cm":
            m = _matrix(operands)
            if m is not None:
                stack[-1] = concat(m, stack[-1])
        elif op == "Do" and operands:
            if state.budget <= 0:
                return
            _do(state, resources, operands[0], stack[-1], path, depth)


def scan_display_sizes(pdf: pikepdf.Pdf) -> tuple[dict[ObjKey, tuple[float, float]], set[ObjKey]]:
    """每張圖片在頁面上最大的顯示尺寸（pt）與屬於透明遮罩的圖片。"""
    state = _Scan()
    for page in pdf.pages:
        if state.budget <= 0:
            break
        _scan_stream(state, page, page.obj.get("/Resources"), IDENTITY, (), 0)
    return state.sizes, state.masks


# ---------------------------------------------------------------- 圖片重新壓縮


def _filters(obj: pikepdf.Stream) -> list[str]:
    f = obj.get("/Filter")
    if isinstance(f, pikepdf.Array):
        return [str(x) for x in f]
    return [str(f)] if f is not None else []


def _skip(obj: pikepdf.Stream) -> bool:
    """原版不處理的圖片：太小、遮罩、色鍵遮罩、有 Decode 陣列。"""
    try:
        w, h = int(obj.get("/Width", 0)), int(obj.get("/Height", 0))
    except (TypeError, ValueError):
        return True
    if w <= MIN_IMAGE_SIDE or h <= MIN_IMAGE_SIDE or w * h > MAX_DECODE_PIXELS:
        return True
    if bool(obj.get("/ImageMask", False)):
        return True
    if isinstance(obj.get("/Mask"), pikepdf.Array):
        return True
    return "/Decode" in obj


def target_scale(w: int, h: int, display: tuple[float, float] | None, level: Level) -> float:
    scale = 1.0
    if display is not None and display[0] > 1 and display[1] > 1:
        need_w = display[0] / 72 * level.dpi
        need_h = display[1] / 72 * level.dpi
        scale = min(1.0, max(need_w / w, need_h / h))
    return min(scale, level.max_pixels / max(w, h))


def _decode(obj: pikepdf.Stream) -> Image.Image | None:
    try:
        image = pikepdf.PdfImage(obj).as_pil_image()
    except Exception as exc:  # noqa: BLE001 — 無法解碼的圖片原樣保留（原版同樣略過）
        log.info("圖片無法解碼，保留原樣：%s", exc)
        return None
    if image.mode == "CMYK" and _filters(obj)[-1:] == ["/DCTDecode"]:
        return None  # CMYK JPEG（常見的反相格式）不處理
    return image


def _flatten(image: Image.Image, gray: bool, size: tuple[int, int]) -> Image.Image:
    """畫在白色背景上並縮放成 size。"""
    if image.mode in ("RGBA", "LA", "PA") or (image.mode == "P" and "transparency" in image.info):
        rgba = image.convert("RGBA")
        bg = Image.new("RGBA", rgba.size, (255, 255, 255, 255))
        image = Image.alpha_composite(bg, rgba)
    if image.mode in ("I;16", "I;16B", "I"):
        image = image.point(lambda v: v / 256).convert("L")
    image = image.convert("L" if gray else "RGB")
    if image.size != size:
        image = image.resize(size, Image.LANCZOS)
    return image


def recompress_image(
    obj: pikepdf.Stream, level: Level, display: tuple[float, float] | None, is_mask: bool
) -> bool:
    """依原版規則重新壓縮一張圖片；有替換回傳 True。"""
    if _skip(obj):
        return False
    w, h = int(obj.Width), int(obj.Height)
    original = len(obj.read_raw_bytes())
    scale = target_scale(w, h, display, level)
    size = (max(1, round(w * scale)), max(1, round(h * scale)))
    is_jpeg = _filters(obj)[-1:] == ["/DCTDecode"]
    if is_jpeg and scale > 0.92 and level.quality >= 0.75:
        return False  # 已經是 JPEG 又不需要縮小：重新壓縮得不償失
    image = _decode(obj)
    if image is None:
        return False
    gray = is_mask or image.mode in ("L", "1", "LA", "I;16", "I;16B", "I")
    pixels = _flatten(image, gray, size)
    if is_mask:
        data = zlib.compress(pixels.tobytes(), 9)
        if len(data) >= original * level.min_saving:
            return False
        obj.write(data, filter=Name.FlateDecode)
    else:
        buf = io.BytesIO()
        pixels.save(buf, "JPEG", quality=int(round(level.quality * 100)), optimize=True)
        data = buf.getvalue()
        if len(data) >= original * level.min_saving:
            return False
        obj.write(data, filter=Name.DCTDecode)
        obj.ColorSpace = Name.DeviceGray if gray else Name.DeviceRGB
    obj.Width, obj.Height, obj.BitsPerComponent = size[0], size[1], 8
    for key in ("/DecodeParms", "/Decode", "/Intent"):
        if key in obj:
            del obj[key]
    return True


def _image_objects(pdf: pikepdf.Pdf) -> list[pikepdf.Stream]:
    return [
        o for o in pdf.objects if isinstance(o, pikepdf.Stream) and o.get("/Subtype") == Name.Image
    ]


def _compress_with_pikepdf(
    src: Path, out: Path, level: Level, progress: Callable[[float], None], check: Callable[[], None]
) -> int:
    """主要方式；回傳重新壓縮的圖片數。"""
    with open_pdf(src) as pdf:
        sizes, masks = scan_display_sizes(pdf)
        images = _image_objects(pdf)
        done = 0
        for i, obj in enumerate(images):
            check()
            key = obj.objgen
            if recompress_image(obj, level, sizes.get(key), key in masks):
                done += 1
            progress((i + 1) / max(len(images), 1))
        write_info(pdf, read_info(pdf))
        pdf.save(
            out,
            encryption=False,
            compress_streams=True,
            object_stream_mode=pikepdf.ObjectStreamMode.generate,
            recompress_flate=True,
        )
        count = len(pdf.pages)
    verify_pages(out, count)
    return done


def _compress_with_gs(ctx: ToolContext, src: Path, out: Path, level: Level) -> bool:
    """備援：Ghostscript（會重新嵌入字型，只在特殊 PDF 才用）。"""
    gs = backends.find("gs")
    if gs is None:
        return False
    dpi = int(level.dpi)
    args = [gs, "-q", "-dNOPAUSE", "-dBATCH", "-dSAFER", "-sDEVICE=pdfwrite"]
    args += ["-dCompatibilityLevel=1.7", "-dDownsampleColorImages=true"]
    args += ["-dDownsampleGrayImages=true", f"-dColorImageResolution={dpi}"]
    args += [f"-dGrayImageResolution={dpi}", "-dColorImageDownsampleType=/Bicubic"]
    args += ["-dGrayImageDownsampleType=/Bicubic", "-dAutoFilterColorImages=false"]
    args += ["-dAutoFilterGrayImages=false", "-dColorImageFilter=/DCTEncode"]
    args += ["-dGrayImageFilter=/DCTEncode", f"-dJPEGQ={int(level.quality * 100)}"]
    # -sOutputFile 的 % 是頁碼格式，要寫成 %%
    args += ["-sOutputFile=" + str(out).replace("%", "%%"), str(src.resolve())]
    try:
        process.run_checked(args, "Ghostscript", cancel=ctx.cancel_event)
        with pikepdf.open(out) as check, pikepdf.open(src) as orig:
            return len(check.pages) == len(orig.pages)
    except errors.Cancelled:
        raise
    except (errors.QSError, pikepdf.PdfError, OSError) as exc:
        log.warning("Ghostscript 壓縮失敗：%s", exc)
        return False


def _compress_file(file: Path, values: Values, ctx: ToolContext) -> list[Path]:
    level = LEVELS.get(str(values.get("level") or "recommended"), LEVELS["recommended"])
    with open_pdf(file) as doc:
        pages = len(doc.pages)
    ctx.progress(0.05, f"壓縮「{file.name}」（{pages} 頁）…")
    tmp = work_dir(ctx, file) / "compressed.pdf"
    try:
        _compress_with_pikepdf(
            file,
            tmp,
            level,
            lambda f: ctx.progress(0.1 + 0.85 * f, f"壓縮中…（{int(f * 100)}%）"),
            ctx.check_cancelled,
        )
    except errors.QSError:
        raise
    except Exception as exc:  # noqa: BLE001 — 原版遇到任何非預期錯誤都改用備援方式
        log.warning("圖片重新壓縮失敗，改用 Ghostscript：%s", exc)
        tmp.unlink(missing_ok=True)
        if not _compress_with_gs(ctx, file, tmp, level):
            raise errors.write_failed(f"無法壓縮「{file.name}」。") from exc
    ctx.check_cancelled()
    return _deliver(file, tmp, ctx)


def _deliver(file: Path, tmp: Path, ctx: ToolContext) -> list[Path]:
    before = file.stat().st_size
    after = tmp.stat().st_size if tmp.exists() else 0
    name = ctx.output_name(file, "已壓縮", "pdf")
    if after <= 0 or after >= before:
        # 沒有變小：保留原始內容
        tmp.unlink(missing_ok=True)
        shutil.copyfile(file, tmp)
        out = ctx.deliver(tmp, name, file)
        ctx.progress(
            1,
            f"「{file.name}」已經很精簡，壓縮後沒有變小，保留原始內容（{format_size(before)}）",
        )
        return [out]
    saved = int((1 - after / before) * 100)
    out = ctx.deliver(tmp, name, file)
    ctx.progress(1, f"已壓縮：{format_size(before)} → {format_size(after)}（減少 {saved}%）")
    return [out]


def run_compress(files: list[Path], values: Values, ctx: ToolContext) -> list[Path]:
    return per_file(files, ctx, lambda f, sub: _compress_file(f, values, sub), skip_errors=False)


def _size_note(values: Values, info: Info) -> tuple[str, bool] | None:
    total = int(info.get("total_size") or 0)
    return (f"目前大小：{format_size(total)}", False) if total > 0 else None


COMPRESS = ToolDefinition(
    id="pdf.compress",
    category=PDF,
    title="壓縮 PDF",
    subtitle="縮小 PDF 檔案大小，文字保持清晰可選取，圖片重新壓縮。",
    icon="arrow.down.right.and.arrow.up.left",
    run=run_compress,
    input_types=PDF_TYPES,
    input_label=INPUT_LABEL,
    action_title="壓縮 PDF",
    keywords=(
        "compress",
        "reduce size",
        "shrink",
        "optimize",
        "壓縮",
        "縮小",
        "減少大小",
        "瘦身",
    ),
    badge="PDF",
    options=(
        Option(
            "level",
            Kind.CARDS,
            "壓縮程度",
            "recommended",
            choices=(
                Choice(
                    "extreme",
                    "極度壓縮",
                    "檔案最小，圖片畫質較低（約 96 dpi）",
                    "arrow.down.to.line.compact",
                ),
                Choice(
                    "recommended",
                    "建議壓縮",
                    "檔案明顯變小，畫質維持良好（約 150 dpi）",
                    "checkmark.seal",
                    badge="推薦",
                ),
                Choice("low", "低度壓縮", "畫質最好，檔案稍微變小（約 220 dpi）", "photo"),
            ),
            group="壓縮程度",
            group_icon="gauge.with.dots.needle.33percent",
        ),
        note("", key="_compress_size", note=_size_note, group_icon="internaldrive"),
        note(
            "只重新壓縮與縮小圖片，文字與向量圖形保持原樣、仍可選取與搜尋。"
            "若壓縮後沒有變小，會保留原始內容。"
        ),
    ),
    presets={"pdf.compress-recommended": {"level": "recommended"}},
    inspect=inspect_pdfs,
    validate=make_validator(),
)
