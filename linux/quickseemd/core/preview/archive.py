"""不解壓縮即可列出 ZIP／TAR／TAR.GZ／TAR.XZ 內容（對應原版 QSArchive）。

只讀檔案、不會寫出任何東西，所以沒有路徑穿越的問題；檔名只拿來顯示（會跳脫）。
Linux 版補充（惡意檔案不能拖垮預覽）：
- 處理的紀錄數有上限（MAX_ENTRIES 的 10 倍），大量中繼資料不會拖住預覽。
- 每個路徑最多保留 MAX_PATH_CHARS 個字元（tar 的長檔名紀錄可達 1 MB，壓縮後卻很小）。
- 解壓縮一律分段進行，輸出有上限。
"""

from __future__ import annotations

import logging
import lzma
import re
import struct
import zlib
from collections.abc import Iterator
from contextlib import closing
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from pathlib import Path

from .page import from_timestamp

log = logging.getLogger(__name__)

MAX_ENTRIES = 20000
MAX_PATH_CHARS = 4096
RECORD_FACTOR = 10
ZIP_TAIL = 65_557
MAX_CENTRAL_DIR = 64 * 1024 * 1024
META_LIMIT = 1_000_000
MAX_TAR_OUTPUT = 4 * 1024 * 1024 * 1024
CHUNK = 256 * 1024
TAR_READ = 64 * 1024
_CD_RECORD = struct.Struct("<4sHHHHHHIIIHHHHHII")
_INT = re.compile(rb"[+-]?[0-9]+")
_FLOAT = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")


@dataclass(frozen=True)
class Entry:
    path: str
    is_dir: bool
    size: int | None
    compressed_size: int | None
    modified: datetime | None
    encrypted: bool = False
    is_link: bool = False


@dataclass
class Listing:
    format: str
    entries: list[Entry] = field(default_factory=list)
    truncated: bool = False
    hidden_mac_metadata: int = 0
    comment: str | None = None


def _clip(path: str) -> str:
    return path if len(path) <= MAX_PATH_CHARS else path[:MAX_PATH_CHARS] + "…"


def _record_cap() -> int:
    return MAX_ENTRIES * RECORD_FACTOR


def u16(b: bytes, o: int) -> int:
    return b[o] | b[o + 1] << 8 if 0 <= o and o + 1 < len(b) else 0


def u32(b: bytes, o: int) -> int:
    return u16(b, o) | u16(b, o + 2) << 16 if 0 <= o and o + 3 < len(b) else 0


def u64(b: bytes, o: int) -> int:
    return u32(b, o) | u32(b, o + 4) << 32


def decode_name(raw: bytes, utf8_flag: bool) -> str:
    """檔名解碼：UTF-8 旗標或純 ASCII 直接當 UTF-8；否則依序試 UTF-8、Big5、GB18030、CP437。"""
    if utf8_flag or raw.isascii():
        return raw.decode("utf-8", "replace")
    for enc in ("utf-8", "big5", "gb18030"):
        try:
            return raw.decode(enc)
        except UnicodeDecodeError:
            continue
    return raw.decode("cp437")


def dos_date(date: int, time: int) -> datetime | None:
    """DOS 日期時間（本地時間）；時分秒超出範圍時順延，與系統日曆的寬鬆換算相同。"""
    month, day = (date >> 5) & 0xF, date & 0x1F
    if not (1 <= month <= 12 and 1 <= day <= 31):
        return None
    base = datetime(1980 + (date >> 9), month, 1)
    return base + timedelta(
        days=day - 1, hours=time >> 11, minutes=(time >> 5) & 0x3F, seconds=(time & 0x1F) * 2
    )


def is_mac_metadata(path: str) -> bool:
    last = path.rstrip("/").rsplit("/", 1)[-1]
    return (
        path.startswith("__MACOSX/")
        or path == "__MACOSX"
        or last.startswith("._")
        or last == ".DS_Store"
    )


def _read_at(fh, offset: int, count: int) -> bytes:  # noqa: ANN001
    try:
        fh.seek(offset)
        return fh.read(count)
    except (OSError, OverflowError, ValueError):
        return b""


# ---------------------------------------------------------------- ZIP


def _zip64(
    fh, tail: bytes, eocd: int, values: tuple[int, int, int]
) -> tuple[int, int, int]:  # noqa: ANN001
    total, cd_size, cd_offset = values
    if not (total == 0xFFFF or cd_size == 0xFFFFFFFF or cd_offset == 0xFFFFFFFF):
        return values
    if eocd < 20 or tail[eocd - 20 : eocd - 16] != b"PK\x06\x07":
        return values
    e64 = _read_at(fh, u64(tail, eocd - 20 + 8), 56)
    if len(e64) >= 56 and e64[:4] == b"PK\x06\x06":
        return u64(e64, 32), u64(e64, 40), u64(e64, 48)
    return values


def _extras(cd: bytes, e: int, length: int, state: list) -> None:
    """讀附加欄位：ZIP64 大小、Unix 時間、Unicode 路徑（state = [名稱, 原始大小, 壓縮後, 時間]）。"""
    e_end = min(e + length, len(cd))
    while e + 4 <= e_end:
        hid, ln = u16(cd, e), u16(cd, e + 2)
        d = e + 4
        if hid == 0x0001:
            q = d
            if state[1] == 0xFFFFFFFF and q + 8 <= d + ln:
                state[1] = u64(cd, q)
                q += 8
            if state[2] == 0xFFFFFFFF and q + 8 <= d + ln:
                state[2] = u64(cd, q)
        elif hid == 0x5455 and ln >= 5 and d < len(cd) and cd[d] & 1:
            state[3] = from_timestamp(u32(cd, d + 1))
        elif hid == 0x7075 and ln > 5 and d + ln <= len(cd):
            state[0] = cd[d + 5 : d + ln].decode("utf-8", "replace")
        e = d + ln


def _zip_entries(cd: bytes, total: int, listing: Listing) -> None:
    o = count = 0
    while o + 46 <= len(cd) and count < total:
        rec = _CD_RECORD.unpack_from(cd, o)
        if rec[0] != b"PK\x01\x02":
            break
        flags, tm, date, csize, usize = rec[3], rec[5], rec[6], rec[8], rec[9]
        name_len, extra_len, cmt_len, ext_attr = rec[10], rec[11], rec[12], rec[15]
        start = o + 46
        if start + name_len > len(cd):
            break
        state = [decode_name(cd[start : start + name_len], bool(flags & 0x800)), usize, csize,
                 dos_date(date, tm)]  # fmt: skip
        _extras(cd, start + name_len, extra_len, state)
        o = start + name_len + extra_len + cmt_len
        count += 1
        if count >= _record_cap():
            listing.truncated = True
            break
        name = str(state[0])
        if is_mac_metadata(name):
            listing.hidden_mac_metadata += 1
            continue
        is_dir = name.endswith("/")
        mode = (ext_attr >> 16) & 0xFFFF
        listing.entries.append(
            Entry(_clip(name[:-1] if is_dir else name), is_dir, None if is_dir else int(state[1]),
                  None if is_dir else int(state[2]), state[3], bool(flags & 1),
                  mode & 0xF000 == 0xA000)  # fmt: skip
        )
        if len(listing.entries) >= MAX_ENTRIES:
            listing.truncated = True
            break


def read_zip(path: Path, size: int) -> Listing | None:
    """讀中央目錄；找不到結尾紀錄就回傳 None。"""
    try:
        with open(path, "rb") as fh:
            tail_len = min(size, ZIP_TAIL)
            tail = _read_at(fh, size - tail_len, tail_len)
            if len(tail) < 22:
                return None
            eocd = tail.rfind(b"PK\x05\x06", 0, len(tail) - 18)
            if eocd < 0:
                return None
            listing = Listing("ZIP")
            comment_len = u16(tail, eocd + 20)
            if comment_len > 0 and eocd + 22 + comment_len <= len(tail):
                listing.comment = decode_name(tail[eocd + 22 : eocd + 22 + comment_len], False)
            values = (u16(tail, eocd + 10), u32(tail, eocd + 12), u32(tail, eocd + 16))
            total, cd_size, cd_offset = _zip64(fh, tail, eocd, values)
            cd = _read_at(fh, cd_offset, min(cd_size, MAX_CENTRAL_DIR))
    except OSError:
        log.debug("讀不到 ZIP：%s", path, exc_info=True)
        return None
    _zip_entries(cd, total, listing)
    return listing


# ---------------------------------------------------------------- TAR


def parse_number(h: bytes, a: int, length: int) -> int:
    """tar 的數字欄位：八進位（遇到 NUL 或數字後的空白停止），或 base-256。"""
    if h[a] & 0x80:
        v = 0
        for c in h[a + 1 : a + length]:
            v = v << 8 | c
        return v
    v = 0
    for c in h[a : a + length]:
        if 48 <= c <= 55:
            v = v * 8 + c - 48
        elif c == 0 or (c == 32 and v > 0):
            break
    return v


def _tar_str(h: bytes, a: int, length: int) -> str:
    raw = h[a : a + length]
    nul = raw.find(b"\x00")
    return decode_name(raw if nul < 0 else raw[:nul], False)


def looks_like_tar(d: bytes) -> bool:
    return len(d) >= 262 and d[257:262] == b"ustar"


class TarParser:
    """串流解析（可以接在解壓縮後面）：GNU 長檔名、PAX、ustar 前綴、base-256。"""

    def __init__(self) -> None:
        self.entries: list[Entry] = []
        self.hidden = 0
        self.done = False
        self.invalid = False
        self.truncated = False
        self.skip = 0
        self._buf = bytearray()
        self._collect = 0
        self._collect_kind = 0
        self._collect_pad = 0
        self._long_name: str | None = None
        self._pax: dict[str, object] = {}
        self._zero_blocks = 0
        self._headers = 0

    def consume_skip(self, n: int) -> None:
        self.skip = max(0, self.skip - n)

    def feed(self, data: bytes) -> None:
        mv = memoryview(data)
        i, n = 0, len(data)
        while i < n and not self.done:
            if self.skip > 0:
                k = min(self.skip, n - i)
                self.skip -= k
                i += k
            elif self._collect > 0:
                k = min(self._collect - len(self._buf), n - i)
                self._buf += mv[i : i + k]
                i += k
                if len(self._buf) == self._collect:
                    self._process_meta(bytes(self._buf))
                    self._buf = bytearray()
                    self._collect = 0
                    self.skip = self._collect_pad
            else:
                k = min(512 - len(self._buf), n - i)
                self._buf += mv[i : i + k]
                i += k
                if len(self._buf) == 512:
                    header = bytes(self._buf)
                    self._buf = bytearray()
                    self._process_header(header)

    def _checksum_ok(self, h: bytes) -> bool:
        return sum(h) - sum(h[148:156]) + 8 * 32 == parse_number(h, 148, 8)

    def _process_header(self, h: bytes) -> None:
        if not h.strip(b"\x00"):
            self._zero_blocks += 1
            self.done = self._zero_blocks >= 2
            return
        self._zero_blocks = 0
        if not self._checksum_ok(h):
            self.invalid = self._headers == 0
            self.done = True
            return
        self._headers += 1
        if self._headers >= _record_cap():
            self.truncated = self.done = True
            return
        size = parse_number(h, 124, 12)
        typ = h[156]
        pad = (512 - size % 512) % 512
        if typ in (76, 120):  # 'L' GNU 長檔名、'x' PAX
            self._collect = min(size, META_LIMIT)
            self._collect_kind = typ
            self._collect_pad = pad + (size - self._collect)
            if self._collect == 0:
                self.skip = pad
            return
        if typ in (103, 75):  # 'g' 全域 PAX、'K' 長連結名
            self.skip = size + pad
            return
        self._entry(h, size, pad, typ)

    def _entry(self, h: bytes, size: int, pad: int, typ: int) -> None:
        name = _tar_str(h, 0, 100)
        if h[257:262] == b"ustar":
            prefix = _tar_str(h, 345, 155)
            if prefix:
                name = prefix + "/" + name
        name = self._long_name if self._long_name is not None else name
        name = str(self._pax.get("path", name))
        real_size = int(self._pax.get("size", size))  # type: ignore[call-overload]
        mtime = self._pax.get("mtime", parse_number(h, 136, 12))
        self._long_name = None
        self._pax = {}
        is_dir = typ == 53 or name.endswith("/")
        name = name.rstrip("/")
        if name.startswith("./"):
            name = name[2:]
        if typ in (49, 50):
            self.skip = size + pad
        else:
            self.skip = 0 if typ == 53 else real_size + (512 - real_size % 512) % 512
        if name in ("", "."):
            return
        if is_mac_metadata(name):
            self.hidden += 1
            return
        when = from_timestamp(float(mtime))  # type: ignore[arg-type]
        self.entries.append(
            Entry(_clip(name), is_dir, None if is_dir else real_size, None, when, is_link=typ == 50)
        )
        if len(self.entries) >= MAX_ENTRIES:
            self.truncated = self.done = True

    def _process_meta(self, buf: bytes) -> None:
        if self._collect_kind == 76:
            nul = buf.find(b"\x00")
            self._long_name = decode_name(buf if nul < 0 else buf[:nul], False)
            return
        i = 0  # PAX：「長度 鍵=值\n」
        while i < len(buf):
            j = buf.find(b" ", i)
            j = len(buf) if j < 0 else j
            if not _INT.fullmatch(buf[i:j]):
                break
            length = int(buf[i:j])
            if length <= 0 or i + length > len(buf):
                break
            key, sep, value = buf[j + 1 : i + length - 1].decode("utf-8", "replace").partition("=")
            if sep:
                self._pax_value(key, value)
            i += length

    def _pax_value(self, key: str, value: str) -> None:
        if key == "path":
            self._pax["path"] = value
        elif key == "size" and _INT.fullmatch(value.encode()):
            self._pax["size"] = int(value)
        elif key == "mtime" and _FLOAT.fullmatch(value):
            self._pax["mtime"] = float(value)


def read_tar(path: Path) -> Listing | None:
    """未壓縮的 tar；第一個檔頭的檢查碼就不對時回傳 None。"""
    parser = TarParser()
    try:
        with open(path, "rb") as fh:
            while not parser.done:
                chunk = fh.read(TAR_READ)
                if not chunk:
                    break
                parser.feed(chunk)
                if parser.skip > 0:
                    fh.seek(parser.skip, 1)
                    parser.consume_skip(parser.skip)
    except OSError:
        log.debug("讀不到 tar：%s", path, exc_info=True)
        return None
    if parser.invalid:
        return None
    return Listing("TAR", parser.entries, parser.truncated, parser.hidden)


# ---------------------------------------------------------------- gzip／xz


@dataclass(frozen=True)
class GzipInfo:
    header_length: int
    original_name: str | None
    modified: datetime | None
    uncompressed_size: int | None


def gzip_info(path: Path, size: int) -> GzipInfo | None:
    """手動解析 gzip 檔頭（保留原始檔名）；原始大小取自結尾 4 位元組（以 4 GiB 為模）。"""
    try:
        with open(path, "rb") as fh:
            h = fh.read(4096)
            trailer = _read_at(fh, max(0, size - 4), 4)
    except OSError:
        return None
    if len(h) < 18 or h[0] != 0x1F or h[1] != 0x8B or h[2] != 8:
        return None
    flags, mtime, p = h[3], u32(h, 4), 10
    if flags & 4:
        p += 2 + u16(h, p)
    name = None
    if flags & 8:
        end = h.find(b"\x00", p)
        end = len(h) if end < 0 else end
        name = decode_name(h[p:end], False)
        p = end + 1
    if flags & 16:
        end = h.find(b"\x00", p)
        p = (len(h) if end < 0 else end) + 1
    if flags & 2:
        p += 2
    modified = from_timestamp(mtime) if mtime > 0 else None
    return GzipInfo(p, name, modified, u32(trailer, 0) if len(trailer) == 4 else None)


def _chunks(fh, kind: str) -> Iterator[bytes]:  # noqa: ANN001
    """把壓縮資料一段一段解開（每段最多 CHUNK 位元組，不會一次吃光記憶體）。"""
    if kind == "gzip":
        z = zlib.decompressobj(-15)
        while not z.eof:
            data = z.unconsumed_tail or fh.read(CHUNK)
            if not data:
                return
            out = z.decompress(data, CHUNK)
            if out:
                yield out
        return
    x = lzma.LZMADecompressor()
    while not x.eof:
        data = b""
        if x.needs_input:
            data = fh.read(CHUNK)
            if not data:
                return
        out = x.decompress(data, CHUNK)
        if out:
            yield out


def decompress(path: Path, kind: str, skip: int, max_output: int) -> Iterator[bytes]:
    """解壓縮輸出（最多 max_output 位元組）；資料損毀時就停在那裡。"""
    produced = 0
    try:
        with open(path, "rb") as fh:
            fh.seek(skip)
            for out in _chunks(fh, kind):
                yield out
                produced += len(out)
                if produced >= max_output:
                    return
    except (OSError, zlib.error, lzma.LZMAError, EOFError):
        log.debug("解壓縮中斷：%s", path, exc_info=True)


def decompress_head(path: Path, kind: str, skip: int, limit: int) -> bytes:
    out = bytearray()
    with closing(decompress(path, kind, skip, limit)) as chunks:
        for chunk in chunks:
            out += chunk
            if len(out) >= limit:
                break
    return bytes(out[:limit])


def read_compressed_tar(path: Path, kind: str, skip: int, fmt_name: str) -> Listing | None:
    parser = TarParser()
    with closing(decompress(path, kind, skip, MAX_TAR_OUTPUT)) as chunks:
        for chunk in chunks:
            parser.feed(chunk)
            if parser.done:
                break
    if parser.invalid or (not parser.entries and parser.hidden == 0):
        return None
    return Listing(fmt_name, parser.entries, parser.truncated, parser.hidden)
