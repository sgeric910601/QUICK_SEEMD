"""解析 `quickseemd open [--tool ID] [檔案…]`（對應原版 quickseemd://open?tool=…&f=…）。

- 檔案可以是相對路徑（以呼叫端的工作資料夾為準）或 file:// 網址；其他網址略過。
- 不存在的檔案、含 NUL 的路徑直接略過（原版行為）；重複的只留第一個。
- `--` 之後一律當成檔案（檔名可以是 --開頭）。
"""

from __future__ import annotations

import logging
import os
import re
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from urllib.parse import unquote, urlparse

LOG = logging.getLogger(__name__)
_TOOL_ID = re.compile(r"^[a-z0-9][a-z0-9.\-_]{0,80}$")
MAX_FILES = 10000


@dataclass(frozen=True)
class OpenRequest:
    tool_id: str | None = None
    files: tuple[Path, ...] = ()


def _to_path(raw: str, cwd: Path) -> Path | None:
    if "\0" in raw or not raw:
        return None
    if "://" in raw:
        parsed = urlparse(raw)
        if parsed.scheme != "file":
            return None
        raw = unquote(parsed.path)
        if not raw:
            return None
    p = Path(raw)
    if not p.is_absolute():
        p = cwd / p
    return Path(os.path.normpath(p))


def _valid_tool_id(raw: str) -> str | None:
    return raw if _TOOL_ID.match(raw) else None


def _split_args(args: list[str]) -> tuple[str | None, list[str]]:
    """把參數分成（工具代碼, 檔案字串清單）。"""
    tool_id: str | None = None
    raw_files: list[str] = []
    i = 0
    while i < len(args):
        a = args[i]
        if a == "--":
            raw_files.extend(args[i + 1 :])
            break
        if a == "--tool":
            if i + 1 < len(args):
                tool_id = _valid_tool_id(args[i + 1])
            i += 2
            continue
        if a.startswith("--tool="):
            tool_id = _valid_tool_id(a.split("=", 1)[1])
        elif a.startswith("-") and len(a) > 1:
            LOG.info("略過不認得的參數：%s", a)
        else:
            raw_files.append(a)
        i += 1
    return tool_id, raw_files


def parse_open_args(args: Sequence[str], cwd: str | os.PathLike[str] | None = None) -> OpenRequest:
    """args 是程式名稱之後的參數，例如 []、["open"]、["open", "--tool", "pdf.merge", "a.pdf"]。"""
    items = list(args)
    if items and items[0] == "open":
        items = items[1:]
    tool_id, raw_files = _split_args(items)
    base = Path(cwd) if cwd else Path.cwd()
    out: list[Path] = []
    seen: set[Path] = set()
    for raw in raw_files[:MAX_FILES]:
        p = _to_path(raw, base)
        if p is None or p in seen or not p.exists():
            continue
        seen.add(p)
        out.append(p)
    return OpenRequest(tool_id, tuple(out))
