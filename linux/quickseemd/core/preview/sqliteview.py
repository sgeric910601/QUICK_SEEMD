"""SQLite 資料庫（對應原版 QSSQLite）：資料表、欄位結構與前 100 筆資料。

以唯讀＋immutable 開啟（不會改動檔案，也不會產生 -wal／-journal）。
Linux 版補充：整頁的查詢有時間上限（惡意的檢視可能跑不完），超過就中止剩下的查詢。
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
MAGIC = b"SQLite format 3\x00"
_PROGRESS_STEPS = 2000


def is_sqlite(head: bytes) -> bool:
    return len(head) >= 16 and head[:16] == MAGIC


def _connect(path: Path) -> sqlite3.Connection:
    uri = path.resolve().as_uri() + "?mode=ro&immutable=1"
    con = sqlite3.connect(uri, uri=True)
    con.text_factory = lambda b: b.decode("utf-8", "replace")
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


def _cell(v: object) -> str:
    if v is None:
        return '<td class="null">NULL</td>'
    if isinstance(v, bool | int):
        return f'<td class="num">{int(v)}</td>'
    if isinstance(v, float):
        return f'<td class="num">{v!r}</td>'
    if isinstance(v, bytes | bytearray | memoryview):
        return f'<td class="null">BLOB · {fmt.bytes_label(len(bytes(v)))}</td>'
    s = str(v)
    return f"<td>{escape(s[:TEXT_LIMIT] + '…' if len(s) > TEXT_LIMIT else s)}</td>"


def _object_html(con: sqlite3.Connection, index: int, kind: str, name: str, sql: str) -> str:
    q = quote_ident(name)
    counted = _query(con, f"SELECT COUNT(*) FROM {q}")
    count = int(counted[0][0]) if counted else -1
    columns = [
        (str(r[1]), str(r[2] or ""), bool(r[5]))
        for r in _query(con, f"PRAGMA table_info({q})") or []
    ]
    label = "檢視" if kind == "view" else "資料表"
    amount = f"{fmt.number(count)} 筆" if count >= 0 else "?"
    h = [
        f'<div class="tbl-title" id="t{index}"><h2>{escape(name)}</h2>'
        f'<span class="sub">{label} · {amount} · {len(columns)} 欄</span></div>',
        '<div class="scroll-x"><table class="grid"><thead><tr><th class="rn"></th>',
    ]
    for col, ctype, pk in columns:
        key = "🔑 " if pk else ""
        h.append(f'<th>{key}{escape(col)}<span class="ctype">{escape(ctype or "ANY")}</span></th>')
    h.append("</tr></thead><tbody>")
    rows = _query(con, f"SELECT * FROM {q} LIMIT {ROW_LIMIT}") or []
    for n, row in enumerate(rows, start=1):
        h.append(f'<tr><td class="rn">{n}</td>' + "".join(_cell(v) for v in row) + "</tr>")
    if not rows:
        span = max(1, len(columns))
        h.append(
            f'<tr><td class="rn"></td><td class="null" colspan="{span}">（沒有資料）</td></tr>'
        )
    h.append("</tbody></table></div>")
    if count > ROW_LIMIT:
        h.append(f'<div class="schema" style="margin-top:6px">只顯示前 {ROW_LIMIT} 筆</div>')
    if sql:
        schema = highlight.inline(sql, BY_ID["sql"])
        h.append(f'<details class="schema"><summary>結構定義（SQL）</summary>{schema}</details>')
    return "".join(h)


def _page(con: sqlite3.Connection) -> Page | None:
    objects = _query(
        con,
        "SELECT type, name, IFNULL(sql,'') FROM sqlite_master WHERE type IN ('table','view') "
        "AND name NOT LIKE 'sqlite_%' ORDER BY type, name",
    )
    if objects is None:
        return None
    counted = _query(con, "SELECT COUNT(*) FROM sqlite_master WHERE type='index'")
    index_count = int(counted[0][0]) if counted else 0
    tables = sum(1 for o in objects if o[0] == "table")
    page = Page(kind="SQLite 資料庫", size=(1000, 720))
    page.chips = [
        Chip(f"{tables} 個資料表"),
        Chip(f"{len(objects) - tables} 個檢視"),
        Chip(f"{index_count} 個索引"),
    ]
    if not objects:
        page.body = '<div class="empty">資料庫裡沒有資料表</div>'
        return page
    toc = "".join(
        f'<a href="#t{i}">{"👁 " if o[0] == "view" else ""}{escape(str(o[1]))}</a>'
        for i, o in enumerate(objects)
    )
    parts = [f'<nav class="toc">{toc}</nav>']
    for i, (kind, name, sql) in enumerate(objects[:TABLE_LIMIT]):
        parts.append(_object_html(con, i, str(kind), str(name), str(sql)))
    if len(objects) > TABLE_LIMIT:
        page.notices.append(Notice(f"資料表太多，只顯示前 {TABLE_LIMIT} 個的內容"))
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
