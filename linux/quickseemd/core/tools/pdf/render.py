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
from .common import (
    TIMEOUT_RENDER_PAGE,
    TIMEOUT_TEXT_BASE,
    TIMEOUT_TEXT_PAGE,
    check_pixels,
    crop_box,
    open_image,
    rotation,
)

log = logging.getLogger(__name__)

MAX_SIDE_PX = 12000.0
BLANK_DPI = 40
BLANK_THRESHOLD = 235
BLANK_RATIO = 0.002


def page_points(page: pikepdf.Page) -> tuple[float, float]:
    """poppler 繪製時的頁面大小（裁切框、已旋轉；和 Quartz 一樣不看 UserUnit）。"""
    x0, y0, x1, y1 = crop_box(page)
    w, h = x1 - x0, y1 - y0
    return (h, w) if rotation(page) in (90, 270) else (w, h)


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
    *,
    points: tuple[float, float],
    gray: bool = False,
) -> Path:
    """畫出第 page_no 頁（1 起算）成 PNG，回傳圖檔路徑。out_base 不含副檔名。

    points：頁面大小（pt，見 page_points）。一律套用長邊 12000 px 上限（頁面框或 UserUnit
    再離譜也一樣）；pdftocairo 換算像素時無條件進位，這裡把解析度微調成四捨五入後的像素數。
    """
    dpi = effective_dpi(points, dpi)
    args = [backends.require("pdftocairo"), "-png", "-cropbox", "-singlefile"]
    args += _resolution_args(dpi, points) + ["-f", str(page_no), "-l", str(page_no)]
    if gray:
        args.append("-gray")
    args += [str(Path(src).resolve()), str(out_base)]
    _run(ctx, args, "pdftocairo", TIMEOUT_RENDER_PAGE)
    out = out_base.with_name(out_base.name + ".png")
    if not out.is_file():
        raise errors.QSError(f"第 {page_no} 頁繪製失敗。")
    _check_rendered(out, page_no)
    return out


def _check_rendered(path: Path, page_no: int) -> None:
    """確認畫出來的圖沒有超過上限（多留 2 px 給進位）。"""
    with open_image(path) as im:
        w, h = im.size
    if max(w, h) > MAX_SIDE_PX + 2:
        raise errors.QSError(f"第 {page_no} 頁繪製失敗。")


def _run(ctx: ToolContext | None, args: list[str], name: str, timeout: float) -> str:
    """執行外部程式（一律有逾時，超過就結束並顯示「執行逾時」）。"""
    from ... import process  # noqa: PLC0415 — 避免循環匯入

    if ctx is not None:
        return ctx.run_process(args, name, timeout=timeout).stdout
    return process.run_checked(args, name, timeout=timeout).stdout


def _resolution_args(dpi: float, points: tuple[float, float] | None) -> list[str]:
    if points is None:
        return ["-r", f"{dpi:.4f}"]
    out: list[str] = []
    for flag, pt in (("-rx", points[0]), ("-ry", points[1])):
        pt = max(pt, 1e-3)
        px = max(1, round(pt * dpi / 72.0))
        out += [flag, f"{(px - 0.5) * 72.0 / pt:.6f}"]
    return out


def page_texts(ctx: ToolContext | None, src: Path, first: int, last: int) -> list[str]:
    """第 first～last 頁的文字（pdftotext，閱讀順序）；回傳每頁一個字串。"""
    args = [backends.require("pdftotext"), "-enc", "UTF-8", "-f", str(first), "-l", str(last)]
    args += [str(Path(src).resolve()), "-"]
    timeout = TIMEOUT_TEXT_BASE + TIMEOUT_TEXT_PAGE * (last - first + 1)
    out = _run(ctx, args, "pdftotext", timeout)
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
    with open_image(path) as im:
        check_pixels(im, path.name)
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
    dpi = effective_dpi(page_points(page), BLANK_DPI)
    image = render_page(
        ctx,
        src,
        page_no,
        dpi,
        work / f"blank-{page_no}",
        gray=True,
        points=page_points(page),
    )
    return is_blank_image(image)
