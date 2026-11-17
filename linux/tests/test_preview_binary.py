"""預覽核心：二進位格式辨識、十六進位傾印、資訊面板、屬性列表。"""

from __future__ import annotations

import hashlib
import plistlib
import struct
from pathlib import Path

import pytest

from quickseemd.core.preview import binary, engine, plist
from quickseemd.core.preview.page import FileInfo


def test_identify_system_elf() -> None:
    head = Path("/usr/bin/ls").read_bytes()[:4096]
    fmt_name, details = binary.identify(head)
    assert fmt_name == "ELF（Linux）"
    assert details == [("位元", "64 位元"), ("架構", "x86-64"), ("類型", "共用物件")]


def test_identify_fat_macho_sample(samples: Path) -> None:
    fmt_name, details = binary.identify((samples / "hello-binary").read_bytes()[:4096])
    assert fmt_name == "Mach-O 通用二進位（Universal）"
    assert details == [("架構", "x86_64、arm64")]


def test_identify_thin_macho_java_pe_dos() -> None:
    macho = struct.pack("<IIIII", 0xFEEDFACF, 0x0100000C, 2, 6, 17) + b"\x00" * 16
    assert binary.identify(macho) == (
        "Mach-O 64 位元",
        [("架構", "arm64e"), ("類型", "動態程式庫 (.dylib)"), ("載入命令", "17 個")],
    )
    macho32 = struct.pack("<IIIII", 0xFEEDFACE, 7, 3, 99, 1)
    assert binary.identify(macho32)[1][0] == ("架構", "i386")
    assert binary.identify(macho32)[1][1] == ("類型", "未知")
    java = bytes.fromhex("cafebabe00000034") + b"\x00" * 8
    assert binary.identify(java) == ("Java 類別檔", [("版本", "Java 8")])
    pe = bytearray(512)
    pe[0:2] = b"MZ"
    pe[0x3C:0x40] = struct.pack("<I", 0x80)
    pe[0x80:0x86] = b"PE\x00\x00" + struct.pack("<H", 0x8664)
    assert binary.identify(bytes(pe)) == ("Windows PE 執行檔", [("架構", "x64")])
    assert binary.identify(b"MZ" + b"\x00" * 100) == ("DOS / Windows 執行檔", [])


@pytest.mark.parametrize(
    ("head", "name"),
    [
        (b"\x89PNG\r\n\x1a\n" + b"\x00" * 8, "PNG 圖片"),
        (b"%PDF-1.7\n", "PDF 文件"),
        (b"\x00\x00\x00\x18ftypisom", "ISO 媒體（MP4 / MOV / HEIC）"),
        (b"\x00asm\x01\x00\x00\x00", "WebAssembly 模組"),
        (b"-----BEGIN CERTIFICATE-----", "PEM 憑證 / 金鑰"),
        (b"!<arch>\ndebian", "ar 封存（.a / .deb）"),
        (b"\x7fELF\x01", "ELF 執行檔"),  # 太短，不解析細節，交給特徵表
        (b"koly" + b"\x00" * 600, None),  # 原版的 DMG 特徵在 -512，永遠比對不到
        (b"\x01\x02\x03", None),
    ],
)
def test_magic_table(head: bytes, name: str | None) -> None:
    assert binary.identify(head)[0] == name


def test_hex_dump_markup() -> None:
    assert binary.hex_dump(b"Ab\x00\n\xff") == (
        '<span class="o">00000000</span>  <span class="p">41</span> <span class="p">62</span> '
        '<span class="z">00</span> <span class="w">0a</span> <span class="h">ff</span> '
        + " " * 34
        + ' <span class="sep">│</span><span class="p">Ab</span><span class="z">·</span>'
        '<span class="w">·</span><span class="h">·</span><span class="sep">│</span>\n'
    )
    row = binary.hex_dump(bytes(range(0x20, 0x30)), base_offset=16)
    assert row.startswith('<span class="o">00000010</span>  <span class="w">20</span> ')
    assert '<span class="p">27</span>  <span class="p">28</span>' in row  # 第 8 個位元組後多一格
    assert '<span class="w"> </span><span class="p">!&quot;#$%&amp;\'()*+,-./</span>' not in row
    assert '<span class="p">!"#$%&amp;\'()*+,-./</span>' in row
    assert binary.hex_dump(b"<\x01").count('<span class="x">01</span>') == 1


def test_binary_page_for_system_binary(tmp_path: Path) -> None:
    f = Path("/usr/bin/ls")
    doc = engine.render(f)
    assert doc.kind_label == "ELF（Linux）"
    assert doc.preferred_size == (820, 640)
    html = doc.html
    assert 'class="ico"' not in html
    assert '<section class="info"><img class="big" src="cid:big-icon" alt="">' in html
    assert '<div class="name">ls</div>' in html
    assert "<dt>偵測格式</dt><dd>ELF（Linux）</dd>" in html
    assert "<dt>架構</dt><dd>x86-64</dd>" in html
    digest = hashlib.sha256(f.read_bytes()).hexdigest()
    assert f'<dt>SHA-256</dt><dd class="mono">{digest}</dd>' in html
    assert '<dt>權限</dt><dd class="mono">rwxr-xr-x（755）</dd>' in html
    assert '<div class="section-title">十六進位內容（前 8 KB）</div>' in html
    assert html.count('<span class="o">') == 512
    assert "big-icon" in doc.attachments


def test_binary_page_small_file_and_large_file(tmp_path: Path) -> None:
    small = tmp_path / "blob.qzq"
    small.write_bytes(bytes(range(256)))
    doc = engine.render(small)
    assert doc.kind_label == "二進位檔"
    assert '<span class="chip ">QZQ 檔案</span>' in doc.html
    assert '<div class="section-title">十六進位內容</div>' in doc.html
    assert "十六進位內容（前" not in doc.html
    assert "<dt>大小</dt><dd>256 位元組（256 位元組）</dd>" in doc.html
    big = tmp_path / "big.bin"
    with open(big, "wb") as fh:
        fh.write(b"\x00\x01")
        fh.truncate(64 * 1024 * 1024 + 1)
    doc = engine.render(big)
    assert "SHA-256" not in doc.html


def test_random_sample(samples: Path) -> None:
    doc = engine.render(samples / "random.dat")
    assert doc.kind_label == "二進位檔"
    assert '<span class="chip ">DAT 檔案</span>' in doc.html


def test_hostile_file_name_is_escaped(tmp_path: Path) -> None:
    f = tmp_path / '<img src=x onerror="alert(1)">.bin'
    f.write_bytes(b"\x00\x01\x02")
    html = engine.render(f).html
    assert "<img src=x" not in html
    assert "&lt;img src=x onerror=&quot;alert(1)&quot;&gt;.bin" in html


# ------------------------------------------------------------------ 屬性列表


def test_binary_plist_sample(samples: Path) -> None:
    doc = engine.render(samples / "settings.plist")
    assert doc.kind_label == "屬性列表 (plist)"
    assert '<span class="chip ">二進位 plist，已轉為 XML 顯示</span>' in doc.html
    assert '<span class="g">key</span>' in doc.html
    assert '<span class="chip ">UTF-8</span><span class="chip ">LF</span>' in doc.html


def test_plist_expansion_is_bounded(tmp_path: Path) -> None:
    # 同一個大字串被引用很多次：XML 會展開成每次都寫一遍
    data = {"items": ["x" * 100_000] * 2000}
    f = tmp_path / "bomb.plist"
    f.write_bytes(plistlib.dumps(data, fmt=plistlib.FMT_BINARY))
    assert f.stat().st_size < 1_000_000
    assert plist.to_xml(FileInfo.of(f)) is None
    ok = tmp_path / "ok.plist"
    ok.write_bytes(plistlib.dumps({"a": [1, 2]}, fmt=plistlib.FMT_BINARY))
    xml = plist.to_xml(FileInfo.of(ok))
    assert xml is not None and "<key>a</key>" in xml


def test_corrupt_plist_falls_back(tmp_path: Path) -> None:
    f = tmp_path / "bad.plist"
    f.write_bytes(b"bplist00" + b"\xff" * 64)
    assert plist.to_xml(FileInfo.of(f)) is None
    assert engine.render(f).kind_label != "屬性列表 (plist)"


def test_universal_binary_is_not_described_as_java(samples: Path) -> None:
    html = engine.render(samples / "hello-binary").html
    assert '<span class="chip ">Mach-O 二進位檔</span>' in html
    assert '<div class="sub">Mach-O 二進位檔</div>' in html
    assert "Java" not in html
