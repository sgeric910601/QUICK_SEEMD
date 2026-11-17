"""建立 ZIP 與 TAR.GZ（取代原版的 ditto、zip、tar）。

- 保留最上層名稱：資料夾「相簿」→ 壓縮檔裡是「相簿/…」。
- 順序固定（依名稱排序，資料夾在自己的內容之前），不同時間壓同樣的東西得到同樣的項目清單。
- 不收 .DS_Store 與 __MACOSX；資料夾裡的符號連結照原樣存成連結（同 zip -y、tar），不跟進去。
- 管線、裝置檔、socket 會略過（讀管線會卡住）。
- 使用者直接選的項目若是符號連結，壓縮它指向的內容（名稱用連結本身的名稱）。
"""

from __future__ import annotations

import logging
import os
import stat
import tarfile
import time
import zipfile
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from ... import errors
from ...model import ToolContext
from . import textcodec
from .archivekind import JUNK_DIRS, JUNK_NAMES

log = logging.getLogger(__name__)

CHUNK = 1 << 20
GZIP_LEVEL = 6
ZIP_EPOCH = (1980, 1, 1, 0, 0, 0)
ZIP_LAST = (2107, 12, 31, 23, 59, 58)

Progress = Callable[[int], None]


@dataclass(frozen=True)
class Entry:
    """要放進壓縮檔的一個項目。kind：file、dir、link。"""

    source: Path
    arcname: str
    kind: str
    size: int = 0


# ---------------------------------------------------------------- 收集


def _entry_for(path: Path, arcname: str, st: os.stat_result) -> Entry | None:
    if stat.S_ISLNK(st.st_mode):
        return Entry(path, arcname, "link")
    if stat.S_ISDIR(st.st_mode):
        return Entry(path, arcname, "dir")
    if stat.S_ISREG(st.st_mode):
        return Entry(path, arcname, "file", st.st_size)
    log.info("略過特殊檔案：%s", path)
    return None


def _walk(directory: Path, prefix: str) -> list[Entry]:
    out: list[Entry] = []
    try:
        children = sorted(os.scandir(directory), key=lambda e: e.name)
    except OSError as exc:
        raise errors.unreadable(textcodec.read_error_text(directory, exc)) from exc
    for child in children:
        if child.name in JUNK_NAMES:
            continue
        try:
            st = child.stat(follow_symlinks=False)
        except FileNotFoundError:
            log.info("壓縮時檔案已不見，略過：%s", child.path)
            continue
        if child.name in JUNK_DIRS and stat.S_ISDIR(st.st_mode):
            continue
        entry = _entry_for(Path(child.path), f"{prefix}/{child.name}", st)
        if entry is None:
            continue
        out.append(entry)
        if entry.kind == "dir":
            out.extend(_walk(entry.source, entry.arcname))
    return out


def collect(item: Path, arcname: str) -> list[Entry]:
    """item 與它的全部內容；最上層的符號連結會跟進去。"""
    source = Path(item)
    if source.is_symlink() and source.exists():
        source = source.resolve()
    try:
        st = source.lstat()
    except OSError as exc:
        raise errors.file_not_found(Path(item).name) from exc
    top = _entry_for(source, arcname, st)
    if top is None:
        raise errors.unreadable(f"「{Path(item).name}」不是一般的檔案或資料夾。")
    if top.kind != "dir":
        return [top]
    return [top, *_walk(source, arcname)]


def total_size(entries: list[Entry]) -> int:
    return sum(e.size for e in entries)


# ---------------------------------------------------------------- 讀檔時回報進度與取消


class _Reader:
    """包住來源檔：每讀一塊就檢查取消並回報讀了多少。"""

    def __init__(self, raw: BinaryIO, ctx: ToolContext, report: Progress) -> None:
        self._raw = raw
        self._ctx = ctx
        self._report = report

    def read(self, size: int = -1) -> bytes:
        self._ctx.check_cancelled()
        chunk = self._raw.read(size)
        self._report(len(chunk))
        return chunk


def _open_source(path: Path) -> BinaryIO:
    try:
        return open(path, "rb")  # noqa: SIM115 — 呼叫端用 with 關閉
    except OSError as exc:
        raise errors.unreadable(textcodec.read_error_text(path, exc)) from exc


# ---------------------------------------------------------------- ZIP


def _zip_time(mtime: float) -> tuple[int, int, int, int, int, int]:
    stamp = tuple(time.localtime(mtime)[:6])
    return max(min(stamp, ZIP_LAST), ZIP_EPOCH)  # type: ignore[return-value]


def _zip_link(zf: zipfile.ZipFile, entry: Entry) -> None:
    st = os.lstat(entry.source)
    info = zipfile.ZipInfo(entry.arcname, _zip_time(st.st_mtime))
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    zf.writestr(info, os.readlink(entry.source).encode("utf-8", "surrogateescape"))


def _zip_file(zf: zipfile.ZipFile, entry: Entry, ctx: ToolContext, report: Progress) -> None:
    info = zipfile.ZipInfo.from_file(entry.source, entry.arcname, strict_timestamps=False)
    info.compress_type = zipfile.ZIP_DEFLATED
    with _open_source(entry.source) as raw, zf.open(info, "w") as dst:
        reader = _Reader(raw, ctx, report)
        while chunk := reader.read(CHUNK):
            dst.write(chunk)


def write_zip(entries: list[Entry], out: Path, ctx: ToolContext, report: Progress) -> None:
    with zipfile.ZipFile(out, "x", zipfile.ZIP_DEFLATED, strict_timestamps=False) as zf:
        for entry in entries:
            ctx.check_cancelled()
            if entry.kind == "dir":
                info = zipfile.ZipInfo.from_file(
                    entry.source, entry.arcname + "/", strict_timestamps=False
                )
                zf.writestr(info, b"")
            elif entry.kind == "link":
                _zip_link(zf, entry)
            else:
                _zip_file(zf, entry, ctx, report)


# ---------------------------------------------------------------- TAR.GZ


def write_tgz(entries: list[Entry], out: Path, ctx: ToolContext, report: Progress) -> None:
    with tarfile.open(out, "x:gz", compresslevel=GZIP_LEVEL, format=tarfile.PAX_FORMAT) as tf:
        for entry in entries:
            ctx.check_cancelled()
            info = tf.gettarinfo(str(entry.source), arcname=entry.arcname)
            if info.isreg():
                with _open_source(entry.source) as raw:
                    tf.addfile(info, _Reader(raw, ctx, report))  # type: ignore[arg-type]
            else:
                tf.addfile(info)


WRITERS: dict[str, Callable[[list[Entry], Path, ToolContext, Progress], None]] = {
    "zip": write_zip,
    "tgz": write_tgz,
}


def write(fmt: str, entries: list[Entry], out: Path, ctx: ToolContext, report: Progress) -> None:
    """依格式寫出；寫入失敗時丟出「寫入輸出檔失敗：…」。"""
    try:
        WRITERS[fmt](entries, out, ctx, report)
    except errors.QSError:
        raise
    except OSError as exc:
        raise errors.write_failed(exc.strerror or str(exc)) from exc
