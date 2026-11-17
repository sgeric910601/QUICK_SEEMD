"""預覽核心：偵測順序（Samples 全部檔案）、分類、資料夾／特殊檔、字型樣張、惡意內容。"""

from __future__ import annotations

import os
import re
import shutil
import socket
import threading
from pathlib import Path

import pytest

from quickseemd.core import preview
from quickseemd.core.preview import engine

SKELETON = (
    '<!DOCTYPE html><html><head><meta charset="utf-8">'
    '<meta name="generator" content="QUICK_SEEMD"><style>'
)

EXPECTED_KINDS = {
    "analysis.ipynb": "Jupyter Notebook",
    "app.log": "記錄檔",
    "app.log.gz": "gzip 壓縮檔",
    "app.py": "Python",
    "archive.tar.gz": "TAR.GZ 壓縮檔",
    "archive.tar.xz": "TAR.XZ 壓縮檔",
    "archive.zip": "ZIP 壓縮檔",
    "big5.txt": "純文字",
    "broken.json": "JSON",
    "Cargo.toml": "TOML",
    "change.diff": "Diff / Patch",
    "config.yaml": "YAML",
    "data.csv": "CSV 表格",
    "data.tsv": "TSV 表格",
    "deploy": "Shell",
    "Dockerfile": "Dockerfile",
    "empty.txt": "純文字",
    "hello-binary": "Mach-O 通用二進位（Universal）",
    "hello.swift": "Swift",
    "icon.png": "PNG 圖片",
    "index.tsx": "TypeScript",
    "main.rs": "Rust",
    "Makefile": "Makefile",
    "minified.json": "JSON",
    "query.sql": "SQL",
    "random.dat": "二進位檔",
    "README.md": "Markdown",
    "sample.env": "環境變數 (.env)",
    "server.go": "Go",
    "settings.ini": "INI / 設定檔",
    "settings.plist": "屬性列表 (plist)",
    "shop.sqlite": "SQLite 資料庫",
    "styles.css": "CSS",
    "utf16.txt": "純文字",
}


def chips(html: str) -> list[str]:
    return re.findall(r'<span class="chip ?[a-z]*">([^<]*)</span>', html)


def notices(html: str) -> list[str]:
    return re.findall(r'<div class="notice[^"]*">([^<]*)</div>', html)


def test_every_sample_is_covered(samples: Path) -> None:
    assert sorted(EXPECTED_KINDS) == sorted(p.name for p in samples.iterdir())


@pytest.mark.parametrize("name", sorted(EXPECTED_KINDS))
def test_sample_pages(samples: Path, name: str) -> None:
    doc = preview.render(samples / name)
    assert isinstance(doc, preview.Document)
    assert doc.kind_label == EXPECTED_KINDS[name]
    assert doc.html.startswith(SKELETON)
    assert doc.html.endswith("</body></html>")
    assert "<script" not in doc.html.lower()
    assert '<header class="qs-bar">' in doc.html
    size = (samples / name).stat().st_size
    assert re.search(
        r'<span class="spacer"></span><span class="chip">[^<]+</span></header>', doc.html
    )
    for ref in set(re.findall(r"cid:([A-Za-z0-9._-]+)", doc.html)):
        assert ref in doc.attachments, ref
    for att_name, att in doc.attachments.items():
        assert isinstance(att, preview.Attachment) and att.name == att_name and att.mime
    assert size >= 0


def test_sample_details(samples: Path) -> None:
    html = engine.render(samples / "minified.json").html
    assert chips(html)[:3] == ["✓ 有效 JSON", "物件 · 5 個鍵", "已自動排版"]
    html = engine.render(samples / "broken.json").html
    assert "JSON 格式錯誤" in chips(html)
    assert notices(html)[0].startswith("第 ")
    html = engine.render(samples / "empty.txt").html
    assert chips(html)[-4:] == ["0 行", "ASCII", "LF", "0 位元組"]
    assert notices(html) == ["這是空白檔案"]
    assert chips(engine.render(samples / "utf16.txt").html)[1] == "UTF-16 LE"
    assert chips(engine.render(samples / "big5.txt").html)[1] == "繁體中文（Big 5）"
    html = engine.render(samples / "README.md").html
    assert chips(html)[0].startswith("約 ") and chips(html)[1] == "UTF-8"
    assert '<article class="md"><div class="front"><table>' in html
    assert '<input type="checkbox" class="task" disabled checked>' in html
    assert '<img src="cid:md-img-1" alt="圖示">' in html
    html = engine.render(samples / "data.csv").html
    assert chips(html)[1:3] == ["分隔：逗號", "UTF-8"]
    html = engine.render(samples / "app.log").html
    assert '<span class="le">ERROR</span>' in html


def test_code_page_sizes(samples: Path) -> None:
    assert engine.render(samples / "app.py").preferred_size == (920, 700)
    assert engine.render(samples / "app.log").preferred_size == (820, 680)
    assert engine.render(samples / "README.md").preferred_size == (900, 780)
    assert engine.render(samples / "data.csv").preferred_size == (1000, 700)


def test_large_text_is_truncated(tmp_path: Path) -> None:
    f = tmp_path / "big.txt"
    f.write_bytes(b"line\n" * (2_621_440 // 5))
    html = engine.render(f).html
    assert notices(html) == ["檔案很大（2.6 MB），只顯示前 2.1 MB"]
    j = tmp_path / "big.json"
    j.write_bytes(b"[" + b"1," * 1_200_000 + b"1]")
    html = engine.render(j).html
    assert "有效 JSON" not in html and "JSON 格式錯誤" not in html


def test_hostile_text_is_escaped(tmp_path: Path) -> None:
    f = tmp_path / "x.txt"
    f.write_text('</style><script>alert(1)</script><img src=x onerror="y">', "utf-8")
    html = engine.render(f).html
    assert "<script" not in html.lower() and "<img src=x" not in html
    assert "&lt;/style&gt;&lt;script&gt;" in html
    md = tmp_path / "x.md"
    md.write_text('# t\n\n<script>alert(1)</script>\n\n<img src=x onerror="y">\n', "utf-8")
    html = engine.render(md).html
    assert "<script" not in html.lower() and "onerror" not in html


def test_zip_with_other_extension(tmp_path: Path) -> None:
    import zipfile

    f = tmp_path / "doc.docx"
    with zipfile.ZipFile(f, "w") as z:
        z.writestr("word/document.xml", "<w/>")
    html = engine.render(f).html
    assert "DOCX 使用 ZIP 格式" in chips(html)


def test_markdown_degraded_notice(tmp_path: Path) -> None:
    f = tmp_path / "long.md"
    f.write_text("_a" * 20_000 + "\n", "utf-8")
    assert "部分段落太長或太複雜，以純文字顯示" in notices(engine.render(f).html)


# ------------------------------------------------------------------ 分類


def test_classify(tmp_path: Path, png_file: Path, pdf_file: Path, audio_file: Path) -> None:
    assert preview.classify(tmp_path) == "folder"
    assert preview.classify(png_file) == "image"
    assert preview.classify(pdf_file) == "pdf"
    assert preview.classify(audio_file) == "audio"
    font = tmp_path / "f.ttf"
    shutil.copy("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", font)
    assert preview.classify(font) == "font"
    code = tmp_path / "a.py"
    code.write_text("x = 1\n", "utf-8")
    assert preview.classify(code) == "html"
    assert preview.classify(tmp_path / "missing") == "special"
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    assert preview.classify(fifo) == "special"


def test_classify_video_and_office(tmp_path: Path) -> None:
    mp4 = tmp_path / "v.mp4"
    mp4.write_bytes(b"\x00\x00\x00\x18ftypisom\x00\x00\x02\x00isomiso2" + b"\x00" * 64)
    assert preview.classify(mp4) == "video"
    import docx

    d = tmp_path / "w.docx"
    docx.Document().save(d)
    assert preview.classify(d) == "office"


# ------------------------------------------------------------------ 資料夾與特殊檔


def test_folder_summary(tmp_path: Path) -> None:
    root = tmp_path / "proj"
    (root / "a" / "b").mkdir(parents=True)
    (root / "x.txt").write_bytes(b"x" * 1500)
    (root / "a" / "y.txt").write_bytes(b"y" * 500)
    (root / "a" / "b" / ".hidden").write_bytes(b"z")
    (root / "link").symlink_to("/usr")  # 不跟隨連結
    doc = preview.render(root)
    assert doc.kind_label == "資料夾"
    html = doc.html
    assert "<dt>內容</dt><dd>2 個資料夾、4 個檔案</dd>" in html
    assert "<dt>第一層</dt><dd>3 個項目</dd>" in html
    assert "<dt>總大小</dt><dd>2 KB（2,001 位元組）</dd>" in html
    assert '<span class="spacer"></span><span class="chip">2 KB</span>' in html
    assert 'class="ico"' not in html and "big-icon" in doc.attachments


def test_folder_scan_is_bounded(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from quickseemd.core.preview import special

    monkeypatch.setattr(special, "MAX_SCAN", 5)
    for i in range(20):
        (tmp_path / f"f{i}").write_bytes(b"1")
    html = preview.render(tmp_path).html
    assert "項目太多，數字只計算了一部分" in notices(html)


def test_special_files_are_never_opened(tmp_path: Path) -> None:
    fifo = tmp_path / "pipe"
    os.mkfifo(fifo)
    result: dict[str, preview.Document] = {}
    t = threading.Thread(target=lambda: result.setdefault("doc", preview.render(fifo)), daemon=True)
    t.start()
    t.join(10)
    assert not t.is_alive(), "預覽打開了管線檔（會卡住）"
    doc = result["doc"]
    assert doc.kind_label == "具名管線（FIFO）"
    assert "這不是一般檔案，預覽不會打開它" in notices(doc.html)
    sock_path = tmp_path / "s.sock"
    s = socket.socket(socket.AF_UNIX)
    s.bind(str(sock_path))
    try:
        assert preview.render(sock_path).kind_label == "通訊端（Socket）"
    finally:
        s.close()
    assert preview.render(Path("/dev/null")).kind_label == "字元裝置"
    broken = tmp_path / "dangling"
    broken.symlink_to(tmp_path / "nowhere")
    doc = preview.render(broken)
    assert doc.kind_label == "失效的連結"
    assert "<dt>連結目標</dt>" in doc.html
    assert preview.render(tmp_path / "nothing").kind_label == "找不到檔案"


# ------------------------------------------------------------------ 字型


def test_font_specimen(tmp_path: Path) -> None:
    font = tmp_path / "DejaVu Sans.ttf"
    shutil.copy("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", font)
    doc = preview.render_font(font)
    assert doc.kind_label == "字型"
    html = doc.html
    assert "@font-face" in html and "url(cid:font-file)" in html
    assert chips(html)[:2] == ["DejaVu Sans", "TTF"]
    assert "我能吞下玻璃而不傷身體" in html and "The quick brown fox" in html
    att = doc.attachments["font-file"]
    assert att.mime == "font/ttf" and att.data == font.read_bytes()
    assert "<script" not in html.lower()


def test_font_specimen_without_family(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    from quickseemd.core.preview import font as font_mod

    monkeypatch.setattr(font_mod, "FC_SCAN", "/nonexistent/fc-scan")
    f = tmp_path / "<b>Odd.otf"
    f.write_bytes(b"OTTO" + b"\x00" * 64)
    html = preview.render_font(f).html
    assert "&lt;b&gt;Odd" in html and "<b>Odd" not in html


# ------------------------------------------------------------------ 惡意文字：每種頁面都要跳脫

EVIL = "<img src=x onerror=alert(1)><script>alert(2)</script>"
EVIL_NAME = "<img src=x onerror=alert(1)>"


def _hostile_files(tmp_path: Path) -> dict[str, Path]:
    import gzip
    import io
    import json
    import lzma
    import plistlib
    import sqlite3
    import tarfile
    import zipfile

    files: dict[str, Path] = {}

    def add(key: str, name: str, data: bytes) -> None:
        p = tmp_path / key
        p.mkdir()
        (p / name).write_bytes(data)
        files[key] = p / name

    add("code", f"{EVIL_NAME}.py", f"x = '{EVIL}'\n".encode())
    add("text", f"{EVIL_NAME}.txt", EVIL.encode())
    add("csv", "t.csv", f"{EVIL},b\n{EVIL},2\n".encode())
    add("json", "t.json", json.dumps({EVIL: [EVIL] * 30}).encode())
    add(
        "md", "t.md", f"# {EVIL}\n\n[{EVIL}](javascript:x) ![{EVIL}](nope.png)\n\n{EVIL}\n".encode()
    )
    nb = {"cells": [
        {"cell_type": "markdown", "source": EVIL},
        {"cell_type": "code", "execution_count": EVIL, "source": EVIL, "outputs": [
            {"output_type": "stream", "name": "stdout", "text": EVIL},
            {"output_type": "error", "ename": EVIL, "evalue": EVIL, "traceback": []},
            {"output_type": "display_data", "data": {"text/html": EVIL, "image/svg+xml": EVIL}},
        ]},
        {"cell_type": EVIL, "source": EVIL},
    ], "metadata": {"kernelspec": {"language": EVIL}}}  # fmt: skip
    add("nb", "t.ipynb", json.dumps(nb).encode())
    zbuf = io.BytesIO()
    with zipfile.ZipFile(zbuf, "w") as z:
        z.writestr(f"{EVIL_NAME}/{EVIL_NAME}.txt", EVIL)
        z.comment = EVIL.encode()
    add("zip", "t.zip", zbuf.getvalue())
    tbuf = io.BytesIO()
    with tarfile.open(fileobj=tbuf, mode="w") as tf:
        info = tarfile.TarInfo(f"{EVIL_NAME}.txt")
        info.size = len(EVIL)
        tf.addfile(info, io.BytesIO(EVIL.encode()))
    add("tar", "t.tar", tbuf.getvalue())
    add("targz", "t.tar.gz", gzip.compress(tbuf.getvalue()))
    gbuf = io.BytesIO()
    with gzip.GzipFile(filename=EVIL_NAME, mode="wb", fileobj=gbuf) as gz:
        gz.write(EVIL.encode())
    add("gz", "t.gz", gbuf.getvalue())
    add("xz", f"{EVIL_NAME}.html.xz", lzma.compress(EVIL.encode()))
    add("bin", f"{EVIL_NAME}.bin", b"\x00\x01" + EVIL.encode())
    add("plist", "t.plist", plistlib.dumps({EVIL: EVIL}, fmt=plistlib.FMT_BINARY))
    add(
        "font",
        f"{EVIL_NAME}.ttf",
        Path("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf").read_bytes(),
    )
    dbdir = tmp_path / "db"
    dbdir.mkdir()
    db = dbdir / "t.sqlite"
    con = sqlite3.connect(db)
    con.execute(f'CREATE TABLE "{EVIL}"("{EVIL}" TEXT)')
    con.execute(f'INSERT INTO "{EVIL}" VALUES (?)', (EVIL,))
    con.commit()
    con.close()
    files["sqlite"] = db
    folder = tmp_path / "folder" / EVIL_NAME
    folder.mkdir(parents=True)
    files["folder"] = folder
    fifo = tmp_path / "fifo"
    fifo.mkdir()
    os.mkfifo(fifo / EVIL_NAME)
    files["special"] = fifo / EVIL_NAME
    return files


def _assert_safe(html: str) -> None:
    lowered = html.lower()
    assert "<script" not in lowered
    assert "<img src=x" not in lowered
    for tag in re.findall(r"<[a-z][^>]*>", lowered):  # 只看真正的標籤
        assert "onerror" not in tag and "javascript:" not in tag, tag


def test_hostile_text_in_every_page_type(tmp_path: Path) -> None:
    files = _hostile_files(tmp_path)
    for key, path in files.items():
        doc = preview.render_font(path) if key == "font" else preview.render(path)
        _assert_safe(doc.html)
        assert doc.html.startswith(SKELETON), key
