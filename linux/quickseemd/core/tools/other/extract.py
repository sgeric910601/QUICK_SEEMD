"""解壓縮（對應原版 OtherToolCatalog.extract；引擎在 unpack）。"""

from __future__ import annotations

from pathlib import Path

from ... import errors
from ...model import OTHER, TINTS, ToolContext, ToolDefinition, per_file
from ...options import Values
from . import textcodec, unpack
from .archivekind import archive_kind

TARGET_LABEL = "解壓縮到資料夾"  # 原版萬用轉換的格式名稱，用在「無法轉換成…」


def _check_readable(path: Path) -> None:
    try:
        with open(path, "rb"):
            pass
    except OSError as exc:
        raise errors.unreadable(textcodec.read_error_text(path, exc)) from exc


def extract_one(archive: Path, ctx: ToolContext) -> Path:
    """解開一個壓縮檔並交付（單一最上層項目交付它本身，否則交付「主檔名」資料夾）。"""
    kind = archive_kind(archive)
    if kind is None:
        raise errors.unsupported_target(archive.name, TARGET_LABEL)
    _check_readable(archive)
    work = ctx.make_work_dir(ctx.output_directory(archive))
    result = unpack.extract(archive, kind, work, ctx)
    return ctx.deliver(result.path, result.name, archive)


def run(files: list[Path], values: Values, ctx: ToolContext) -> list[Path]:  # noqa: ARG001
    return per_file(files, ctx, lambda f, sub: [extract_one(f, sub)])


TOOL = ToolDefinition(
    id="other.extract",
    category=OTHER,
    title="解壓縮",
    subtitle="解開 ZIP、TAR、TAR.GZ、7z、GZ 等壓縮檔到同名資料夾。",
    icon="archivebox",
    run=run,
    input_types=("archive",),
    input_label="壓縮檔",
    action_title="解壓縮",
    keywords=("unzip", "extract", "解壓", "rar", "7z"),
    tint=TINTS["slate"],
    badge="UNZIP",
    presets={"other.unzip": {}},
)
