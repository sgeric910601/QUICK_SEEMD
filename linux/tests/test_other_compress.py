"""壓縮檔案（other.compress）。"""

from __future__ import annotations

import os
import stat
import tarfile
import zipfile
from pathlib import Path

import pytest

from quickseemd.core import errors
from quickseemd.core.model import ToolContext
from quickseemd.core.options import Kind
from quickseemd.core.tools.other import TOOLS


def _tool():
    return next(t for t in TOOLS if t.id == "other.compress")


def _run(files, out=None, **values):
    t = _tool()
    vals = t.defaults()
    vals.update(values)
    ctx = ToolContext(out)
    try:
        return t.run([Path(f) for f in files], vals, ctx), ctx
    finally:
        ctx.cleanup()


def _tree(root: Path) -> Path:
    """測試資料夾：一般檔、子資料夾、空資料夾、隱藏檔、垃圾檔、內部符號連結。"""
    (root / "sub" / "deep").mkdir(parents=True)
    (root / "empty").mkdir()
    (root / "a.txt").write_text("甲")
    (root / "sub" / "b.txt").write_text("乙")
    (root / "sub" / "deep" / "c.bin").write_bytes(os.urandom(5000))
    (root / ".hidden").write_text("h")
    (root / ".DS_Store").write_bytes(b"junk")
    (root / "sub" / ".DS_Store").write_bytes(b"junk")
    (root / "__MACOSX").mkdir()
    (root / "__MACOSX" / "x").write_text("junk")
    (root / "run.sh").write_text("#!/bin/sh\n")
    (root / "run.sh").chmod(0o755)
    (root / "link").symlink_to("a.txt")
    return root


def _zip_names(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as zf:
        return zf.namelist()


# ------------------------------------------------------------------ 定義


def test_definition():
    t = _tool()
    assert t.title == "壓縮檔案"
    assert t.subtitle == "把檔案或資料夾壓成 ZIP 或 TAR.GZ，可各自壓縮或全部打包成一個。"
    assert t.badge == "ZIP" and t.action_title == "壓縮"
    assert t.input_label == "檔案或資料夾"
    fmt, together, name = t.option("format"), t.option("together"), t.option("name")
    assert fmt.kind is Kind.CARDS and [c.value for c in fmt.choices] == ["zip", "tgz"]
    assert together.kind is Kind.CARDS and [c.value for c in together.choices] == [True, False]
    assert [c.title for c in together.choices] == ["全部打包成一個", "各自壓縮"]
    assert name.kind is Kind.TEXT and name.label == "壓縮檔名稱" and name.placeholder == "封存"
    assert t.defaults() == {"format": "zip", "together": True, "name": ""}
    assert name.is_visible({"together": True}, {})
    assert not name.is_visible({"together": False}, {})
    assert t.presets == {"other.zip": {"format": "zip", "together": True}}


def test_accepts_folders(tmp_path):
    assert _tool().accepts(tmp_path)


# ------------------------------------------------------------------ 單一項目


def test_single_file_zip(tmp_path):
    f = tmp_path / "報告.txt"
    f.write_text("內容")
    [out], _ = _run([f])
    assert out == tmp_path / "報告.zip"
    assert _zip_names(out) == ["報告.txt"]
    with zipfile.ZipFile(out) as zf:
        assert zf.read("報告.txt").decode() == "內容"
        info = zf.getinfo("報告.txt")
        assert info.flag_bits & 0x800  # UTF-8 檔名


def test_folder_zip_keeps_top_level_and_excludes_junk(tmp_path, outdir):
    root = _tree(tmp_path / "相簿")
    [out], _ = _run([root], outdir)
    assert out == outdir / "相簿.zip"
    # 依名稱排序、資料夾在自己的內容之前；不含 .DS_Store 與 __MACOSX
    assert _zip_names(out) == [
        "相簿/",
        "相簿/.hidden",
        "相簿/a.txt",
        "相簿/empty/",
        "相簿/link",
        "相簿/run.sh",
        "相簿/sub/",
        "相簿/sub/b.txt",
        "相簿/sub/deep/",
        "相簿/sub/deep/c.bin",
    ]
    with zipfile.ZipFile(out) as zf:
        assert zf.read("相簿/sub/deep/c.bin") == (root / "sub" / "deep" / "c.bin").read_bytes()
        link = zf.getinfo("相簿/link")
        assert stat.S_ISLNK(link.external_attr >> 16)
        assert zf.read(link) == b"a.txt"
        assert (zf.getinfo("相簿/run.sh").external_attr >> 16) & 0o111


def test_zip_is_deterministic(tmp_path, outdir):
    root = _tree(tmp_path / "data")
    [a], _ = _run([root], outdir)
    [b], _ = _run([root], outdir)
    assert b.name == "data (1).zip"
    assert _zip_names(a) == _zip_names(b)


def test_folder_tgz(tmp_path, outdir):
    root = _tree(tmp_path / "proj")
    [out], _ = _run([root], outdir, format="tgz")
    assert out == outdir / "proj.tar.gz"
    with tarfile.open(out) as tf:
        names = tf.getnames()
        assert "proj" in names and "proj/sub/deep/c.bin" in names and "proj/empty" in names
        assert not any(".DS_Store" in n or "__MACOSX" in n for n in names)
        assert tf.getmember("proj/link").issym()
        assert tf.getmember("proj/run.sh").mode & 0o111
        assert tf.extractfile("proj/a.txt").read().decode() == "甲"


def test_never_overwrites(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("x")
    (tmp_path / "a.zip").write_bytes(b"existing")
    [out], _ = _run([f])
    assert out.name == "a (1).zip"
    assert (tmp_path / "a.zip").read_bytes() == b"existing"


def test_skips_special_files(tmp_path, outdir):
    root = tmp_path / "f"
    root.mkdir()
    (root / "a.txt").write_text("a")
    os.mkfifo(root / "pipe")
    [out], _ = _run([root], outdir)
    assert _zip_names(out) == ["f/", "f/a.txt"]
    [out2], _ = _run([root], outdir, format="tgz")
    with tarfile.open(out2) as tf:
        assert tf.getnames() == ["f", "f/a.txt"]


def test_top_level_symlink_is_followed(tmp_path, outdir):
    real = tmp_path / "real.txt"
    real.write_text("真的")
    link = tmp_path / "捷徑.txt"
    link.symlink_to(real)
    [out], _ = _run([link], outdir)
    with zipfile.ZipFile(out) as zf:
        assert zf.namelist() == ["捷徑.txt"]
        assert zf.read("捷徑.txt").decode() == "真的"


# ------------------------------------------------------------------ 多個項目


def test_together_default_name_next_to_first(tmp_path):
    a = tmp_path / "a.txt"
    a.write_text("a")
    d = _tree(tmp_path / "dir")
    [out], ctx = _run([a, d])
    assert out == tmp_path / "封存.zip"
    names = _zip_names(out)
    assert names[0] == "a.txt"
    assert "dir/sub/b.txt" in names
    assert ctx.last_message == "壓縮中…"


def test_together_custom_name_sanitized(tmp_path, outdir):
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("a")
    b.write_text("b")
    [out], _ = _run([a, b], outdir, name="  旅行/照片:2024 ")
    assert out == outdir / "旅行-照片-2024.zip"
    [out2], _ = _run([a, b], outdir, name="   ")
    assert out2 == outdir / "封存.zip"


def test_together_duplicate_names_get_numbers(tmp_path, outdir):
    (tmp_path / "x").mkdir()
    (tmp_path / "y").mkdir()
    a = tmp_path / "x" / "a.txt"
    b = tmp_path / "y" / "a.txt"
    a.write_text("first")
    b.write_text("second")
    [out], _ = _run([a, b], outdir)
    with zipfile.ZipFile(out) as zf:
        assert zf.namelist() == ["a.txt", "a (1).txt"]
        assert zf.read("a (1).txt") == b"second"


def test_together_tgz(tmp_path, outdir):
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("a")
    b.write_text("b")
    [out], _ = _run([a, b], outdir, format="tgz", name="打包")
    assert out == outdir / "打包.tar.gz"
    with tarfile.open(out) as tf:
        assert tf.getnames() == ["a.txt", "b.txt"]


def test_together_does_not_unpack_archives(tmp_path, outdir):
    z = tmp_path / "in.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("x.txt", "x")
    other = tmp_path / "o.txt"
    other.write_text("o")
    [out], _ = _run([z, other], outdir)
    assert _zip_names(out) == ["in.zip", "o.txt"]


def test_each_separately(tmp_path, outdir):
    a = tmp_path / "a.txt"
    b = tmp_path / "b.txt"
    a.write_text("a")
    b.write_text("b")
    outs, _ = _run([a, b], outdir, together=False)
    assert [o.name for o in outs] == ["a.zip", "b.zip"]


def test_empty_file_list():
    outs, _ = _run([])
    assert outs == []


# ------------------------------------------------------------------ 壓縮檔重新封裝


def test_repack_tar_gz_to_zip(tmp_path, outdir):
    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("a")
    (src / "b.txt").write_text("b")
    tgz = tmp_path / "備份.tar.gz"
    with tarfile.open(tgz, "w:gz") as tf:
        tf.add(src / "a.txt", "a.txt")
        tf.add(src / "b.txt", "b.txt")
    [out], _ = _run([tgz], outdir)
    assert out == outdir / "備份.zip"
    assert _zip_names(out) == ["備份/", "備份/a.txt", "備份/b.txt"]


def test_repack_zip_to_tgz_single_folder(tmp_path, outdir):
    z = tmp_path / "pack.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("folder/x.txt", "x")
    [out], _ = _run([z], outdir, format="tgz")
    assert out == outdir / "pack.tar.gz"
    with tarfile.open(out) as tf:
        assert tf.getnames() == ["folder", "folder/x.txt"]


@pytest.mark.parametrize(
    ("name", "fmt", "label"),
    [("a.zip", "zip", "ZIP"), ("a.tar.gz", "tgz", "TAR.GZ"), ("a.tar", "tgz", "TAR.GZ")],
)
def test_repack_to_same_kind_is_refused(tmp_path, name, fmt, label):
    src = tmp_path / name
    if name.endswith(".zip"):
        with zipfile.ZipFile(src, "w") as zf:
            zf.writestr("x.txt", "x")
    else:
        with tarfile.open(src, "w:gz" if name.endswith(".gz") else "w") as tf:
            info = tarfile.TarInfo("x.txt")
            tf.addfile(info)
    with pytest.raises(errors.QSError) as exc:
        _run([src], format=fmt)
    assert str(exc.value) == f"「{name}」無法轉換成 {label}。"


def test_single_compressed_file_is_refused(tmp_path):
    import gzip

    src = tmp_path / "data.csv.gz"
    src.write_bytes(gzip.compress(b"a,b\n"))
    with pytest.raises(errors.QSError) as exc:
        _run([src])
    assert str(exc.value) == "「data.csv.gz」無法轉換成 ZIP。"


def test_cancel(tmp_path):
    import threading

    f = tmp_path / "a.txt"
    f.write_text("a")
    t = _tool()
    cancel = threading.Event()
    cancel.set()
    ctx = ToolContext(None, None, cancel)
    with pytest.raises(errors.Cancelled):
        t.run([f], t.defaults(), ctx)
    ctx.cleanup()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["a.txt"]


def test_invalid_format(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("a")
    with pytest.raises(errors.QSError) as exc:
        _run([f], format="rar")
    assert str(exc.value) == "不支援的壓縮格式：rar"


def test_special_top_level_item_is_refused(tmp_path):
    os.mkfifo(tmp_path / "pipe")
    with pytest.raises(errors.QSError) as exc:
        _run([tmp_path / "pipe"])
    assert str(exc.value) == "無法讀取檔案內容：「pipe」不是一般的檔案或資料夾。"


def test_unreadable_content(tmp_path, outdir):
    root = tmp_path / "d"
    root.mkdir()
    secret = root / "secret.txt"
    secret.write_text("s")
    secret.chmod(0)
    try:
        with pytest.raises(errors.QSError) as exc:
            _run([root], outdir)
        assert str(exc.value) == "無法讀取檔案內容：沒有權限讀取「secret.txt」。"
        assert list(outdir.iterdir()) == []
    finally:
        secret.chmod(0o644)


def test_unreadable_folder(tmp_path, outdir):
    root = tmp_path / "d"
    (root / "locked").mkdir(parents=True)
    (root / "locked").chmod(0)
    try:
        with pytest.raises(errors.QSError) as exc:
            _run([root], outdir, format="tgz")
        assert str(exc.value) == "無法讀取檔案內容：沒有權限讀取「locked」。"
    finally:
        (root / "locked").chmod(0o755)


def test_missing_item(tmp_path):
    with pytest.raises(errors.QSError) as exc:
        _run([tmp_path / "gone.txt"])
    assert str(exc.value) == "找不到檔案「gone.txt」，可能已被移動或刪除。"


def test_repack_7z_to_zip(tmp_path, outdir):
    from quickseemd.core import backends, process

    src = tmp_path / "src"
    src.mkdir()
    (src / "x.txt").write_text("x")
    archive = tmp_path / "pack.7z"
    process.run_checked(
        [
            backends.require("bsdtar"),
            "-cf",
            str(archive),
            "--format",
            "7zip",
            "-C",
            str(src),
            "x.txt",
        ],
        "bsdtar",
    )
    [out], _ = _run([archive], outdir)
    assert out == outdir / "pack.zip"
    assert _zip_names(out) == ["x.txt"]
