"""HEIF／AVIF 的檔案結構（ISO BMFF 盒子）：判斷品牌、讀影像尺寸，完全不解碼像素。

尺寸只從 meta → iprp → ipco → ispe 取得（其他地方出現的「ispe」字樣不算）；
走訪的盒子數與 meta 的大小都有上限，壞掉或刻意構造的檔案不會讓程式卡住或吃光記憶體。
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from typing import BinaryIO

HEIC_BRANDS = frozenset({b"heic", b"heix", b"hevc", b"hevx", b"heim", b"heis", b"mif1", b"msf1"})
AVIF_BRANDS = frozenset({b"avif", b"avis"})
MAX_BOXES = 4096  # 每一層最多走訪的盒子數
MAX_META_BYTES = 16 << 20  # meta 盒子最大 16 MB
ISPE_PATH = (b"iprp", b"ipco", b"ispe")


def kind(path: Path) -> str | None:
    """HEIF 家族的檔案回傳 "heic" 或 "avif"（看 ftyp 的品牌）。"""
    try:
        with open(path, "rb") as fh:
            head = fh.read(64)
    except OSError:
        return None
    if len(head) < 16 or head[4:8] != b"ftyp":
        return None
    box = int.from_bytes(head[0:4], "big")
    brands = {head[8:12]}
    brands.update(head[i : i + 4] for i in range(16, min(box, len(head)) - 3, 4))
    if brands & AVIF_BRANDS:
        return "avif"
    if brands & HEIC_BRANDS:
        return "heic"
    return None


def _header(head: bytes, remaining: int) -> tuple[int, bytes, int] | None:
    """盒子的（總長度, 類型, 標頭長度）；長度不合理時回傳 None。"""
    if len(head) < 8:
        return None
    size, box_type, header = int.from_bytes(head[0:4], "big"), head[4:8], 8
    if size == 1:
        if len(head) < 16:
            return None
        size, header = int.from_bytes(head[8:16], "big"), 16
    elif size == 0:
        size = remaining  # 一直到檔案結尾
    if size < header or size > remaining:
        return None
    return size, box_type, header


def _boxes(data: bytes, start: int, end: int) -> Iterator[tuple[bytes, int, int]]:
    """data[start:end] 裡的子盒子：（類型, 內容開頭, 盒子結尾）。"""
    pos = start
    for _ in range(MAX_BOXES):
        parsed = _header(data[pos : pos + 16], end - pos) if pos + 8 <= end else None
        if parsed is None:
            return
        size, box_type, header = parsed
        yield box_type, pos + header, pos + size
        pos += size


def _find(data: bytes, start: int, end: int, path: tuple[bytes, ...]) -> Iterator[tuple[int, int]]:
    for box_type, body, stop in _boxes(data, start, end):
        if box_type != path[0]:
            continue
        if len(path) == 1:
            yield body, stop
        else:
            yield from _find(data, body, stop, path[1:])


def _meta(fh: BinaryIO, file_size: int) -> bytes | None:
    """最上層的 meta 盒子內容（只讀盒子標頭跳過其他盒子，例如很大的 mdat）。"""
    pos = 0
    for _ in range(MAX_BOXES):
        fh.seek(pos)
        parsed = _header(fh.read(16), file_size - pos)
        if parsed is None:
            return None
        size, box_type, header = parsed
        if box_type == b"meta":
            if size - header > MAX_META_BYTES:
                return None
            fh.seek(pos + header)
            return fh.read(size - header)
        pos += size
    return None


def dimensions(path: Path) -> tuple[int, int] | None:
    """所有 ispe 中面積最大的（寬, 高）；找不到時回傳 None（呼叫端必須拒絕處理）。"""
    try:
        with open(path, "rb") as fh:
            meta = _meta(fh, Path(path).stat().st_size)
    except OSError:
        return None
    if meta is None:
        return None
    best: tuple[int, int] | None = None
    # meta 是 FullBox：前 4 位元組是版本與旗標；ispe 也是 FullBox，接著寬、高各 4 位元組
    for body, stop in _find(meta, 4, len(meta), ISPE_PATH):
        if stop - body < 12:
            continue
        w = int.from_bytes(meta[body + 4 : body + 8], "big")
        h = int.from_bytes(meta[body + 8 : body + 12], "big")
        if best is None or w * h > best[0] * best[1]:
            best = (w, h)
    return best
