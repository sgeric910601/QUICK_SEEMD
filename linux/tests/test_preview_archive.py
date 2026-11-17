"""預覽核心：ZIP／TAR／gzip／xz 清單與樹狀檢視。"""

from __future__ import annotations

import sys
import gzip
import io
import lzma
import struct
import tarfile
import time
import zlib
from datetime import datetime
from pathlib import Path

import pytest

from quickseemd.core.preview import archive, archiveview, engine
from quickseemd.core.preview.page import Chip, FileInfo

# 覆蓋率量測會讓 Python 慢好幾倍；那時放寬時間上限，平常照原本的預算檢查
TIME_SCALE = 8.0 if sys.gettrace() is not None else 1.0


def _dos(dt: datetime) -> tuple[int, int]:
    date = ((dt.year - 1980) << 9) | (dt.month << 5) | dt.day
    tm = (dt.hour << 11) | (dt.minute << 5) | (dt.second // 2)
    return date, tm


def raw_zip(entries: list[dict], comment: bytes = b"") -> bytes:
    """最小的 ZIP：只有中央目錄有意義（清單只讀中央目錄）。"""
    local = b""
    central = b""
    for e in entries:
        name = e["name"]
        extra = e.get("extra", b"")
        date, tm = _dos(e.get("when", datetime(2026, 1, 2, 3, 4, 6)))
        usize = e.get("size", 10)
        csize = e.get("csize", 5)
        offset = len(local)
        local += (
            struct.pack(
                "<IHHHHHIIIHH",
                0x04034B50,
                20,
                e.get("flags", 0),
                8,
                tm,
                date,
                0,
                csize,
                usize,
                len(name),
                0,
            )
            + name
        )
        central += (
            struct.pack(
                "<IHHHHHHIIIHHHHHII",
                0x02014B50,
                0x031E,
                20,
                e.get("flags", 0),
                8,
                tm,
                date,
                0,
                csize,
                usize,
                len(name),
                len(extra),
                0,
                0,
                0,
                e.get("mode", 0o100644) << 16,
                offset,
            )
            + name
            + extra
        )
    eocd = (
        struct.pack(
            "<IHHHHIIH",
            0x06054B50,
            0,
            0,
            len(entries),
            len(entries),
            len(central),
            len(local),
            len(comment),
        )
        + comment
    )
    return local + central + eocd


def _write(tmp_path: Path, name: str, data: bytes) -> Path:
    p = tmp_path / name
    p.write_bytes(data)
    return p


def chips(page) -> list[str]:  # noqa: ANN001
    return [c.text for c in page.chips]


# ------------------------------------------------------------------ ZIP


def test_sample_zip(samples: Path) -> None:
    f = samples / "archive.zip"
    listing = archive.read_zip(f, f.stat().st_size)
    assert listing is not None and listing.format == "ZIP"
    names = [e.path for e in listing.entries]
    assert "專案/docs/說明.md" in names and "專案/src" in names
    page = archiveview.render(listing, FileInfo.of(f))
    assert page.kind == "ZIP 壓縮檔"
    assert page.size == (860, 620)
    assert chips(page)[:2] == ["3 個資料夾、4 個檔案", "原始 3 KB"]
    assert chips(page)[2].startswith("壓縮率 ")


def test_zip_parsing_details(tmp_path: Path) -> None:
    big5 = "中文.txt".encode("big5")
    entries = [
        {"name": b"a.txt", "size": 100, "csize": 40},
        {"name": big5},
        {"name": "日本.txt".encode(), "flags": 0x800},
        {"name": b"secret.bin", "flags": 1},
        {"name": b"link", "mode": 0o120777},
        {"name": b"stamp.txt", "extra": struct.pack("<HHBI", 0x5455, 5, 1, 1_700_000_000)},
        {"name": b"x.txt", "extra": struct.pack("<HHBI", 0x7075, 5 + 11, 1, 0) + b"unicode.txt"},
        {"name": b"__MACOSX/._a.txt"},
        {"name": b"sub/.DS_Store"},
        {
            "name": b"huge.bin",
            "size": 0xFFFFFFFF,
            "csize": 0xFFFFFFFF,
            "extra": struct.pack("<HHQQ", 0x0001, 16, 5_000_000_000, 4_000_000_000),
        },
        {"name": b"dir/"},
    ]
    f = _write(tmp_path, "t.zip", raw_zip(entries, comment=b"hello <b>"))
    listing = archive.read_zip(f, f.stat().st_size)
    by = {e.path: e for e in listing.entries}
    assert "中文.txt" in by and "日本.txt" in by and "unicode.txt" in by
    assert by["secret.bin"].encrypted and not by["a.txt"].encrypted
    assert by["link"].is_link
    assert by["stamp.txt"].modified == datetime.fromtimestamp(1_700_000_000)
    assert by["a.txt"].modified == datetime(2026, 1, 2, 3, 4, 6)
    assert by["huge.bin"].size == 5_000_000_000 and by["huge.bin"].compressed_size == 4_000_000_000
    assert by["dir"].is_dir and by["dir"].size is None
    assert listing.hidden_mac_metadata == 2
    assert listing.comment == "hello <b>"
    page = archiveview.render(listing, FileInfo.of(f), [Chip("DOCX 使用 ZIP 格式")])
    texts = chips(page)
    assert texts[-3:] == ["DOCX 使用 ZIP 格式", "含加密檔案", "已隱藏 2 個 macOS 中繼資料"]
    assert page.chips[-2].tone == "warn"
    assert [n.html for n in page.notices] == ['<div class="notice">註解：hello &lt;b&gt;</div>']
    assert ' <span class="enc">🔒</span>' in page.body
    assert ' <span class="enc">↪︎</span>' in page.body


def test_zip_rejects_garbage(tmp_path: Path) -> None:
    f = _write(tmp_path, "bad.zip", b"PK\x03\x04" + b"\x00" * 50)
    assert archive.read_zip(f, f.stat().st_size) is None
    tiny = _write(tmp_path, "tiny.zip", b"PK")
    assert archive.read_zip(tiny, 2) is None


def test_zip_tree_markup(tmp_path: Path) -> None:
    entries = [
        {"name": b"dir/", "when": datetime(2025, 5, 6, 7, 8)},
        {"name": b"dir/file10.txt", "size": 2048, "csize": 1024},
        {"name": b"dir/file2.txt", "size": 10, "csize": 5},
        {"name": b"<b>.txt"},
        {"name": b"../../etc/passwd"},
    ]
    f = _write(tmp_path, "t.zip", raw_zip(entries))
    page = archiveview.render(archive.read_zip(f, f.stat().st_size), FileInfo.of(f))
    body = page.body
    assert body.startswith(
        '<div class="arc"><div class="hdr"><span>名稱</span><span class="num">原始大小</span>'
        '<span class="num">壓縮後</span><span>修改日期</span></div>'
    )
    # 資料夾在前、自然排序（file2 在 file10 前面）
    assert body.index("..") < body.index("dir") < body.index("&lt;b&gt;.txt")
    assert body.index("file2.txt") < body.index("file10.txt")
    assert (
        '<details open><summary class="row"><span class="nm" style="padding-left:12px">'
        '<span class="tw"></span><img src="cid:ico-' in body
    )
    assert '<span class="nm" style="padding-left:30px">' in body
    assert '<span class="num">2 KB</span><span class="num">1 KB</span>' in body
    assert '<span class="dt">2025/05/06 07:08</span>' in body
    assert "<b>.txt" not in body
    for name in (n for n in page.attachments):
        assert name.startswith("ico-")


def test_archive_tree_open_state_and_icon_limit() -> None:
    entries = [archive.Entry(f"d{i}/s/t/u/f{i}.e{i}", False, 1, None, None) for i in range(450)]
    page = archiveview.render(archive.Listing("TAR", entries), FileInfo.virtual("x.tar"))
    assert page.body.count("<details open>") == 450  # 超過 400 項只展開第一層
    assert len(page.attachments) <= 81
    assert "ico-fallback" in page.attachments
    small = [archive.Entry("a/b/c/d/e.txt", False, 1, None, None)]
    page = archiveview.render(archive.Listing("TAR", small), FileInfo.virtual("x.tar"))
    assert page.body.count("<details open>") == 3 and page.body.count("<details>") == 1
    assert '<span class="num">—</span>' in page.body


def test_empty_and_truncated_listing() -> None:
    page = archiveview.render(archive.Listing("ZIP", []), FileInfo.virtual("e.zip"))
    assert page.body == '<div class="empty">壓縮檔是空的</div>'
    listing = archive.Listing("TAR", [archive.Entry("a", False, 1, None, None)], truncated=True)
    page = archiveview.render(listing, FileInfo.virtual("t.tar"))
    assert [n.text for n in page.notices] == ["項目太多，只列出前 20,000 個"]
    assert page.notices[0].warn


# ------------------------------------------------------------------ TAR


def _tar(fmt: int, members: list[tuple[tarfile.TarInfo, bytes | None]]) -> bytes:
    buf = io.BytesIO()
    with tarfile.open(fileobj=buf, mode="w", format=fmt) as tf:
        for info, data in members:
            tf.addfile(info, io.BytesIO(data) if data is not None else None)
    return buf.getvalue()


def _member(name: str, kind: bytes = tarfile.REGTYPE, data: bytes = b"", **kw: object):
    info = tarfile.TarInfo(name)
    info.type = kind
    info.size = len(data) if kind == tarfile.REGTYPE else 0
    info.mtime = kw.get("mtime", 1_700_000_000)
    if "linkname" in kw:
        info.linkname = kw["linkname"]
    return info, (data if kind == tarfile.REGTYPE else None)


def test_tar_formats(tmp_path: Path) -> None:
    long_name = "deep/" + "n" * 120 + ".txt"
    members = [
        _member("./top/", tarfile.DIRTYPE),
        _member("./top/a.txt", data=b"hello"),
        _member(long_name, data=b"x" * 600),
        _member("top/link", tarfile.SYMTYPE, linkname="a.txt"),
        _member("top/hard", tarfile.LNKTYPE, linkname="top/a.txt"),
        _member("top/._meta", data=b"m"),
        _member("中文/檔案.txt", data=b"z", mtime=1_600_000_000),
    ]
    for fmt in (tarfile.GNU_FORMAT, tarfile.PAX_FORMAT):
        f = _write(tmp_path, f"t{fmt}.tar", _tar(fmt, members))
        listing = archive.read_tar(f)
        by = {e.path: e for e in listing.entries}
        assert set(by) == {"top", "top/a.txt", long_name, "top/link", "top/hard", "中文/檔案.txt"}
        assert by["top"].is_dir and by["top"].size is None
        assert by[long_name].size == 600
        assert by["top/link"].is_link
        assert by["中文/檔案.txt"].modified == datetime.fromtimestamp(1_600_000_000)
        assert listing.hidden_mac_metadata == 1
        assert listing.format == "TAR"


def test_tar_rejects_bad_checksum_and_limits(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    data = bytearray(_tar(tarfile.GNU_FORMAT, [_member("a.txt", data=b"x")]))
    data[0] ^= 0xFF
    assert archive.read_tar(_write(tmp_path, "bad.tar", bytes(data))) is None
    empty = archive.read_tar(_write(tmp_path, "empty.tar", b"\x00" * 1024))
    assert empty is not None and empty.entries == []
    monkeypatch.setattr(archive, "MAX_ENTRIES", 3)
    many = _tar(tarfile.GNU_FORMAT, [_member(f"f{i}", data=b"1") for i in range(6)])
    listing = archive.read_tar(_write(tmp_path, "many.tar", many))
    assert len(listing.entries) == 3 and listing.truncated


def test_tar_number_parsing() -> None:
    header = bytearray(512)
    header[124:136] = b"\x80" + b"\x00" * 9 + b"\x01\x00"
    assert archive.parse_number(bytes(header), 124, 12) == 256
    assert archive.parse_number(b"  0000017 \x00", 0, 11) == 15
    assert archive.looks_like_tar(b"\x00" * 257 + b"ustar")
    assert not archive.looks_like_tar(b"\x00" * 300)


def test_compressed_tar_samples(samples: Path) -> None:
    for name, kind in (("archive.tar.gz", "TAR.GZ 壓縮檔"), ("archive.tar.xz", "TAR.XZ 壓縮檔")):
        doc = engine.render(samples / name)
        assert doc.kind_label == kind
        assert "說明.md" in doc.html
        assert "3 個資料夾、4 個檔案" in doc.html


def test_gzip_single_file(tmp_path: Path) -> None:
    buf = io.BytesIO()
    with gzip.GzipFile(filename="inner.json", mode="wb", fileobj=buf, mtime=1_700_000_000) as gz:
        gz.write(b'{"a": 1}\n' * 100)
    f = _write(tmp_path, "data.gz", buf.getvalue())
    info = archive.gzip_info(f, f.stat().st_size)
    assert info.original_name == "inner.json"
    assert info.uncompressed_size == 900
    assert info.modified == datetime.fromtimestamp(1_700_000_000)
    doc = engine.render(f)
    assert doc.kind_label == "gzip 壓縮檔"
    assert doc.preferred_size == (860, 680)
    assert "<dt>原始檔名</dt><dd>inner.json</dd>" in doc.html
    assert "<dt>原始大小</dt><dd>900 位元組</dd>" in doc.html
    assert "<dt>壓縮率</dt>" in doc.html
    assert '<div class="section-title">解壓縮後的內容 · JSON</div>' in doc.html
    assert 'class="ico"' not in doc.html


def test_gzip_and_xz_binary_payload_shows_hex(tmp_path: Path) -> None:
    f = _write(tmp_path, "blob.bin.gz", gzip.compress(bytes(range(256)) * 4))
    doc = engine.render(f)
    assert '<div class="section-title">解壓縮後的內容（十六進位）</div>' in doc.html
    assert "<dt>原始檔名</dt><dd>blob.bin</dd>" in doc.html
    x = _write(tmp_path, "notes.txt.xz", lzma.compress("你好\n".encode()))
    doc = engine.render(x)
    assert doc.kind_label == "xz 壓縮檔"
    assert '<div class="section-title">解壓縮後的內容 · 純文字</div>' in doc.html
    assert "<dt>壓縮格式</dt><dd>xz</dd>" in doc.html


def test_decompression_is_bounded(tmp_path: Path) -> None:
    bomb = _write(tmp_path, "bomb.gz", gzip.compress(b"\x00" * (200 * 1024 * 1024), 9))
    info = archive.gzip_info(bomb, bomb.stat().st_size)
    start = time.perf_counter()
    head = archive.decompress_head(bomb, "gzip", info.header_length, 2 * 1024 * 1024)
    assert len(head) == 2 * 1024 * 1024
    assert time.perf_counter() - start < 1.0 * TIME_SCALE
    xz = _write(tmp_path, "bomb.xz", lzma.compress(b"\x00" * (20 * 1024 * 1024)))
    assert len(archive.decompress_head(xz, "xz", 0, 1024)) == 1024


# ------------------------------------------------------------------ 惡意壓縮檔不可拖垮預覽


def test_huge_tar_names_are_clipped(tmp_path: Path) -> None:
    members = [_member(f"{i}/" + "n" * 200_000, data=b"x") for i in range(20)]
    f = _write(tmp_path, "long.tar.gz", gzip.compress(_tar(tarfile.GNU_FORMAT, members)))
    assert f.stat().st_size < 1_000_000
    doc = engine.render(f)
    assert doc.kind_label == "TAR.GZ 壓縮檔"
    assert len(doc.html) < 2_000_000
    listing = archive.read_compressed_tar(f, "gzip", 10, "TAR.GZ")
    assert all(len(e.path) <= archive.MAX_PATH_CHARS + 1 for e in listing.entries)


def test_deep_paths_are_bounded(tmp_path: Path) -> None:
    deep = b"d/" * 20_000 + b"leaf.txt"
    f = _write(tmp_path, "deep.zip", raw_zip([{"name": deep}]))
    start = time.perf_counter()
    doc = engine.render(f)
    assert time.perf_counter() - start < 2.0 * TIME_SCALE
    assert doc.html.count("<details") == archiveview.MAX_DEPTH - 1


def test_node_and_record_caps(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(archiveview, "MAX_NODES", 10)
    entries = [archive.Entry(f"f{i}.txt", False, 1, None, None) for i in range(20)]
    page = archiveview.render(archive.Listing("TAR", entries), FileInfo.virtual("x.tar"))
    assert [n.text for n in page.notices] == ["路徑太多或太深，只列出部分項目"]
    assert page.body.count('<div class="row">') == 10
    monkeypatch.setattr(archive, "MAX_ENTRIES", 2)
    meta = [{"name": f"__MACOSX/._{i}".encode()} for i in range(50)]
    f = _write(tmp_path, "meta.zip", raw_zip(meta))
    listing = archive.read_zip(f, f.stat().st_size)
    assert listing.truncated and listing.hidden_mac_metadata < 50


# ------------------------------------------------------------------ 解壓縮炸彈與異常大小


def _streamed_gzip(path: Path, header: bytes, zero_mib: int) -> Path:
    """一段一段壓縮，不會在記憶體裡放整份解壓後的資料。"""
    comp = zlib.compressobj(9, zlib.DEFLATED, 31)  # 31：gzip 格式
    zero = b"\x00" * (1024 * 1024)
    with open(path, "wb") as fh:
        fh.write(comp.compress(header))
        for _ in range(zero_mib):
            fh.write(comp.compress(zero))
        fh.write(comp.flush())
    return path


def _tar_header(name: str, size: int) -> bytes:
    info = tarfile.TarInfo(name)
    info.size = size
    return info.tobuf(tarfile.GNU_FORMAT)


def test_tar_gz_bomb_is_bounded(tmp_path: Path) -> None:
    size = 300 * 1024 * 1024
    bomb = _streamed_gzip(tmp_path / "bomb.tar.gz", _tar_header("big.bin", size), 300)
    assert bomb.stat().st_size < 2_000_000
    start = time.perf_counter()
    doc = engine.render(bomb)
    assert time.perf_counter() - start < 6.0 * TIME_SCALE
    assert doc.kind_label == "TAR.GZ 壓縮檔"
    assert "壓縮檔太大，只列出前面的部分" in doc.html
    assert "big.bin" in doc.html


def test_listing_deadline(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(archive, "LISTING_SECONDS", 0.0)
    f = _write(
        tmp_path,
        "t.tar.gz",
        gzip.compress(_tar(tarfile.GNU_FORMAT, [_member("a.txt", data=b"x")] * 3)),
    )
    listing = archive.read_compressed_tar(f, "gzip", 10, "TAR.GZ")
    assert listing is None or listing.partial


def test_metadata_budget_across_archive(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(archive, "META_TOTAL_LIMIT", 300_000)
    members = [_member(f"{i}/" + "n" * 100_000, data=b"x") for i in range(10)]
    f = _write(tmp_path, "m.tar", _tar(tarfile.GNU_FORMAT, members))
    listing = archive.read_tar(f)
    assert listing.partial and len(listing.entries) < 10


@pytest.mark.parametrize(
    "size_field",
    [
        b"77777777777\x00",  # 8 GiB，比檔案大很多
        b"\xff" * 12,  # base-256，超過 64 位元
        b"\x80" + b"\xff" * 11,
    ],
    ids=["octal-8gib", "base256-all-ones", "base256-huge"],
)
def test_absurd_size_fields_do_not_crash(tmp_path: Path, size_field: bytes) -> None:
    header = bytearray(_tar_header("x.bin", 0))
    header[124:136] = size_field
    header[148:156] = b"        "
    header[148:155] = b"%06o\x00" % sum(header)
    data = bytes(header) + b"\x00" * 1024
    for name, blob in (("a.tar", data), ("a.tar.gz", gzip.compress(data))):
        f = _write(tmp_path, name, blob)
        doc = engine.render(f)
        assert doc.html.endswith("</body></html>")
    listing = archive.read_tar(_write(tmp_path, "b.tar", data))
    assert listing is not None and [e.path for e in listing.entries] == ["x.bin"]


@pytest.mark.parametrize(
    "value", ["-5", "9" * 5000, "1e9", ""], ids=["negative", "huge", "float", "empty"]
)
def test_bad_pax_sizes_are_ignored(tmp_path: Path, value: str) -> None:
    record = f"size={value}\n"
    length = len(record) + 3
    length += len(str(length)) - 2
    pax = f"{length} {record}".encode()
    xinfo = tarfile.TarInfo("././@PaxHeader")
    xinfo.type = tarfile.XHDTYPE
    xinfo.size = len(pax)
    blob = xinfo.tobuf(tarfile.USTAR_FORMAT) + pax + b"\x00" * (-len(pax) % 512)
    blob += _tar_header("real.txt", 3) + b"abc" + b"\x00" * 509 + b"\x00" * 1024
    listing = archive.read_tar(_write(tmp_path, "p.tar", blob))
    assert listing is not None
    assert [(e.path, e.size) for e in listing.entries] == [("real.txt", 3)]


def test_inner_gzip_preview_is_bounded(tmp_path: Path) -> None:
    bomb = _streamed_gzip(tmp_path / "zeros.bin.gz", b"", 300)
    start = time.perf_counter()
    doc = engine.render(bomb)
    assert time.perf_counter() - start < 3.0 * TIME_SCALE
    assert doc.kind_label == "gzip 壓縮檔"
    assert "解壓縮後的內容（十六進位）" in doc.html
