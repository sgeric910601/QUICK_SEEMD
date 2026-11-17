"""轉換圖片格式（對應原版 ITConvertTool）。

輸出檔名沒有標記（「名稱.副檔名」，同名加編號）；ICO／ICNS 交給製作圖示（原樣、不留白、透明背景）；
動畫 GIF 轉 GIF 保留所有畫格。
"""

from __future__ import annotations

from pathlib import Path

from ...model import ToolContext, per_file
from ...options import Choice, Info, Kind, Option, Values
from . import colors, formats, make_icon, pipeline, writer
from .common import color_option, color_value, define, dynamic_note, quality_text

FORMAT_ORDER: tuple[str, ...] = tuple(formats.FORMATS)
CUSTOM_BACKGROUND = colors.Rgba(0.96, 0.94, 0.88)
_ALL_CHOICES: tuple[Choice, ...] = tuple(
    Choice(fid, formats.get(fid).title, formats.HINTS[fid]) for fid in FORMAT_ORDER
)


def _format_choices(values: Values, info: Info) -> tuple[Choice, ...]:
    """這台電腦能寫出的格式（沒有 heif-enc 時不出現 HEIC、AVIF）。"""
    return tuple(c for c in _ALL_CHOICES if formats.is_writable(c.value))


def _fmt(values: Values) -> formats.ImageFormat:
    return formats.FORMATS.get(str(values.get("format")), formats.get("jpeg"))


def _quality_note(values: Values, info: Info) -> tuple[str, bool]:
    q = float(values.get("quality", 0.9))
    if q >= 0.9:
        return ("幾乎看不出差異，檔案較大", False)
    if q >= 0.7:
        return ("畫質與大小的平衡點", False)
    return ("檔案最小，細節會變模糊", False)


def _background_note(values: Values, info: Info) -> tuple[str, bool] | None:
    if info.get("has_alpha"):
        return None
    return ("目前的圖片沒有透明背景，不會用到這個設定。", False)


def _metadata_note(values: Values, info: Info) -> tuple[str, bool] | None:
    fmt = _fmt(values)
    return None if fmt.metadata else (f"{fmt.title} 格式無法保存相片資訊。", False)


def run(files: list[Path], values: Values, ctx: ToolContext) -> list[Path]:
    fid = str(values.get("format", "jpeg"))
    if fid in ("ico", "icns"):
        icon = make_icon.IconOptions(
            output=fid, shape="original", padding=0, background=colors.CLEAR
        )
        return per_file(files, ctx, lambda f, sub: [make_icon.make(f, icon, sub)])
    quality = float(values.get("quality", 0.9))
    background = color_value(values, "background", CUSTOM_BACKGROUND)
    metadata = writer.KEEP if values.get("keep_metadata", True) else writer.REMOVE_ALL

    def output_for(source: pipeline.Source) -> writer.Output:
        return writer.Output(fid, quality, metadata, background, keeps_alpha=source.has_alpha)

    def body(f: Path, sub: ToolContext) -> list[Path]:
        return [pipeline.run_file(f, sub, None, output_for, lambda im, _s: im)]

    return per_file(files, ctx, body)


OPTIONS: tuple[Option, ...] = (
    Option(
        "format",
        Kind.CHIPS,
        "轉成",
        "jpeg",
        _ALL_CHOICES,
        group="轉成",
        group_icon="photo.on.rectangle.angled",
        dynamic_choices=_format_choices,
    ),
    dynamic_note(
        "_format_hint",
        lambda v, i: (formats.HINTS.get(_fmt(v).id, ""), False),
        group="轉成",
        group_icon="lightbulb",
    ),
    Option(
        "quality",
        Kind.SLIDER,
        "壓縮品質",
        0.9,
        minimum=0.3,
        maximum=1,
        step=0.05,
        display=quality_text,
        group="品質",
        group_icon="dial.medium",
        visible=lambda v, i: _fmt(v).lossy,
        note=_quality_note,
    ),
    color_option(
        "background",
        "透明部分的底色",
        (colors.WHITE_CHOICE, colors.BLACK_CHOICE, colors.CUSTOM_CHOICE),
        "#ffffff",
        group="透明部分的底色",
        group_icon="square.on.square.dashed",
        visible=lambda v, i: not _fmt(v).alpha,
        note=_background_note,
    ),
    Option(
        "keep_metadata",
        Kind.TOGGLE,
        "保留 EXIF 等相片資訊",
        True,
        caption="拍攝日期、相機、位置等；關閉可保護隱私",
        group="相片資訊",
        group_icon="info.circle",
        note=_metadata_note,
    ),
)

TOOL = define(
    "image.convert",
    "轉換圖片格式",
    "在 JPG、PNG、HEIC、AVIF、TIFF、GIF、ICO、PDF 等格式間互轉，可調品質與透明背景顏色。",
    "arrow.triangle.2.circlepath",
    run,
    badge="JPG",
    action_title=lambda v, i: f"轉成 {_fmt(v).title}",
    keywords=(
        "convert",
        "format",
        "jpg",
        "jpeg",
        "png",
        "heic",
        "avif",
        "webp",
        "tiff",
        "gif",
        "bmp",
        "ico",
        "icns",
        "pdf",
        "格式",
        "轉檔",
    ),
    options=OPTIONS,
    presets={
        "image.to-jpg": {"format": "jpeg", "quality": 0.9},
        "image.to-png": {"format": "png"},
        "image.to-heic": {"format": "heic", "quality": 0.85},
    },
)
