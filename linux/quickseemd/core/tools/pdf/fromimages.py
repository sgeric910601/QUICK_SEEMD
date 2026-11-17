"""圖片轉 PDF（對應原版 PTImages.swift 後半 PTImagePDFWriter）。

- JPEG 原始資料直接放進 PDF（DCTDecode），不重新壓縮；EXIF 方向以座標轉換轉正
  （包括鏡像的 2／4／5／7，原版這幾種會先重畫，這裡同樣不重新壓縮）
- HEIC／HEIF／AVIF 先用 heif-convert 解碼；HEIC／AVIF／WebP 沒有透明時存成品質 90 的 JPEG
- 其他圖片無損（Flate）放入，透明度做成 SMask
- 多頁 TIFF 每一頁各成一頁
"""

from __future__ import annotations

import io
import logging
import secrets
import zlib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import pikepdf
from PIL import Image, ImageOps, ImageSequence, UnidentifiedImageError
from pikepdf import Name

from ... import backends, errors, naming
from ...model import TINTS, PDF, ToolContext, ToolDefinition
from ...options import Choice, Info, Kind, Option, Values, note
from .common import A4, LETTER, as_float, work_dir

log = logging.getLogger(__name__)

PAPERS: dict[str, tuple[float, float] | None] = {"a4": A4, "letter": LETTER, "fit": None}
HEIF_EXTS = frozenset({"heic", "heif", "hif", "avif"})
LOSSY_FORMATS = frozenset({"HEIF", "AVIF", "WEBP"})
JPEG_REENCODE_QUALITY = 90

# EXIF 方向：存檔座標 (s, t)（左上為原點、0–1）→ 顯示座標 (u, v)
_ORIENT = {
    1: lambda s, t: (s, t),
    2: lambda s, t: (1 - s, t),
    3: lambda s, t: (1 - s, 1 - t),
    4: lambda s, t: (s, 1 - t),
    5: lambda s, t: (t, s),
    6: lambda s, t: (1 - t, s),
    7: lambda s, t: (1 - t, 1 - s),
    8: lambda s, t: (t, 1 - s),
}


@dataclass(frozen=True)
class Picture:
    """要放進一頁的圖片：PDF 圖片物件的內容與屬性、EXIF 方向與解析度。"""

    data: bytes
    filter: str  # "/DCTDecode" 或 "/FlateDecode"
    width: int
    height: int
    colorspace: str
    orientation: int = 1
    dpi: float = 72.0
    decode: tuple[int, ...] | None = None
    alpha: bytes | None = None  # Flate 壓縮後的透明度（8 bit 灰階）


# ---------------------------------------------------------------- 版面


def page_layout(
    natural: tuple[float, float], paper: str, orientation: str, margin: float
) -> tuple[tuple[float, float], tuple[float, float, float, float]]:
    """（頁面大小, 圖片放置的矩形 x, y, w, h）——等比例縮放並置中（小圖也會放大）。"""
    nw, nh = max(natural[0], 1e-6), max(natural[1], 1e-6)
    size = PAPERS.get(paper, A4)
    if size is None:
        page = (nw + margin * 2, nh + margin * 2)
    else:
        landscape = nw > nh if orientation == "auto" else orientation == "landscape"
        page = (size[1], size[0]) if landscape else size
    page = (max(page[0], 1.0), max(page[1], 1.0))
    aw, ah = page[0] - margin * 2, page[1] - margin * 2
    scale = min(aw / nw, ah / nh)
    w, h = nw * scale, nh * scale
    return page, (margin + (aw - w) / 2, margin + (ah - h) / 2, w, h)


def placement_matrix(rect: tuple[float, float, float, float], orientation: int) -> list[float]:
    """圖片單位正方形 → 頁面矩形，並依 EXIF 方向轉正。"""
    x, y, w, h = rect
    fn = _ORIENT.get(orientation, _ORIENT[1])

    def point(px: float, py: float) -> tuple[float, float]:
        u, v = fn(px, 1 - py)  # PDF 圖片的 (0,1) 是存檔的左上角
        return x + u * w, y + (1 - v) * h

    ox, oy = point(0, 0)
    ax, ay = point(1, 0)
    cx, cy = point(0, 1)
    return [ax - ox, ay - oy, cx - ox, cy - oy, ox, oy]


# ---------------------------------------------------------------- 讀圖


def _dpi(image: Image.Image) -> float:
    dpi = image.info.get("dpi")
    try:
        d = float(dpi[0]) if dpi else 72.0
    except (TypeError, ValueError, IndexError):
        d = 72.0
    return d if d >= 1 else 72.0


def _orientation(image: Image.Image) -> int:
    try:
        value = int(image.getexif().get(0x0112, 1))
    except (TypeError, ValueError, AttributeError):
        return 1
    return value if value in _ORIENT else 1


def _jpeg_picture(path: Path, image: Image.Image) -> Picture | None:
    mode = image.mode
    spaces = {"L": "/DeviceGray", "RGB": "/DeviceRGB", "CMYK": "/DeviceCMYK"}
    if mode not in spaces:
        return None
    decode = None
    if mode == "CMYK" and "adobe" in image.info:
        decode = (1, 0, 1, 0, 1, 0, 1, 0)  # Adobe 的 CMYK JPEG 是反相存放
    return Picture(
        path.read_bytes(),
        "/DCTDecode",
        image.width,
        image.height,
        spaces[mode],
        _orientation(image),
        _dpi(image),
        decode,
    )


def _to_jpeg(image: Image.Image, dpi: float) -> Picture:
    buf = io.BytesIO()
    rgb = image.convert("L" if image.mode in ("L", "1") else "RGB")
    rgb.save(buf, "JPEG", quality=JPEG_REENCODE_QUALITY)
    space = "/DeviceGray" if rgb.mode == "L" else "/DeviceRGB"
    return Picture(buf.getvalue(), "/DCTDecode", rgb.width, rgb.height, space, 1, dpi)


def _has_alpha(image: Image.Image) -> bool:
    return image.mode in ("RGBA", "LA", "PA") or (
        image.mode == "P" and "transparency" in image.info
    )


def _lossless(image: Image.Image, dpi: float) -> Picture:
    alpha = None
    if _has_alpha(image):
        rgba = image.convert("RGBA")
        alpha = zlib.compress(rgba.getchannel("A").tobytes())
        image = rgba.convert("RGB")
    elif image.mode in ("I;16", "I;16B", "I", "F"):
        image = image.point(lambda v: v / 256).convert("L")
    elif image.mode not in ("L", "RGB", "CMYK"):
        image = image.convert("RGB")
    spaces = {"L": "/DeviceGray", "RGB": "/DeviceRGB", "CMYK": "/DeviceCMYK"}
    data = zlib.compress(image.tobytes())
    return Picture(
        data, "/FlateDecode", image.width, image.height, spaces[image.mode], 1, dpi, None, alpha
    )


def _frame_picture(frame: Image.Image, fmt: str, dpi: float) -> Picture:
    upright = ImageOps.exif_transpose(frame) or frame
    if fmt in LOSSY_FORMATS and not _has_alpha(upright):
        return _to_jpeg(upright, dpi)
    return _lossless(upright, dpi)


def _decode_heif(ctx: ToolContext, path: Path, work: Path) -> Path:
    out = work / f"heif-{secrets.token_hex(6)}.png"
    ctx.run_process(
        [backends.require("heif-convert"), "--quiet", str(path.resolve()), str(out)], "heif-convert"
    )
    if out.is_file():
        return out
    found = sorted(work.glob(out.stem + "*.png"))
    if not found:
        raise errors.unreadable(f"無法解碼「{path.name}」。")
    return found[0]


def pictures(ctx: ToolContext, path: Path, work: Path) -> Iterator[Picture]:
    """一個圖檔裡要放進 PDF 的圖片（多頁 TIFF 有好幾張）。"""
    source, fmt = path, None
    if naming.split_name(path.name)[1].lower() in HEIF_EXTS:
        source, fmt = _decode_heif(ctx, path, work), "HEIF"
    try:
        image = Image.open(source)
    except Image.DecompressionBombError as exc:
        raise errors.unreadable(f"無法解碼「{path.name}」。") from exc
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise errors.unreadable(f"「{path.name}」不是可辨識的圖片。") from exc
    with image:
        fmt = fmt or image.format or ""
        try:
            if fmt == "JPEG":
                pic = _jpeg_picture(path, image)
                yield pic if pic is not None else _lossless(image.convert("RGB"), _dpi(image))
                return
            frames = ImageSequence.Iterator(image) if fmt == "TIFF" else [image]
            for frame in frames:
                frame.load()
                yield _frame_picture(frame, fmt, _dpi(frame))
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            raise errors.unreadable(f"無法解碼「{path.name}」。") from exc


# ---------------------------------------------------------------- 寫出


def _image_object(pdf: pikepdf.Pdf, pic: Picture) -> pikepdf.Stream:
    obj = pikepdf.Stream(pdf, pic.data)
    obj.Type, obj.Subtype = Name.XObject, Name.Image
    obj.Width, obj.Height, obj.BitsPerComponent = pic.width, pic.height, 8
    obj.ColorSpace = Name(pic.colorspace)
    obj.Filter = Name(pic.filter)
    if pic.decode:
        obj.Decode = pikepdf.Array(list(pic.decode))
    if pic.alpha is not None:
        mask = pikepdf.Stream(pdf, pic.alpha)
        mask.Type, mask.Subtype = Name.XObject, Name.Image
        mask.Width, mask.Height, mask.BitsPerComponent = pic.width, pic.height, 8
        mask.ColorSpace, mask.Filter = Name.DeviceGray, Name.FlateDecode
        obj.SMask = mask
    return obj


def add_page(pdf: pikepdf.Pdf, pic: Picture, values: Values) -> None:
    swap = pic.orientation in (5, 6, 7, 8)
    px = (pic.height, pic.width) if swap else (pic.width, pic.height)
    natural = (px[0] * 72 / pic.dpi, px[1] * 72 / pic.dpi)
    margin = as_float(values.get("margin"), 0)
    page_size, rect = page_layout(
        natural, str(values.get("paper") or "a4"), str(values.get("orientation") or "auto"), margin
    )
    m = " ".join(f"{v:.4f}" for v in placement_matrix(rect, pic.orientation))
    content = pdf.make_stream(f"q {m} cm /Im0 Do Q\n".encode())
    page = pikepdf.Dictionary(
        Type=Name.Page,
        MediaBox=pikepdf.Array([0, 0, round(page_size[0], 4), round(page_size[1], 4)]),
        Resources=pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im0=_image_object(pdf, pic))),
        Contents=content,
    )
    pdf.pages.append(pikepdf.Page(pdf.make_indirect(page)))


def write_images(files: list[Path], out: Path, values: Values, ctx: ToolContext) -> None:
    pdf = pikepdf.new()
    work = out.parent
    try:
        for i, file in enumerate(files):
            ctx.check_cancelled()
            ctx.progress(i / len(files), f"加入「{file.name}」（{i + 1}/{len(files)}）…")
            for pic in pictures(ctx, file, work):
                add_page(pdf, pic, values)
        if len(pdf.pages) == 0:
            raise errors.empty_content("沒有可放進 PDF 的圖片。")
        pdf.docinfo["/Creator"] = pikepdf.String("QUICK_SEEMD")
        pdf.save(out)
    finally:
        pdf.close()


def run_from_images(files: list[Path], values: Values, ctx: ToolContext) -> list[Path]:
    if not files:
        raise errors.QSError("請選擇至少一張圖片。")
    work = work_dir(ctx, files[0])
    if values.get("output", "merge") == "merge":
        tmp = work / "images.pdf"
        write_images(files, tmp, values, ctx)
        name = ctx.output_name(files[0], "已合併" if len(files) > 1 else None, "pdf")
        return [ctx.deliver(tmp, name, files[0])]
    outputs: list[Path] = []
    for i, file in enumerate(files):
        ctx.check_cancelled()
        sub = ctx.segment(i, len(files))
        tmp = work / f"image-{i}.pdf"
        write_images([file], tmp, values, sub)
        outputs.append(sub.deliver(tmp, sub.output_name(file, None, "pdf"), file))
    return outputs


def _action_title(values: Values, info: Info) -> str:
    count = int(info.get("file_count") or 0)
    if values.get("output", "merge") == "merge" or count <= 1:
        return "轉成 PDF"
    return f"轉成 {count} 個 PDF"


FROM_IMAGES = ToolDefinition(
    id="pdf.from-images",
    category=PDF,
    title="圖片轉 PDF",
    subtitle="把 JPG、PNG、HEIC 等圖片排好順序，轉成一份或多份 PDF。",
    icon="photo.stack",
    run=run_from_images,
    input_types=("image/*",),
    input_label="圖片",
    reorderable=True,
    action_title=_action_title,
    keywords=(
        "jpg to pdf",
        "image to pdf",
        "png to pdf",
        "photos to pdf",
        "scan",
        "圖片轉PDF",
        "照片",
        "掃描",
    ),
    tint=TINTS["image"],
    badge="PDF",
    options=(
        Option(
            "paper",
            Kind.CHIPS,
            "頁面大小",
            "a4",
            choices=(Choice("a4", "A4"), Choice("letter", "Letter"), Choice("fit", "符合圖片大小")),
            group="頁面大小",
            group_icon="doc",
        ),
        Option(
            "orientation",
            Kind.SEGMENTED,
            "方向",
            "auto",
            choices=(
                Choice("auto", "自動"),
                Choice("portrait", "直向"),
                Choice("landscape", "橫向"),
            ),
            group="方向",
            group_icon="rectangle.portrait.rotate",
            visible=lambda v, i: v.get("paper", "a4") != "fit",
        ),
        Option(
            "margin",
            Kind.SEGMENTED,
            "邊界",
            0,
            choices=(Choice(0, "無"), Choice(20, "小"), Choice(50, "大")),
            group="邊界",
            group_icon="square.dashed",
        ),
        Option(
            "output",
            Kind.CARDS,
            "輸出",
            "merge",
            choices=(
                Choice("merge", "合併成一個 PDF", "依左側順序，每張圖片一頁", "doc.richtext"),
                Choice(
                    "separate", "每張圖片各一個 PDF", "分別輸出，檔名與圖片相同", "square.grid.2x2"
                ),
            ),
            group="輸出",
            group_icon="doc.on.doc",
        ),
        note("JPEG 照片會直接放進 PDF，不會重新壓縮而降低畫質；方向會依照片的拍攝資訊自動轉正。"),
    ),
    presets={"pdf.images-to-pdf": {"output": "merge"}},
    inspect=lambda files: {"file_count": len(files)},
)
