"""超大圖片不能吃光記憶體：先讀尺寸；超過上限時只走原生縮小（JPEG、SVG），否則顯示資訊卡。

載入在記憶體受限的子程序裡跑（資料區上限 1 GB），全尺寸解碼一定會失敗，證明沒有配置全尺寸。
"""

from __future__ import annotations

import json
import os
import struct
import subprocess
import sys
import zlib
from pathlib import Path

import pytest

import conftest
from quickseemd.core import errors
from quickseemd.previewer import loaders

PROBE = r"""
import json, resource, sys
cap = int(sys.argv[2])
resource.setrlimit(resource.RLIMIT_DATA, (cap, cap))
from pathlib import Path
from quickseemd.previewer import content
from quickseemd.previewer.uri import resolve
c = content.load(resolve(Path(sys.argv[1]).as_uri()))
out = {"view": c.view, "natural": c.natural_size, "subtitle": c.subtitle,
       "html": c.document.html if c.document else "",
       "maxrss_kb": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss}
print(json.dumps(out, ensure_ascii=False))
"""


def write_bilevel_png(path: Path, width: int, height: int) -> Path:
    """一列一列寫出 1 位元灰階 PNG：檔案很小、產生時也不用大量記憶體。"""

    def chunk(kind: bytes, data: bytes) -> bytes:
        body = kind + data
        return struct.pack(">I", len(data)) + body + struct.pack(">I", zlib.crc32(body))

    row = b"\x00" + b"\x55" * ((width + 7) // 8)  # 每列：濾波類型 0＋黑白相間
    packer = zlib.compressobj(9)
    parts = [packer.compress(row) for _ in range(height)]
    parts.append(packer.flush())
    ihdr = struct.pack(">IIBBBBB", width, height, 1, 0, 0, 0, 0)
    with open(path, "xb") as fh:
        fh.write(b"\x89PNG\r\n\x1a\n")
        fh.write(chunk(b"IHDR", ihdr))
        fh.write(chunk(b"IDAT", b"".join(parts)))
        fh.write(chunk(b"IEND", b""))
    return path


def _probe(path: Path, cap_bytes: int) -> dict:
    env = dict(os.environ, PYTHONPATH=str(conftest.LINUX_DIR))
    env.pop("DISPLAY", None)
    res = subprocess.run(
        [sys.executable, "-c", PROBE, str(path), str(cap_bytes)],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )
    assert res.returncode == 0, res.stderr[-3000:]
    return json.loads(res.stdout.strip().splitlines()[-1])


@pytest.mark.parametrize("edge", [2000, 20000])  # 先小後大
def test_huge_png_shows_info_card_under_1gb(tmp_path: Path, edge: int):
    png = write_bilevel_png(tmp_path / f"巨大-{edge}.png", edge, edge)
    out = _probe(png, 1 << 30)
    if edge * edge <= loaders.MAX_PIXELS:
        assert out["view"] == "image"
        return
    assert png.stat().st_size < 5_000_000
    assert out["view"] == "page"
    assert "圖片太大" in out["html"]
    assert f"{edge} × {edge}" in out["html"]
    assert out["maxrss_kb"] < 1024 * 1024


@pytest.mark.slow
def test_huge_jpeg_is_scaled_while_decoding(tmp_path: Path):
    from PIL import Image  # noqa: PLC0415

    jpg = tmp_path / "很大的相片.jpg"
    img = Image.new("L", (12000, 12000), 200)  # 灰階：產生時 144 MB
    img.paste(30, (0, 0, 6000, 6000))
    img.save(jpg, quality=80)
    del img
    # 全尺寸解成 RGB 要 432 MB；上限 400 MB 只有邊解碼邊縮小才放得下
    out = _probe(jpg, 400 << 20)
    assert out["view"] == "image"
    assert max(out["natural"]) <= loaders.SCALED_EDGE
    assert "12000 × 12000" in out["subtitle"]


def test_huge_svg_is_rendered_small(tmp_path: Path):
    svg = tmp_path / "大圖.svg"
    svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="20000" height="20000">'
        '<rect width="20000" height="20000" fill="#3366cc"/></svg>',
        "utf-8",
    )
    out = _probe(svg, 1 << 30)
    assert out["view"] == "image"
    assert max(out["natural"]) <= loaders.SCALED_EDGE


class _FakeFormat:
    def __init__(self, name: str) -> None:
        self._name = name

    def get_name(self) -> str:
        return self._name


def test_huge_gif_is_refused_without_decoding(tmp_path: Path, monkeypatch):
    gif = tmp_path / "動畫.gif"
    gif.write_bytes(b"GIF89a")

    def fake_info(_path):
        return _FakeFormat("gif"), 20000, 20000

    def must_not_decode(*_a, **_kw):
        raise AssertionError("不應該解碼")

    monkeypatch.setattr(loaders.GdkPixbuf.Pixbuf, "get_file_info", fake_info)
    monkeypatch.setattr(loaders.GdkPixbuf.PixbufAnimation, "new_from_file", must_not_decode)
    with pytest.raises(errors.QSError, match="圖片太大"):
        loaders.load_image(gif)


def test_unknown_format_is_unreadable(tmp_path: Path):
    junk = tmp_path / "不是圖.png"
    junk.write_bytes(b"not an image at all")
    with pytest.raises(errors.QSError):
        loaders.load_image(junk)
