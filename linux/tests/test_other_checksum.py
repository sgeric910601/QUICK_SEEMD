"""計算校驗碼（other.checksum）。"""

from __future__ import annotations

import hashlib
import os
from pathlib import Path

import pytest

from quickseemd.core import errors
from quickseemd.core.model import ToolContext
from quickseemd.core.options import Kind
from quickseemd.core.tools.other import TOOLS

HELLO_256 = "2cf24dba5fb0a30e26e83b2ac5b9e29e1b161e5c1fa7425e73043362938b9824"
HELLO_1 = "aaf4c61ddcc5e8a2dabede0f3b482cd9aea9434d"
HELLO_MD5 = "5d41402abc4b2a76b9719d911017c592"


def _tool():
    return next(t for t in TOOLS if t.id == "other.checksum")


def _run(files, out=None, **values):
    t = _tool()
    vals = t.defaults()
    vals.update(values)
    ctx = ToolContext(out)
    try:
        return t.run([Path(f) for f in files], vals, ctx), ctx
    finally:
        ctx.cleanup()


def test_definition():
    t = _tool()
    assert t.title == "計算校驗碼"
    assert t.badge == "SHA" and t.action_title == "計算"
    assert t.defaults() == {"sha256": True, "sha1": False, "md5": True}
    labels = [(o.key, o.kind, o.label) for o in t.options if o.kind is Kind.TOGGLE]
    assert labels == [
        ("sha256", Kind.TOGGLE, "SHA-256"),
        ("sha1", Kind.TOGGLE, "SHA-1"),
        ("md5", Kind.TOGGLE, "MD5"),
    ]
    notes = [o.label for o in t.options if o.kind is Kind.NOTE]
    assert notes == ["結果會存成文字檔，放在第一個檔案旁邊。"]


def test_validate():
    t = _tool()
    off = {"sha256": False, "sha1": False, "md5": False}
    assert t.validate([], off, {}) == "至少選一種演算法"
    assert t.validate([], {**off, "sha1": True}, {}) is None


def test_default_report(tmp_path):
    f = tmp_path / "hello.txt"
    f.write_bytes(b"hello")
    [out], ctx = _run([f])
    assert out == tmp_path / "hello-校驗碼.txt"
    assert out.read_text("utf-8") == (
        f"hello.txt\n  SHA-256  {HELLO_256}\n  MD5      {HELLO_MD5}\n\n"
    )
    assert ctx.last_message == "hello.txt"


def test_all_algorithms_and_folders_skipped(tmp_path, outdir):
    a = tmp_path / "a.bin"
    a.write_bytes(b"hello")
    folder = tmp_path / "folder"
    folder.mkdir()
    big = tmp_path / "big.bin"
    data = os.urandom(9 * 1024 * 1024 + 123)
    big.write_bytes(data)
    [out], _ = _run([a, folder, big], outdir, sha1=True)
    assert out == outdir / "a-校驗碼.txt"
    assert out.read_text("utf-8") == (
        f"a.bin\n  SHA-256  {HELLO_256}\n  SHA-1    {HELLO_1}\n  MD5      {HELLO_MD5}\n\n"
        f"big.bin\n  SHA-256  {hashlib.sha256(data).hexdigest()}\n"
        f"  SHA-1    {hashlib.sha1(data).hexdigest()}\n  MD5      {hashlib.md5(data).hexdigest()}\n\n"
    )


def test_only_sha1(tmp_path, outdir):
    f = tmp_path / "hello.txt"
    f.write_bytes(b"hello")
    [out], _ = _run([f], outdir, sha256=False, sha1=True, md5=False)
    assert out.read_text("utf-8") == f"hello.txt\n  SHA-1    {HELLO_1}\n\n"


def test_none_selected_is_refused_at_run(tmp_path):
    f = tmp_path / "hello.txt"
    f.write_bytes(b"hello")
    with pytest.raises(errors.QSError) as exc:
        _run([f], sha256=False, sha1=False, md5=False)
    assert str(exc.value) == "至少選一種演算法"


def test_unreadable_file(tmp_path, outdir):
    f = tmp_path / "secret.bin"
    f.write_bytes(b"x")
    f.chmod(0)
    try:
        with pytest.raises(errors.QSError) as exc:
            _run([f], outdir)
        assert str(exc.value).startswith("無法讀取檔案內容：")
    finally:
        f.chmod(0o644)


def test_special_files_are_skipped(tmp_path, outdir):
    import os

    os.mkfifo(tmp_path / "pipe")
    f = tmp_path / "hello.txt"
    f.write_bytes(b"hello")
    [out], _ = _run([tmp_path / "pipe", f], outdir, md5=False)
    assert out == outdir / "pipe-校驗碼.txt"
    assert out.read_text("utf-8") == f"hello.txt\n  SHA-256  {HELLO_256}\n\n"
