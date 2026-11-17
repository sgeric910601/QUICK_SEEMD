"""圖片與 PDF 依序合併成一份 PDF（對應原版 CVImageConverter.writePDF）。

- 圖片：一張一頁，頁面大小＝像素 × 72 ÷ DPI（沒有 DPI 資訊時用 72）。用 cairo 畫成單頁 PDF；
  沒有轉向與縮小的 JPEG 直接放原始資料，不重新壓縮。
- PDF：用 pikepdf 原樣複製每一頁（裁切框與旋轉都保留）；有密碼時先試空白密碼。
- 建立者標記為 QUICK_SEEMD。
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from .. import errors
from .image import Loaded, load_image

CREATOR = "QUICK_SEEMD"


def _cairo_surface(img):  # noqa: ANN001, ANN202
    import cairo  # noqa: PLC0415

    if img.mode == "RGBA":
        data = bytearray(img.convert("RGBa").tobytes("raw", "BGRa"))
        fmt = cairo.FORMAT_ARGB32
    else:
        data = bytearray(img.convert("RGB").tobytes("raw", "BGRX"))
        fmt = cairo.FORMAT_RGB24
    stride = cairo.ImageSurface.format_stride_for_width(fmt, img.width)
    if stride != img.width * 4:
        raise errors.write_failed("無法建立 PDF。")
    return cairo.ImageSurface.create_for_data(data, fmt, img.width, img.height, stride)


def image_page(loaded: Loaded, out: Path) -> None:
    """把一張圖畫成單頁 PDF。"""
    import cairo  # noqa: PLC0415

    img = loaded.image
    dpi = loaded.dpi if loaded.dpi > 0 else 72.0
    width, height = img.width * 72.0 / dpi, img.height * 72.0 / dpi
    source = _cairo_surface(img)
    if loaded.source_jpeg is not None:
        source.set_mime_data(cairo.MIME_TYPE_JPEG, loaded.source_jpeg)
    surface = cairo.PDFSurface(str(out), width, height)
    surface.set_metadata(cairo.PDFMetadata.CREATOR, CREATOR)
    cr = cairo.Context(surface)
    cr.scale(width / img.width, height / img.height)
    cr.set_source_surface(source, 0, 0)
    cr.get_source().set_filter(cairo.FILTER_BEST)
    cr.paint()
    surface.finish()


def open_pdf(path: Path):  # noqa: ANN201
    """用 pikepdf 開啟；加密時試空白密碼，打不開就回報受密碼保護。"""
    import pikepdf  # noqa: PLC0415

    try:
        return pikepdf.open(path)
    except pikepdf.PasswordError as exc:
        raise errors.password_protected() from exc
    except (pikepdf.PdfError, OSError) as exc:
        raise errors.unreadable("PDF 檔案損毀或格式不正確。") from exc


def merge(inputs: list[Path], out: Path, options: dict, ctx) -> None:  # noqa: ANN001
    import pikepdf  # noqa: PLC0415

    scratch = Path(tempfile.mkdtemp(prefix=".merge-", dir=out.parent))
    result = pikepdf.new()
    opened = []
    try:
        for i, path in enumerate(inputs):
            ctx.check_cancelled()
            if path.suffix.lower() == ".pdf":
                src = open_pdf(path)
            else:
                page = scratch / f"page-{i}.pdf"
                image_page(load_image(path, scratch, ctx, options.get("image_max_pixel")), page)
                src = pikepdf.open(page)
            opened.append(src)
            result.pages.extend(src.pages)
            ctx.progress((i + 1) / len(inputs) * 0.95)
        if len(result.pages) == 0:
            raise errors.empty_content("沒有可放進 PDF 的頁面。")
        result.docinfo["/Creator"] = CREATOR
        result.docinfo["/Producer"] = CREATOR
        try:
            result.save(out)
        except (pikepdf.PdfError, OSError) as exc:
            raise errors.write_failed(str(exc)) from exc
    finally:
        for src in opened:
            src.close()
        result.close()
    ctx.progress(1)
