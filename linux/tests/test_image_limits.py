"""像素上限：超大的圖（檔案很小的「解壓縮炸彈」）要在配置記憶體之前就以中文錯誤拒絕。"""

from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
import time
import zlib
from pathlib import Path

import pytest
from conftest import LINUX_DIR, make_image
from PIL import Image

from quickseemd.core import backends, errors, process
from quickseemd.core.tools.image import formats, heif, icon_io, limits, loader

# 和正式執行一樣把 Pillow 的炸彈警告當成錯誤（pytest 每個測試會重設警告過濾）
pytestmark = pytest.mark.filterwarnings("error::PIL.Image.DecompressionBombWarning")

BOMB_MESSAGE = "圖片太大（400 百萬像素），超過可以處理的上限。"
MEMORY_CAP = 1024 * 1024 * 1024  # 子程序的位址空間上限 1 GB


def _chunk(kind: bytes, data: bytes) -> bytes:
    crc = zlib.crc32(kind + data) & 0xFFFFFFFF
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", crc)


def huge_png(path: Path, width: int = 20000, height: int = 20000) -> Path:
    """寬高很大、檔案卻很小的 1 位元灰階 PNG（逐列壓縮，不佔記憶體）。"""
    comp = zlib.compressobj(9)
    row = b"\x00" * (1 + (width + 7) // 8)
    data = b"".join(comp.compress(row) for _ in range(height)) + comp.flush()
    ihdr = struct.pack(">IIBBBBB", width, height, 1, 0, 0, 0, 0)
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n" + _chunk(b"IHDR", ihdr) + _chunk(b"IDAT", data) + _chunk(b"IEND", b"")
    )
    return path


@pytest.fixture(scope="module")
def bomb(tmp_path_factory) -> Path:
    p = huge_png(tmp_path_factory.mktemp("bomb") / "炸彈.png")
    assert p.stat().st_size < 2_000_000
    return p


def _capped_python(code: str, *args: str) -> dict:
    """在 1 GB 位址空間上限的子程序裡執行，回傳它印出的 JSON。"""
    prelude = (
        "import json, resource, sys\n"
        f"resource.setrlimit(resource.RLIMIT_AS, ({MEMORY_CAP}, {MEMORY_CAP}))\n"
        f"sys.path.insert(0, {str(LINUX_DIR)!r})\n"
    )
    env = {**os.environ, "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"}
    started = time.monotonic()
    res = subprocess.run(
        [sys.executable, "-c", prelude + code, *args],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    )
    assert res.returncode == 0, res.stderr[-2000:]
    out = json.loads(res.stdout.strip().splitlines()[-1])
    out["seconds"] = time.monotonic() - started
    return out


# ------------------------------------------------------------------ 上限本身


def test_budget_and_message():
    assert limits.MAX_PIXELS == 16384 * 16384
    limits.check_pixels(16384, 16384)
    with pytest.raises(errors.QSError) as exc:
        limits.check_pixels(16385, 16384)
    assert str(exc.value) == "圖片太大（268 百萬像素），超過可以處理的上限。"
    assert str(limits.too_large(400_000_000)) == BOMB_MESSAGE
    assert str(limits.too_large(10)) == "圖片太大（1 百萬像素），超過可以處理的上限。"
    assert Image.MAX_IMAGE_PIXELS == limits.MAX_PIXELS


def test_open_checked(bomb, jpg_file):
    started = time.monotonic()
    with pytest.raises(errors.QSError) as exc:
        loader.open_checked(bomb)
    assert str(exc.value) == BOMB_MESSAGE
    assert time.monotonic() - started < 2
    with loader.open_checked(jpg_file) as im:
        assert im.size == (640, 480)


def test_load_rejects_bomb_in_process(bomb):
    started = time.monotonic()
    with pytest.raises(errors.QSError, match=r"圖片太大（400 百萬像素）"):
        loader.load(bomb)
    assert time.monotonic() - started < 2


def test_inspect_still_reads_size(bomb):
    assert loader.pixel_size(bomb) == (20000, 20000)
    assert loader.quick_has_alpha(bomb) is False


def test_resample_output_budget():
    small = Image.new("RGB", (10, 10))
    with pytest.raises(errors.QSError, match="超過可以處理的上限"):
        icon_io.resample(small, 20000, 20000)
    with pytest.raises(errors.QSError, match="上限 32768 px"):
        icon_io.resample(small, 40000, 10)


def test_animated_total_budget(tmp_path, monkeypatch):
    p = tmp_path / "動畫.gif"
    frames = [Image.new("RGB", (100, 100), c) for c in ("red", "lime", "blue")]
    frames[0].save(p, save_all=True, append_images=frames[1:], duration=100)
    monkeypatch.setattr(limits, "MAX_PIXELS", 25_000)
    assert len(loader.load(p).frames) == 1  # 只讀第一格：1 萬像素
    with pytest.raises(errors.QSError, match="超過可以處理的上限"):
        loader.load(p, all_frames=True)  # 3 格合計 3 萬像素


def test_heif_dimensions_checked_before_decoding(tmp_path):
    if not backends.available("heif-enc"):
        pytest.skip("沒有 heif-enc")
    png = make_image(tmp_path / "a.png", size=(64, 48))
    heic = tmp_path / "a.heic"
    process.run_checked([backends.require("heif-enc"), "-q", "50", "-o", str(heic), str(png)], "x")
    w, h = loader.heif_dimensions(heic)
    assert w >= 64 and h >= 48  # ispe 是編碼尺寸（可能補到 64×64），當上限檢查剛好
    data = bytearray(heic.read_bytes())
    i = data.index(b"ispe")
    data[i + 8 : i + 16] = struct.pack(">II", 40000, 40000)
    bad = tmp_path / "炸彈.heic"
    bad.write_bytes(bytes(data))
    with pytest.raises(errors.QSError, match=r"圖片太大（1600 百萬像素）"):
        loader.load(bad)


def test_rotate_checks_before_jpegtran(tmp_path, outdir):
    src = make_image(tmp_path / "a.jpg", size=(64, 48), quality=80)
    data = bytearray(src.read_bytes())
    sof = data.index(b"\xff\xc0")
    data[sof + 5 : sof + 9] = struct.pack(">HH", 30000, 30000)  # 高、寬
    bad = tmp_path / "大.jpg"
    bad.write_bytes(bytes(data))
    from test_image_kit import run_tool

    with pytest.raises(errors.QSError, match=r"圖片太大（900 百萬像素）"):
        run_tool("image.rotate", [bad], outdir)
    assert not any(outdir.iterdir())


# ------------------------------------------------------------------ 1 GB 記憶體上限下


def test_bomb_fails_fast_under_memory_cap(bomb):
    out = _capped_python(
        "from quickseemd.core import errors\n"
        "from quickseemd.core.tools.image import loader\n"
        "try:\n"
        "    loader.load(sys.argv[1])\n"
        "    print(json.dumps({'error': None}))\n"
        "except errors.QSError as exc:\n"
        "    print(json.dumps({'error': str(exc)}, ensure_ascii=False))\n",
        str(bomb),
    )
    assert out["error"] == BOMB_MESSAGE
    assert out["seconds"] < 20


@pytest.mark.parametrize(
    "tool_id,message",
    [
        ("image.convert", BOMB_MESSAGE),
        ("image.compress", BOMB_MESSAGE),
        ("image.rotate", BOMB_MESSAGE),
        ("image.blur-faces", BOMB_MESSAGE),
        ("image.upscale", "放大後超過 16384 px，請改用較小的倍數"),  # 驗證只讀檔頭就擋下
    ],
)
def test_tools_report_bomb_under_memory_cap(bomb, tool_id, message):
    out = _capped_python(
        "from quickseemd.core.runner import JobSpec, run_spec\n"
        "res = run_spec(JobSpec('tool', sys.argv[1], [sys.argv[2]]))\n"
        "print(json.dumps({'error': res.error, 'outputs': [str(p) for p in res.outputs]},"
        " ensure_ascii=False))\n",
        tool_id,
        str(bomb),
    )
    assert out["error"] == message and out["outputs"] == []


def test_huge_svg_refused_under_memory_cap(tmp_path):
    svg = tmp_path / "大.svg"
    svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="100000" height="100000">'
        '<rect width="100000" height="100000" fill="red"/></svg>'
    )
    out = _capped_python(
        "from quickseemd.core import errors\n"
        "from quickseemd.core.tools.image import loader\n"
        "try:\n"
        "    loader.load(sys.argv[1])\n"
        "    print(json.dumps({'error': None}))\n"
        "except errors.QSError as exc:\n"
        "    print(json.dumps({'error': str(exc)}, ensure_ascii=False))\n",
        str(svg),
    )
    assert out["error"] == "圖片太大（10000 百萬像素），超過可以處理的上限。"


def test_writable_check_unaffected():
    assert formats.is_writable("png")


# ------------------------------------------------------------------ 不確定就拒絕（HEIF、SVG、動畫）


def _small_heic(tmp_path: Path) -> Path:
    if not backends.available("heif-enc"):
        pytest.skip("沒有 heif-enc")
    png = make_image(tmp_path / "a.png", size=(64, 48))
    heic = tmp_path / "a.heic"
    process.run_checked([backends.require("heif-enc"), "-q", "50", "-o", str(heic), str(png)], "x")
    return heic


def test_heif_without_ispe_is_refused(tmp_path):
    heic = _small_heic(tmp_path)
    data = heic.read_bytes().replace(b"ispe", b"xxxx")
    # 在 meta 以外的地方放一個看起來像 ispe 的假資料，不能被當成尺寸
    fake = b"ispe" + bytes(4) + struct.pack(">II", 8, 8)
    data += struct.pack(">I", 8 + len(fake)) + b"free" + fake
    bad = tmp_path / "沒有尺寸.heic"
    bad.write_bytes(data)
    assert heif.dimensions(bad) is None
    with pytest.raises(errors.QSError, match="無法判斷「沒有尺寸.heic」的圖片尺寸。"):
        loader.load(bad)


def test_heif_box_walker_is_bounded(tmp_path):
    heic = _small_heic(tmp_path)
    w, h = heif.dimensions(heic)
    assert w >= 64 and h >= 48
    raw = heic.read_bytes()
    truncated = tmp_path / "截斷.heic"
    truncated.write_bytes(raw[:40])
    assert heif.dimensions(truncated) is None
    lying = bytearray(raw)
    lying[0:4] = struct.pack(">I", 0xFFFFFFF0)  # ftyp 宣稱比檔案還大
    liar = tmp_path / "說謊.heic"
    liar.write_bytes(bytes(lying))
    assert heif.dimensions(liar) is None
    endless = tmp_path / "很多盒子.heic"
    endless.write_bytes(raw[:32] + (struct.pack(">I", 8) + b"free") * 10000)
    assert heif.dimensions(endless) is None


def test_svg_with_zero_or_bad_size_is_refused(tmp_path):
    zero = tmp_path / "零.svg"
    zero.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="0" height="0">'
        '<rect width="10" height="10"/></svg>'
    )
    with pytest.raises(errors.QSError, match="零.svg"):
        loader.load(zero)


def test_svg_rendering_is_bounded_by_checked_size(tmp_path, monkeypatch):
    import types

    svg = tmp_path / "a.svg"
    svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="30">'
        '<rect width="40" height="30" fill="red"/></svg>'
    )
    gdk, glib = loader._gdk_pixbuf()  # noqa: SLF001 — 測試替換尺寸查詢

    class Pixbuf:
        get_file_info = staticmethod(lambda path: (gdk.Pixbuf.get_file_info(path)[0], 100, 50))
        new_from_file_at_scale = staticmethod(gdk.Pixbuf.new_from_file_at_scale)

    monkeypatch.setattr(loader, "_gdk_pixbuf", lambda: (types.SimpleNamespace(Pixbuf=Pixbuf), glib))
    assert loader.load(svg).size == (100, 50)  # 點陣化的大小就是檢查過的大小

    class Negative(Pixbuf):
        get_file_info = staticmethod(lambda path: (gdk.Pixbuf.get_file_info(path)[0], -1, 30))

    monkeypatch.setattr(
        loader, "_gdk_pixbuf", lambda: (types.SimpleNamespace(Pixbuf=Negative), glib)
    )
    with pytest.raises(errors.QSError, match="無法判斷「a.svg」的圖片尺寸。"):
        loader.load(svg)


def literal_lzw(pixels: int) -> bytes:
    """GIF 的 LZW 資料：全部是索引 0，每兩個點送一次清除碼，碼長固定 3 位元。"""
    codes: list[int] = []
    for i in range(pixels):
        if i % 2 == 0:
            codes.append(4)  # 清除碼
        codes.append(0)
    codes.append(5)  # 結束碼
    acc = nbits = 0
    packed = bytearray()
    for code in codes:
        acc |= code << nbits
        nbits += 3
        while nbits >= 8:
            packed.append(acc & 0xFF)
            acc >>= 8
            nbits -= 8
    if nbits:
        packed.append(acc & 0xFF)
    blocks = b"".join(
        bytes([len(packed[i : i + 255])]) + bytes(packed[i : i + 255])
        for i in range(0, len(packed), 255)
    )
    return b"\x02" + blocks + b"\x00"


ONE_PIXEL = literal_lzw(1)


def raw_gif(frames: list[tuple[int, int]], screen: tuple[int, int] = (10, 10)) -> bytes:
    """手工組的 GIF：每格從左上角開始；大格只放 1 個點的資料（解碼前就該被擋下）。"""
    out = bytearray(b"GIF89a" + struct.pack("<HH", *screen) + bytes([0x80, 0, 0]))
    out += b"\x00\x00\x00\xff\xff\xff"
    out += b"\x21\xff\x0bNETSCAPE2.0\x03\x01\x00\x00\x00"
    for w, h in frames:
        out += b"\x21\xf9\x04\x00\x0a\x00\x00\x00"
        out += b"\x2c" + struct.pack("<HHHH", 0, 0, w, h) + b"\x00"
        out += literal_lzw(w * h) if w * h <= 250_000 else ONE_PIXEL
    return bytes(out + b"\x3b")


def test_gif_running_total_counts_later_frames(tmp_path, monkeypatch):
    p = tmp_path / "越來越大.gif"
    p.write_bytes(raw_gif([(10, 10), (200, 200), (200, 200)]))
    assert len(loader.load(p, all_frames=True).frames) == 3
    monkeypatch.setattr(limits, "MAX_PIXELS", 50_000)  # 單格 4 萬像素可以，合計就超過
    with pytest.raises(errors.QSError, match="超過可以處理的上限"):
        loader.load(p, all_frames=True)
    assert loader.load(p).size == (10, 10)  # 只讀第一格不受影響


def test_gif_frame_count_cap(tmp_path, monkeypatch):
    p = tmp_path / "很多格.gif"
    p.write_bytes(raw_gif([(1, 1)] * 30))
    monkeypatch.setattr(limits, "MAX_FRAMES", 20)
    with pytest.raises(errors.QSError, match="動畫的畫格太多（超過 20 格），超過可以處理的上限。"):
        loader.load(p, all_frames=True)
    monkeypatch.setattr(limits, "MAX_FRAMES", 30)
    assert len(loader.load(p, all_frames=True).frames) == 30


_LOAD_ALL_FRAMES = (
    "from quickseemd.core import errors\n"
    "from quickseemd.core.tools.image import loader\n"
    "try:\n"
    "    loader.load(sys.argv[1], all_frames=True)\n"
    "    print(json.dumps({'error': None}))\n"
    "except errors.QSError as exc:\n"
    "    print(json.dumps({'error': str(exc)}, ensure_ascii=False))\n"
)


def test_gif_bigger_later_frame_fails_fast_under_memory_cap(tmp_path):
    p = tmp_path / "第二格很大.gif"
    p.write_bytes(raw_gif([(10, 10), (20000, 20000)]))
    assert p.stat().st_size < 1000
    out = _capped_python(_LOAD_ALL_FRAMES, str(p))
    assert out["error"] == BOMB_MESSAGE
    assert out["seconds"] < 20


def test_gif_huge_frame_count_fails_fast_under_memory_cap(tmp_path):
    p = tmp_path / "十萬格.gif"
    p.write_bytes(raw_gif([(1, 1)] * 100_000))
    out = _capped_python(_LOAD_ALL_FRAMES, str(p))
    assert out["error"] == "動畫的畫格太多（超過 2000 格），超過可以處理的上限。"
    assert out["seconds"] < 20
