"""解壓縮（other.extract）：各種格式、交付規則、路徑穿越防護。"""

from __future__ import annotations

import bz2
import gzip
import io
import lzma
import os
import stat
import tarfile
import time
import zipfile
from pathlib import Path

import pytest

from quickseemd.core import backends, errors, process
from quickseemd.core.model import ToolContext
from quickseemd.core.tools.other import TOOLS

UNSAFE_PREFIX = "含有不安全的路徑"


def _tool():
    return next(t for t in TOOLS if t.id == "other.extract")


def _run(files, out=None):
    t = _tool()
    ctx = ToolContext(out)
    try:
        return t.run([Path(f) for f in files], t.defaults(), ctx)
    finally:
        ctx.cleanup()


def _run_refused(archive: Path, out: Path, tmp_path: Path) -> str:
    """執行並預期被拒絕；在清掉暫存資料夾之前確認沒有任何「evil」檔案被寫出來。"""
    t = _tool()
    ctx = ToolContext(out)
    try:
        with pytest.raises(errors.QSError) as exc:
            t.run([archive], t.defaults(), ctx)
        roots = [tmp_path, Path(os.environ["XDG_CACHE_HOME"])]
        leaked = [p for r in roots for p in r.rglob("*evil*")]
        assert leaked == [], f"寫出了不該寫的檔案：{leaked}"
        assert list(out.iterdir()) == []
    finally:
        ctx.cleanup()
    return str(exc.value)


def _zip(path: Path, entries: dict[str, bytes | str], **kw) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED, **kw) as zf:
        for name, data in entries.items():
            zf.writestr(name, data)
    return path


def _zip_symlink(path: Path, name: str, target: str, extra: dict[str, str] | None = None) -> Path:
    with zipfile.ZipFile(path, "w") as zf:
        for n, d in (extra or {}).items():
            zf.writestr(n, d)
        info = zipfile.ZipInfo(name)
        info.create_system = 3
        info.external_attr = (stat.S_IFLNK | 0o777) << 16
        zf.writestr(info, target)
    return path


def _tar(path: Path, members: list[tarfile.TarInfo], mode: str = "w") -> Path:
    with tarfile.open(path, mode) as tf:
        for m in members:
            if m.isreg():
                tf.addfile(m, io.BytesIO(b"x" * m.size))
            else:
                tf.addfile(m)
    return path


def _member(name: str, kind: bytes = tarfile.REGTYPE, linkname: str = "") -> tarfile.TarInfo:
    info = tarfile.TarInfo(name)
    info.type = kind
    info.linkname = linkname
    info.size = 1 if kind == tarfile.REGTYPE else 0
    info.mode = 0o644
    return info


@pytest.fixture
def indir(tmp_path: Path) -> Path:
    d = tmp_path / "in"
    d.mkdir()
    return d


# ------------------------------------------------------------------ 定義


def test_definition(indir):
    t = _tool()
    assert t.title == "解壓縮"
    assert t.subtitle == "解開 ZIP、TAR、TAR.GZ、7z、GZ 等壓縮檔到同名資料夾。"
    assert t.badge == "UNZIP" and t.action_title == "解壓縮" and t.input_label == "壓縮檔"
    assert t.options == ()
    assert t.presets == {"other.unzip": {}}
    assert t.accepts(_zip(indir / "a.zip", {"a.txt": "a"}))
    plain = indir / "a.txt"
    plain.write_text("x")
    assert not t.accepts(plain)


# ------------------------------------------------------------------ 交付規則


def test_zip_several_items_become_folder(indir, outdir):
    z = _zip(indir / "照片.zip", {"a.txt": "甲", "sub/b.txt": "乙"})
    [out] = _run([z], outdir)
    assert out == outdir / "照片"
    assert (out / "a.txt").read_text() == "甲"
    assert (out / "sub" / "b.txt").read_text() == "乙"


def test_zip_single_folder_is_delivered_as_itself(indir, outdir):
    z = _zip(indir / "pack.zip", {"folder/a.txt": "a", "folder/b.txt": "b"})
    [out] = _run([z], outdir)
    assert out == outdir / "folder"
    assert sorted(p.name for p in out.iterdir()) == ["a.txt", "b.txt"]


def test_zip_single_file(indir):
    z = _zip(indir / "pack.zip", {"only.txt": "one"})
    [out] = _run([z])
    assert out == indir / "only.txt"
    assert out.read_text() == "one"
    assert z.exists()


def test_name_clash_gets_number(indir):
    z = _zip(indir / "pack.zip", {"a.txt": "a", "b.txt": "b"})
    (indir / "pack").mkdir()
    [out] = _run([z])
    assert out == indir / "pack (1)"
    assert list((indir / "pack").iterdir()) == []


def test_junk_is_removed(indir, outdir):
    z = _zip(
        indir / "mac.zip",
        {
            "__MACOSX/._a.txt": "junk",
            "a.txt": "a",
            ".DS_Store": "junk",
            "._a.txt": "junk",
            "dir/.DS_Store": "junk",
            "dir/._x": "junk",
            "dir/x": "x",
        },
    )
    [out] = _run([z], outdir)
    assert out == outdir / "mac"
    found = sorted(str(p.relative_to(out)) for p in out.rglob("*"))
    assert found == ["a.txt", "dir", "dir/x"]


def test_zip_with_only_junk_is_empty(indir):
    z = _zip(indir / "j.zip", {"__MACOSX/x": "junk", ".DS_Store": "junk"})
    with pytest.raises(errors.QSError) as exc:
        _run([z])
    assert str(exc.value) == "檔案沒有可轉換的內容：壓縮檔裡沒有任何檔案。"


def test_empty_zip(indir):
    z = _zip(indir / "e.zip", {})
    with pytest.raises(errors.QSError) as exc:
        _run([z])
    assert str(exc.value) == "檔案沒有可轉換的內容：壓縮檔裡沒有任何檔案。"


def test_not_an_archive(indir):
    f = indir / "a.txt"
    f.write_text("x")
    with pytest.raises(errors.QSError) as exc:
        _run([f])
    assert str(exc.value) == "「a.txt」無法轉換成 解壓縮到資料夾。"


def test_corrupt_zip(indir, outdir):
    z = indir / "bad.zip"
    z.write_bytes(b"PK\x03\x04" + b"\x00garbage" * 20)
    with pytest.raises(errors.QSError) as exc:
        _run([z], outdir)
    assert str(exc.value) == "「bad.zip」無法解壓縮：檔案可能已損毀，或不是支援的壓縮格式。"
    assert list(outdir.iterdir()) == []


def test_zip_permissions_and_times(indir, outdir):
    z = indir / "p.zip"
    with zipfile.ZipFile(z, "w") as zf:
        info = zipfile.ZipInfo("run.sh", date_time=(2020, 1, 2, 3, 4, 6))
        info.create_system = 3
        info.external_attr = (stat.S_IFREG | 0o4755) << 16
        zf.writestr(info, "#!/bin/sh\n")
        zf.writestr("b.txt", "b")
    [out] = _run([z], outdir)
    st = (out / "run.sh").stat()
    assert st.st_mode & 0o111
    assert not st.st_mode & stat.S_ISUID
    assert time.localtime(st.st_mtime)[:6] == (2020, 1, 2, 3, 4, 6)


def test_zip_internal_symlink_is_kept(indir, outdir):
    z = _zip_symlink(indir / "l.zip", "d/link", "a.txt", {"d/a.txt": "a"})
    [out] = _run([z], outdir)
    assert out == outdir / "d"
    assert (out / "link").is_symlink()
    assert os.readlink(out / "link") == "a.txt"


def test_zip_legacy_big5_names(indir, outdir):
    z = _zip(indir / "big5.zip", {"AAAA.txt": "x"})
    raw = z.read_bytes()
    assert raw.count(b"AAAA") == 2
    z.write_bytes(raw.replace(b"AAAA", "測試".encode("cp950")))
    [out] = _run([z], outdir)
    assert out == outdir / "測試.txt"


def test_encrypted_zip(indir, outdir, tmp_path):
    src = tmp_path / "secret.txt"
    src.write_text("s")
    z = indir / "locked.zip"
    process.run_checked(
        [backends.require("7z"), "a", "-tzip", "-pSECRET", "-bd", str(z), str(src)], "7z"
    )
    with pytest.raises(errors.QSError) as exc:
        _run([z], outdir)
    assert str(exc.value) == "「locked.zip」有密碼保護，目前無法解壓縮。"


# ------------------------------------------------------------------ tar 與單一壓縮檔


@pytest.mark.parametrize(
    ("name", "mode"),
    [
        ("a.tar", "w"),
        ("a.tar.gz", "w:gz"),
        ("a.tgz", "w:gz"),
        ("a.tar.bz2", "w:bz2"),
        ("a.tar.xz", "w:xz"),
    ],
)
def test_tar_family(indir, outdir, name, mode):
    src = indir / "src"
    (src / "sub").mkdir(parents=True)
    (src / "x.txt").write_text("x")
    (src / "sub" / "y.txt").write_text("y")
    (src / "hard").hardlink_to(src / "x.txt")
    (src / "ln").symlink_to("x.txt")
    archive = indir / name
    with tarfile.open(archive, mode) as tf:
        for p in ("x.txt", "sub", "sub/y.txt", "hard", "ln"):
            tf.add(src / p, p, recursive=False)
    [out] = _run([archive], outdir)
    assert out == outdir / "a"
    assert (out / "x.txt").read_text() == "x"
    assert (out / "sub" / "y.txt").read_text() == "y"
    assert (out / "hard").read_text() == "x"
    assert os.readlink(out / "ln") == "x.txt"


@pytest.mark.parametrize(
    ("name", "compress"),
    [
        ("data.csv.gz", gzip.compress),
        ("data.csv.bz2", bz2.compress),
        ("data.csv.xz", lzma.compress),
    ],
)
def test_single_compressed_file(indir, name, compress):
    payload = b"a,b\n1,2\n" * 1000
    src = indir / name
    src.write_bytes(compress(payload))
    [out] = _run([src])
    assert out == indir / "data.csv"
    assert out.read_bytes() == payload


def test_corrupt_gz(indir, outdir):
    src = indir / "x.gz"
    src.write_bytes(b"\x1f\x8b\x08\x00garbage-garbage")
    with pytest.raises(errors.QSError) as exc:
        _run([src], outdir)
    assert str(exc.value) == "「x.gz」無法解壓縮：檔案可能已損毀，或不是支援的壓縮格式。"
    assert list(outdir.iterdir()) == []


def _bsdtar(*args: str) -> None:
    process.run_checked([backends.require("bsdtar"), *args], "bsdtar")


@pytest.mark.parametrize(
    ("name", "fmt"), [("a.7z", ["--format", "7zip"]), ("a.cpio", ["--format", "cpio"])]
)
def test_external_formats(indir, outdir, name, fmt):
    src = indir / "src"
    src.mkdir()
    (src / "x.txt").write_text("x")
    (src / "y.txt").write_text("y")
    archive = indir / name
    _bsdtar("-cf", str(archive), *fmt, "-C", str(src), "x.txt", "y.txt")
    [out] = _run([archive], outdir)
    assert out == outdir / "a"
    assert sorted(p.name for p in out.iterdir()) == ["x.txt", "y.txt"]


def test_tar_zst_via_bsdtar(indir, outdir):
    src = indir / "src"
    src.mkdir()
    (src / "x.txt").write_text("x")
    archive = indir / "a.tar.zst"
    _bsdtar("-cf", str(archive), "--zstd", "-C", str(src), "x.txt")
    [out] = _run([archive], outdir)
    assert out == outdir / "x.txt"


def test_iso(indir, outdir):
    src = indir / "src"
    src.mkdir()
    (src / "readme.txt").write_text("iso")
    (src / "two.txt").write_text("2")
    archive = indir / "disc.iso"
    _bsdtar("-cf", str(archive), "--format", "iso9660", "-C", str(src), ".")
    [out] = _run([archive], outdir)
    assert out == outdir / "disc"
    assert (out / "readme.txt").read_text() == "iso"


def test_7z_fallback_without_bsdtar(indir, outdir, monkeypatch):
    src = indir / "src"
    src.mkdir()
    (src / "x.txt").write_text("x")
    (src / "y.txt").write_text("y")
    archive = indir / "b.7z"
    _bsdtar("-cf", str(archive), "--format", "7zip", "-C", str(src), "x.txt", "y.txt")
    real_find = backends.find
    monkeypatch.setattr(backends, "find", lambda k: None if k == "bsdtar" else real_find(k))
    [out] = _run([archive], outdir)
    assert sorted(p.name for p in out.iterdir()) == ["x.txt", "y.txt"]


def test_no_external_tool(indir, monkeypatch):
    archive = indir / "c.7z"
    archive.write_bytes(b"7z\xbc\xaf\x27\x1c" + b"\x00" * 30)
    monkeypatch.setattr(backends, "find", lambda k: None)
    with pytest.raises(errors.QSError) as exc:
        _run([archive])
    assert str(exc.value) == "找不到 bsdtar，這個格式需要先安裝它。"


# ------------------------------------------------------------------ 路徑穿越防護


@pytest.mark.parametrize(
    "bad",
    [
        "../evil.txt",
        "/evil.txt",
        "a/../../evil.txt",
        "..\\evil.txt",
        "C:/evil.txt",
        "ok/../../evil",
    ],
)
def test_zip_slip_refused(indir, outdir, tmp_path, bad):
    z = _zip(indir / "bad.zip", {"fine.txt": "ok", bad: "evil"})
    msg = _run_refused(z, outdir, tmp_path)
    assert msg.startswith(f"「bad.zip」{UNSAFE_PREFIX}")
    assert msg.endswith("為了保護你的檔案，已停止解壓縮。")


@pytest.mark.parametrize("target", ["/etc", "../../evil-target", "x/../../.."])
def test_zip_symlink_escape_refused(indir, outdir, tmp_path, target):
    z = _zip_symlink(indir / "bad.zip", "lnk", target, {"a.txt": "a"})
    assert UNSAFE_PREFIX in _run_refused(z, outdir, tmp_path)


def test_zip_device_entry_refused(indir, outdir, tmp_path):
    z = indir / "bad.zip"
    with zipfile.ZipFile(z, "w") as zf:
        info = zipfile.ZipInfo("dev")
        info.create_system = 3
        info.external_attr = (stat.S_IFCHR | 0o644) << 16
        zf.writestr(info, "")
    assert UNSAFE_PREFIX in _run_refused(z, outdir, tmp_path)


@pytest.mark.parametrize(
    "members",
    [
        [_member("../evil.txt")],
        [_member("/evil.txt")],
        [_member("ok.txt"), _member("lnk", tarfile.SYMTYPE, "/etc")],
        [_member("lnk", tarfile.SYMTYPE, "../evil-dir"), _member("lnk/evil.txt")],
        [_member("ok.txt"), _member("hard", tarfile.LNKTYPE, "../evil.txt")],
        [_member("dev", tarfile.CHRTYPE)],
        [_member("blk", tarfile.BLKTYPE)],
        [_member("fifo", tarfile.FIFOTYPE)],
    ],
)
def test_tar_traversal_refused(indir, outdir, tmp_path, members):
    archive = _tar(indir / "bad.tar.gz", members, "w:gz")
    assert UNSAFE_PREFIX in _run_refused(archive, outdir, tmp_path)


def test_7z_traversal_refused_via_bsdtar(indir, outdir, tmp_path):
    evil_tar = _tar(tmp_path / "src.tar", [_member("ok.txt"), _member("../evil.txt")])
    archive = indir / "bad.7z"
    _bsdtar("-cf", str(archive), "--format", "7zip", f"@{evil_tar}")
    assert UNSAFE_PREFIX in _run_refused(archive, outdir, tmp_path)


def test_7z_traversal_refused_via_7z(indir, outdir, tmp_path, monkeypatch):
    evil_tar = _tar(tmp_path / "src.tar", [_member("ok.txt"), _member("../evil.txt")])
    archive = indir / "bad.7z"
    _bsdtar("-cf", str(archive), "--format", "7zip", f"@{evil_tar}")
    real_find = backends.find
    monkeypatch.setattr(backends, "find", lambda k: None if k == "bsdtar" else real_find(k))
    assert UNSAFE_PREFIX in _run_refused(archive, outdir, tmp_path)


def test_7z_absolute_symlink_refused_via_7z(indir, outdir, tmp_path, monkeypatch):
    src = tmp_path / "srcdir"
    src.mkdir()
    (src / "a.txt").write_text("a")
    (src / "lnk").symlink_to("/etc")
    archive = indir / "bad.7z"
    _bsdtar("-cf", str(archive), "--format", "7zip", "-C", str(src), "a.txt", "lnk")
    real_find = backends.find
    monkeypatch.setattr(backends, "find", lambda k: None if k == "bsdtar" else real_find(k))
    msg = _run_refused(archive, outdir, tmp_path)
    assert UNSAFE_PREFIX in msg or "7-Zip" in msg


# ------------------------------------------------------------------ 連結串接、衝突與其他邊界


def test_zip_symlink_chain_escape_refused(indir, outdir, tmp_path):
    """l1 → .、l2 → l1/..：文字上看似在裡面，實際指到外面；建立後的檢查要擋下。"""
    z = indir / "bad.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for name, target in (("l1", "."), ("l2", "l1/..")):
            info = zipfile.ZipInfo(name)
            info.create_system = 3
            info.external_attr = (stat.S_IFLNK | 0o777) << 16
            zf.writestr(info, target)
    assert UNSAFE_PREFIX in _run_refused(z, outdir, tmp_path)


def _run_contained(archive: Path, out: Path, tmp_path: Path) -> None:
    """執行（可能成功也可能被拒絕），確認沒有任何「evil」檔案出現在輸出資料夾以外、
    解出來的符號連結也都指在輸出的項目裡面。"""
    t = _tool()
    ctx = ToolContext(out)
    try:
        try:
            outputs = t.run([archive], t.defaults(), ctx)
        except errors.QSError as exc:
            assert UNSAFE_PREFIX in str(exc)
            outputs = []
        roots = [tmp_path, Path(os.environ["XDG_CACHE_HOME"])]
        leaked = [p for r in roots for p in r.rglob("*evil*") if out not in p.parents]
        assert leaked == []
        for o in outputs:
            real_out = os.path.realpath(o)
            for p in [o, *o.rglob("*")] if o.is_dir() else [o]:
                if p.is_symlink():
                    assert os.path.realpath(p).startswith(real_out)
    finally:
        ctx.cleanup()


def test_tar_symlink_chain_stays_inside(indir, outdir, tmp_path):
    """tar 的 data 篩選器會把連結目標正規化（l1/.. → .）或拒絕；兩種結果都不能跑到外面。"""
    members = [_member("l1", tarfile.SYMTYPE, "."), _member("l2", tarfile.SYMTYPE, "l1/..")]
    _run_contained(_tar(indir / "bad.tar", members), outdir, tmp_path)


def test_tar_write_through_symlink_chain_stays_inside(indir, outdir, tmp_path):
    members = [
        _member("l1", tarfile.SYMTYPE, "."),
        _member("l2", tarfile.SYMTYPE, "l1/.."),
        _member("l2/evil.txt"),
    ]
    _run_contained(_tar(indir / "bad.tar", members), outdir, tmp_path)


def test_tar_empty_hardlink_refused(indir, outdir, tmp_path):
    archive = _tar(indir / "bad.tar", [_member("hard", tarfile.LNKTYPE, ".")])
    assert UNSAFE_PREFIX in _run_refused(archive, outdir, tmp_path)


def test_zip_huge_link_target_refused(indir, outdir, tmp_path):
    z = _zip_symlink(indir / "bad.zip", "lnk", "a" * 5000)
    assert UNSAFE_PREFIX in _run_refused(z, outdir, tmp_path)


@pytest.mark.parametrize(
    "entries",
    [
        {"a": "file", "a/b.txt": "under a file"},
        {"a/": "", "a": "file over folder"},
    ],
)
def test_zip_conflicting_entries(indir, outdir, entries):
    z = _zip(indir / "c.zip", entries)
    with pytest.raises(errors.QSError) as exc:
        _run([z], outdir)
    assert str(exc.value).startswith("「c.zip」裡有互相衝突的項目「")
    assert list(outdir.iterdir()) == []


@pytest.mark.filterwarnings("ignore:Duplicate name")
def test_zip_link_conflicts_with_file(indir, outdir):
    z = _zip_symlink(indir / "c.zip", "x", "y", {"x": "file", "y": "y"})
    with pytest.raises(errors.QSError) as exc:
        _run([z], outdir)
    assert str(exc.value) == "「c.zip」裡有互相衝突的項目「x」，無法解壓縮。"


def test_zip_unix_directories(indir, outdir):
    z = indir / "d.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.mkdir("top")
        zf.mkdir("top/empty")
        zf.writestr("top/a.txt", "a")
    [out] = _run([z], outdir)
    assert out == outdir / "top"
    assert (out / "empty").is_dir()


def test_truncated_tar_gz(indir, outdir):
    good = _tar(indir / "src.tar.gz", [_member(f"f{i}.txt") for i in range(50)], "w:gz")
    bad = indir / "cut.tar.gz"
    bad.write_bytes(good.read_bytes()[:60])
    with pytest.raises(errors.QSError) as exc:
        _run([bad], outdir)
    assert str(exc.value) == "「cut.tar.gz」無法解壓縮：檔案可能已損毀，或不是支援的壓縮格式。"


def test_unreadable_archive(indir, outdir):
    z = _zip(indir / "locked.zip", {"a.txt": "a"})
    z.chmod(0)
    try:
        with pytest.raises(errors.QSError) as exc:
            _run([z], outdir)
        assert str(exc.value) == "無法讀取檔案內容：沒有權限讀取「locked.zip」。"
    finally:
        z.chmod(0o644)


def _seven_zip(*args: str) -> None:
    process.run_checked([backends.require("7z"), "-bd", *args], "7z")


def test_encrypted_7z_via_bsdtar(indir, outdir, tmp_path):
    src = tmp_path / "secret.txt"
    src.write_text("s")
    archive = indir / "locked.7z"
    _seven_zip("a", "-pSECRET", str(archive), str(src))
    with pytest.raises(errors.QSError) as exc:
        _run([archive], outdir)
    assert str(exc.value) == "「locked.7z」有密碼保護，目前無法解壓縮。"


@pytest.mark.parametrize("header", [False, True])
def test_encrypted_7z_via_7z(indir, outdir, tmp_path, monkeypatch, header):
    src = tmp_path / "secret.txt"
    src.write_text("s")
    archive = indir / "locked.7z"
    _seven_zip("a", "-pSECRET", f"-mhe={'on' if header else 'off'}", str(archive), str(src))
    real_find = backends.find
    monkeypatch.setattr(backends, "find", lambda k: None if k == "bsdtar" else real_find(k))
    with pytest.raises(errors.QSError) as exc:
        _run([archive], outdir)
    assert str(exc.value) == "「locked.7z」有密碼保護，目前無法解壓縮。"


def test_7z_route_removes_junk(indir, outdir, monkeypatch):
    src = indir / "src"
    (src / "__MACOSX").mkdir(parents=True)
    (src / "__MACOSX" / "._a").write_text("junk")
    (src / "sub").mkdir()
    (src / "sub" / ".DS_Store").write_text("junk")
    (src / "sub" / "x.txt").write_text("x")
    (src / "a.txt").write_text("a")
    archive = indir / "m.7z"
    _bsdtar("-cf", str(archive), "--format", "7zip", "-C", str(src), ".")
    real_find = backends.find
    monkeypatch.setattr(backends, "find", lambda k: None if k == "bsdtar" else real_find(k))
    [out] = _run([archive], outdir)
    found = sorted(str(p.relative_to(out)) for p in out.rglob("*"))
    assert found == ["a.txt", "sub", "sub/x.txt"]


def test_corrupt_7z_without_7z(indir, outdir, monkeypatch):
    archive = indir / "bad.7z"
    archive.write_bytes(b"7z\xbc\xaf\x27\x1c\x00\x04" + bytes(range(7, 200)))
    real_find = backends.find
    monkeypatch.setattr(backends, "find", lambda k: None if k == "7z" else real_find(k))
    with pytest.raises(errors.QSError) as exc:
        _run([archive], outdir)
    assert str(exc.value) == "「bad.7z」無法解壓縮：檔案可能已損毀，或不是支援的壓縮格式。"


def test_7z_extraction_failure(indir, outdir, monkeypatch):
    from quickseemd.core.tools.other import unpack_external

    src = indir / "src"
    src.mkdir()
    (src / "x.txt").write_text("x")
    archive = indir / "f.7z"
    _bsdtar("-cf", str(archive), "--format", "7zip", "-C", str(src), "x.txt")
    real_find = backends.find
    monkeypatch.setattr(backends, "find", lambda k: None if k == "bsdtar" else real_find(k))
    real_run = process.run

    def fake_run(args, **kw):
        if args[1] == "x":
            return process.Result(2, "", "ERROR: Data Error")
        return real_run(args, **kw)

    monkeypatch.setattr(unpack_external.process, "run", fake_run)
    with pytest.raises(errors.QSError) as exc:
        _run([archive], outdir)
    assert "無法解壓縮" in str(exc.value)


def test_check_listing_rules():
    from quickseemd.core.tools.other import unpack_external
    from quickseemd.core.tools.other.archivekind import Encrypted, UnsafeEntry

    ok = "Path = a.txt\nAttributes = A -rw-r--r--\nEncrypted = -\n\nPath = d\nAttributes = D\n"
    unpack_external.check_listing(ok)
    with pytest.raises(UnsafeEntry):
        unpack_external.check_listing("Path = dev/null\nAttributes = A crw-rw-rw-\n")
    with pytest.raises(UnsafeEntry):
        unpack_external.check_listing("Path = ../x\nAttributes = A -rw-r--r--\n")
    with pytest.raises(Encrypted):
        unpack_external.check_listing("Path = s.txt\nEncrypted = +\n")


# ------------------------------------------------------------------ 種類判斷與路徑規則


@pytest.mark.parametrize(
    ("name", "kind"),
    [
        ("a.zip", "zip"),
        ("A.ZIP", "zip"),
        ("a.tar", "tar"),
        ("a.tgz", "tar"),
        ("a.tar.zst", "tar"),
        ("a.7z", "7z"),
        ("a.rar", "rar"),
        ("a.pkg", "xar"),
        ("a.cpio", "cpio"),
        ("a.iso", "iso"),
        ("a.gz", "gzip"),
        ("a.bz2", "bzip2"),
        ("a.xz", "xz"),
        ("a.docx", None),
        ("a.epub", None),
        ("a.txt", None),
        ("noext", None),
        (".zip", None),
    ],
)
def test_archive_kind(tmp_path, name, kind):
    from quickseemd.core.tools.other.archivekind import archive_kind

    p = tmp_path / name
    p.write_bytes(b"")
    result = archive_kind(p)
    assert (result.value if result else None) == kind


def test_archive_kind_folder(tmp_path):
    from quickseemd.core.tools.other.archivekind import archive_kind

    d = tmp_path / "x.zip"
    d.mkdir()
    assert archive_kind(d) is None


def test_path_rules():
    from quickseemd.core.tools.other.archivekind import (
        UnsafeEntry,
        is_junk,
        link_stays_inside,
        safe_parts,
    )

    assert safe_parts("./a//b/") == ("a", "b")
    assert safe_parts("a\\b", backslash=True) == ("a", "b")
    assert safe_parts("a\\..\\b") == ("a\\..\\b",)
    for bad in ("a\0b", "/x", "C:x", "a/../../b"):
        with pytest.raises(UnsafeEntry):
            safe_parts(bad)
    assert link_stays_inside(("d", "l"), "../x")
    assert link_stays_inside(("d", "l"), "./y/../z")
    assert not link_stays_inside(("l",), "../x")
    assert not link_stays_inside(("l",), "")
    assert not link_stays_inside(("l",), "C:/x")
    assert is_junk(("__MACOSX", "a")) and is_junk(("a", "._b")) and is_junk((".DS_Store",))
    assert not is_junk(()) and not is_junk(("a", "b"))


def test_verify_tree_rejects_special_files(tmp_path):
    from quickseemd.core.tools.other.archivekind import UnsafeEntry, verify_tree

    os.mkfifo(tmp_path / "pipe")
    with pytest.raises(UnsafeEntry):
        verify_tree(tmp_path)
