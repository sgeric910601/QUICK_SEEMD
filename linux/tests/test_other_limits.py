"""解壓縮的大小上限：壓縮炸彈、總量上限、磁碟可用空間；失敗時不留任何暫存檔。

炸彈都用串流方式一段一段建立（記憶體只用到 1 MiB），解開時應該在寫出約 10 MiB 前後就停下。
"""

from __future__ import annotations

import bz2
import io
import os
import tarfile
import time
import zipfile
from collections import namedtuple
from pathlib import Path

import pytest

from quickseemd.core import backends, errors, process
from quickseemd.core.model import ToolContext
from quickseemd.core.tools.other import TOOLS, unpack
from quickseemd.core.tools.other.archivekind import ArchiveKind
from quickseemd.core.tools.other.extractlimits import (
    DEFAULT_LIMITS,
    ExtractLimits,
)

MiB = 1 << 20
BOMB_MIB = 256
RATIO_TEXT = "的壓縮比異常（超過 1000 倍），可能是惡意的壓縮炸彈，已停止解壓縮。"
FAST_SECONDS = 10


class _Zeros(io.RawIOBase):
    """讀出指定長度的 0x00（不佔記憶體）。"""

    def __init__(self, size: int) -> None:
        self._left = size

    def readable(self) -> bool:
        return True

    def readinto(self, buf) -> int:  # noqa: ANN001
        n = min(len(buf), self._left)
        buf[:n] = bytes(n)
        self._left -= n
        return n


def _bz2_bomb(path: Path, mib: int = BOMB_MIB) -> Path:
    comp = bz2.BZ2Compressor(9)
    with open(path, "xb") as out:
        for _ in range(mib):
            out.write(comp.compress(bytes(MiB)))
        out.write(comp.flush())
    return path


def _tar_bomb(path: Path, mib: int = BOMB_MIB) -> Path:
    with tarfile.open(path, "w:bz2") as tf:
        info = tarfile.TarInfo("zeros.bin")
        info.size = mib * MiB
        tf.addfile(info, io.BufferedReader(_Zeros(info.size)))
    return path


def _zip_bomb(path: Path, mib: int = BOMB_MIB) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_BZIP2) as zf:
        info = zipfile.ZipInfo("zeros.bin")
        info.compress_type = zipfile.ZIP_BZIP2
        with zf.open(info, "w", force_zip64=True) as dst:
            for _ in range(mib):
                dst.write(bytes(MiB))
        zf.writestr("readme.txt", "hello")
    return path


def _sevenzip_bomb(path: Path, tmp_path: Path, mib: int = BOMB_MIB) -> Path:
    sparse = tmp_path / "zeros.bin"
    with open(sparse, "xb") as fh:
        fh.truncate(mib * MiB)  # 稀疏檔：不佔磁碟
    process.run_checked(
        [backends.require("bsdtar"), "-cf", str(path), "--format", "7zip"]
        + ["-C", str(tmp_path), "zeros.bin"],
        "bsdtar",
    )
    return path


def _leftovers(out: Path) -> list[Path]:
    roots = [Path(os.environ["XDG_CACHE_HOME"]), out]
    found = [p for r in roots for p in r.rglob(".extract-*")]
    found += [p for r in roots for p in r.rglob(".convert-*")]
    return found


def _run_refused(archive: Path, out: Path) -> str:
    t = next(t for t in TOOLS if t.id == "other.extract")
    ctx = ToolContext(out)
    started = time.monotonic()
    try:
        with pytest.raises(errors.QSError) as exc:
            t.run([archive], t.defaults(), ctx)
        assert time.monotonic() - started < FAST_SECONDS
        assert _leftovers(out) == [], "中止後不能留下暫存資料夾"
        assert list(out.iterdir()) == []
    finally:
        ctx.cleanup()
    return str(exc.value)


@pytest.fixture
def indir(tmp_path: Path) -> Path:
    d = tmp_path / "in"
    d.mkdir()
    return d


# ------------------------------------------------------------------ 設定值


def test_default_limits_are_documented_values():
    assert DEFAULT_LIMITS.reserve_bytes == 1 << 30
    assert DEFAULT_LIMITS.max_total_bytes == 20 << 30
    assert DEFAULT_LIMITS.max_ratio == 1000
    assert DEFAULT_LIMITS.ratio_floor_bytes == 10 * MiB


# ------------------------------------------------------------------ 壓縮炸彈


def test_bz2_bomb(indir, outdir):
    bomb = _bz2_bomb(indir / "zeros.bin.bz2")
    assert bomb.stat().st_size < 4096
    assert _run_refused(bomb, outdir) == f"「zeros.bin.bz2」{RATIO_TEXT}"


def test_tar_bz2_bomb_fails_before_writing(indir, outdir):
    bomb = _tar_bomb(indir / "zeros.tar.bz2")
    assert _run_refused(bomb, outdir) == f"「zeros.tar.bz2」{RATIO_TEXT}"


def test_zip_bomb_fails_before_writing(indir, outdir):
    bomb = _zip_bomb(indir / "zeros.zip")
    assert _run_refused(bomb, outdir) == f"「zeros.zip」{RATIO_TEXT}"


def test_7z_bomb_via_bsdtar(indir, outdir, tmp_path):
    bomb = _sevenzip_bomb(indir / "zeros.7z", tmp_path)
    assert _run_refused(bomb, outdir) == f"「zeros.7z」{RATIO_TEXT}"


def test_7z_bomb_via_7z(indir, outdir, tmp_path, monkeypatch):
    bomb = _sevenzip_bomb(indir / "zeros.7z", tmp_path)
    real_find = backends.find
    monkeypatch.setattr(backends, "find", lambda k: None if k == "bsdtar" else real_find(k))
    assert _run_refused(bomb, outdir) == f"「zeros.7z」{RATIO_TEXT}"


def test_small_compressible_archive_is_fine(indir, outdir):
    """壓縮比很高但解開後不到 10 MiB 的檔案照常解開。"""
    small = _bz2_bomb(indir / "pad.bin.bz2", mib=8)
    t = next(t for t in TOOLS if t.id == "other.extract")
    ctx = ToolContext(outdir)
    try:
        [out] = t.run([small], t.defaults(), ctx)
    finally:
        ctx.cleanup()
    assert out.stat().st_size == 8 * MiB


# ------------------------------------------------------------------ 總量與磁碟空間


def _random_zip(path: Path, size: int) -> Path:
    with zipfile.ZipFile(path, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("a.bin", os.urandom(size))
        zf.writestr("b.txt", "b")
    return path


def _extract_with(archive: Path, kind: ArchiveKind, work: Path, limits: ExtractLimits) -> str:
    ctx = ToolContext(work)
    with pytest.raises(errors.QSError) as exc:
        unpack.extract(archive, kind, work, ctx, limits=limits)
    assert [p for p in work.iterdir()] == [], "中止後工作資料夾要清乾淨"
    return str(exc.value)


def test_total_cap(indir, tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    z = _random_zip(indir / "big.zip", 2 * MiB)
    limits = ExtractLimits(max_total_bytes=1 * MiB)
    assert _extract_with(z, ArchiveKind.ZIP, work, limits) == (
        "「big.zip」解開後超過 1 MB，為了避免塞滿磁碟，已停止解壓縮。"
    )


def test_total_cap_single_file(indir, tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    gz = indir / "big.bin.bz2"
    gz.write_bytes(bz2.compress(os.urandom(2 * MiB)))
    limits = ExtractLimits(max_total_bytes=1 * MiB)
    msg = _extract_with(gz, ArchiveKind.BZIP2, work, limits)
    assert msg == "「big.bin.bz2」解開後超過 1 MB，為了避免塞滿磁碟，已停止解壓縮。"


Usage = namedtuple("Usage", "total used free")


def test_free_space_cap(indir, tmp_path, monkeypatch):
    from quickseemd.core.tools.other import extractlimits

    work = tmp_path / "work"
    work.mkdir()
    reserve = DEFAULT_LIMITS.reserve_bytes
    monkeypatch.setattr(
        extractlimits.shutil, "disk_usage", lambda _p: Usage(0, 0, reserve + MiB // 2)
    )
    z = _random_zip(indir / "big.zip", MiB)
    assert _extract_with(z, ArchiveKind.ZIP, work, DEFAULT_LIMITS) == (
        "「big.zip」解開後會用掉太多磁碟空間（要保留 1 GB 可用空間），已停止解壓縮。"
    )


def test_no_free_space_at_start(indir, tmp_path, monkeypatch):
    from quickseemd.core.tools.other import extractlimits

    work = tmp_path / "work"
    work.mkdir()
    monkeypatch.setattr(extractlimits.shutil, "disk_usage", lambda _p: Usage(0, 0, MiB))
    z = _random_zip(indir / "small.zip", 1000)
    assert _extract_with(z, ArchiveKind.ZIP, work, DEFAULT_LIMITS) == (
        "磁碟可用空間不足 1 GB，無法解壓縮「small.zip」。"
    )


def test_tar_total_cap(indir, tmp_path):
    work = tmp_path / "work"
    work.mkdir()
    archive = indir / "big.tar"
    with tarfile.open(archive, "w") as tf:
        data = os.urandom(2 * MiB)
        info = tarfile.TarInfo("a.bin")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
    limits = ExtractLimits(max_total_bytes=1 * MiB)
    assert _extract_with(archive, ArchiveKind.TAR, work, limits) == (
        "「big.tar」解開後超過 1 MB，為了避免塞滿磁碟，已停止解壓縮。"
    )


def test_compress_repack_also_limited(indir, outdir):
    """壓縮工具重新封裝壓縮檔時也走同一套上限。"""
    bomb = _tar_bomb(indir / "zeros.tar.bz2")
    t = next(t for t in TOOLS if t.id == "other.compress")
    ctx = ToolContext(outdir)
    try:
        with pytest.raises(errors.QSError) as exc:
            t.run([bomb], t.defaults(), ctx)
        assert _leftovers(outdir) == []
    finally:
        ctx.cleanup()
    assert str(exc.value) == f"「zeros.tar.bz2」{RATIO_TEXT}"


def test_cancel_during_external_tool_cleans_up(indir, tmp_path):
    import threading

    src = tmp_path / "src"
    src.mkdir()
    (src / "x.txt").write_text("x")
    archive = indir / "ok.7z"
    process.run_checked(
        [backends.require("bsdtar"), "-cf", str(archive), "--format", "7zip"]
        + ["-C", str(src), "x.txt"],
        "bsdtar",
    )
    work = tmp_path / "work"
    work.mkdir()
    cancel = threading.Event()
    cancel.set()
    ctx = ToolContext(work, None, cancel)
    with pytest.raises(errors.Cancelled):
        unpack.extract(archive, ArchiveKind.SEVEN_ZIP, work, ctx)
    assert list(work.iterdir()) == []


def _set_second_method(z: Path, method: int) -> None:
    """把第二個項目的壓縮方式欄位改成 method（本機標頭與中央目錄都改）。"""
    raw = bytearray(z.read_bytes())
    local = raw.index(b"PK\x03\x04", 1)
    raw[local + 8 : local + 10] = method.to_bytes(2, "little")
    central = raw.index(b"PK\x01\x02")
    central = raw.index(b"PK\x01\x02", central + 1)
    raw[central + 10 : central + 12] = method.to_bytes(2, "little")
    z.write_bytes(bytes(raw))


def test_python_failure_midway_discards_partial_output(indir, tmp_path):
    """第二個項目的壓縮方式 zipfile 不支援：寫了一半就改用外部工具，寫一半的內容要先刪掉。"""
    z = indir / "half.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("a.bin", b"A" * 4096)
        zf.writestr("b.bin", b"B" * 4096)
    _set_second_method(z, 99)
    work = tmp_path / "work"
    work.mkdir()
    with pytest.raises(errors.QSError) as exc:
        unpack.extract(z, ArchiveKind.ZIP, work, ToolContext(work))
    assert str(exc.value) == "「half.zip」無法解壓縮：檔案可能已損毀，或不是支援的壓縮格式。"
    assert list(work.iterdir()) == []


def test_crc_mismatch_is_corrupt_not_retried(indir, tmp_path):
    """內容和 CRC 對不上就是壞檔，不交給比較寬鬆的外部工具（免得交出壞掉的資料）。"""
    z = indir / "crc.zip"
    with zipfile.ZipFile(z, "w", zipfile.ZIP_STORED) as zf:
        zf.writestr("a.bin", b"A" * 4096)
        zf.writestr("b.bin", b"B" * 4096)
    raw = bytearray(z.read_bytes())
    pos = raw.index(b"B" * 4096)
    raw[pos : pos + 16] = b"C" * 16
    z.write_bytes(bytes(raw))
    work = tmp_path / "work"
    work.mkdir()
    with pytest.raises(errors.QSError) as exc:
        unpack.extract(z, ArchiveKind.ZIP, work, ToolContext(work))
    assert str(exc.value) == "「crc.zip」無法解壓縮：檔案可能已損毀，或不是支援的壓縮格式。"
    assert list(work.iterdir()) == []


def test_hard_links_are_charged_when_linking_fails(indir, tmp_path, monkeypatch):
    """檔案系統不支援硬連結時 tarfile 會改成複製；每個連結都要算進額度。"""
    archive = indir / "links.tar"
    data = os.urandom(MiB)
    with tarfile.open(archive, "w") as tf:
        info = tarfile.TarInfo("one.bin")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
        for i in range(300):
            link = tarfile.TarInfo(f"copy{i}.bin")
            link.type = tarfile.LNKTYPE
            link.linkname = "one.bin"
            tf.addfile(link)

    def no_links(*_args, **_kw):
        raise OSError(1, "不支援硬連結")

    monkeypatch.setattr(os, "link", no_links)
    work = tmp_path / "work"
    work.mkdir()
    limits = ExtractLimits(max_total_bytes=50 * MiB)
    assert _extract_with(archive, ArchiveKind.TAR, work, limits) == (
        "「links.tar」解開後超過 50 MB，為了避免塞滿磁碟，已停止解壓縮。"
    )


def test_symlink_targets_are_charged(indir, tmp_path):
    archive = indir / "symlinks.tar"
    data = os.urandom(MiB)
    with tarfile.open(archive, "w") as tf:
        info = tarfile.TarInfo("d/one.bin")
        info.size = len(data)
        tf.addfile(info, io.BytesIO(data))
        for i in range(60):
            link = tarfile.TarInfo(f"d/l{i}")
            link.type = tarfile.SYMTYPE
            link.linkname = "one.bin"
            tf.addfile(link)
    work = tmp_path / "work"
    work.mkdir()
    limits = ExtractLimits(max_total_bytes=50 * MiB)
    assert "解開後超過 50 MB" in _extract_with(archive, ArchiveKind.TAR, work, limits)
