"""預覽核心：CSV／TSV、JSON、Jupyter 筆記本、SQLite。"""

from __future__ import annotations

import sys
import base64
import hashlib
import json
import sqlite3
import time
from pathlib import Path

import pytest

from quickseemd.core.preview import jsonview, notebook, sqliteview, tabular
from quickseemd.core.preview.page import FileInfo

# 覆蓋率量測會讓 Python 慢好幾倍；那時放寬時間上限，平常照原本的預算檢查
TIME_SCALE = 8.0 if sys.gettrace() is not None else 1.0


def chips(page) -> list[str]:  # noqa: ANN001
    return [c.text for c in page.chips]


# ------------------------------------------------------------------ CSV


def test_csv_basic_table() -> None:
    page = tabular.render("a,b\n1,x\n2,y\n", FileInfo.virtual("t.csv"), False)
    assert page.kind == "CSV 表格"
    assert page.size == (1000, 700)
    assert chips(page) == ["2 列 × 2 欄", "分隔：逗號"]
    assert page.body == (
        '<div class="grid-wrap"><table class="grid"><thead><tr><th class="rn"></th>'
        '<th>a<span class="ctype">123</span></th><th>b<span class="ctype">abc</span></th>'
        '</tr></thead><tbody><tr><td class="rn">1</td><td class="num">1</td><td>x</td></tr>'
        '<tr><td class="rn">2</td><td class="num">2</td><td>y</td></tr></tbody></table></div>'
    )
    assert page.notices == []


@pytest.mark.parametrize(
    ("text", "name", "delim", "kind"),
    [
        ("a\tb\n1\t2\n", "x.csv", "Tab", "TSV 表格"),
        ("a,b\n1,2\n", "x.tsv", "Tab", "TSV 表格"),
        ("a;b;c\n1;2;3\n", "x.csv", "分號", "CSV 表格"),
        ("a|b\n1|2\n", "x.csv", "直線 |", "CSV 表格"),
        ("single\nvalue\n", "x.csv", "逗號", "CSV 表格"),
        ('"a;b",c\n"d;e",f\n', "x.csv", "逗號", "CSV 表格"),
    ],
)
def test_csv_delimiter_detection(text: str, name: str, delim: str, kind: str) -> None:
    page = tabular.render(text, FileInfo.virtual(name), False)
    assert f"分隔：{delim}" in chips(page)
    assert page.kind == kind


def test_csv_quoting_rules() -> None:
    rows, total = tabular.parse('"Lin, Amy",新竹\n"多行\r\n備註",台中\n"a""b",c\nx"y",z', ",", 10)
    assert rows == [["Lin, Amy", "新竹"], ["多行\r\n備註", "台中"], ['a"b', "c"], ['x"y"', "z"]]
    assert total == 4


def test_csv_row_limit_and_truncation_notice() -> None:
    text = "h\n" + "".join(f"{i}\n" for i in range(2500))
    page = tabular.render(text, FileInfo.virtual("big.csv"), False)
    assert chips(page)[0] == "2,500 列 × 1 欄"
    assert [n.text for n in page.notices] == ["只顯示前 2,000 列"]
    assert page.body.count("<tr>") == 2001
    small = tabular.render("h\n1\n", FileInfo.virtual("x.csv"), True)
    assert [n.text for n in small.notices] == ["只顯示前 1 列（檔案較大，僅讀取開頭部分）"]


def test_csv_empty_and_long_and_hostile_cells() -> None:
    empty = tabular.render("", FileInfo.virtual("e.csv"), False)
    assert empty.body == '<div class="empty">空白的表格</div>'
    assert empty.chips == []
    long = tabular.render("h\n" + "x" * 600 + "\n", FileInfo.virtual("l.csv"), False)
    assert "x" * 500 + "…</td>" in long.body
    assert "x" * 501 not in long.body
    hostile = tabular.render('<h1>,"</table><script>"\n1,2\n', FileInfo.virtual("h.csv"), False)
    assert "<script" not in hostile.body
    assert "&lt;/table&gt;&lt;script&gt;" in hostile.body


def test_csv_numeric_columns() -> None:
    text = 'n,p,m\n"1,234",50%,a\n2,1e3,b\n-3.5,.5,c\n+4,7%,d\n,inf,5\n'
    page = tabular.render(text, FileInfo.virtual("n.csv"), False)
    assert page.body.count('<span class="ctype">123</span>') == 2
    assert '<th>m<span class="ctype">abc</span></th>' in page.body
    assert not tabular.is_number("1_000")
    assert tabular.is_number("1.")
    assert tabular.is_number("0x1p3")
    assert not tabular.is_number("")


def test_csv_ragged_rows() -> None:
    page = tabular.render("a\n1,2,3\n", FileInfo.virtual("r.csv"), False)
    assert chips(page)[0] == "1 列 × 3 欄"
    assert "<th><span" in page.body


def test_csv_hostile_quotes_are_fast() -> None:
    text = ('"' * 7990 + "\n") * 262 + ("a," * 3995 + "\n") * 262
    start = time.perf_counter()
    tabular.render(text, FileInfo.virtual("q.csv"), False)
    assert time.perf_counter() - start < 1.5 * TIME_SCALE


# ------------------------------------------------------------------ JSON


def test_json_inspect() -> None:
    assert jsonview.inspect('{"a": 1, "b": 2}') == ("物件 · 2 個鍵", None)
    assert jsonview.inspect("[1, 2, 3]") == ("陣列 · 3 個項目", None)
    assert jsonview.inspect("42") == ("單一值", None)
    summary, error = jsonview.inspect('{\n  "a": 1,\n  "b": [1, 2,\n}\n')
    assert summary is None
    assert error is not None and error.startswith("第 4 行第 1 欄：")
    assert jsonview.inspect("NaN")[1] is not None
    assert jsonview.inspect("[" * 100000)[1] is not None


def test_json_minified_and_pretty_print() -> None:
    assert not jsonview.looks_minified('{"a": 1}')
    assert jsonview.looks_minified('{"k": "' + "x" * 300 + '"}')
    assert not jsonview.looks_minified("{\n" + '  "k": 1,\n' * 30 + "}")
    assert jsonview.pretty_print('{"a":[1,2],"b":{},"c":"x,y:{z}","d":[ ]}') == (
        '{\n  "a": [\n    1,\n    2\n  ],\n  "b": {},\n  "c": "x,y:{z}",\n  "d": []\n}'
    )
    assert jsonview.pretty_print('{"s":"a\\"b"}') == '{\n  "s": "a\\"b"\n}'
    assert jsonview.pretty_print('{"open') == '{\n  "open'


def test_json_pretty_print_output_is_bounded() -> None:
    start = time.perf_counter()
    assert jsonview.pretty_print("[" * 100_000) is None  # 縮排會平方成長
    assert jsonview.pretty_print('{"a":[1]}', limit=5) is None
    assert time.perf_counter() - start < 1.0 * TIME_SCALE


def test_json_pretty_print_is_fast() -> None:
    blob = json.dumps([{"id": i, "v": [i, "x" * 5, None]} for i in range(60000)])
    start = time.perf_counter()
    jsonview.pretty_print(blob)
    assert time.perf_counter() - start < 1.0 * TIME_SCALE


# ------------------------------------------------------------------ 筆記本


def _write_nb(tmp_path: Path, cells: list, meta: dict | None = None) -> tuple[FileInfo, str]:
    f = tmp_path / "n.ipynb"
    f.write_text(json.dumps({"cells": cells, "metadata": meta or {}}), "utf-8")
    return FileInfo.of(f), f.read_text("utf-8")


def test_notebook_sample(samples: Path) -> None:
    f = samples / "analysis.ipynb"
    page = notebook.render(FileInfo.of(f), f.read_text("utf-8"))
    assert page.kind == "Jupyter Notebook"
    assert page.size == (980, 800)
    assert chips(page) == ["Python", "4 個儲存格（3 個程式碼）"]
    body = page.body
    assert body.startswith(
        '<div class="nb"><div class="cell"><div class="prompt"></div><div class="md">'
    )
    assert (
        '<div class="prompt in">In [1]:</div><div class="cellsrc"><div class="code nogutter">'
        in body
    )
    assert "<pre>3.141592653589793\n</pre>" in body
    assert (
        '<div class="prompt">Out [2]:</div><div class="out"><pre>{&#x27;a&#x27;: 1}</pre>'
        not in body
    )
    assert '<div class="prompt">Out [2]:</div><div class="out"><pre>{\'a\': 1}</pre>' in body
    assert '<pre class="err">ZeroDivisionError: division by zero</pre>' in body
    assert "\x1b" not in body


def test_notebook_outputs(tmp_path: Path) -> None:
    png = base64.b64encode(b"\x89PNG fake").decode()
    cells = [
        {
            "cell_type": "code",
            "source": ["x"],
            "outputs": [
                {"output_type": "display_data", "data": {"image/png": png}},
                {"output_type": "stream", "name": "stderr", "text": "warn <b>"},
                {
                    "output_type": "display_data",
                    "data": {"text/html": "<b onclick='x'>t</b><script>x</script>"},
                },
                {"output_type": "display_data", "data": {"text/markdown": "**m**"}},
                {"output_type": "error", "ename": "E", "evalue": "v", "traceback": []},
            ],
        },
        {"cell_type": "raw", "source": "<raw>"},
    ]
    page = notebook.render(*_write_nb(tmp_path, cells, {"kernelspec": {"language": "R"}}))
    assert chips(page) == ["R", "2 個儲存格（1 個程式碼）"]
    assert '<div class="prompt in">In [ ]:</div>' in page.body
    assert '<img src="cid:nb-img-1" alt="">' in page.body
    assert page.attachments["nb-img-1"].data == b"\x89PNG fake"
    assert page.attachments["nb-img-1"].mime == "image/png"
    assert '<pre class="err">warn &lt;b&gt;</pre>' in page.body
    assert "<b>t</b>" in page.body and "<script" not in page.body
    assert '<div class="md"><p><strong>m</strong></p>\n</div>' in page.body
    assert '<pre class="err">E: v</pre>' in page.body
    assert "<pre>&lt;raw&gt;</pre>" in page.body


def test_notebook_rejects_non_notebooks(tmp_path: Path) -> None:
    info = FileInfo.virtual("x.ipynb")
    assert notebook.render(info, "[1, 2]") is None
    assert notebook.render(info, '{"cells": 3}') is None
    assert notebook.render(info, '{"cells": [1]}') is None
    assert notebook.render(info, "not json") is None


def test_notebook_markdown_images_get_distinct_names(tmp_path: Path) -> None:
    (tmp_path / "a.png").write_bytes(b"A")
    (tmp_path / "b.png").write_bytes(b"B")
    cells = [
        {"cell_type": "markdown", "source": "![a](a.png)"},
        {"cell_type": "markdown", "source": "![b](b.png)"},
    ]
    page = notebook.render(*_write_nb(tmp_path, cells))
    assert page.attachments["md-img-1"].data == b"A"
    assert page.attachments["md-img-2"].data == b"B"


# ------------------------------------------------------------------ SQLite


def _make_db(path: Path, tables: int = 1, rows: int = 3) -> Path:
    con = sqlite3.connect(path)
    con.execute("CREATE TABLE items(id INTEGER PRIMARY KEY, name TEXT, price REAL, pic BLOB, x)")
    con.executemany(
        "INSERT INTO items(name, price, pic, x) VALUES (?, ?, ?, ?)",
        [(f"<n{i}>", i + 0.5, b"\x00" * 2048 if i == 0 else None, None) for i in range(rows)],
    )
    con.execute("CREATE VIEW cheap AS SELECT * FROM items WHERE price < 2")
    con.execute("CREATE INDEX idx_name ON items(name)")
    for t in range(tables - 1):
        con.execute(f'CREATE TABLE "t{t:03d}"(v TEXT)')
    con.commit()
    con.close()
    return path


def test_sqlite_page(tmp_path: Path) -> None:
    db = _make_db(tmp_path / "shop.db")
    before = hashlib.sha256(db.read_bytes()).hexdigest()
    mtime = db.stat().st_mtime_ns
    page = sqliteview.render(FileInfo.of(db))
    assert page.kind == "SQLite 資料庫"
    assert page.size == (1000, 720)
    assert chips(page) == ["1 個資料表", "1 個檢視", "1 個索引"]
    body = page.body
    assert body.startswith(
        '<nav class="toc"><a href="#t0">items</a><a href="#t1">👁 cheap</a></nav>'
    )
    assert (
        '<div class="tbl-title" id="t0"><h2>items</h2><span class="sub">資料表 · 3 筆 · 5 欄</span></div>'
    ) in body
    assert '<th>🔑 id<span class="ctype">INTEGER</span></th>' in body
    assert '<th>x<span class="ctype">ANY</span></th>' in body
    assert '<td class="null">BLOB · 2 KB</td>' in body
    assert '<td class="num">0.5</td>' in body
    assert "<td>&lt;n0&gt;</td>" in body
    assert '<td class="null">NULL</td>' in body
    assert (
        '<details class="schema"><summary>結構定義（SQL）</summary><span class="k">CREATE</span>'
        in body
    )
    assert '<span class="sub">檢視 · 2 筆 · 5 欄</span>' in body
    assert hashlib.sha256(db.read_bytes()).hexdigest() == before
    assert db.stat().st_mtime_ns == mtime
    assert not (tmp_path / "shop.db-wal").exists()


def test_sqlite_limits(tmp_path: Path) -> None:
    db = _make_db(tmp_path / "many.db", tables=45, rows=150)
    page = sqliteview.render(FileInfo.of(db))
    assert chips(page) == ["45 個資料表", "1 個檢視", "1 個索引"]
    assert [n.text for n in page.notices] == ["資料表太多，只顯示前 40 個的內容"]
    assert page.body.count('class="tbl-title"') == 40
    assert '<div class="schema" style="margin-top:6px">只顯示前 100 筆</div>' in page.body
    assert '<td class="null" colspan="1">（沒有資料）</td>' in page.body


def test_sqlite_empty_and_invalid(tmp_path: Path) -> None:
    empty = tmp_path / "empty.sqlite"
    sqlite3.connect(empty).execute("PRAGMA user_version = 1").connection.close()
    page = sqliteview.render(FileInfo.of(empty))
    assert page.body == '<div class="empty">資料庫裡沒有資料表</div>'
    bad = tmp_path / "bad.sqlite"
    bad.write_bytes(b"SQLite format 3\x00" + b"\xff" * 200)
    assert sqliteview.render(FileInfo.of(bad)) is None
    assert sqliteview.is_sqlite(b"SQLite format 3\x00rest")
    assert not sqliteview.is_sqlite(b"SQLite format 3")


def test_sqlite_sample(samples: Path) -> None:
    page = sqliteview.render(FileInfo.of(samples / "shop.sqlite"))
    assert chips(page) == ["2 個資料表", "1 個檢視", "1 個索引"]


def test_sqlite_runaway_view_is_aborted(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sqliteview, "QUERY_BUDGET_SECONDS", 0.5)
    db = tmp_path / "loop.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE t(x)")
    con.execute(
        "CREATE VIEW forever AS WITH RECURSIVE c(n) AS "
        "(SELECT 1 UNION ALL SELECT n + 1 FROM c) SELECT count(*) AS k FROM c"
    )
    con.commit()
    con.close()
    start = time.perf_counter()
    page = sqliteview.render(FileInfo.of(db))
    assert time.perf_counter() - start < 2.5 * TIME_SCALE
    assert page is not None
    assert '<span class="sub">檢視 · ? · 1 欄</span>' in page.body


def test_csv_column_cap() -> None:
    text = "," * 100_000 + "\n" + "1\n" * 2000
    page = tabular.render(text, FileInfo.virtual("wide.csv"), False)
    assert chips(page)[0] == "2,000 列 × 100001 欄"  # 原版欄數不加千分位
    assert [n.text for n in page.notices] == ["欄位太多，只顯示前 500 欄"]
    assert page.body.count("<th>") == 500
    assert len(page.body) < 30_000_000


def test_sqlite_large_values_are_truncated_in_the_query(tmp_path: Path) -> None:
    db = tmp_path / "big.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE t(a TEXT, b BLOB)")
    con.execute("INSERT INTO t VALUES (?, ?)", ("字" * 1_000_000, b"\x00" * 3_000_000))
    con.commit()
    con.close()
    page = sqliteview.render(FileInfo.of(db))
    assert "<td>" + "字" * 300 + "…</td>" in page.body
    assert '<td class="null">BLOB · 3 MB</td>' in page.body
    assert len(page.body) < 100_000
    huge = tmp_path / "huge.db"  # 超過單一值上限：SQLite 直接回報錯誤，不會讀進來
    con = sqlite3.connect(huge)
    con.execute("CREATE TABLE t(a)")
    con.execute("INSERT INTO t VALUES (?)", (b"\x01" * (sqliteview.MAX_VALUE_BYTES + 10),))
    con.commit()
    con.close()
    page = sqliteview.render(FileInfo.of(huge))
    assert page is not None and len(page.body) < 100_000


def test_sqlite_wide_tables_and_cell_budget(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    db = tmp_path / "wide.db"
    con = sqlite3.connect(db)
    cols = ", ".join(f"c{i}" for i in range(150))
    con.execute(f"CREATE TABLE wide({cols})")
    con.execute(f"INSERT INTO wide VALUES ({', '.join('1' for _ in range(150))})")
    con.execute("CREATE TABLE z(v)")
    con.execute("INSERT INTO z VALUES (1)")
    con.commit()
    con.close()
    page = sqliteview.render(FileInfo.of(db))
    assert '<span class="sub">資料表 · 1 筆 · 150 欄</span>' in page.body
    assert page.body.count('<span class="ctype">ANY</span>') == 100 + 1
    assert "欄位太多，只顯示前 100 欄" in page.body
    monkeypatch.setattr(sqliteview, "MAX_CELLS", 50)
    page = sqliteview.render(FileInfo.of(db))
    assert "（內容太多，已略過）" in page.body
    assert [n.text for n in page.notices] == ["內容太多，部分資料表沒有載入資料"]


def test_sqlite_random_typed_view_cannot_inject_html(tmp_path: Path) -> None:
    db = tmp_path / "evil.db"
    con = sqlite3.connect(db)
    con.execute("CREATE TABLE n(i)")
    con.executemany("INSERT INTO n VALUES (?)", [(i,) for i in range(100)])
    con.execute(
        "CREATE VIEW v AS SELECT CASE WHEN random() % 2 THEN 1 "
        "ELSE '<img src=x onerror=alert(1)>' END AS a, "
        "CASE WHEN random() % 2 THEN 2.5 ELSE '<script>x()</script>' END AS b FROM n"
    )
    con.commit()
    con.close()
    for _ in range(3):
        body = sqliteview.render(FileInfo.of(db)).body
        assert "<img" not in body and "<script" not in body
        assert "&lt;img src=x onerror=alert(1)&gt;" in body


def test_sqlite_cell_html_escapes_every_type() -> None:
    assert sqliteview.cell_html(None) == '<td class="null">NULL</td>'
    assert sqliteview.cell_html(7) == '<td class="num">7</td>'
    assert sqliteview.cell_html(True) == '<td class="num">1</td>'
    assert sqliteview.cell_html(0.5) == '<td class="num">0.5</td>'
    assert sqliteview.cell_html(b"\x00" * 2048) == '<td class="null">BLOB · 2 KB</td>'
    assert sqliteview.cell_html("<b>") == "<td>&lt;b&gt;</td>"
    assert sqliteview._cell_value("<img src=x>") == "<td>&lt;img src=x&gt;</td>"
