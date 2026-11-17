"""壓縮、解壓縮與重新封裝（對應原版 CVArchiveConverter）。

- ZIP、TAR.GZ 用 Python 內建模組；7z 與 RAR／ISO 等用 bsdtar（libarchive）。
- 解壓縮一律先檢查每個項目：絕對路徑、「..」、指到外面的連結都直接拒絕整個壓縮檔。
- 只有一個最上層項目時直接交付它，否則包進以檔名命名的資料夾。
"""

from __future__ import annotations

import bz2
import gzip
import lzma
import os
import re
import shutil
import stat
import tarfile
import tempfile
import time
import zipfile
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .. import backends, errors, naming
from .formats import Format
from .profile import Profile

JUNK = frozenset({"__MACOSX", ".DS_Store"})
CHUNK = 4 * 1024 * 1024
LIST_TIMEOUT = 300.0
_DRIVE = re.compile(r"^[A-Za-z]:")
Check = Callable[[], None]


def _unsafe(name: str) -> errors.QSError:
    return errors.QSError(f"壓縮檔中有不安全的路徑「{name}」，已拒絕解壓縮。")


def safe_parts(name: str) -> list[str]:
    """壓縮檔裡的路徑 → 各層名稱；絕對路徑、磁碟代號或含「..」時丟出錯誤。"""
    n = name.replace("\\", "/")
    if n.startswith("/") or _DRIVE.match(n) or "\0" in n:
        raise _unsafe(name)
    parts = [p for p in n.split("/") if p not in ("", ".")]
    if ".." in parts:
        raise _unsafe(name)
    return parts


def _inside(root: Path, target: str) -> bool:
    real_root = os.path.realpath(root)
    real = os.path.realpath(target)
    return real == real_root or real.startswith(real_root + os.sep)


# ---------------------------------------------------------------- 建立壓縮檔


def _zip_symlink(z: zipfile.ZipFile, path: Path, arcname: str) -> None:
    info = zipfile.ZipInfo(arcname, time.localtime(path.lstat().st_mtime)[:6])
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    z.writestr(info, os.readlink(path))


def _zip_add(z: zipfile.ZipFile, path: Path, arcname: str, check: Check) -> None:
    check()
    if path.is_symlink():
        _zip_symlink(z, path, arcname)
    elif path.is_dir():
        z.write(path, arcname + "/")
        for child in sorted(path.iterdir(), key=lambda c: c.name):
            _zip_add(z, child, f"{arcname}/{child.name}", check)
    elif path.is_file():
        z.write(path, arcname)


def create_zip(src: Path, out: Path, check: Check) -> None:
    """壓成 ZIP，保留最上層名稱（同 ditto --keepParent）。"""
    with zipfile.ZipFile(out, "x", zipfile.ZIP_DEFLATED, strict_timestamps=False) as z:
        _zip_add(z, src, src.name, check)


def create_tgz(src: Path, out: Path, check: Check) -> None:
    def keep(info: tarfile.TarInfo) -> tarfile.TarInfo:
        check()
        return info

    with tarfile.open(out, "x:gz") as tar:
        tar.add(src, arcname=src.name, recursive=True, filter=keep)


def create_7z(src: Path, out: Path, ctx) -> None:  # noqa: ANN001
    tool = backends.require("bsdtar")
    args = [tool, "--format", "7zip", "-cf", str(out), "-C", str(src.parent), "--", src.name]
    ctx.run_process(args, "bsdtar")


# ---------------------------------------------------------------- 解壓縮


DISK_MARGIN = 64 * 1024 * 1024  # 解壓縮時保留給系統的磁碟空間


def _free_bytes(path: Path) -> int:
    return shutil.disk_usage(path).free


def _no_space() -> errors.QSError:
    return errors.write_failed("解壓縮後的檔案太大，磁碟空間不足。")


def _stream_copy(opener: Callable[[], Any], out: Path, check: Check) -> None:
    """邊解壓邊寫；寫入量超過可用空間（保留一些給系統）就停止，避免解壓縮炸彈塞滿磁碟。"""
    limit = _free_bytes(out.parent) - DISK_MARGIN
    written = 0
    try:
        with opener() as fin, open(out, "xb") as fout:
            while True:
                check()
                chunk = fin.read(CHUNK)
                if not chunk:
                    break
                written += len(chunk)
                if written > limit:
                    raise _no_space()
                fout.write(chunk)
    except (OSError, EOFError, lzma.LZMAError) as exc:
        raise errors.unreadable(str(exc)) from exc


def _extract_single(path: Path, kind: str, dest: Path, check: Check) -> Path:
    out = dest / naming.base_name(path)
    openers = {"gzip": gzip.open, "bzip2": bz2.open, "xz": lzma.open}
    _stream_copy(lambda: openers[kind](path, "rb"), out, check)
    return out


def _check_space(dest: Path, needed: int) -> None:
    if needed > _free_bytes(dest) - DISK_MARGIN:
        raise _no_space()


def zip_name(info: zipfile.ZipInfo) -> str:
    """沒有標示 UTF-8 的檔名：zipfile 當成 CP437 解讀；改試 UTF-8、Big5、GB18030（同 ditto）。"""
    if info.flag_bits & 0x800:
        return info.filename
    try:
        raw = info.filename.encode("cp437")
    except UnicodeEncodeError:
        return info.filename
    for codec in ("utf-8", "cp950", "gb18030"):
        try:
            return raw.decode(codec)
        except UnicodeDecodeError:
            continue
    return info.filename


def _zip_mode(info: zipfile.ZipInfo) -> int:
    return (info.external_attr >> 16) if info.create_system == 3 else 0


def _zip_entries(z: zipfile.ZipFile) -> tuple[list[tuple[zipfile.ZipInfo, list[str]]], int]:
    entries = []
    total = 0
    for info in z.infolist():
        parts = safe_parts(zip_name(info))
        if info.flag_bits & 0x1:
            raise errors.QSError("這個壓縮檔有密碼保護，目前無法解壓縮。")
        if parts and parts[0] != "__MACOSX":
            entries.append((info, parts))
            total += info.file_size
    return entries, total


def _zip_link(z: zipfile.ZipFile, info: zipfile.ZipInfo, target: Path, root: Path) -> None:
    link = z.read(info).decode("utf-8", "surrogateescape")
    if link.startswith("/") or not _inside(root, os.path.join(target.parent, link)):
        raise _unsafe(info.filename)
    target.parent.mkdir(parents=True, exist_ok=True)
    os.symlink(link, target)


def _zip_file(z: zipfile.ZipFile, info: zipfile.ZipInfo, target: Path, check: Check) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    if target.is_symlink():
        raise _unsafe(info.filename)
    if target.exists():
        target.unlink()  # 壓縮檔裡重複的項目：後面的取代前面的（都在自己的暫存資料夾裡）
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | getattr(os, "O_NOFOLLOW", 0)
    with z.open(info) as fin, os.fdopen(os.open(target, flags, 0o644), "wb") as fout:
        while True:
            check()
            chunk = fin.read(CHUNK)
            if not chunk:
                break
            fout.write(chunk)


def _zip_finish(info: zipfile.ZipInfo, target: Path, is_dir: bool) -> None:
    mode = _zip_mode(info) & 0o777
    if mode:
        os.chmod(target, mode | (0o700 if is_dir else 0o600))
    try:
        stamp = time.mktime(info.date_time + (0, 0, -1))
        os.utime(target, (stamp, stamp), follow_symlinks=False)
    except (OverflowError, ValueError, OSError):
        pass  # 日期不合理就保留現在時間


def extract_zip(path: Path, staging: Path, check: Check) -> None:
    try:
        z = zipfile.ZipFile(path)
    except (zipfile.BadZipFile, OSError) as exc:
        raise errors.unreadable(str(exc)) from exc
    with z:
        entries, total = _zip_entries(z)
        _check_space(staging, total)
        dirs = []
        for info, parts in entries:
            target = staging.joinpath(*parts)
            if not _inside(staging, str(target.parent)):
                raise _unsafe(info.filename)
            if info.is_dir():
                target.mkdir(parents=True, exist_ok=True)
                dirs.append((info, target))
            elif stat.S_ISLNK(_zip_mode(info)):
                _zip_link(z, info, target, staging)
            else:
                _zip_file(z, info, target, check)
                _zip_finish(info, target, False)
        for info, target in reversed(dirs):
            _zip_finish(info, target, True)


def _tar_filter(staging: Path, check: Check):  # noqa: ANN202
    def keep(member: tarfile.TarInfo, dest: str) -> tarfile.TarInfo | None:
        check()
        if safe_parts(member.name)[:1] == ["__MACOSX"]:
            return None
        try:
            return tarfile.data_filter(member, dest)
        except tarfile.FilterError as exc:
            raise _unsafe(member.name) from exc

    return keep


def extract_tar(path: Path, staging: Path, check: Check) -> bool:
    """用 tarfile 解開；格式不支援（zstd、lzip）時回傳 False 交給 bsdtar。"""
    try:
        tar = tarfile.open(path, "r:*")
    except (tarfile.ReadError, tarfile.CompressionError):
        return False
    with tar:
        members = tar.getmembers()
        total = 0
        for m in members:
            safe_parts(m.name)
            total += m.size
        _check_space(staging, total)
        tar.extractall(staging, members=members, filter=_tar_filter(staging, check))
    return True


def extract_bsdtar(path: Path, staging: Path, ctx) -> None:  # noqa: ANN001
    tool = backends.require("bsdtar")
    listing = ctx.run_process([tool, "-tf", str(path)], "bsdtar", timeout=LIST_TIMEOUT)
    for name in listing.stdout.splitlines():
        safe_parts(name)
    args = [tool, "-xf", str(path), "-C", str(staging), "--no-same-owner", "--exclude", "__MACOSX"]
    ctx.run_process(args, "bsdtar")


def _remove(path: Path) -> None:
    if path.is_dir() and not path.is_symlink():
        shutil.rmtree(path, ignore_errors=True)
    else:
        path.unlink(missing_ok=True)


def _settle(staging: Path, dest: Path, base: str) -> Path:
    for name in JUNK:
        junk = staging / name
        if junk.exists() or junk.is_symlink():
            _remove(junk)
    items = [n for n in os.listdir(staging) if not n.startswith("._")]
    if not items:
        raise errors.empty_content("壓縮檔裡沒有任何檔案。")
    if len(items) == 1:
        # 暫存資料夾留在工作資料夾裡，工作結束時一併清掉
        target = naming.unique_path(dest, items[0])
        os.rename(staging / items[0], target)
        return target
    target = naming.unique_path(dest, base)
    os.rename(staging, target)
    return target


def extract(path: Path, kind: str | None, dest: Path, ctx) -> Path:  # noqa: ANN001
    """解壓縮到 dest；只有一個最上層項目就回傳它，否則回傳以檔名命名的資料夾。"""
    check = ctx.check_cancelled
    if kind in ("gzip", "bzip2", "xz"):
        return _extract_single(path, kind, dest, check)
    staging = Path(tempfile.mkdtemp(prefix=".extract-", dir=dest))
    try:
        if kind == "zip":
            extract_zip(path, staging, check)
        elif kind != "tar" or not extract_tar(path, staging, check):
            extract_bsdtar(path, staging, ctx)
        return _settle(staging, dest, naming.base_name(path))
    except (OSError, tarfile.TarError, zipfile.BadZipFile, EOFError) as exc:
        raise errors.unreadable(getattr(exc, "strerror", None) or str(exc)) from exc


# ---------------------------------------------------------------- 路線入口


def convert(
    path: Path, profile: Profile, fmt: Format, options: dict, work: Path, ctx
) -> list[Path]:  # noqa: ANN001, ARG001, E501
    if fmt.id == "extract":
        result = extract(path, profile.archive_kind, work, ctx)
        ctx.progress(1)
        return [result]
    source = path
    if profile.archive_kind is not None:
        # 壓縮檔 → 另一種壓縮檔：先解開再重新打包
        scratch = Path(tempfile.mkdtemp(prefix=".scratch-", dir=work))
        source = extract(path, profile.archive_kind, scratch, ctx)
        ctx.progress(0.4)
    out = work / naming.join_name(naming.base_name(path), fmt.ext)
    if fmt.id == "zip":
        create_zip(source, out, ctx.check_cancelled)
    elif fmt.id == "tgz":
        create_tgz(source, out, ctx.check_cancelled)
    elif fmt.id == "7z":
        create_7z(source, out, ctx)
    else:
        raise errors.unsupported_target(path.name, fmt.display_name)
    ctx.progress(1)
    return [out]
