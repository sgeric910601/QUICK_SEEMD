"""安全的解壓縮（對應原版 CVArchiveConverter.extract）。

- ZIP 用 zipfile、tar 系列用 tarfile（加 data 篩選器），單一 .gz／.bz2／.xz 用 Python 模組；
  其他格式交給 unpack_external（bsdtar 轉存成 tar 後仍由這裡檢查，或 7z）。
- 全部項目先檢查完才開始寫，而且只寫進工作資料夾裡的新暫存資料夾，最後由工具用 ctx.deliver 交付。
- 不寫出 macOS 的中繼資料（__MACOSX、.DS_Store、._*）。
- 只有一個最上層項目時交付它本身，否則交付整個資料夾（名稱＝壓縮檔主檔名）。
  唯一的最上層項目是隱藏檔（.bash_profile 之類）時也包進資料夾，不直接放到壓縮檔旁；
  交付名稱一律不以「.」開頭（.hidden.zip → _hidden）。
- 大小上限見 extractlimits：開始前量可用空間，每次寫入都記帳，超過就中止並清掉這次寫出的一切。

萬用轉換也會呼叫 extract()；介面（參數、回傳值、錯誤）請保持不變。
"""

from __future__ import annotations

import bz2
import gzip
import logging
import lzma
import os
import shutil
import stat
import tarfile
import tempfile
import time
import zipfile
import zlib
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from ... import errors, naming
from ...model import ToolContext
from . import textcodec, unpack_external
from .extractlimits import DEFAULT_LIMITS, Budget, ExtractLimits, TooLarge, tree_size
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


def extract(
    archive: Path,
    kind: ArchiveKind,
    work: Path,
    ctx: ToolContext,
    *,
    limits: ExtractLimits | None = None,
) -> Extracted:
    """把 archive 解到 work 裡的新暫存資料夾。

    - 回傳的 path 在 work 裡面，呼叫端用 ctx.deliver(result.path, result.name, archive) 交付。
    - 失敗時丟出中文的 errors.QSError（不安全的路徑、密碼、損毀、衝突、太大、空的），
      並且先刪掉這次在 work 裡寫出的所有東西。
    - limits 預設 DEFAULT_LIMITS（測試或特殊用途才需要改）。
    """
    lim = limits or DEFAULT_LIMITS
    try:
        budget = Budget.for_target(archive, work, lim)
    except TooLarge as exc:
        raise exc.error(archive) from exc
    except OSError as exc:
        raise errors.unreadable(textcodec.read_error_text(archive, exc)) from exc
    holder = Path(tempfile.mkdtemp(prefix=".extract-", dir=work))
    try:
        return _extract_mapped(archive, kind, holder, ctx, budget)
    except BaseException:
        _discard(holder)
        raise


def _discard(holder: Path) -> None:
    """中止時刪掉這次自己建的暫存資料夾（只有 mkdtemp 建出來的 .extract-*）。"""
    if not holder.name.startswith(".extract-") or holder.is_symlink():
        return
    shutil.rmtree(holder, onexc=lambda _f, p, e: log.warning("刪不掉暫存檔 %s：%s", p, e))


def _extract_mapped(
    archive: Path, kind: ArchiveKind, holder: Path, ctx: ToolContext, budget: Budget
) -> Extracted:
    try:
        if kind in SINGLE_FILE_KINDS:
            return _single_file(archive, kind, holder, ctx, budget)
        staging = _extract_tree(archive, kind, holder, ctx, budget)
        result = _result(archive, staging)
        # 以實際交付的項目為根檢查連結：單一項目交付到使用者資料夾後，「..」就是那個資料夾
        verify_tree(result.path)
    except UnsafeEntry as exc:
        raise unsafe_error(archive, exc.entry) from exc
    except Encrypted as exc:
        raise encrypted_error(archive) from exc
    except Corrupt as exc:
        raise corrupt_error(archive) from exc
    except Conflict as exc:
        raise conflict_error(archive, exc.entry) from exc
    except TooLarge as exc:
        raise exc.error(archive) from exc
    return result


def _new_staging(holder: Path) -> Path:
    return Path(tempfile.mkdtemp(prefix=".staging-", dir=holder))


def _extract_tree(
    archive: Path, kind: ArchiveKind, holder: Path, ctx: ToolContext, budget: Budget
) -> Path:
    staging = _new_staging(holder)
    try:
        if kind is ArchiveKind.ZIP:
            _extract_zip(archive, staging, ctx, budget)
        elif kind is ArchiveKind.TAR:
            extract_tar(archive, staging, ctx, budget)
        else:
            raise NeedExternal
    except NeedExternal:
        _discard_partial(staging, budget)
        staging = _new_staging(holder)

        def converted(path: Path, root: Path, sub: ToolContext) -> None:
            _extract_converted(path, root, sub, budget)

        unpack_external.extract(
            archive, staging, holder, ctx, budget, converted, allow_7z=kind is not ArchiveKind.TAR
        )
    return staging


def _discard_partial(staging: Path, budget: Budget) -> None:
    """Python 讀到一半放棄、改用外部工具時，先刪掉寫了一半的內容並退回額度。"""
    used = tree_size(staging)
    shutil.rmtree(staging, onexc=lambda _f, p, e: log.warning("刪不掉暫存檔 %s：%s", p, e))
    budget.release(used)


def visible_name(name: str) -> str:
    """交付名稱不以「.」開頭（避免在使用者的資料夾裡多出隱藏檔）。"""
    return naming.sanitize(name) if name.startswith(".") else name


def _result(archive: Path, staging: Path) -> Extracted:
    items = sorted(n for n in os.listdir(staging) if not is_junk((n,)))
    if not items:
        raise errors.empty_content(EMPTY_MESSAGE)
    if len(items) == 1 and not items[0].startswith("."):
        return Extracted(staging / items[0], items[0])
    return Extracted(staging, visible_name(naming.base_name(archive)))


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


def _single_file(
    archive: Path, kind: ArchiveKind, holder: Path, ctx: ToolContext, budget: Budget
) -> Extracted:
    """解開單一檔案的壓縮，輸出名稱是去掉副檔名的檔名（data.csv.gz → data.csv）。"""
    base = visible_name(naming.base_name(archive))
    out = _new_staging(holder) / base
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
            budget.charge(len(chunk))
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
        with _open_member(zf, item.info) as src:
            target = b"".join(_member_chunks(src)).decode("utf-8", "surrogateescape")
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


# 讀項目內容時的錯誤＝內容損毀（例如 CRC 不符）；不改用外部工具，免得把壞掉的資料當成好的交出去
_ZIP_DATA_ERRORS = (zipfile.BadZipFile, zlib.error, lzma.LZMAError, EOFError, OSError)


def _open_member(zf: zipfile.ZipFile, info: zipfile.ZipInfo) -> BinaryIO:
    """打開一個項目；zipfile 不支援的壓縮方式（例如 Deflate64）改用外部工具。"""
    try:
        return zf.open(info)
    except NotImplementedError as exc:
        raise NeedExternal from exc
    except _ZIP_DATA_ERRORS as exc:
        raise Corrupt from exc


def _member_chunks(src: BinaryIO) -> Iterator[bytes]:
    while True:
        try:
            chunk = src.read(CHUNK)
        except _ZIP_DATA_ERRORS as exc:
            raise Corrupt from exc
        if not chunk:
            return
        yield chunk


def _write_zip_file(
    zf: zipfile.ZipFile, item: _ZipItem, root: Path, ctx: ToolContext, budget: Budget
) -> None:
    parent = _make_dirs(root, item.parts[:-1])
    path = parent / item.parts[-1]
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC | os.O_NOFOLLOW | os.O_CLOEXEC
    try:
        fd = os.open(path, flags, 0o666)
    except OSError as exc:
        raise Conflict(item.name) from exc
    with os.fdopen(fd, "wb") as out, _open_member(zf, item.info) as src:
        for chunk in _member_chunks(src):
            ctx.check_cancelled()
            budget.charge(len(chunk))
            _write_chunk(out, chunk)
            ctx.progress(budget.content / max(budget.planned, 1))
    _apply_zip_attrs(path, item.info)


def _extract_zip(archive: Path, root: Path, ctx: ToolContext, budget: Budget) -> None:
    """先檢查全部項目（含宣告的大小），再寫資料夾與檔案，最後才建立符號連結（檔案絕不會寫穿連結）。

    zipfile 讀出的資料不會超過項目宣告的大小，所以宣告大小的總和就是會寫出的上限。
    """
    try:
        zf = zipfile.ZipFile(archive)
    except (zipfile.BadZipFile, EOFError, ValueError) as exc:
        log.info("zipfile 讀不了 %s：%s", archive.name, exc)
        raise NeedExternal from exc
    with zf:
        plan = _plan_zip(zf)
        links = _zip_links(zf, plan)
        budget.plan(sum(i.info.file_size for i in plan if i.kind == "file"))
        for item in plan:
            ctx.check_cancelled()
            if item.kind == "dir":
                _make_dirs(root, item.parts)
            elif item.kind == "file":
                _write_zip_file(zf, item, root, ctx, budget)
        for item, target in links:
            _make_link(root, item.parts, target)


# ---------------------------------------------------------------- tar


def _keep_tar_member(m: tarfile.TarInfo) -> bool:
    """檢查一個 tar 項目；不安全就丟 UnsafeEntry，根目錄與 macOS 中繼資料回傳 False。"""
    parts = safe_parts(m.name)
    if not parts or is_junk(parts):
        return False
    if m.issym():
        if not link_stays_inside(parts, m.linkname):
            raise UnsafeEntry(m.name)
    elif m.islnk():
        if not safe_parts(m.linkname):
            raise UnsafeEntry(m.name)
    elif not (m.isreg() or m.isdir()):
        raise UnsafeEntry(m.name)
    return True


def _link_key(member: tarfile.TarInfo) -> str:
    """連結指向的項目名稱（硬連結相對於壓縮檔根目錄，符號連結相對於自己所在的資料夾）。"""
    if member.islnk():
        return "/".join(safe_parts(member.linkname))
    depth = list(safe_parts(member.name)[:-1])
    for comp in member.linkname.split("/"):
        if comp == "..":
            if depth:
                depth.pop()
        elif comp not in ("", "."):
            depth.append(comp)
    return "/".join(depth)


def _member_cost(member: tarfile.TarInfo, sizes: dict[str, int]) -> int:
    """會寫進磁碟的大小。連結也算目標的大小：檔案系統不支援連結（exFAT、FUSE…）時
    tarfile 會改成複製一份。"""
    if member.isreg():
        return member.size
    if member.islnk() or member.issym():
        return sizes.get(_link_key(member), 0)
    return 0


def _scan_tar(tf: tarfile.TarFile, budget: Budget) -> list[tuple[tarfile.TarInfo, int]]:
    """逐一讀項目標頭並檢查；宣告大小一超過額度就立刻停（不必先解完整個壓縮串流）。"""
    plan: list[tuple[tarfile.TarInfo, int]] = []
    sizes: dict[str, int] = {}
    declared = 0
    while True:
        try:
            member = tf.next()
        except _READ_ERRORS as exc:
            raise NeedExternal from exc
        if member is None:
            break
        keep = _keep_tar_member(member)
        cost = _member_cost(member, sizes)
        # 略過的 macOS 中繼資料也記下大小：連結指向它而連結失敗時，tarfile 會複製它的內容
        sizes["/".join(safe_parts(member.name))] = cost
        if not keep:
            continue
        declared += cost
        budget.check(extra_disk=declared, extra_content=declared)
        plan.append((member, cost))
    budget.plan(declared)
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


def extract_tar(path: Path, root: Path, ctx: ToolContext, budget: Budget) -> None:
    """tar 系列（含 gz／bz2／xz 壓縮）；Python 開不了時丟 NeedExternal。"""
    try:
        tf = tarfile.open(path, "r:*")
    except _READ_ERRORS as exc:
        raise NeedExternal from exc
    with tf:
        plan = _scan_tar(tf, budget)
        for i, (member, cost) in enumerate(plan):
            ctx.check_cancelled()
            _extract_member(tf, member, root)
            budget.charge(cost)
            ctx.progress((i + 1) / len(plan))


def _extract_converted(path: Path, root: Path, ctx: ToolContext, budget: Budget) -> None:
    """bsdtar 轉存出來的 tar；再讀不了就是壞檔。"""
    try:
        extract_tar(path, root, ctx, budget)
    except NeedExternal as exc:
        raise Corrupt from exc
