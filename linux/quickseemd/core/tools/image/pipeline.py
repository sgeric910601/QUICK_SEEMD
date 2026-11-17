"""逐檔流程（對應原版 ITPipeline）與選項面板用的檔案資訊。"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from ... import errors
from ...model import ToolContext, per_file
from . import formats, loader, writer
from .loader import Frame, Source

OutputFor = Callable[[Source], writer.Output]
Transform = Callable[[Any, Source], Any]
INFO_LIMIT = 50  # 選項面板最多讀前 50 個檔案的尺寸與透明度（原版 hasTransparency 也是 50）


def work_dir(ctx: ToolContext, for_input: Path) -> Path:
    """和輸出位置同一顆磁碟的暫存資料夾（工作結束時自動清掉）。

    一律回傳絕對路徑：交給外部程式的路徑才不會被誤認成以「-」開頭的參數。
    """
    return ctx.make_work_dir(ctx.output_directory(for_input)).resolve()


def run_file(
    file: Path,
    ctx: ToolContext,
    tag: str | None,
    output_for: OutputFor,
    transform: Transform,
    animated: bool = True,
) -> Path:
    """讀取 → 處理（動畫 GIF 每一格都處理）→ 寫出 → 交付到輸出位置。"""
    ctx.progress(0.02, f"讀取：{file.name}")
    source = loader.load(file, all_frames=animated)
    ctx.check_cancelled()
    out = output_for(source)
    frames = source.frames if out.format == "gif" else source.frames[:1]
    done: list[Frame] = []
    for i, frame in enumerate(frames):
        ctx.check_cancelled()
        done.append(Frame(transform(frame.image, source), frame.delay))
        ctx.progress(0.1 + 0.7 * (i + 1) / len(frames), f"處理中：{file.name}")
    ctx.check_cancelled()
    return deliver_frames(done, source, out, ctx, tag)


def deliver_frames(
    frames: Sequence[Frame],
    source: Source,
    output: writer.Output,
    ctx: ToolContext,
    tag: str | None,
) -> Path:
    """寫到暫存區後交付（同名自動加編號，絕不覆蓋）。"""
    work = work_dir(ctx, source.path)
    ext = source.output_extension(output.format)
    tmp = work / f"output.{ext}"
    ctx.progress(0.85, f"寫入 {formats.title(output.format)}…")
    writer.write(frames, source, tmp, output, cancel=ctx.cancel_event)
    url = ctx.deliver(tmp, ctx.output_name(source.path, tag, ext), source.path)
    ctx.progress(1, f"已儲存：{url.name}")
    return url


def deliver_file(tmp: Path, for_input: Path, ctx: ToolContext, tag: str | None, ext: str) -> Path:
    """把自己產生的暫存檔（或資料夾）交付。"""
    return ctx.deliver(tmp, ctx.output_name(for_input, tag, ext), for_input)


def each_file(
    files: Sequence[Path], ctx: ToolContext, body: Callable[[Path, ToolContext], list[Path]]
) -> tuple[list[Path], list[str]]:
    """逐檔處理；多個檔案時略過 SkipFile 的檔案，回傳（輸出, 「檔名：原因」清單）。"""
    skipped: list[str] = []

    def wrapped(f: Path, sub: ToolContext) -> list[Path]:
        try:
            return body(f, sub)
        except errors.SkipFile as exc:
            skipped.append(f"{f.name}：{exc}")
            raise

    outputs = per_file(list(files), ctx, wrapped)
    return outputs, skipped


def image_info(files: Sequence[Path]) -> dict[str, Any]:
    """選項面板用：前 50 個檔案的尺寸、有沒有透明、有沒有 PNG、總大小。"""
    paths = [Path(f) for f in files]
    sizes: list[list[int] | None] = []
    has_alpha = False
    for p in paths[:INFO_LIMIT]:
        size = loader.pixel_size(p)
        sizes.append(list(size) if size else None)
        has_alpha = has_alpha or loader.quick_has_alpha(p)
    total = 0
    for p in paths:
        try:
            total += p.stat().st_size
        except OSError:
            continue
    return {
        "count": len(paths),
        "sizes": sizes,
        "has_alpha": has_alpha,
        "has_png": any(p.suffix.lower() == ".png" for p in paths),
        "total_size": total,
    }


def first_size(info: dict[str, Any]) -> tuple[int, int] | None:
    sizes = info.get("sizes") or []
    if sizes and sizes[0]:
        return int(sizes[0][0]), int(sizes[0][1])
    return None
