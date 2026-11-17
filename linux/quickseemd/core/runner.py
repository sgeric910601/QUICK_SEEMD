"""執行一個工作（工具、一鍵動作、萬用轉換）。

工作程序（quickseemd.worker）與命令列 quickseemd run 都走這裡；介面程序不直接呼叫，
一律透過 jobs.WorkerJob 開新程序執行，才能真的取消、也不會被工具當掉拖垮介面。
"""

from __future__ import annotations

import sys
import threading
import time
import traceback
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from . import errors, quickactions, registry
from .model import Reporter, RunResult, ToolContext

KINDS = ("tool", "action", "convert", "merge-pdf")


@dataclass
class JobSpec:
    """交給工作程序的工作描述（可轉成 JSON）。

    kind = "tool"      id 是工具代碼，options 覆蓋預設選項值
    kind = "action"    id 是一鍵動作代碼（右鍵、輪盤），一律用預設值＋動作的 preset
    kind = "convert"   id 是萬用轉換的格式代碼，options 是轉換選項
    kind = "merge-pdf" 把 files（圖片／PDF）依序合成一份 PDF，options 是轉換選項
    output_folder 為 None 時輸出到原始檔案旁邊。
    """

    kind: str
    id: str
    files: list[str]
    options: dict[str, Any] = field(default_factory=dict)
    output_folder: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> JobSpec:
        kind = str(raw.get("kind", ""))
        if kind not in KINDS:
            raise ValueError(f"未知的工作種類：{kind}")
        files = raw.get("files") or []
        if not isinstance(files, list) or not all(isinstance(f, str) for f in files):
            raise ValueError("files 必須是字串清單")
        options = raw.get("options") or {}
        if not isinstance(options, dict):
            raise ValueError("options 必須是物件")
        out = raw.get("output_folder")
        return cls(
            kind, str(raw.get("id", "")), files, options, out if isinstance(out, str) else None
        )


def _existing(files: list[str]) -> list[Path]:
    return [Path(f) for f in files if Path(f).exists()]


def _run_tool(spec: JobSpec, ctx: ToolContext) -> tuple[list[Path], str]:
    files = _existing(spec.files)
    if spec.kind == "action":
        act = quickactions.action(spec.id)
        if act is None:
            raise errors.QSError(f"這個版本的 QUICK_SEEMD 不認得動作「{spec.id}」。")
        tool = registry.tool(act.tool_id)
        if tool is None:
            raise errors.QSError(f"這個版本的 QUICK_SEEMD 不認得動作「{spec.id}」。")
        values = tool.values_for_preset(act.id)
        if not files:
            raise errors.file_not_found(Path(spec.files[0]).name if spec.files else "")
        return tool.run(files, values, ctx), act.id
    tool = registry.tool(spec.id)
    if tool is None:
        raise errors.QSError(f"找不到工具或動作：{spec.id}")
    values = tool.defaults()
    values.update(spec.options)
    reason = tool.check_files(files)
    if reason is None and tool.validate is not None:
        info = tool.inspect(files) if tool.inspect is not None else {}
        reason = tool.validate(files, values, info)
    if reason:
        raise errors.QSError(reason)
    return tool.run(files, values, ctx), tool.id


def _run_convert(spec: JobSpec, ctx: ToolContext) -> list[Path]:
    from . import converter  # noqa: PLC0415 — 轉換器較大，用到才載入

    files = _existing(spec.files)
    if not files:
        raise errors.file_not_found(Path(spec.files[0]).name if spec.files else "")
    if spec.kind == "merge-pdf":
        return [converter.merge_to_pdf(files, ctx, spec.options)]
    outputs: list[Path] = []
    n = len(files)
    for i, f in enumerate(files):
        ctx.check_cancelled()
        sub = ctx.segment(i, n)
        sub.progress(0, f"處理中：{f.name}")
        outputs.extend(converter.convert(f, spec.id, sub, spec.options))
        sub.progress(1)
    return outputs


def run_spec(
    spec: JobSpec, reporter: Reporter | None = None, cancel: threading.Event | None = None
) -> RunResult:
    started = time.monotonic()
    out_folder = Path(spec.output_folder) if spec.output_folder else None
    ctx = ToolContext(out_folder, reporter, cancel or threading.Event())
    result = RunResult(action=spec.id)
    try:
        if spec.kind in ("tool", "action"):
            outputs, result.action = _run_tool(spec, ctx)
        else:
            outputs = _run_convert(spec, ctx)
        result.outputs = [Path(p) for p in outputs]
        result.skipped = ctx.skipped
        if not result.outputs and result.skipped:
            result.error = result.skipped[0]
    except errors.QSError as exc:
        result.error = str(exc)
    except Exception as exc:  # noqa: BLE001 — 任何意外都要回報成中文訊息，不能讓工作程序無聲掛掉
        traceback.print_exc(file=sys.stderr)
        result.error = f"發生未預期的錯誤：{exc}"
    finally:
        ctx.cleanup()
    result.summary = ctx.last_message
    result.clipboard = ctx.clipboard
    result.seconds = time.monotonic() - started
    return result
