"""萬用轉換引擎（對應原版 ConversionEngine）：與介面無關，主程式、輪盤、命令列共用。

convert 的步驟照原版：確認檔案存在 → 完整分析 → 在規劃中找到目標格式 → 確認輸出位置可寫 →
建立暫存資料夾 → 執行路線 → 以 ctx.deliver 交付（同名加編號、絕不覆蓋）。
"""

from __future__ import annotations

import importlib
import os
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .. import errors, naming
from . import Target, Unavailable, textcodec
from .formats import Format, format_by_id
from .plan import TEXT_SIZE_LIMIT, plan, unavailable as plan_unavailable
from .profile import Profile, analyze, inspect

MERGE_NAME = "合併文件.pdf"

# 路線 → （模組, 函式）；函式簽名一律是 (path, profile, fmt, options, work, ctx) -> list[Path]
ROUTES: dict[str, tuple[str, str]] = {
    "image": ("image", "convert_image"),
    "imageToPDF": ("image", "image_to_pdf"),
    "ocr": ("ocr", "image_to_text"),
    "animatedToVideo": ("media", "animated_to_video"),
    "pdfToImages": ("pdf", "to_images"),
    "pdfToText": ("pdf", "to_text"),
    "document": ("document", "convert"),
    "data": ("data", "convert"),
    "text": ("text", "convert"),
    "audio": ("media", "convert_audio"),
    "video": ("media", "convert_video"),
    "videoToGIF": ("media", "video_to_gif"),
    "videoFrame": ("media", "video_frame"),
    "archive": ("archive", "convert"),
    "external": ("external", "run"),
}


@dataclass(frozen=True)
class Summary:
    kind: str
    detail: str | None


def targets_for(path: Path) -> list[Target]:
    """這個檔案在這台電腦上實際能轉成的格式（含影音軌偵測）。"""
    return plan(analyze(Path(path)))


def unavailable(path: Path) -> list[Unavailable]:
    return plan_unavailable(analyze(Path(path)))


def summary(path: Path, profile: Profile | None = None) -> Summary:
    """介面與命令列用的「種類（細節）」：文字檔的編碼與換行、資料夾的項目數、動畫的格數。"""
    p = profile or analyze(Path(path))
    detail: str | None = None
    if p.is_plain_text:
        detail = _text_detail(p.path)
    elif p.is_dir:
        try:
            count = sum(1 for n in os.listdir(p.path) if not n.startswith("."))
        except OSError:
            count = 0
        detail = f"{count} 個項目"
    elif p.is_image and p.is_animated:
        detail = f"動畫 · {p.frame_count} 格"
    return Summary(p.kind_label, detail)


def _text_detail(path: Path) -> str | None:
    try:
        if path.stat().st_size > TEXT_SIZE_LIMIT:
            return None
        decoded = textcodec.decode_file(path)
    except (OSError, errors.QSError):
        return None
    ending = decoded.line_ending
    bom = "（含 BOM）" if decoded.had_bom else ""
    tail = "" if ending is textcodec.LineEnding.NONE else f" · {ending.value}"
    return decoded.display_name + bom + tail


def _find_target(path: Path, fmt: Format, profile: Profile) -> Target:
    for t in plan(profile):
        if t.id == fmt.id:
            return t
    raise errors.unsupported_target(path.name, fmt.display_name)


def _route_function(route: str) -> Callable[..., list[Path]]:
    module, func = ROUTES[route]
    return getattr(importlib.import_module(f"{__package__}.{module}"), func)


def _run_route(
    target: Target, path: Path, profile: Profile, options: dict, work: Path, ctx
) -> list[Path]:  # noqa: ANN001, E501
    func = _route_function(target.route)
    extra = {"backend": target.backend} if target.route == "external" else {}
    try:
        return func(path, profile, target.format, options, work, ctx, **extra)
    except OSError as exc:
        raise errors.write_failed(exc.strerror or str(exc)) from exc


def _finish_message(delivered: list[Path]) -> str:
    if len(delivered) == 1:
        return f"完成：{delivered[0].name}"
    return f"完成：{len(delivered)} 個項目"


def convert(path: Path, format_id: str, ctx, options: dict[str, Any]) -> list[Path]:  # noqa: ANN001
    """轉換單一檔案；輸出先寫進暫存資料夾，完成後才交付到輸出位置。"""
    path = Path(path)
    if not path.exists():
        raise errors.file_not_found(path.name)
    fmt = format_by_id(format_id)
    if fmt is None:
        raise errors.QSError(f"未知的格式代碼「{format_id}」。")
    profile = analyze(path)
    target = _find_target(path, fmt, profile)
    out_dir = ctx.output_directory(path)
    naming.ensure_writable(out_dir)
    work = ctx.make_work_dir(out_dir)
    ctx.progress(0)
    produced = _run_route(target, path, profile, options, work, ctx)
    ctx.check_cancelled()
    delivered = [ctx.deliver(item, item.name, path) for item in produced]
    ctx.progress(1, _finish_message(delivered))
    return delivered


def merge_to_pdf(paths: list[Path], ctx, options: dict[str, Any]) -> Path:  # noqa: ANN001
    """把圖片與 PDF 依清單順序合併成「合併文件.pdf」。"""
    usable = []
    for p in paths:
        prof = inspect(Path(p))
        if prof.is_image or prof.is_pdf:
            usable.append(Path(p))
    if not usable:
        raise errors.empty_content("沒有可合併的圖片或 PDF。")
    out_dir = ctx.output_directory(usable[0])
    naming.ensure_writable(out_dir)
    work = ctx.make_work_dir(out_dir)
    out = work / naming.sanitize(MERGE_NAME)
    image = importlib.import_module(f"{__package__}.image")
    ctx.progress(0)
    image.write_pdf(usable, out, options, ctx)
    result = ctx.deliver(out, out.name, usable[0])
    ctx.progress(1, f"已將 {len(usable)} 個檔案合併成「{result.name}」")
    return result
