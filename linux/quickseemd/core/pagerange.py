"""頁碼範圍（對應原版 PageRange.parse 與 PageRangeField 的說明文字）。"""

from __future__ import annotations

import re

_SEPARATORS = re.compile(r"[,，]")
_SEPARATORS_WITH_ENUM = re.compile(r"[,，、]")
_DASHES = ("-", "–", "~")

PLACEHOLDER = "全部頁面"


def parse(text: str, page_count: int, allow_enum_comma: bool = False) -> list[int] | None:
    """解析「1-3, 5, 8-」這類輸入，回傳 1 起算的頁碼清單（保留輸入順序與重複）。

    空字串＝全部頁面。任何一段不正確或超出範圍就整個無效（回傳 None）。
    allow_enum_comma=True 時也接受頓號「、」當分隔（拆分 PDF 的範圍用）。
    """
    total = max(page_count, 1)
    stripped = text.strip()
    if not stripped:
        return list(range(1, total + 1))
    splitter = _SEPARATORS_WITH_ENUM if allow_enum_comma else _SEPARATORS
    pages: list[int] = []
    for raw in splitter.split(stripped):
        part = raw.strip()
        if not part:
            continue
        rng = _parse_part(part, page_count)
        if rng is None:
            return None
        pages.extend(rng)
    return pages or None


def parse_groups(text: str, page_count: int) -> list[list[int]] | None:
    """拆分 PDF 用：每一段各成一組（「1-3, 4-6, 7-」→ 三組）。"""
    stripped = text.strip()
    if not stripped:
        return None
    groups: list[list[int]] = []
    for raw in _SEPARATORS_WITH_ENUM.split(stripped):
        part = raw.strip()
        if not part:
            continue
        rng = _parse_part(part, page_count)
        if rng is None:
            return None
        groups.append(rng)
    return groups or None


def _parse_part(part: str, page_count: int) -> list[int] | None:
    dash_pos = -1
    for d in _DASHES:
        pos = part.find(d)
        if pos != -1:
            dash_pos = pos
            break
    if dash_pos == -1:
        n = _to_int(part)
        if n is None or not 1 <= n <= page_count:
            return None
        return [n]
    left = part[:dash_pos].strip()
    right = part[dash_pos + 1 :].strip()
    start = 1 if not left else _to_int(left)
    end = page_count if not right else _to_int(right)
    if start is None or end is None:
        return None
    if not (1 <= start <= page_count and 1 <= end <= page_count and start <= end):
        return None
    return list(range(start, end + 1))


def _to_int(s: str) -> int | None:
    s = s.strip()
    if not s.isdigit():
        return None
    try:
        return int(s)
    except ValueError:
        return None


def caption(text: str, page_count: int | None) -> tuple[str, bool]:
    """輸入框下方的說明（文字, 是否為警告）。"""
    if page_count is None:
        return "例如：1-3, 5, 8-", False
    if parse(text, page_count) is None:
        return f"範圍不正確（共 {page_count} 頁）", True
    return f"例如：1-3, 5, 8-　共 {page_count} 頁", False
