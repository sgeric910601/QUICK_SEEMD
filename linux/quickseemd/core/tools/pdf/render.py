"""PDF 頁面繪製與文字擷取（poppler：pdftocairo、pdftotext）。

- 繪製用裁切框、套用頁面旋轉、白色底（原版 CVPDFConverter.render）。
- 長邊上限 12000 px，避免超大頁面吃光記憶體。
"""

from __future__ import annotations

import logging
from pathlib import Path

import pikepdf

from ... import backends, errors
from ...model import ToolContext
from .common import visual_size

log = logging.getLogger(__name__)

MAX_SIDE_PX = 12000.0
BLANK_DPI = 40
BLANK_THRESHOLD = 235
BLANK_RATIO = 0.002

# pdftocairo 的輸出格式 → (參數, 副檔名)
CAIRO_FORMATS: dict[str, tuple[str, str]] = {
    "png": ("-png", "png"),
    "jpg": ("-jpeg", "jpg"),
    "tiff": ("-tiff", "tif"),
}


def effective_dpi(size_pt: tuple[float, float], dpi: float) -> float:
    """套用長邊 12000 px 上限後的解析度。"""
    longest = max(size_pt[0], size_pt[1], 1.0)
    return min(float(dpi), MAX_SIDE_PX / longest * 72.0)


def render_page(
    ctx: ToolContext | None,
    src: Path,
    page_no: int,
    dpi: float,
    out_base: Path,
    fmt: str = "png",
    *,
    gray: bool = False,
    jpeg_quality: int | None = None,
    password: str | None = None,
) -> Path:
    """畫出第 page_no 頁（1 起算），回傳圖檔路徑。out_base 不含副檔名。"""
    flag, ext = CAIRO_FORMATS[fmt]
    args = [backends.require("pdftocairo"), flag, "-cropbox", "-singlefile"]
    args += ["-r", f"{dpi:.4f}", "-f", str(page_no), "-l", str(page_no)]
    if gray:
        args.append("-gray")
    if jpeg_quality is not None and fmt == "jpg":
        args += ["-jpegopt", f"quality={int(jpeg_quality)}"]
    if password:
        args += ["-upw", password]
    args += [str(Path(src).resolve()), str(out_base)]
    _run(ctx, args, "pdftocairo")
    out = out_base.with_name(out_base.name + "." + ext)
    if not out.is_file():
        raise errors.QSError(f"第 {page_no} 頁繪製失敗。")
    return out


def _run(ctx: ToolContext | None, args: list[str], name: str) -> str:
    from ... import process  # noqa: PLC0415 — 避免循環匯入

    if ctx is not None:
        return ctx.run_process(args, name).stdout
    return process.run_checked(args, name).stdout


def page_texts(ctx: ToolContext | None, src: Path, first: int, last: int) -> list[str]:
    """第 first～last 頁的文字（pdftotext，閱讀順序）；回傳每頁一個字串。"""
    args = [backends.require("pdftotext"), "-enc", "UTF-8", "-f", str(first), "-l", str(last)]
    args += [str(Path(src).resolve()), "-"]
    out = _run(ctx, args, "pdftotext")
    parts = out.split("\f")
    count = last - first + 1
    parts = (parts + [""] * count)[:count]
    return parts


def all_page_texts(ctx: ToolContext | None, src: Path, count: int) -> list[str]:
    return page_texts(ctx, src, 1, count) if count > 0 else []


def has_visible_annotations(page: pikepdf.Page) -> bool:
    """有連結以外的註解（原版空白頁判斷的條件之一）。"""
    annots = page.obj.get("/Annots")
    if not isinstance(annots, pikepdf.Array):
        return False
    for a in annots:
        if isinstance(a, pikepdf.Dictionary) and a.get("/Subtype") != pikepdf.Name.Link:
            return True
    return False


def is_blank_image(path: Path) -> bool:
    """灰階縮圖中比 235 暗的像素少於 0.2%。"""
    from PIL import Image  # noqa: PLC0415

    with Image.open(path) as im:
        gray = im.convert("L")
        hist = gray.histogram()
    total = max(sum(hist), 1)
    dark = sum(hist[:BLANK_THRESHOLD])
    return dark / total < BLANK_RATIO


def is_blank_page(
    ctx: ToolContext | None, src: Path, page: pikepdf.Page, page_no: int, text: str, work: Path
) -> bool:
    """沒有文字、沒有註解，而且 40 dpi 縮圖幾乎全白。"""
    if text.strip():
        return False
    if has_visible_annotations(page):
        return False
    dpi = effective_dpi(visual_size(page), BLANK_DPI)
    image = render_page(ctx, src, page_no, dpi, work / f"blank-{page_no}", "png", gray=True)
    return is_blank_image(image)
