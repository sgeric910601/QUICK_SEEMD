"""壓縮圖片（對應原版 ITCompressTool）。

絕不輸出比原檔更大的檔案：壓縮後沒有變小時交付原始內容（要移除相片資訊且移除後較小時用移除後的版本）。
"""

from __future__ import annotations

import logging
import shutil
from pathlib import Path

from ... import errors
from ...model import ToolContext
from ...options import Choice, Info, Kind, Option, Values
from ...result import format_size
from . import colors, formats, loader, metadata, palette, pipeline, writer
from .common import define, dynamic_note

log = logging.getLogger(__name__)

_QUALITY = {
    "jpeg": {"maximum": 0.5, "recommended": 0.7, "light": 0.85},
    "heif": {"maximum": 0.4, "recommended": 0.55, "light": 0.72},
}
_KEEP_FORMATS = ("jpeg", "png", "heic", "avif", "tiff", "gif")
_TARGET_HINTS = {
    "keep": "JPG 維持 JPG、PNG 維持 PNG；BMP 會改存成 PNG。",
    "jpeg": "相容性最好；透明的部分會變成白色。",
    "heic": "同畫質約為 JPG 的一半大小，Apple 裝置與新版 Windows 可直接開啟。",
}
NEVER_LARGER = "如果壓縮後反而變大，會保留原始內容，絕不輸出比原檔更大的檔案。"


def quality(fid: str, level: str) -> float:
    """各格式在各壓縮程度的品質。"""
    table = _QUALITY["jpeg"] if fid == "jpeg" else _QUALITY["heif"]
    return table.get(level, table["light"])


def percent(value: float) -> str:
    return f"{round(value * 100)}%"


def summary(before: int, after: int, total: bool = False) -> str:
    if before <= 0 or after >= before:
        return "已是最精簡的大小，保留原始內容"
    saved = (before - after) / before
    prefix = "共" if total else ""
    return f"{prefix}省下 {percent(saved)}（{format_size(before)} → {format_size(after)}）"


def target_format(source: loader.Source, target: str) -> str:
    if target == "jpeg":
        return "jpeg"
    if target == "heic":
        return "heic" if formats.is_writable("heic") else "jpeg"
    fmt = source.format
    if fmt in _KEEP_FORMATS and formats.is_writable(fmt):
        return fmt
    if fmt == "bmp":
        return "png"
    return "png" if source.has_alpha else "jpeg"


def _encode(source: loader.Source, fid: str, level: str, strip: bool, tmp: Path, ctx: ToolContext) -> None:
    meta = writer.REMOVE_ALL if strip else writer.KEEP
    if fid == "png":
        written = palette.write(
            source.image,
            128 if level == "maximum" else 256,
            lossless_only=level == "light",
            dpi=source.dpi,
            icc=source.icc,
            path=tmp,
            cancel=ctx.cancel_event,
        )
        if not written:
            out = writer.Output("png", metadata=meta, keeps_alpha=source.has_alpha)
            writer.write_image(source.image, source, tmp, out)
        return
    frames = source.frames if fid == "gif" else source.frames[:1]
    out = writer.Output(fid, quality(fid, level), meta, colors.WHITE, source.has_alpha)
    writer.write(frames, source, tmp, out, cancel=ctx.cancel_event)


def _keep_original(file: Path, source: loader.Source, strip: bool, work: Path) -> tuple[Path, str, int]:
    """壓縮沒有變小：保留原始內容（需要時只移除相片資訊、不重新壓縮）。"""
    ext = file.suffix.lstrip(".").lower() or (formats.get(source.format).ext if source.format else "img")
    original = source.file_size
    if strip:
        try:
            report = metadata.strip(file, metadata.ALL, work / "stripped")
            size = report.file.stat().st_size
            if report.ext == ext and size <= original:
                return report.file, ext, size
        except (errors.QSError, OSError, ValueError) as exc:
            log.warning("移除相片資訊失敗，改保留原檔：%s", exc)
    keep = work / f"original.{ext}"
    shutil.copyfile(file, keep)
    return keep, ext, original


def compress_file(file: Path, values: Values, ctx: ToolContext) -> tuple[Path, int, int]:
    """壓縮一個檔案，回傳（輸出, 原大小, 新大小）。"""
    level = str(values.get("level", "recommended"))
    strip = bool(values.get("strip_metadata", False))
    ctx.progress(0.02, f"讀取：{file.name}")
    source = loader.load(file, all_frames=True)
    original = source.file_size
    ctx.check_cancelled()
    fid = target_format(source, str(values.get("target", "keep")))
    work = pipeline.work_dir(ctx, file)
    ext = source.output_extension(fid)
    tmp = work / f"compressed.{ext}"
    ctx.progress(0.15, f"壓縮中：{file.name}")
    _encode(source, fid, level, strip, tmp, ctx)
    ctx.check_cancelled()
    ctx.progress(0.85)
    size = tmp.stat().st_size
    if 0 < size < original:
        url = pipeline.deliver_file(tmp, file, ctx, "已壓縮", ext)
        ctx.progress(1, summary(original, size))
        return url, original, size
    keep, keep_ext, kept = _keep_original(file, source, strip, work)
    url = pipeline.deliver_file(keep, file, ctx, "已壓縮", keep_ext)
    if kept < original:
        ctx.progress(1, summary(original, kept))
    else:
        ctx.progress(1, f"已是最精簡的大小，保留原始內容（{format_size(original)}）")
    return url, original, kept


def run(files: list[Path], values: Values, ctx: ToolContext) -> list[Path]:
    before = after = 0
    outputs: list[Path] = []
    for i, f in enumerate(files):
        ctx.check_cancelled()
        url, b, a = compress_file(Path(f), values, ctx.segment(i, len(files)))
        outputs.append(url)
        before += b
        after += a
    if len(files) > 1:
        ctx.progress(1, summary(before, after, total=True))
    return outputs


def _png_note(values: Values, info: Info) -> tuple[str, bool]:
    level = values.get("level")
    if level == "light":
        return ("PNG：顏色不超過 256 色時改存成調色盤 PNG（完全無損）。", False)
    n = 128 if level == "maximum" else 256
    return (f"PNG 會減色成調色盤（最多 {n} 色）並保留透明，截圖與圖表效果最好。", False)


def _shield_note(values: Values, info: Info) -> tuple[str, bool]:
    total = int(info.get("total_size", 0) or 0)
    return (NEVER_LARGER + (f"\n目前共 {format_size(total)}。" if total > 0 else ""), False)


OPTIONS: tuple[Option, ...] = (
    Option(
        "level",
        Kind.CARDS,
        "壓縮程度",
        "recommended",
        (
            Choice(
                "maximum",
                "最高壓縮",
                "檔案最小，畫質略降。適合傳訊息、上傳網頁或寄信。",
                "arrow.down.to.line.compact",
            ),
            Choice(
                "recommended", "建議", "畫質與大小兼顧，肉眼幾乎看不出差別。", "hand.thumbsup", "推薦"
            ),
            Choice("light", "保留品質", "只做輕微、近乎無損的最佳化，縮小幅度較少。", "sparkles"),
        ),
        group="壓縮程度",
        group_icon="gauge.with.dots.needle.67percent",
    ),
    Option(
        "target",
        Kind.SEGMENTED,
        "轉成更省空間的格式",
        "keep",
        (Choice("keep", "維持原格式"), Choice("jpeg", "JPG"), Choice("heic", "HEIC")),
        group="轉成更省空間的格式",
        group_icon="arrow.triangle.2.circlepath",
        note=lambda v, i: (_TARGET_HINTS.get(str(v.get("target")), _TARGET_HINTS["keep"]), False),
    ),
    Option(
        "strip_metadata",
        Kind.TOGGLE,
        "移除相片資訊",
        False,
        caption="EXIF、GPS 位置等，可再省一點空間並保護隱私",
        group="相片資訊",
        group_icon="info.circle",
    ),
    dynamic_note(
        "_png_note",
        _png_note,
        group_icon="paintpalette",
        visible=lambda v, i: bool(i.get("has_png")) and v.get("target") == "keep",
    ),
    dynamic_note("_never_larger", _shield_note, group_icon="checkmark.shield"),
)

TOOL = define(
    "image.compress",
    "壓縮圖片",
    "在畫質與檔案大小間取得最佳平衡，JPG、PNG、HEIC 都能大幅縮小；絕不輸出比原檔更大的檔案。",
    "arrow.down.right.and.arrow.up.left",
    run,
    tint="excel",
    badge="IMG",
    action_title="壓縮圖片",
    keywords=("compress", "optimize", "reduce", "smaller", "shrink", "壓縮", "縮小檔案", "最佳化", "減肥"),
    options=OPTIONS,
    presets={"image.compress-recommended": {"level": "recommended"}},
)
