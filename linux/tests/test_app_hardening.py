"""主程式的安全與效能強化：大圖不整張解碼、ffmpeg 參數、PDF 只開一次、時間格式共用核心。"""

from __future__ import annotations

import os
import resource
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from quickseemd.app import options_model, thumbnails
from quickseemd.app.views import media_probe
from quickseemd.core import process
from quickseemd.core.tools.media import timefmt

LINUX_DIR = Path(__file__).resolve().parent.parent
MEMORY_CAP = 1 << 30  # 1 GB


def _huge_png(path: Path, side: int) -> Path:
    """全黑的 1 位元 PNG：檔案很小，但解碼成 RGBA 要 side² × 4 位元組。"""
    from PIL import Image

    Image.new("1", (side, side)).save(path, optimize=True)
    return path


# ---------------------------------------------------------------- 時間格式共用核心


def test_time_helpers_come_from_core():
    assert options_model.format_time is timefmt.format_time
    assert options_model.parse_time is timefmt.parse_time


# ---------------------------------------------------------------- 大圖


def test_image_size_reads_header_only(tmp_path, png_file):
    assert thumbnails.image_size(png_file) == (640, 480)
    assert thumbnails.decodable(png_file)
    bad = tmp_path / "壞.png"
    bad.write_bytes(b"not an image")
    assert thumbnails.image_size(bad) is None and not thumbnails.decodable(bad)


def test_preview_of_normal_image(jpg_file):
    p = thumbnails.load_preview(jpg_file, 300)
    assert p.pixbuf is not None and (p.width, p.height) == (640, 480) and not p.too_big
    assert max(p.pixbuf.get_width(), p.pixbuf.get_height()) == 300


@pytest.mark.slow
@pytest.mark.parametrize("side", [9000, 20000])  # 由小到大：剛超過上限，再到 4 億像素
def test_huge_png_is_never_fully_decoded(tmp_path, side):
    """在 1 GB 的記憶體上限下產生縮圖與裁剪預覽：超過 6400 萬像素就不解碼。"""
    big = _huge_png(tmp_path / f"巨大-{side}.png", side)
    script = textwrap.dedent(
        f"""
        import sys
        sys.path.insert(0, {str(LINUX_DIR)!r})
        from pathlib import Path
        from quickseemd.app import thumbnails
        p = Path({str(big)!r})
        assert thumbnails.image_size(p) == ({side}, {side})
        assert thumbnails._make(p, 128, 146) is None
        pv = thumbnails.load_preview(p, 1800)
        assert pv.pixbuf is None and pv.too_big and (pv.width, pv.height) == ({side}, {side})
        print("ok")
        """
    )

    def cap() -> None:
        resource.setrlimit(resource.RLIMIT_DATA, (MEMORY_CAP, MEMORY_CAP))

    res = subprocess.run(
        [sys.executable, "-c", script],
        capture_output=True,
        text=True,
        timeout=120,
        preexec_fn=cap,  # noqa: PLW1509 — 只設記憶體上限
        env={**os.environ, "DISPLAY": ""},
    )
    assert res.returncode == 0, res.stderr[-2000:]
    assert res.stdout.strip() == "ok"


# ---------------------------------------------------------------- PDF 只開一次


def test_pdf_render_from_one_open_document(tmp_path):
    from conftest import make_pdf

    pdf = make_pdf(tmp_path / "多頁.pdf", pages=4)
    doc = thumbnails.open_pdf(pdf)
    assert doc is not None and doc.get_n_pages() == 4
    pages = [thumbnails.render_page(doc, i, 120) for i in range(4)]
    assert all(p is not None and p.startswith(b"\x89PNG") for p in pages)
    assert thumbnails.render_page(doc, 99, 120) is None
    assert thumbnails.render_pdf_page(pdf, 0, 60).startswith(b"\x89PNG")
    assert thumbnails.open_pdf(tmp_path / "沒有.pdf") is None
    assert thumbnails.render_pdf_page(tmp_path / "沒有.pdf", 0, 60) is None


# ---------------------------------------------------------------- ffmpeg／ffprobe 參數


@pytest.fixture
def captured(monkeypatch):
    calls: list[list[str]] = []

    def fake_run(args, **_kw):  # noqa: ANN001, ANN202
        calls.append([str(a) for a in args])
        return process.Result(1, "{}", "")

    monkeypatch.setattr(process, "run", fake_run)
    return calls


def _check_args(args: list[str], is_ffmpeg: bool) -> None:
    assert os.path.isabs(args[0])
    i = args.index("-i")
    w = args.index("-protocol_whitelist")
    assert w < i and args[w + 1] == "file,pipe"
    assert os.path.isabs(args[i + 1])
    if is_ffmpeg:
        assert args.index("-nostdin") < i
        assert os.path.isabs(args[-1])


def test_ffmpeg_and_ffprobe_args_are_hardened(captured, tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    rel = Path("影片.mp4")
    (tmp_path / rel).write_bytes(b"x")
    thumbnails.video_frame(rel, 1.0, 48)
    media_probe.probe(rel)
    media_probe.peaks(rel)
    assert len(captured) == 3
    for args in captured:
        _check_args(args, is_ffmpeg=Path(args[0]).name == "ffmpeg")
