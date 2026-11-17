"""SQLite 資料庫（對應原版 QSSQLite）：資料表、欄位結構與前 100 筆資料。

以唯讀＋immutable 開啟（不會改動檔案，也不會產生 -wal／-journal）。
Linux 版補充（惡意或超大的資料庫不能拖垮預覽，所有上限都在查詢裡套用）：
- 整頁的查詢有時間上限（惡意的檢視可能跑不完），超過就中止剩下的查詢。
- 文字只取前 301 個字、BLOB 只取長度（在 SQLite 裡截斷，不會把整個大值讀進來）；
  單一值超過 MAX_VALUE_BYTES 時 SQLite 直接回報錯誤。
- 每張表最多顯示 MAX_COLUMNS 欄；整頁最多 MAX_CELLS 個儲存格，之後的表格不載入資料。
- 物件清單最多 MAX_OBJECTS 個，名稱過長的物件略過；結構定義最多 MAX_SCHEMA_CHARS 字。
"""

from __future__ import annotations

import logging
import sqlite3
import time
from pathlib import Path

from . import fmt, highlight
from .languages import BY_ID
from .page import Chip, FileInfo, Notice, Page, escape

log = logging.getLogger(__name__)

ROW_LIMIT = 100
TABLE_LIMIT = 40
TEXT_LIMIT = 300
QUERY_BUDGET_SECONDS = 3.0
MAX_COLUMNS = 100
MAX_CELLS = 50_000
MAX_OBJECTS = 2_000
MAX_NAME_CHARS = 1_000
MAX_SCHEMA_CHARS = 100_000
MAX_VALUE_BYTES = 4 * 1024 * 1024
MAGIC = b"SQLite format 3\x00"
SKIPPED_ROW = "（內容太多，已略過）"
SKIPPED_NOTICE = "內容太多，部分資料表沒有載入資料"
_PROGRESS_STEPS = 2000


def is_sqlite(head: bytes) -> bool:
    return len(head) >= 16 and head[:16] == MAGIC


def _connect(path: Path) -> sqlite3.Connection:
    uri = path.resolve().as_uri() + "?mode=ro&immutable=1"
    con = sqlite3.connect(uri, uri=True)
    con.text_factory = lambda b: b.decode("utf-8", "replace")
    con.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, MAX_VALUE_BYTES)
    deadline = time.monotonic() + QUERY_BUDGET_SECONDS
    con.set_progress_handler(lambda: int(time.monotonic() > deadline), _PROGRESS_STEPS)
    return con


def _query(con: sqlite3.Connection, sql: str) -> list[tuple] | None:
    """執行查詢；一筆都還沒拿到就失敗時回傳 None，中途失敗則保留已拿到的列。"""
    rows: list[tuple] = []
    try:
        for row in con.execute(sql):
            rows.append(row)
    except sqlite3.Error:
        log.debug("SQLite 查詢失敗：%s", sql, exc_info=True)
        return rows or None
    return rows


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def _select_list(columns: list[str]) -> str:
    """每欄取（型別, 截斷後的值）：文字取前 301 字、BLOB 只取長度。"""
    parts = []
    for col in columns:
        q = quote_ident(col)
        parts.append(
            f"typeof({q}), CASE typeof({q}) WHEN 'text' THEN substr({q}, 1, {TEXT_LIMIT + 1}) "
            f"WHEN 'blob' THEN length({q}) ELSE {q} END"
        )
    return ", ".join(parts)


def _cell(kind: str, v: object) -> str:
    if kind == "null" or v is None:
        return '<td class="null">NULL</td>'
    if kind == "integer":
        return f'<td class="num">{v}</td>'
    if kind == "real":
        return f'<td class="num">{float(v)!r}</td>'  # type: ignore[arg-type]
    if kind == "blob":
        return f'<td class="null">BLOB · {fmt.bytes_label(int(v))}</td>'  # type: ignore[call-overload]
    s = str(v)
    return f"<td>{escape(s[:TEXT_LIMIT] + '…' if len(s) > TEXT_LIMIT else s)}</td>"


class _Renderer:
    def __init__(self, con: sqlite3.Connection) -> None:
        self.con = con
        self.cells_left = MAX_CELLS
        self.skipped = False

    def columns(self, q: str) -> list[tuple[str, str, bool]]:
        rows = _query(self.con, f"PRAGMA table_info({q})") or []
        return [(str(r[1]), str(r[2] or ""), bool(r[5])) for r in rows]

    def rows_html(self, q: str, shown: list[str]) -> str:
        span = max(1, len(shown))
        if self.cells_left < len(shown) or not shown:
            self.skipped = self.skipped or bool(shown)
            text = SKIPPED_ROW if shown else "（沒有資料）"
            return f'<tr><td class="rn"></td><td class="null" colspan="{span}">{text}</td></tr>'
        limit = min(ROW_LIMIT, self.cells_left // len(shown))
        rows = _query(self.con, f"SELECT {_select_list(shown)} FROM {q} LIMIT {limit}") or []
        self.cells_left -= len(rows) * len(shown)
        out = []
        for n, row in enumerate(rows, start=1):
            cells = "".join(_cell(str(row[k]), row[k + 1]) for k in range(0, len(row), 2))
            out.append(f'<tr><td class="rn">{n}</td>{cells}</tr>')
        if not out:
            out.append(
                f'<tr><td class="rn"></td><td class="null" colspan="{span}">（沒有資料）</td></tr>'
            )
        return "".join(out)

    def object_html(self, index: int, kind: str, name: str, sql: str) -> str:
        q = quote_ident(name)
        counted = _query(self.con, f"SELECT COUNT(*) FROM {q}")
        count = int(counted[0][0]) if counted else -1
        columns = self.columns(q)
        shown = columns[:MAX_COLUMNS]
        label = "檢視" if kind == "view" else "資料表"
        amount = f"{fmt.number(count)} 筆" if count >= 0 else "?"
        h = [
            f'<div class="tbl-title" id="t{index}"><h2>{escape(name)}</h2>'
            f'<span class="sub">{label} · {amount} · {len(columns)} 欄</span></div>',
            '<div class="scroll-x"><table class="grid"><thead><tr><th class="rn"></th>',
        ]
        for col, ctype, pk in shown:
            key = "🔑 " if pk else ""
            h.append(
                f'<th>{key}{escape(col)}<span class="ctype">{escape(ctype or "ANY")}</span></th>'
            )
        h.append("</tr></thead><tbody>")
        h.append(self.rows_html(q, [c[0] for c in shown]))
        h.append("</tbody></table></div>")
        if len(columns) > MAX_COLUMNS:
            h.append(
                f'<div class="schema" style="margin-top:6px">欄位太多，只顯示前 {MAX_COLUMNS} 欄</div>'
            )
        if count > ROW_LIMIT:
            h.append(f'<div class="schema" style="margin-top:6px">只顯示前 {ROW_LIMIT} 筆</div>')
        if sql:
            schema = highlight.inline(sql, BY_ID["sql"])
            h.append(
                f'<details class="schema"><summary>結構定義（SQL）</summary>{schema}</details>'
            )
        return "".join(h)


def _counts(con: sqlite3.Connection) -> tuple[int, int, int] | None:
    rows = _query(con, "SELECT type, COUNT(*) FROM sqlite_master GROUP BY type")
    if rows is None:
        return None
    by_type = {str(t): int(n) for t, n in rows}
    hidden = (
        _query(
            con,
            "SELECT type, COUNT(*) FROM sqlite_master WHERE type IN ('table','view') "
            "AND name LIKE 'sqlite_%' GROUP BY type",
        )
        or []
    )
    skip = {str(t): int(n) for t, n in hidden}
    tables = by_type.get("table", 0) - skip.get("table", 0)
    views = by_type.get("view", 0) - skip.get("view", 0)
    return tables, views, by_type.get("index", 0)


def _page(con: sqlite3.Connection) -> Page | None:
    counts = _counts(con)
    objects = _query(
        con,
        f"SELECT type, name, substr(IFNULL(sql,''), 1, {MAX_SCHEMA_CHARS}) FROM sqlite_master "
        f"WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' "
        f"AND length(name) <= {MAX_NAME_CHARS} ORDER BY type, name LIMIT {MAX_OBJECTS}",
    )
    if counts is None or objects is None:
        return None
    tables, views, indexes = counts
    page = Page(kind="SQLite 資料庫", size=(1000, 720))
    page.chips = [Chip(f"{tables} 個資料表"), Chip(f"{views} 個檢視"), Chip(f"{indexes} 個索引")]
    if not objects:
        page.body = '<div class="empty">資料庫裡沒有資料表</div>'
        return page
    toc = "".join(
        f'<a href="#t{i}">{"👁 " if o[0] == "view" else ""}{escape(str(o[1]))}</a>'
        for i, o in enumerate(objects)
    )
    renderer = _Renderer(con)
    parts = [f'<nav class="toc">{toc}</nav>']
    for i, (kind, name, sql) in enumerate(objects[:TABLE_LIMIT]):
        parts.append(renderer.object_html(i, str(kind), str(name), str(sql)))
    if tables + views > TABLE_LIMIT:
        page.notices.append(Notice(f"資料表太多，只顯示前 {TABLE_LIMIT} 個的內容"))
    if renderer.skipped:
        page.notices.append(Notice(SKIPPED_NOTICE))
    page.body = "".join(parts)
    return page


def render(info: FileInfo) -> Page | None:
    """SQLite 頁面；打不開或不是資料庫時回傳 None。"""
    try:
        con = _connect(info.path)
    except sqlite3.Error:
        log.debug("打不開 SQLite：%s", info.path, exc_info=True)
        return None
    try:
        return _page(con)
    finally:
        con.close()
