"""壓縮檔案（對應原版 OtherToolCatalog.compress 與 OTCompressSession）。

- 只有一個項目、或選「各自壓縮」：每個項目各產生「主檔名.zip／.tar.gz」，放在原檔旁。
  壓縮檔本身會先解開再重新封裝（同種格式不行，例如 ZIP → ZIP）。
- 「全部打包成一個」：所有項目放進同一個壓縮檔（不解開壓縮檔），名稱預設「封存」，放在第一個檔案旁。
"""

from __future__ import annotations

from pathlib import Path

from ... import errors, naming
from ...model import OTHER, TINTS, ToolContext, ToolDefinition, per_file
from ...options import Choice, Kind, Option, Values
from . import pack, unpack
from .archivekind import SINGLE_FILE_KINDS, ArchiveKind, archive_kind

DEFAULT_NAME = "封存"
FORMAT_EXT = {"zip": "zip", "tgz": "tar.gz"}
FORMAT_LABEL = {"zip": "ZIP", "tgz": "TAR.GZ"}
_SAME_KIND = {"zip": ArchiveKind.ZIP, "tgz": ArchiveKind.TAR}

OPTIONS: tuple[Option, ...] = (
    Option(
        key="format",
        kind=Kind.CARDS,
        label="壓縮格式",
        default="zip",
        group="壓縮格式",
        group_icon="doc.zipper",
        choices=(
            Choice("zip", "ZIP", "最通用，Windows、Mac、手機都能直接打開", "doc.zipper"),
            Choice("tgz", "TAR.GZ", "Linux / 開發常用，保留檔案權限", "shippingbox"),
        ),
    ),
    Option(
        key="together",
        kind=Kind.CARDS,
        label="多個項目時",
        default=True,
        group="多個項目時",
        group_icon="square.stack.3d.up",
        choices=(
            Choice(True, "全部打包成一個", "所有選取的項目放在同一個壓縮檔", "archivebox"),
            Choice(False, "各自壓縮", "每個項目各產生一個壓縮檔", "square.grid.2x2"),
        ),
    ),
    Option(
        key="name",
        kind=Kind.TEXT,
        label="壓縮檔名稱",
        default="",
        placeholder=DEFAULT_NAME,
        group="多個項目時",
        visible=lambda values, _info: bool(values.get("together", True)),
    ),
)


def _format(values: Values) -> str:
    fmt = str(values.get("format") or "zip")
    if fmt not in FORMAT_EXT:
        raise errors.QSError(f"不支援的壓縮格式：{fmt}")
    return fmt


def _reporter(ctx: ToolContext, total: int, lower: float, upper: float) -> pack.Progress:
    done = [0]
    span = max(total, 1)

    def report(n: int) -> None:
        done[0] += n
        ctx.progress(lower + (upper - lower) * min(done[0] / span, 1.0))

    return report


def _pack_one(item: Path, fmt: str, ctx: ToolContext) -> Path:
    """單一項目壓成「主檔名.副檔名」；壓縮檔先解開再重新封裝。"""
    ext = FORMAT_EXT[fmt]
    work = ctx.make_work_dir(ctx.output_directory(item))
    source, arcname, lower = item, item.name, 0.0
    kind = archive_kind(item)
    if kind is not None:
        if kind in SINGLE_FILE_KINDS or kind is _SAME_KIND[fmt]:
            raise errors.unsupported_target(item.name, FORMAT_LABEL[fmt])
        extracted = unpack.extract(item, kind, work, ctx.segment(0, 2))
        source, arcname, lower = extracted.path, extracted.name, 0.4
        ctx.progress(lower)
    entries = pack.collect(source, arcname)
    out = work / f"archive.{ext}"
    pack.write(fmt, entries, out, ctx, _reporter(ctx, pack.total_size(entries), lower, 0.95))
    return ctx.deliver(out, ctx.output_name(item, None, ext), item)


def _unique_arcname(name: str, used: set[str]) -> str:
    for candidate in naming.candidate_names(name):
        if candidate not in used:
            used.add(candidate)
            return candidate
    raise errors.write_failed("找不到可用的檔名。")


def _pack_together(files: list[Path], fmt: str, name: str, ctx: ToolContext) -> Path:
    ext = FORMAT_EXT[fmt]
    base = name if name.strip() else DEFAULT_NAME
    used: set[str] = set()
    entries: list[pack.Entry] = []
    for i, f in enumerate(files):
        ctx.check_cancelled()
        entries.extend(pack.collect(f, _unique_arcname(f.name, used)))
        ctx.progress((i + 1) / len(files) * 0.3, f"準備：{f.name}")
    ctx.progress(0.35, "壓縮中…")
    work = ctx.make_work_dir(ctx.output_directory(files[0]))
    out = work / f"archive.{ext}"
    pack.write(fmt, entries, out, ctx, _reporter(ctx, pack.total_size(entries), 0.35, 0.95))
    ctx.progress(0.95)
    return ctx.deliver(out, naming.join_name(naming.sanitize(base), ext), files[0])


def run(files: list[Path], values: Values, ctx: ToolContext) -> list[Path]:
    if not files:
        return []
    fmt = _format(values)
    if not values.get("together", True) or len(files) == 1:
        return per_file(files, ctx, lambda f, sub: [_pack_one(f, fmt, sub)])
    return [_pack_together(files, fmt, str(values.get("name") or ""), ctx)]


TOOL = ToolDefinition(
    id="other.compress",
    category=OTHER,
    title="壓縮檔案",
    subtitle="把檔案或資料夾壓成 ZIP 或 TAR.GZ，可各自壓縮或全部打包成一個。",
    icon="doc.zipper",
    run=run,
    input_types=("item",),
    input_label="檔案或資料夾",
    action_title="壓縮",
    keywords=("zip", "compress", "archive", "tar", "打包"),
    options=OPTIONS,
    tint=TINTS["slate"],
    badge="ZIP",
    presets={"other.zip": {"format": "zip", "together": True}},
)
