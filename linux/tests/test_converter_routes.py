"""萬用轉換：文字編碼、資料、壓縮檔路線（經由 converter.convert 執行並交付）。"""

from __future__ import annotations

import io
import json
import plistlib
import shutil
import tarfile
import zipfile
from pathlib import Path
from typing import Any

import pytest
import yaml

from quickseemd.core import errors
from quickseemd.core.converter import convert, tables, xlsx
from quickseemd.core.model import ToolContext


def run(path: Path, fid: str, out: Path | None = None, **opts: Any) -> list[Path]:
    messages: list[str | None] = []
    ctx = ToolContext(out, lambda f, m: messages.append(m))
    try:
        result = convert(path, fid, ctx, opts)
    finally:
        ctx.cleanup()
    assert messages[-1] is not None and messages[-1].startswith("完成：")
    return result


def copy_sample(samples: Path, name: str, tmp_path: Path) -> Path:
    dest = tmp_path / "in" / name
    dest.parent.mkdir(exist_ok=True)
    shutil.copy2(samples / name, dest)
    return dest


# ------------------------------------------------------------------ 共通


def test_missing_file(tmp_path: Path, outdir: Path) -> None:
    with pytest.raises(errors.QSError) as exc:
        run(tmp_path / "nope.txt", "utf8", outdir)
    assert str(exc.value) == "找不到檔案「nope.txt」，可能已被移動或刪除。"


def test_unknown_and_unsupported_format(samples: Path, outdir: Path) -> None:
    with pytest.raises(errors.QSError) as exc:
        run(samples / "app.py", "nope", outdir)
    assert str(exc.value) == "未知的格式代碼「nope」。"
    with pytest.raises(errors.QSError) as exc:
        run(samples / "app.py", "utf8", outdir)
    assert str(exc.value) == "「app.py」無法轉換成 UTF-8。"


def test_unwritable_output_folder(samples: Path, tmp_path: Path) -> None:
    with pytest.raises(errors.QSError) as exc:
        run(samples / "app.py", "crlf", tmp_path / "missing")
    assert "無法寫入輸出位置" in str(exc.value)


# ------------------------------------------------------------------ 文字編碼


def test_big5_to_utf8_keeps_name(samples: Path, outdir: Path) -> None:
    [out] = run(samples / "big5.txt", "utf8", outdir)
    assert out == outdir / "big5.txt"
    assert out.read_text("utf-8").startswith("這是 Big5 編碼")


def test_same_folder_gets_numbered_name(samples: Path, tmp_path: Path) -> None:
    src = copy_sample(samples, "app.py", tmp_path)
    [out] = run(src, "crlf")
    assert out.name == "app (1).py"
    assert b"\r\n" in out.read_bytes()
    assert src.read_bytes() == (samples / "app.py").read_bytes()


@pytest.mark.parametrize(
    ("fid", "prefix", "codec"),
    [
        ("utf8-bom", b"\xef\xbb\xbf", "utf-8-sig"),
        ("utf16", b"\xff\xfe", "utf-16"),
        ("big5", b"", "cp950"),
        ("gb18030", b"", "gb18030"),
    ],
)
def test_encodings(tmp_path: Path, outdir: Path, fid: str, prefix: bytes, codec: str) -> None:
    src = tmp_path / "t.txt"
    src.write_text("中文測試\n第二行\n", "utf-8")
    [out] = run(src, fid, outdir)
    data = out.read_bytes()
    assert data.startswith(prefix)
    assert data.decode(codec) == "中文測試\n第二行\n"


def test_line_endings_keep_encoding(samples: Path, outdir: Path) -> None:
    [out] = run(samples / "big5.txt", "crlf", outdir)
    assert out.read_bytes().decode("cp950").count("\r\n") == 2


def test_utf16_be_source_becomes_le_bom(tmp_path: Path, outdir: Path) -> None:
    src = tmp_path / "be.txt"
    src.write_bytes(b"\xfe\xff" + "a\r\nb\r\n".encode("utf-16-be"))
    [out] = run(src, "lf", outdir)
    assert out.read_bytes() == b"\xff\xfe" + "a\nb\n".encode("utf-16-le")


def test_unencodable_character(tmp_path: Path, outdir: Path) -> None:
    src = tmp_path / "e.txt"
    src.write_text("hi 😀\n", "utf-8")
    with pytest.raises(errors.QSError) as exc:
        run(src, "big5", outdir)
    assert str(exc.value) == "有些字元無法用 Big5 表示（例如「😀」），建議改用 UTF-8。"


def test_forced_source_encoding(tmp_path: Path, outdir: Path) -> None:
    src = tmp_path / "sj.txt"
    src.write_bytes("テスト".encode("cp932"))
    [out] = run(src, "utf8", outdir, source_encoding="shift_jis")
    assert out.read_text("utf-8") == "テスト"


# ------------------------------------------------------------------ 資料


def test_json_to_all_outputs(tmp_path: Path, outdir: Path) -> None:
    src = tmp_path / "d.json"
    src.write_text('{"name": "QS", "n": 1.50, "list": [1, 2], "none": null}', "utf-8")
    [pretty] = run(src, "json-pretty", outdir)
    assert pretty.name == "d.json"
    assert pretty.read_text() == (
        '{\n  "name": "QS",\n  "n": 1.50,\n  "list": [\n    1,\n    2\n  ],\n  "none": null\n}\n'
    )
    [mini] = run(src, "json-min", outdir)
    assert mini.name == "d (1).json"
    assert mini.read_text() == '{"name":"QS","n":1.50,"list":[1,2],"none":null}'
    [y] = run(src, "yaml", outdir)
    assert yaml.safe_load(y.read_text()) == {"name": "QS", "n": 1.5, "list": [1, 2], "none": None}
    [xml] = run(src, "plist", outdir)
    assert plistlib.loads(xml.read_bytes()) == {"name": "QS", "n": 1.5, "list": [1, 2]}
    [binary] = run(src, "bplist", outdir)
    assert binary.name == "d (1).plist"
    assert plistlib.loads(binary.read_bytes()) == {"name": "QS", "n": 1.5, "list": [1, 2]}


def test_json_array_roundtrip_through_xlsx_csv_tsv_html(tmp_path: Path, outdir: Path) -> None:
    rows = [{"id": "007", "name": "王小明", "score": 88.5}, {"id": "8", "name": "a,b", "ok": True}]
    src = tmp_path / "rows.json"
    src.write_text(json.dumps(rows, ensure_ascii=False), "utf-8")
    [x] = run(src, "xlsx", outdir)
    [back] = run(x, "json", outdir)
    assert json.loads(back.read_text()) == [
        {"id": "007", "name": "王小明", "score": 88.5, "ok": ""},
        {"id": 8, "name": "a,b", "score": "", "ok": True},
    ]
    [c] = run(src, "csv", outdir)
    assert c.read_text() == 'id,name,score,ok\n007,王小明,88.5,\n8,"a,b",,true\n'
    [t] = run(src, "tsv", outdir)
    assert t.read_text().splitlines()[0] == "id\tname\tscore\tok"
    [h] = run(src, "html-table", outdir)
    assert "<h1>rows</h1>" in h.read_text()


def test_json_to_table_requires_array(tmp_path: Path) -> None:
    from quickseemd.core.converter import data, profile
    from quickseemd.core.converter.formats import BY_ID

    src = tmp_path / "o.json"
    src.write_text('{"a": 1}')
    ctx = ToolContext(tmp_path)
    with pytest.raises(errors.QSError) as exc:
        data.convert(src, profile.inspect(src), BY_ID["csv"], {}, tmp_path, ctx)
    assert "最外層必須是陣列" in str(exc.value)


def test_broken_json_reports_line(samples: Path, outdir: Path) -> None:
    with pytest.raises(errors.QSError) as exc:
        run(samples / "broken.json", "yaml", outdir)
    assert str(exc.value).startswith("資料格式不正確：JSON 第 ")


def test_csv_sample_to_json_and_xlsx(samples: Path, outdir: Path) -> None:
    [j] = run(samples / "data.csv", "json", outdir)
    data = json.loads(j.read_text())
    assert data[0] == {"name": "王小明", "city": "台北", "score": 88.5, "joined": "2024-01-03"}
    assert data[2]["name"] == "多行\n備註"
    [x] = run(samples / "data.csv", "xlsx", outdir)
    sheet = xlsx.read_xlsx(x)[0]
    assert sheet.name == "data"
    assert sheet.rows[1] == ("王小明", "台北", "88.5", "2024-01-03")
    [tsv] = run(samples / "data.csv", "tsv", outdir)
    assert tsv.read_text().startswith("name\tcity\tscore\tjoined\n")


def test_tsv_sample_to_csv(samples: Path, outdir: Path) -> None:
    [c] = run(samples / "data.tsv", "csv", outdir)
    assert c.suffix == ".csv"
    assert "," in c.read_text().splitlines()[0]


def test_plist_sample_to_json(samples: Path, outdir: Path) -> None:
    [j] = run(samples / "settings.plist", "json", outdir)
    value = json.loads(j.read_text())
    assert value == plistlib.loads((samples / "settings.plist").read_bytes()) or isinstance(
        value, dict
    )
    assert list(value) == sorted(value)


def test_xlsx_multi_sheet_outputs(tmp_path: Path, outdir: Path) -> None:
    book = tmp_path / "book.xlsx"
    xlsx.write_xlsx(
        [
            tables.Table("一月", (("a", "b"), ("1", "x"))),
            tables.Table("a/b", (("c",), ("2",))),
        ],
        book,
    )
    [folder] = run(book, "csv", outdir)
    assert folder.name == "book-CSV" and folder.is_dir()
    # 工作表名稱不能有「/」，寫入 XLSX 時已換成「_」
    assert sorted(p.name for p in folder.iterdir()) == ["a_b.csv", "一月.csv"]
    assert (folder / "一月.csv").read_text() == "a,b\n1,x\n"
    [j] = run(book, "json", outdir)
    assert json.loads(j.read_text()) == {"一月": [{"a": 1, "b": "x"}], "a_b": [{"c": 2}]}
    [y] = run(book, "yaml", outdir)
    assert yaml.safe_load(y.read_text()) == {"一月": [{"a": 1, "b": "x"}], "a_b": [{"c": 2}]}
    [h] = run(book, "html-table", outdir)
    assert "<h2>一月</h2>" in h.read_text()


def test_single_sheet_xlsx_to_csv_is_a_file(tmp_path: Path, outdir: Path) -> None:
    book = tmp_path / "one.xlsx"
    xlsx.write_xlsx([tables.Table("S", (("a",), ("1",)))], book)
    [c] = run(book, "csv", outdir)
    assert c.name == "one.csv" and c.read_text() == "a\n1\n"


# ------------------------------------------------------------------ 壓縮檔


def _tree(tmp_path: Path) -> Path:
    root = tmp_path / "專案"
    (root / "src").mkdir(parents=True)
    (root / "src" / "a.txt").write_text("A")
    (root / "b.txt").write_text("B")
    (root / "link").symlink_to("b.txt")
    return root


def test_folder_to_zip_tgz_7z_and_back(tmp_path: Path, outdir: Path) -> None:
    root = _tree(tmp_path)
    [z] = run(root, "zip", outdir)
    assert z.name == "專案.zip"
    with zipfile.ZipFile(z) as zf:
        names = set(zf.namelist())
    assert {"專案/", "專案/src/a.txt", "專案/b.txt", "專案/link"} <= names
    [t] = run(root, "tgz", outdir)
    assert t.name == "專案.tar.gz"
    with tarfile.open(t) as tf:
        assert tf.getmember("專案/link").issym()
    [s] = run(root, "7z", outdir)
    assert s.name == "專案.7z"
    for archive in (z, t, s):
        dest = outdir / f"x-{archive.suffix}"
        dest.mkdir()
        [extracted] = run(archive, "extract", dest)
        assert extracted == dest / "專案"
        assert (extracted / "src" / "a.txt").read_text() == "A"
        assert (extracted / "link").is_symlink()


def test_file_to_zip_keeps_name(samples: Path, outdir: Path) -> None:
    [z] = run(samples / "app.py", "zip", outdir)
    with zipfile.ZipFile(z) as zf:
        assert zf.namelist() == ["app.py"]


def test_sample_archives_extract_and_repack(samples: Path, outdir: Path) -> None:
    for name in ("archive.zip", "archive.tar.gz", "archive.tar.xz"):
        dest = outdir / name.replace(".", "_")
        dest.mkdir()
        [folder] = run(samples / name, "extract", dest)
        assert folder.name == "專案"
        assert (folder / "docs" / "說明.md").is_file()
    [repacked] = run(samples / "archive.zip", "tgz", outdir)
    assert repacked.name == "archive.tar.gz"
    with tarfile.open(repacked) as tf:
        assert "專案/src/main.rs" in tf.getnames()


def test_single_gz_xz_bz2(samples: Path, tmp_path: Path, outdir: Path) -> None:
    import bz2
    import lzma

    [log] = run(samples / "app.log.gz", "extract", outdir)
    assert log.name == "app.log"
    assert log.read_bytes() == (samples / "app.log").read_bytes()
    (tmp_path / "n.txt.xz").write_bytes(lzma.compress(b"xz!"))
    (tmp_path / "m.txt.bz2").write_bytes(bz2.compress(b"bz!"))
    [x] = run(tmp_path / "n.txt.xz", "extract", outdir)
    [b] = run(tmp_path / "m.txt.bz2", "extract", outdir)
    assert (x.name, x.read_bytes(), b.name, b.read_bytes()) == ("n.txt", b"xz!", "m.txt", b"bz!")


def test_multiple_top_level_items_get_folder(tmp_path: Path, outdir: Path) -> None:
    src = tmp_path / "many.zip"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("a.txt", "a")
        z.writestr("b/c.txt", "c")
        z.writestr("__MACOSX/._a.txt", "junk")
        z.writestr(".DS_Store", "junk")
        z.writestr("._b", "junk")
    [folder] = run(src, "extract", outdir)
    assert folder == outdir / "many"
    assert sorted(p.name for p in folder.iterdir()) == ["._b", "a.txt", "b"]


def test_single_item_ignores_appledouble_in_count(tmp_path: Path, outdir: Path) -> None:
    src = tmp_path / "one.zip"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("only.txt", "x")
        z.writestr("._only.txt", "junk")
    [item] = run(src, "extract", outdir)
    assert item == outdir / "only.txt"


def test_empty_archive(tmp_path: Path, outdir: Path) -> None:
    src = tmp_path / "empty.zip"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("__MACOSX/x", "junk")
    with pytest.raises(errors.QSError) as exc:
        run(src, "extract", outdir)
    assert str(exc.value) == "檔案沒有可轉換的內容：壓縮檔裡沒有任何檔案。"


# ------------------------------------------------------------------ 壓縮檔：路徑穿越


def _assert_refused(src: Path, outdir: Path, tmp_path: Path) -> None:
    before = sorted(p.name for p in tmp_path.iterdir())
    with pytest.raises(errors.QSError) as exc:
        run(src, "extract", outdir)
    assert "不安全的路徑" in str(exc.value)
    assert sorted(p.name for p in tmp_path.iterdir()) == before
    assert list(outdir.iterdir()) == []


@pytest.mark.parametrize("name", ["../evil.txt", "/tmp/evil.txt", "a/../../evil.txt", "C:/evil"])
def test_zip_traversal_is_refused(tmp_path: Path, outdir: Path, name: str) -> None:
    src = tmp_path / "bad.zip"
    with zipfile.ZipFile(src, "w") as z:
        z.writestr("ok.txt", "ok")
        z.writestr(name, "evil")
    _assert_refused(src, outdir, tmp_path)


def test_zip_symlink_escape_is_refused(tmp_path: Path, outdir: Path) -> None:
    src = tmp_path / "link.zip"
    with zipfile.ZipFile(src, "w") as z:
        info = zipfile.ZipInfo("esc")
        info.create_system = 3
        info.external_attr = (0o120777) << 16
        z.writestr(info, "../../outside")
        z.writestr("esc/x.txt", "evil")
    _assert_refused(src, outdir, tmp_path)


def _tar_with(src: Path, members: list[tarfile.TarInfo]) -> None:
    with tarfile.open(src, "w:gz") as tf:
        for m in members:
            data = b"evil" if m.isfile() else None
            if data is not None:
                m.size = len(data)
            tf.addfile(m, io.BytesIO(data) if data is not None else None)


def test_tar_traversal_is_refused(tmp_path: Path, outdir: Path) -> None:
    src = tmp_path / "bad.tar.gz"
    _tar_with(src, [tarfile.TarInfo("ok.txt"), tarfile.TarInfo("../evil.txt")])
    _assert_refused(src, outdir, tmp_path)


def test_tar_symlink_escape_is_refused(tmp_path: Path, outdir: Path) -> None:
    src = tmp_path / "link.tar.gz"
    link = tarfile.TarInfo("esc")
    link.type = tarfile.SYMTYPE
    link.linkname = "/etc"
    _tar_with(src, [link, tarfile.TarInfo("esc/passwd")])
    with pytest.raises(errors.QSError) as exc:
        run(src, "extract", outdir)
    assert "不安全的路徑" in str(exc.value)
    assert list(outdir.iterdir()) == []


def test_7z_traversal_is_refused(tmp_path: Path, outdir: Path) -> None:
    from quickseemd.core import backends, process

    stage = tmp_path / "stage" / "inner"
    stage.mkdir(parents=True)
    (tmp_path / "stage" / "evil.txt").write_text("evil")
    src = tmp_path / "bad.7z"
    args = [backends.require("bsdtar"), "--format", "7zip", "-cf", str(src), "-C", str(stage)]
    process.run_checked(args + ["-P", "../evil.txt"], "bsdtar")
    _assert_refused(src, outdir, tmp_path)
