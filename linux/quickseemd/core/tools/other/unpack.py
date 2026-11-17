"""安全的解壓縮（對應原版 CVArchiveConverter.extract）。

- ZIP 用 zipfile、tar 系列用 tarfile（加 data 篩選器），單一 .gz／.bz2／.xz 用 Python 模組；
  其他格式交給 unpack_external（bsdtar 轉存成 tar 後仍由這裡檢查，或 7z）。
- 全部項目先檢查完才開始寫，而且只寫進工作資料夾裡的新暫存資料夾，最後由工具用 ctx.deliver 交付。
- 不寫出 macOS 的中繼資料（__MACOSX、.DS_Store、._*）。
- 只有一個最上層項目時交付它本身，否則交付整個資料夾（名稱＝壓縮檔主檔名）。
"""

from __future__ import annotations

import bz2
import gzip
import logging
import lzma
import os
import stat
import tarfile
import tempfile
import time
import zipfile
import zlib
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from ... import errors, naming
from ...model import ToolContext
from . import textcodec, unpack_external
from .archivekind import (
    SINGLE_FILE_KINDS,
    ArchiveKind,
    Conflict,
    Corrupt,
    Encrypted,
    UnsafeEntry,
    conflict_error,
    corrupt_error,
    encrypted_error,
    is_junk,
    link_stays_inside,
    safe_parts,
    unsafe_error,
    verify_tree,
)

log = logging.getLogger(__name__)

CHUNK = 1 << 20
EMPTY_MESSAGE = "壓縮檔裡沒有任何檔案。"
MAX_LINK_TARGET = 4096
_UNIX_SYSTEMS = (3, 19)  # ZIP 的「建立系統」：Unix、macOS
_UTF8_FLAG = 0x800
_ENCRYPTED_FLAG = 0x1
_READ_ERRORS = (OSError, EOFError, zlib.error, lzma.LZMAError, ValueError, tarfile.TarError)


@dataclass(frozen=True)
class Extracted:
    """解出來要交付的東西：path 是檔案或資料夾，name 是交付時的名稱。"""

    path: Path
    name: str


class NeedExternal(Exception):
    """Python 讀不了這個檔案（例如 zstd、deflate64），改用外部工具試試。"""


def extract(archive: Path, kind: ArchiveKind, work: Path, ctx: ToolContext) -> Extracted:
    """把 archive 解到 work 裡的新暫存資料夾；所有安全問題都轉成中文錯誤。"""
    try:
        if kind in SINGLE_FILE_KINDS:
            return _single_file(archive, kind, work, ctx)
        staging = _extract_tree(archive, kind, work, ctx)
    except UnsafeEntry as exc:
        raise unsafe_error(archive, exc.entry) from exc
    except Encrypted as exc:
        raise encrypted_error(archive) from exc
    except Corrupt as exc:
        raise corrupt_error(archive) from exc
    except Conflict as exc:
        raise conflict_error(archive, exc.entry) from exc
    return _result(archive, staging)


def _new_staging(work: Path) -> Path:
    return Path(tempfile.mkdtemp(prefix=".extract-", dir=work))


def _extract_tree(archive: Path, kind: ArchiveKind, work: Path, ctx: ToolContext) -> Path:
    staging = _new_staging(work)
    try:
        if kind is ArchiveKind.ZIP:
            _extract_zip(archive, staging, ctx)
        elif kind is ArchiveKind.TAR:
            extract_tar(archive, staging, ctx)
        else:
            raise NeedExternal
    except NeedExternal:
        staging = _new_staging(work)
        unpack_external.extract(archive, staging, work, ctx, _extract_converted)
    verify_tree(staging)
    return staging


def _result(archive: Path, staging: Path) -> Extracted:
    items = sorted(n for n in os.listdir(staging) if not is_junk((n,)))
    if not items:
        raise errors.empty_content(EMPTY_MESSAGE)
    if len(items) == 1:
        return Extracted(staging / items[0], items[0])
    return Extracted(staging, naming.base_name(archive))


def _write_chunk(out: BinaryIO, chunk: bytes) -> None:
    try:
        out.write(chunk)
    except OSError as exc:
        raise errors.write_failed(exc.strerror or str(exc)) from exc


# ---------------------------------------------------------------- 單一檔案（.gz .bz2 .xz）


def _open_stream(kind: ArchiveKind, raw: BinaryIO) -> BinaryIO:
    if kind is ArchiveKind.GZIP:
        return gzip.GzipFile(fileobj=raw, mode="rb")
    if kind is ArchiveKind.BZIP2:
        return bz2.BZ2File(raw, "rb")
    return lzma.LZMAFile(raw, "rb")


def _single_file(archive: Path, kind: ArchiveKind, work: Path, ctx: ToolContext) -> Extracted:
    """解開單一檔案的壓縮，輸出名稱是去掉副檔名的檔名（data.csv.gz → data.csv）。"""
    base = naming.base_name(archive)
    out = _new_staging(work) / base
    total = max(archive.stat().st_size, 1)
    with open(archive, "rb") as raw, open(out, "xb") as dst, _open_stream(kind, raw) as src:
        while True:
            ctx.check_cancelled()
            try:
                chunk = src.read(CHUNK)
            except _READ_ERRORS as exc:
                raise Corrupt from exc
            if not chunk:
                break
            _write_chunk(dst, chunk)
            ctx.progress(raw.tell() / total)
    return Extracted(out, base)


# ---------------------------------------------------------------- 建立資料夾與連結（不經過符號連結）


def _make_dirs(root: Path, parts: tuple[str, ...]) -> Path:
    """逐層建立資料夾；路上遇到檔案或符號連結就是衝突（絕不穿過符號連結）。"""
    path = root
    for part in parts:
        path = path / part
        try:
            os.mkdir(path)
        except FileExistsError:
            if not stat.S_ISDIR(os.lstat(path).st_mode):
                raise Conflict("/".join(parts)) from None
    return path


def _make_link(root: Path, parts: tuple[str, ...], target: str) -> None:
    parent = _make_dirs(root, parts[:-1])
    try:
        os.symlink(target, parent / parts[-1])
    except FileExistsError as exc:
        raise Conflict("/".join(parts)) from exc


# ---------------------------------------------------------------- ZIP


@dataclass(frozen=True)
class _ZipItem:
    info: zipfile.ZipInfo
    name: str
    parts: tuple[str, ...]
    kind: str  # "dir"、"file"、"link"


def _zip_kind(info: zipfile.ZipInfo) -> str:
    mode = info.external_attr >> 16
    if info.create_system in _UNIX_SYSTEMS and stat.S_IFMT(mode):
        if stat.S_ISLNK(mode):
            return "link"
        if stat.S_ISDIR(mode):
            return "dir"
        if not stat.S_ISREG(mode):
            return "special"
    return "dir" if info.orig_filename.endswith(("/", "\\")) else "file"


def _legacy_codec(infos: list[zipfile.ZipInfo]) -> str | None:
    """沒有 UTF-8 標記的非英文檔名（Windows 的 Big5、GBK…）用什麼編碼。"""
    raw = [
        i.orig_filename.encode("cp437")
        for i in infos
        if not i.flag_bits & _UTF8_FLAG and not i.orig_filename.isascii()
    ]
    if not raw:
        return None
    return textcodec.detect_legacy_encoding(b"\n".join(raw))


def _entry_name(info: zipfile.ZipInfo, codec: str | None) -> str:
    name = info.orig_filename
    if codec is None or info.flag_bits & _UTF8_FLAG or name.isascii():
        return name
    try:
        return name.encode("cp437").decode(codec)
    except UnicodeError:
        return name


def _plan_zip(zf: zipfile.ZipFile) -> list[_ZipItem]:
    infos = zf.infolist()
    codec = _legacy_codec(infos)
    plan: list[_ZipItem] = []
    for info in infos:
        name = _entry_name(info, codec)
        parts = safe_parts(name, backslash=True)
        if not parts or is_junk(parts):
            continue
        kind = _zip_kind(info)
        if kind == "special":
            raise UnsafeEntry(name)
        if info.flag_bits & _ENCRYPTED_FLAG:
            raise Encrypted
        plan.append(_ZipItem(info, name, parts, kind))
    return plan


def _zip_links(zf: zipfile.ZipFile, plan: list[_ZipItem]) -> list[tuple[_ZipItem, str]]:
    links: list[tuple[_ZipItem, str]] = []
    for item in plan:
        if item.kind != "link":
            continue
        if item.info.file_size > MAX_LINK_TARGET:
            raise UnsafeEntry(item.name)
        target = zf.read(item.info).decode("utf-8", "surrogateescape")
        if not link_stays_inside(item.parts, target):
            raise UnsafeEntry(item.name)
        links.append((item, target))
    return links


def _apply_zip_attrs(path: Path, info: zipfile.ZipInfo) -> None:
    mode = (info.external_attr >> 16) & 0o777
    if info.create_system in _UNIX_SYSTEMS and mode:
        os.chmod(path, (mode & 0o755) | 0o600)  # 拿掉 setuid 與群組／其他人的寫入權
    try:
        stamp = time.mktime((*info.date_time, 0, 0, -1))
        os.utime(path, (stamp, stamp))
    except (OverflowError, ValueError, OSError) as exc:
        log.debug("無法設定 %s 的修改時間：%s", path.name, exc)


def _write_zip_file(
    zf: zipfile.ZipFile, item: _ZipItem, root: Path, ctx: ToolContext, progress: list[int]
) -> None:
    parent = _make_dirs(root, item.parts[:-1])
    path = parent / item.parts[-1]
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        fd = os.open(path, flags, 0o666)
    except OSError as exc:
        raise Conflict(item.name) from exc
    with os.fdopen(fd, "wb") as out, zf.open(item.info) as src:
        while chunk := src.read(CHUNK):
            ctx.check_cancelled()
            _write_chunk(out, chunk)
            progress[0] += len(chunk)
            ctx.progress(progress[0] / progress[1])
    _apply_zip_attrs(path, item.info)


def _extract_zip(archive: Path, root: Path, ctx: ToolContext) -> None:
    """先檢查全部項目，再寫資料夾與檔案，最後才建立符號連結（檔案絕不會寫穿連結）。"""
    try:
        with zipfile.ZipFile(archive) as zf:
            plan = _plan_zip(zf)
            links = _zip_links(zf, plan)
            total = sum(i.info.file_size for i in plan if i.kind == "file")
            progress = [0, max(total, 1)]
            for item in plan:
                ctx.check_cancelled()
                if item.kind == "dir":
                    _make_dirs(root, item.parts)
                elif item.kind == "file":
                    _write_zip_file(zf, item, root, ctx, progress)
            for item, target in links:
                _make_link(root, item.parts, target)
    except (zipfile.BadZipFile, NotImplementedError, zlib.error, EOFError) as exc:
        log.info("zipfile 讀不了 %s：%s", archive.name, exc)
        raise NeedExternal from exc


# ---------------------------------------------------------------- tar


def _plan_tar(members: list[tarfile.TarInfo]) -> list[tarfile.TarInfo]:
    plan: list[tarfile.TarInfo] = []
    for m in members:
        parts = safe_parts(m.name)
        if not parts or is_junk(parts):
            continue
        if m.issym():
            if not link_stays_inside(parts, m.linkname):
                raise UnsafeEntry(m.name)
        elif m.islnk():
            if not safe_parts(m.linkname):
                raise UnsafeEntry(m.name)
        elif not (m.isreg() or m.isdir()):
            raise UnsafeEntry(m.name)
        plan.append(m)
    return plan


def _extract_member(tf: tarfile.TarFile, member: tarfile.TarInfo, root: Path) -> None:
    try:
        tf.extract(member, root, set_attrs=True, filter="data")
    except tarfile.FilterError as exc:
        raise UnsafeEntry(member.name) from exc
    except (IsADirectoryError, NotADirectoryError, FileExistsError) as exc:
        raise Conflict(member.name) from exc
    except _READ_ERRORS as exc:
        raise Corrupt from exc


def extract_tar(path: Path, root: Path, ctx: ToolContext) -> None:
    """tar 系列（含 gz／bz2／xz 壓縮）；Python 開不了時丟 NeedExternal。"""
    try:
        tf = tarfile.open(path, "r:*")
    except _READ_ERRORS as exc:
        raise NeedExternal from exc
    with tf:
        try:
            members = tf.getmembers()
        except _READ_ERRORS as exc:
            raise NeedExternal from exc
        plan = _plan_tar(members)
        for i, member in enumerate(plan):
            ctx.check_cancelled()
            _extract_member(tf, member, root)
            ctx.progress((i + 1) / len(plan))


def _extract_converted(path: Path, root: Path, ctx: ToolContext) -> None:
    """bsdtar 轉存出來的 tar；再讀不了就是壞檔。"""
    try:
        extract_tar(path, root, ctx)
    except NeedExternal as exc:
        raise Corrupt from exc
