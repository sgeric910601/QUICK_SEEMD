"""完成頁的文字（對應原版 ToolResultView 的規則）。"""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

from .filetypes import file_ref
from .model import ToolDefinition
from .naming import split_name

_PROGRESS_PREFIXES = ("處理中", "準備", "正在", "讀取", "寫入", "掃描：")
MAX_CARDS = 12
MAX_NEXT_TOOLS = 8
MAX_OPEN_BUTTON = 5


def format_size(n: int) -> str:
    """檔案大小（十進位，與原版 ByteCountFormatter.file 一樣：1 KB = 1000 bytes）。"""
    if n < 1000:
        return f"{n} 位元組" if n != 0 else "零 KB"
    units = ("KB", "MB", "GB", "TB")
    value = float(n)
    for i, unit in enumerate(units):
        value /= 1000.0
        if value < 1000 or i == len(units) - 1:
            if unit == "KB":
                return f"{value:.0f} KB"
            text = f"{value:.1f}" if unit == "MB" else f"{value:.2f}"
            text = text.rstrip("0").rstrip(".")
            return f"{text} {unit}"
    return f"{n} 位元組"


def size_of(path: Path) -> int:
    p = Path(path)
    if p.is_dir():
        total = 0
        for dirpath, _dirs, files in os.walk(p):
            for f in files:
                try:
                    total += os.lstat(os.path.join(dirpath, f)).st_size
                except OSError:
                    pass
        return total
    try:
        return p.stat().st_size
    except OSError:
        return 0


def summary_text(last_message: str | None, inputs: Sequence[Path]) -> str | None:
    """最後一則進度訊息當摘要；空的、像進度的、或等於輸入檔名的不算。"""
    if not last_message:
        return None
    msg = last_message.strip()
    if not msg or msg.startswith(_PROGRESS_PREFIXES):
        return None
    if any(msg == Path(p).name for p in inputs):
        return None
    return msg


def headline(outputs: Sequence[Path]) -> str:
    if not outputs:
        return "完成！"
    if len(outputs) == 1:
        return "檔案已經準備好了！"
    return f"{len(outputs)} 個檔案已經準備好了！"


def detail_lines(
    inputs: Sequence[Path], outputs: Sequence[Path], summary: str | None
) -> list[tuple[str, str]]:
    """完成頁標題下的說明行：[(文字, 樣式)]，樣式為 "good"／"plain"。"""
    lines: list[tuple[str, str]] = []
    if summary:
        good = ("省下" in summary) or ("減少" in summary)
        lines.append((summary, "good" if good else "plain"))
    else:
        in_total = sum(size_of(p) for p in inputs)
        out_total = sum(size_of(p) for p in outputs)
        in_exts = {split_name(Path(p).name)[1].lower() for p in inputs}
        out_exts = {split_name(Path(p).name)[1].lower() for p in outputs}
        if (
            outputs
            and len(outputs) == len(inputs)
            and in_total > 0
            and out_total < in_total
            and in_exts == out_exts
        ):
            pct = int((1 - out_total / in_total) * 100)
            lines.append(
                (f"從 {format_size(in_total)} 縮小到 {format_size(out_total)}，省下 {pct}%", "good")
            )
        elif outputs:
            lines.append((f"共 {format_size(out_total)}", "plain"))
    if outputs:
        folder = Path(outputs[0]).parent.name or str(Path(outputs[0]).parent)
        lines.append((f"儲存在「{folder}」資料夾", "plain"))
    return lines


def next_tools(
    current: ToolDefinition, outputs: Sequence[Path], tools: Sequence[ToolDefinition]
) -> list[ToolDefinition]:
    """「接著可以…」：接受這些輸出的其他工具（不含目前工具與整頁自訂的工具），最多 8 個。"""
    existing = [p for p in outputs if Path(p).exists()]
    if not existing:
        return []
    refs = [file_ref(p) for p in existing]
    out = [t for t in tools if t.id != current.id and t.custom_page is None and t.accepts_all(refs)]
    return out[:MAX_NEXT_TOOLS]
