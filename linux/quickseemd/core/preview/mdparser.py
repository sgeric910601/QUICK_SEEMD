"""markdown-it 解析器設定（CommonMark＋表格＋刪除線，允許原始 HTML）與防護。

惡意或超大的 Markdown 不能拖垮預覽（Linux 版補充，所有上限都依輸入大小設計）：
- 行內解析：單一段落超過 INLINE_LIMIT 字元，或整份文件累計超過時間預算後，其餘段落當純文字。
  （markdown-it 處理很長的段落時，時間與記憶體會隨段落長度平方成長。）
- 表格：markdown-it 會把每一列補齊到表頭的欄數，寬表頭加上大量短列會讓儲存格數暴增；
  整份文件的表格儲存格總數超過 MAX_TABLE_CELLS 時，後面的表格改當一般段落。
- 參照定義（[a]: 網址）：markdown-it 每個定義都會把後面所有相連的行收集起來再解析，
  連續大量定義會變成平方時間；這裡只讓它往下看 REFERENCE_LINES 行。
"""

from __future__ import annotations

import time
from functools import lru_cache

from markdown_it import MarkdownIt
from markdown_it.rules_block import StateBlock
from markdown_it.rules_block.reference import reference as _reference_rule
from markdown_it.rules_block.table import table as _table_rule
from markdown_it.rules_core import StateCore
from markdown_it.token import Token

INLINE_LIMIT = 10_000
INLINE_BUDGET_SECONDS = 1.2
MAX_TABLE_CELLS = 60_000
REFERENCE_LINES = 10
DEADLINE_KEY = "quickseemd_inline_deadline"
DEGRADED_KEY = "quickseemd_degraded"
CELLS_KEY = "quickseemd_table_cells"
_BAD_SCHEMES = ("javascript:", "vbscript:")
_DATA_IMAGES = ("data:image/gif;", "data:image/png;", "data:image/jpeg;")


def _validate_link(url: str) -> bool:
    """與 markdown-it 預設相同，但允許 file:（本機圖片要用）。"""
    v = url.strip().lower()
    if v.startswith(_BAD_SCHEMES):
        return False
    if v.startswith("data:"):
        return v.startswith(_DATA_IMAGES)
    return True


def _guarded_inline(state: StateCore) -> None:
    """取代 markdown-it 的行內解析步驟：太長的段落或超過時間預算後，直接當純文字。"""
    deadline = state.env.get(DEADLINE_KEY)
    for token in state.tokens:
        if token.type != "inline":
            continue
        children: list[Token] = []
        too_long = len(token.content) > INLINE_LIMIT
        if too_long or (deadline is not None and time.monotonic() > deadline):
            plain = Token("text", "", 0)
            plain.content = token.content
            children.append(plain)
            state.env[DEGRADED_KEY] = True
        else:
            state.md.inline.parse(token.content, state.md, state.env, children)
        token.children = children


def _line(state: StateBlock, line: int) -> str:
    return state.src[state.bMarks[line] + state.tShift[line] : state.eMarks[line]]


def _guarded_table(state: StateBlock, start: int, end: int, silent: bool) -> bool:
    """先估算這張表會產生幾個儲存格，超出整份文件的預算就不當表格。

    原規則的「只檢查」模式只看表頭與分隔列，很便宜；確定是表格後才往下數列數，
    數過的列都會被這張表用掉，所以總成本仍是線性。
    """
    if not _table_rule(state, start, end, True):
        return False
    if silent:
        return True
    columns = max(1, len(_line(state, start + 1).strip().strip("|").split("|")))
    used = int(state.env.get(CELLS_KEY, 0))
    max_rows = (MAX_TABLE_CELLS - used) // columns - 1  # 表頭也算一列
    rows, line = 0, start + 2
    while line < end and rows <= max_rows and _line(state, line).strip():
        rows += 1
        line += 1
    if max_rows < 0 or rows > max_rows:
        state.env[DEGRADED_KEY] = True
        return False
    ok = _table_rule(state, start, end, silent)
    if ok and not silent:
        state.env[CELLS_KEY] = used + columns * (rows + 1)
    return ok


def _guarded_reference(state: StateBlock, start: int, end: int, silent: bool) -> bool:
    """原規則直接讀 state.lineMax，所以暫時把它調低，結束後一定還原。"""
    saved = state.lineMax
    state.lineMax = min(saved, start + REFERENCE_LINES)
    try:
        return _reference_rule(state, start, end, silent)
    finally:
        state.lineMax = saved


@lru_cache(maxsize=1)
def parser() -> MarkdownIt:
    md = MarkdownIt("commonmark", {"html": True}).enable(["table", "strikethrough"])
    md.validateLink = _validate_link  # type: ignore[method-assign]
    md.core.ruler.at("inline", _guarded_inline)
    md.block.ruler.at("table", _guarded_table, {"alt": ["paragraph", "reference"]})
    md.block.ruler.at("reference", _guarded_reference)
    return md


def parse(text: str, deadline: float) -> tuple[list[Token], bool]:
    """解析成詞元；第二個值表示有沒有內容因為防護而改成純文字。"""
    env: dict[str, object] = {DEADLINE_KEY: deadline}
    tokens = parser().parse(text, env)
    return tokens, bool(env.get(DEGRADED_KEY))


def new_deadline() -> float:
    return time.monotonic() + INLINE_BUDGET_SECONDS
