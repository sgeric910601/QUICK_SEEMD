from __future__ import annotations

import errno
import os
from pathlib import Path

import pytest

from quickseemd.core import errors, naming


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("a.pdf", ("a", "pdf")),
        ("a.tar.gz", ("a", "tar.gz")),
        ("A.TAR.XZ", ("A", "TAR.XZ")),
        (".bashrc", (".bashrc", "")),
        ("README", ("README", "")),
        ("x.", ("x.", "")),
        ("報告.v2.docx", ("報告.v2", "docx")),
    ],
)
def test_split_name(name, expected):
    assert naming.split_name(name) == expected


def test_sanitize():
    assert naming.sanitize(" a/b:c ") == "a-b-c"
    assert naming.sanitize(".hidden") == "_hidden"
    assert naming.sanitize("   ") == "未命名"


def test_output_name():
    assert naming.output_name("/x/文件.pdf", "已壓縮", "pdf") == "文件-已壓縮.pdf"
    assert naming.output_name("/x/a.tar.gz", None, "zip") == "a.zip"
    assert naming.output_name("/x/a", "QR內容", "txt") == "a-QR內容.txt"
    assert naming.output_name("/x/a.pdf", None, "") == "a"


def test_candidates_keep_compound_ext():
    c = list(naming.candidate_names("a.tar.gz"))[:3]
    assert c == ["a.tar.gz", "a (1).tar.gz", "a (2).tar.gz"]


def test_commit_never_overwrites(tmp_path):
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "a.txt").write_text("原本的內容")
    (dest / "a (1).txt").write_text("也是原本的")
    work = tmp_path / "work"
    work.mkdir()
    src = work / "tmp.txt"
    src.write_text("新的")
    out = naming.commit(src, dest, "a.txt")
    assert out.name == "a (2).txt"
    assert out.read_text() == "新的"
    assert (dest / "a.txt").read_text() == "原本的內容"
    assert not src.exists()


def test_commit_directory(tmp_path):
    dest = tmp_path / "dest"
    dest.mkdir()
    (dest / "pages").mkdir()
    src = tmp_path / "work" / "pages"
    src.mkdir(parents=True)
    (src / "p1.png").write_bytes(b"x")
    out = naming.commit(src, dest, "pages")
    assert out.name == "pages (1)"
    assert (out / "p1.png").read_bytes() == b"x"


def test_commit_broken_symlink_counts_as_taken(tmp_path):
    dest = tmp_path / "d"
    dest.mkdir()
    (dest / "a.txt").symlink_to(tmp_path / "missing")
    src = tmp_path / "s.txt"
    src.write_text("x")
    assert naming.commit(src, dest, "a.txt").name == "a (1).txt"


def test_commit_fallback_without_renameat2(tmp_path, monkeypatch):
    def no_renameat2(src, dst):
        raise OSError(errno.EINVAL, "不支援")

    monkeypatch.setattr(naming, "_rename_noreplace", no_renameat2)
    dest = tmp_path / "d"
    dest.mkdir()
    (dest / "a.txt").write_text("舊")
    src = tmp_path / "s.txt"
    src.write_text("新")
    out = naming.commit(src, dest, "a.txt")
    assert out.name == "a (1).txt" and out.read_text() == "新"
    assert (dest / "a.txt").read_text() == "舊"


def test_commit_cross_device_copies_exclusively(tmp_path, monkeypatch):
    real = naming._rename_noreplace_or_link

    def exdev(src, dst):
        raise OSError(errno.EXDEV, "跨磁碟")

    monkeypatch.setattr(naming, "_rename_noreplace_or_link", exdev)
    dest = tmp_path / "d"
    dest.mkdir()
    src = tmp_path / "s.bin"
    src.write_bytes(os.urandom(1000))
    data = src.read_bytes()
    out = naming.commit(src, dest, "s.bin")
    assert out.read_bytes() == data and not src.exists()
    monkeypatch.setattr(naming, "_rename_noreplace_or_link", real)


def test_commit_unwritable_folder(tmp_path):
    with pytest.raises(errors.QSError, match="無法寫入輸出位置"):
        naming.commit(tmp_path / "x", tmp_path / "missing", "a.txt")


def test_work_dir_is_removable_only_if_ours(tmp_path):
    d = naming.make_work_dir(tmp_path)
    assert d.is_dir()
    naming.remove_work_dir(d)
    assert not d.exists()
    keep = tmp_path / "mine"
    keep.mkdir()
    naming.remove_work_dir(keep)
    assert keep.exists()


def test_unique_path(tmp_path):
    (tmp_path / "a.pdf").write_text("")
    assert naming.unique_path(tmp_path, "a.pdf") == Path(tmp_path / "a (1).pdf")
