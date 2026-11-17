"""二進位檔（對應原版 QSBinary）：辨識格式（Mach-O／ELF／PE …）、資訊面板、彩色十六進位傾印。"""

from __future__ import annotations

import hashlib
import logging
import struct
from pathlib import Path

from . import Attachment, fmt, icons
from .page import Chip, FileInfo, Page, escape

log = logging.getLogger(__name__)

HEX_LIMIT = 8 * 1024
IDENTIFY_BYTES = 4096
SHA_LIMIT = 64 * 1024 * 1024
BIG_ICON_SIZE = 256

MAGICS: tuple[tuple[bytes, int, str], ...] = (
    (b"\x7fELF", 0, "ELF 執行檔"),
    (b"\x00asm", 0, "WebAssembly 模組"),
    (b"PK\x03\x04", 0, "ZIP 壓縮資料"),
    (b"\x1f\x8b", 0, "gzip 壓縮資料"),
    (b"\xfd7zXZ\x00", 0, "xz 壓縮資料"),
    (b"BZh", 0, "bzip2 壓縮資料"),
    (b"\x28\xb5\x2f\xfd", 0, "Zstandard 壓縮資料"),
    (b"7z\xbc\xaf\x27\x1c", 0, "7-Zip 壓縮檔"),
    (b"Rar!\x1a\x07", 0, "RAR 壓縮檔"),
    (b"\x04\x22\x4d\x18", 0, "LZ4 壓縮資料"),
    (b"bplist00", 0, "二進位屬性列表 (bplist)"),
    (b"SQLite format 3", 0, "SQLite 資料庫"),
    (b"%PDF-", 0, "PDF 文件"),
    (b"\x00\x00\x00\x01Bud1", 0, "Finder .DS_Store 中繼資料"),
    (b"PACK", 0, "Git pack 檔"),
    (b"\x00\x01\x00\x00\x00", 0, "TrueType 字型"),
    (b"OTTO", 0, "OpenType 字型"),
    (b"wOFF", 0, "WOFF 網頁字型"),
    (b"wOF2", 0, "WOFF2 網頁字型"),
    (b"ID3", 0, "MP3 音訊（ID3）"),
    (b"OggS", 0, "Ogg 媒體"),
    (b"fLaC", 0, "FLAC 音訊"),
    (b"\x1a\x45\xdf\xa3", 0, "Matroska / WebM 影片"),
    (b"ftyp", 4, "ISO 媒體（MP4 / MOV / HEIC）"),
    (b"RIFF", 0, "RIFF 容器（WAV / AVI / WebP）"),
    (b"\x89PNG", 0, "PNG 圖片"),
    (b"\xff\xd8\xff", 0, "JPEG 圖片"),
    (b"GIF8", 0, "GIF 圖片"),
    (b"8BPS", 0, "PSD 影像"),
    (b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1", 0, "Microsoft Office 舊版文件（OLE2）"),
    (b"koly", -512, "磁碟映像檔 (DMG)"),  # 原版的負位移永遠不比對，照樣保留
    (b"xar!", 0, "XAR 封存（.pkg / .xip）"),
    (b"\xed\xab\xee\xdb", 0, "RPM 套件"),
    (b"!<arch>\n", 0, "ar 封存（.a / .deb）"),
    (b"dex\n", 0, "Android DEX"),
    (b"L\x00\x00\x00\x01\x14\x02\x00", 0, "Windows 捷徑 (.lnk)"),
    (b"-----BEGIN", 0, "PEM 憑證 / 金鑰"),
)
MACHO_TYPES = {
    1: "目的檔 (.o)",
    2: "執行檔",
    3: "固定 VM 共用程式庫",
    4: "核心傾印",
    5: "預先載入執行檔",
    6: "動態程式庫 (.dylib)",
    7: "動態連結器",
    8: "套件 (bundle)",
    9: "動態程式庫 Stub",
    10: "除錯符號 (dSYM)",
    11: "核心延伸功能 (kext)",
}
ELF_MACHINES = {
    0x3E: "x86-64",
    0xB7: "AArch64",
    0x28: "ARM",
    0x03: "x86",
    0xF3: "RISC-V",
    0x08: "MIPS",
}
ELF_TYPES = {1: "可重定位", 2: "執行檔", 3: "共用物件", 4: "核心傾印"}
PE_MACHINES = {0x8664: "x64", 0x14C: "x86", 0xAA64: "ARM64", 0x1C0: "ARM"}

Details = list[tuple[str, str]]


def _u32(h: bytes, o: int, fmt_char: str = "<") -> int:
    return struct.unpack_from(fmt_char + "I", h, o)[0] if o + 3 < len(h) else 0


def _u16(h: bytes, o: int) -> int:
    return struct.unpack_from("<H", h, o)[0] if o + 1 < len(h) else 0


def cpu_name(t: int, sub: int = 0) -> str:
    names = {0x0100000C: "arm64", 0x0200000C: "arm64_32", 0x01000007: "x86_64", 7: "i386",
             12: "arm", 18: "ppc", 0x01000012: "ppc64"}  # fmt: skip
    if t == 0x0100000C and sub & 0xFF == 2:
        return "arm64e"
    return names.get(t, f"0x{t:x}")


def _macho(h: bytes) -> tuple[str, Details] | None:
    m = _u32(h, 0)
    if m not in (0xFEEDFACF, 0xFEEDFACE):
        return None
    details = [
        ("架構", cpu_name(_u32(h, 4), _u32(h, 8))),
        ("類型", MACHO_TYPES.get(_u32(h, 12), "未知")),
        ("載入命令", f"{_u32(h, 16)} 個"),
    ]
    return f"Mach-O {'64' if m == 0xFEEDFACF else '32'} 位元", details


def _fat_or_java(h: bytes) -> tuple[str, Details] | None:
    mb = _u32(h, 0, ">")
    if mb not in (0xCAFEBABE, 0xCAFEBABF):
        return None
    n = _u32(h, 4, ">")
    if 0 < n < 30:
        size = 32 if mb == 0xCAFEBABF else 20
        archs = [
            cpu_name(_u32(h, 8 + k * size, ">"), _u32(h, 12 + k * size, ">")) for k in range(n)
        ]
        return "Mach-O 通用二進位（Universal）", [("架構", "、".join(archs))]
    major = h[6] << 8 | h[7] if len(h) > 7 else 0
    if major >= 45:
        return "Java 類別檔", [("版本", f"Java {major - 44}")]
    return None


def _elf(h: bytes) -> tuple[str, Details] | None:
    if not h.startswith(b"\x7fELF") or len(h) <= 20:
        return None
    machine, kind = _u16(h, 18), _u16(h, 16)
    return "ELF（Linux）", [
        ("位元", "64 位元" if h[4] == 2 else "32 位元"),
        ("架構", ELF_MACHINES.get(machine, f"0x{machine:x}")),
        ("類型", ELF_TYPES.get(kind, "未知")),
    ]


def _pe(h: bytes) -> tuple[str, Details] | None:
    if not h.startswith(b"MZ"):
        return None
    pe = _u32(h, 0x3C)
    if 0 < pe and pe + 6 < len(h) and h[pe : pe + 2] == b"PE":
        machine = _u16(h, pe + 4)
        return "Windows PE 執行檔", [("架構", PE_MACHINES.get(machine, f"0x{machine:x}"))]
    return "DOS / Windows 執行檔", []


def identify(head: bytes) -> tuple[str | None, Details]:
    """辨識前 4096 位元組；回傳（格式名稱, 細節列）。"""
    h = head[:IDENTIFY_BYTES]
    for probe in (_macho, _fat_or_java, _elf, _pe):
        found = probe(h)
        if found is not None:
            return found
    for magic, offset, name in MAGICS:
        if offset >= 0 and h[offset : offset + len(magic)] == magic:
            return name, []
    return None, []


def sha256(path: Path, size: int) -> str | None:
    if size > SHA_LIMIT:
        return None
    digest = hashlib.sha256()
    try:
        with open(path, "rb") as fh:
            while chunk := fh.read(1 << 20):
                digest.update(chunk)
    except OSError:
        log.debug("無法計算雜湊：%s", path, exc_info=True)
        return None
    return digest.hexdigest()


def _cls(c: int) -> str:
    if c == 0:
        return "z"
    if c in (9, 10, 13, 32):
        return "w"
    if 32 < c < 127:
        return "p"
    return "h" if c >= 128 else "x"


_CLS = [_cls(c) for c in range(256)]
_ASCII = ["&amp;" if c == 38 else "&lt;" if c == 60 else "&gt;" if c == 62 else chr(c) if 32 <= c < 127
          else "·" for c in range(256)]  # fmt: skip


def _ascii_column(row: bytes) -> str:
    out: list[str] = []
    last = ""
    for c in row:
        k = _CLS[c]
        if k != last:
            if last:
                out.append("</span>")
            out.append(f'<span class="{k}">')
            last = k
        out.append(_ASCII[c])
    if last:
        out.append("</span>")
    return "".join(out)


def hex_dump(data: bytes, base_offset: int = 0) -> str:
    """每列 16 位元組：位移、十六進位（第 8 個後多一格）、可見字元欄。"""
    out: list[str] = []
    for row in range(0, len(data), 16):
        chunk = data[row : row + 16]
        out.append(f'<span class="o">{base_offset + row:08x}</span>  ')
        for i in range(16):
            if i == 8:
                out.append(" ")
            if i < len(chunk):
                out.append(f'<span class="{_CLS[chunk[i]]}">{chunk[i]:02x}</span> ')
            else:
                out.append("   ")
        out.append(' <span class="sep">│</span>')
        out.append(_ascii_column(chunk))
        out.append('<span class="sep">│</span>\n')
    return "".join(out)


def info_panel(
    info: FileInfo, rows: Details, subtitle: str, mime: str | None = None
) -> tuple[str, dict[str, Attachment]]:
    """大圖示＋檔名＋屬性表（mime 可指定圖示用的類型，例如資料夾）。"""
    icon = icons.for_mime(mime or info.mime, BIG_ICON_SIZE)
    atts = {"big-icon": Attachment("big-icon", icon.data, icon.mime)}
    h = ['<section class="info"><img class="big" src="cid:big-icon" alt="">']
    h.append(
        f'<div><div class="name">{escape(info.name)}</div><div class="sub">{escape(subtitle)}</div>'
    )
    h.append('<dl class="kv">')
    for k, v in rows:
        mono = ' class="mono"' if k in ("SHA-256", "權限") else ""
        h.append(f"<dt>{escape(k)}</dt><dd{mono}>{escape(v)}</dd>")
    h.append("</dl></div></section>")
    return "".join(h), atts


def standard_rows(info: FileInfo) -> Details:
    return [
        ("大小", f"{fmt.bytes_label(info.size)}（{fmt.number(info.size)} 位元組）"),
        ("建立時間", fmt.date_long(info.created)),
        ("修改時間", fmt.date_long(info.modified)),
        ("權限", fmt.permissions(info.mode)),
    ]


def hex_section(data: bytes, title: str) -> str:
    return (
        f'<div class="section-title">{escape(title)}</div>'
        f'<div class="card"><pre class="hex">{hex_dump(data)}</pre></div>'
    )


def render(info: FileInfo, head: bytes) -> Page:
    """二進位資訊頁。"""
    fmt_name, details = identify(head)
    rows: Details = [("偵測格式", fmt_name)] if fmt_name else []
    rows += details + standard_rows(info)
    digest = sha256(info.path, info.size)
    if digest:
        rows.append(("SHA-256", digest))
    description = info.kind_description
    panel, atts = info_panel(info, rows, description)
    page = Page(kind=fmt_name or "二進位檔", show_icon=False, size=(820, 640))
    page.chips = [Chip(description)]
    body = panel
    if head:
        shown = head[:HEX_LIMIT]
        more = f"（前 {fmt.bytes_label(len(shown))}）" if info.size > len(shown) else ""
        body += hex_section(shown, f"十六進位內容{more}")
    page.body = body
    page.attachments = atts
    return page
