"""計算校驗碼（對應原版 OTChecksumSession）。

每次讀 4 MiB 串流計算；資料夾略過。結果存成「第一個檔案-校驗碼.txt」。
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from pathlib import Path

from ... import errors
from ...model import OTHER, TINTS, ToolContext, ToolDefinition
from ...options import Info, Kind, Option, Values, note
from . import textcodec

CHUNK = 4 << 20
NONE_SELECTED = "至少選一種演算法"


@dataclass(frozen=True)
class Algorithm:
    key: str
    label: str
    caption: str
    default: bool
    hashlib_name: str


ALGORITHMS: tuple[Algorithm, ...] = (
    Algorithm("sha256", "SHA-256", "最常用於驗證下載檔案", True, "sha256"),
    Algorithm("sha1", "SHA-1", "Git 與部分舊系統", False, "sha1"),
    Algorithm("md5", "MD5", "快速比對（不適合安全用途）", True, "md5"),
)

OPTIONS: tuple[Option, ...] = (
    *(
        Option(
            key=a.key,
            kind=Kind.TOGGLE,
            label=a.label,
            default=a.default,
            caption=a.caption,
            group="演算法",
            group_icon="number.square",
        )
        for a in ALGORITHMS
    ),
    note("結果會存成文字檔，放在第一個檔案旁邊。", group="演算法"),
)


def selected(values: Values) -> list[Algorithm]:
    return [a for a in ALGORITHMS if values.get(a.key)]


def validate(files: list[Path], values: Values, info: Info) -> str | None:  # noqa: ARG001
    return None if selected(values) else NONE_SELECTED


def hash_file(path: Path, algorithms: list[Algorithm], ctx: ToolContext) -> list[str]:
    """依序回傳各演算法的十六進位結果。"""
    hashers = [hashlib.new(a.hashlib_name, usedforsecurity=False) for a in algorithms]
    try:
        handle = open(path, "rb")  # noqa: SIM115 — 下一行用 with 關閉
    except OSError as exc:
        raise errors.unreadable(textcodec.read_error_text(path, exc)) from exc
    total = max(path.stat().st_size, 1)
    done = 0
    with handle:
        while chunk := handle.read(CHUNK):
            ctx.check_cancelled()
            for h in hashers:
                h.update(chunk)
            done += len(chunk)
            ctx.progress(done / total, path.name)
    return [h.hexdigest() for h in hashers]


def report_block(name: str, algorithms: list[Algorithm], digests: list[str]) -> str:
    lines = [f"  {a.label:<9}{d}\n" for a, d in zip(algorithms, digests, strict=True)]
    return f"{name}\n{''.join(lines)}\n"


def run(files: list[Path], values: Values, ctx: ToolContext) -> list[Path]:
    algorithms = selected(values)
    if not algorithms:
        raise errors.QSError(NONE_SELECTED)
    report = ""
    for i, f in enumerate(files):
        if f.is_dir():
            continue
        ctx.check_cancelled()
        digests = hash_file(f, algorithms, ctx.segment(i, len(files)))
        report += report_block(f.name, algorithms, digests)
    tmp = ctx.make_work_dir(ctx.output_directory(files[0])) / "checksum.txt"
    with open(tmp, "xb") as out:
        out.write(report.encode("utf-8"))
    return [ctx.deliver(tmp, ctx.output_name(files[0], "校驗碼", "txt"), files[0])]


TOOL = ToolDefinition(
    id="other.checksum",
    category=OTHER,
    title="計算校驗碼",
    subtitle="算出 SHA-256、SHA-1、MD5，確認下載的檔案是否完整、有沒有被竄改。",
    icon="number.square",
    run=run,
    input_types=("item",),
    input_label="檔案",
    action_title="計算",
    keywords=("hash", "sha256", "md5", "checksum", "雜湊"),
    options=OPTIONS,
    tint=TINTS["slate"],
    badge="SHA",
    validate=validate,
)
