"""資源限制：外部程式逾時、繪製像素上限、圖片像素上限、開啟的文件一定會關閉。"""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pikepdf
import pytest
from PIL import Image

from conftest import make_pdf
from quickseemd.core import backends, errors, process
from quickseemd.core.model import ToolContext
from quickseemd.core.tools.pdf import common, compress, fromimages, ocr, render, security
from test_pdf_helpers import encrypt, run_tool


class Tracked:
    """包住 pikepdf.Pdf，記錄有沒有被關閉。"""

    instances: list[Tracked] = []

    def __init__(self, pdf: pikepdf.Pdf) -> None:
        self._pdf = pdf
        self.closed = False
        Tracked.instances.append(self)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._pdf, name)

    def __enter__(self) -> Tracked:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self.closed = True
        self._pdf.close()


@pytest.fixture
def tracked():
    Tracked.instances = []
    yield Tracked
    Tracked.instances = []


@pytest.fixture
def calls(monkeypatch):
    """記錄每一次外部程式呼叫的參數。"""
    seen: list[tuple[str, dict]] = []
    real = process.run_checked

    def spy(args, tool_name, **kwargs):
        seen.append((tool_name, kwargs))
        return real(args, tool_name, **kwargs)

    monkeypatch.setattr(process, "run_checked", spy)
    return seen


# ------------------------------------------------------------------ 逾時


def test_every_external_call_has_a_timeout(tmp_path, calls, monkeypatch):
    src = make_pdf(tmp_path / "t.pdf", pages=2)
    run_tool("pdf.to-images", [src], {"format": "png", "dpi": 72})
    run_tool("pdf.to-text", [src])
    monkeypatch.setattr(compress, "_compress_with_pikepdf", lambda *a, **k: 1 / 0)
    run_tool("pdf.compress", [src])
    names = {name for name, _kw in calls}
    assert {"pdftocairo", "pdftotext", "Ghostscript"} <= names
    for name, kw in calls:
        assert kw.get("timeout", 0) > 0, name
    timeouts = {name: kw["timeout"] for name, kw in calls}
    assert timeouts["pdftocairo"] == common.TIMEOUT_RENDER_PAGE
    assert timeouts["pdftotext"] == common.TIMEOUT_TEXT_BASE + 2 * common.TIMEOUT_TEXT_PAGE
    assert timeouts["Ghostscript"] == common.TIMEOUT_GS_BASE + 2 * common.TIMEOUT_GS_PAGE


@pytest.mark.slow
def test_codec_and_ocr_calls_have_timeouts(tmp_path, calls, png_file):
    from test_pdf_ocr import make_scan_pdf

    scan = make_scan_pdf(tmp_path / "s.pdf")
    run_tool("pdf.ocr", [scan], {"language": "english"})
    run_tool("pdf.to-images", [scan], {"format": "heic", "dpi": 72})
    heic = tmp_path / "x.heic"
    Image.open(png_file).convert("RGB").save(tmp_path / "rgb.png")
    process.run_checked(
        [backends.require("heif-enc"), "-o", str(heic), str(tmp_path / "rgb.png")],
        "heif-enc",
        timeout=60,
    )
    run_tool("pdf.from-images", [heic])
    timeouts = {name: kw.get("timeout") for name, kw in calls}
    assert timeouts["Tesseract"] == common.TIMEOUT_OCR_PAGE
    assert timeouts["heif-convert"] == common.TIMEOUT_IMAGE_CODEC
    assert all(kw.get("timeout", 0) > 0 for _n, kw in calls)


def test_hung_renderer_is_stopped(tmp_path, monkeypatch, pdf_file):
    slow = tmp_path / "slow-renderer"
    slow.write_text("#!/bin/sh\nexec /bin/sleep 20\n")
    slow.chmod(0o755)
    real_require = backends.require
    monkeypatch.setattr(
        backends, "require", lambda k: str(slow) if k == "pdftocairo" else real_require(k)
    )
    monkeypatch.setattr(render, "TIMEOUT_RENDER_PAGE", 0.5)
    started = time.monotonic()
    with pytest.raises(errors.QSError, match="執行逾時"):
        run_tool("pdf.to-images", [pdf_file], {"pages": "1"})
    assert time.monotonic() - started < 5


# ------------------------------------------------------------------ 繪製像素上限


def test_render_cap_applies_to_absurd_pages(tmp_path):
    src = tmp_path / "huge.pdf"
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(100, 100))
    pdf.pages[0].obj.MediaBox = [0, 0, 1_000_000, 100]
    pdf.pages[0].obj.UserUnit = 75000
    pdf.save(src)
    with pikepdf.open(src) as doc:
        points = render.page_points(doc.pages[0])
    ctx = ToolContext()
    out = render.render_page(ctx, src, 1, 300, tmp_path / "r", points=points)
    with Image.open(out) as im:
        assert im.size[0] == 12000 and im.size[1] == 1
    # 空白頁偵測（40 dpi）與 OCR 繪製同樣經過這個上限
    assert ocr.ocr_dpi(points, True) <= 72


def test_rendered_size_is_verified(tmp_path, monkeypatch, pdf_file):
    monkeypatch.setattr(render, "MAX_SIDE_PX", 100.0)
    monkeypatch.setattr(render, "effective_dpi", lambda pts, dpi: dpi)  # 假裝上限沒套用
    with pikepdf.open(pdf_file) as doc:
        points = render.page_points(doc.pages[0])
    with pytest.raises(errors.QSError, match="第 1 頁繪製失敗。"):
        render.render_page(ToolContext(), pdf_file, 1, 72, tmp_path / "r", points=points)


# ------------------------------------------------------------------ 圖片像素上限


def test_image_pixel_budget(tmp_path, monkeypatch, png_file, jpg_file):
    monkeypatch.setattr(common, "DECODE_PIXEL_BUDGET", 1000)
    with pytest.raises(errors.QSError, match="無法讀取檔案內容：無法解碼「透明.png」。"):
        run_tool("pdf.from-images", [png_file])
    # JPEG 不解碼、原樣放入，不受影響
    assert run_tool("pdf.from-images", [jpg_file]).outputs[0].name == "相片.pdf"
    with pytest.raises(errors.QSError, match="無法解碼"):
        run_tool("pdf.to-images", [make_pdf(tmp_path / "p.pdf", pages=1)], {"dpi": 72})


def test_tiff_frame_cap(tmp_path, monkeypatch):
    frames = [Image.new("RGB", (30, 30), c) for c in ("red", "green", "blue")]
    tif = tmp_path / "多.tiff"
    frames[0].save(tif, save_all=True, append_images=frames[1:])
    monkeypatch.setattr(fromimages, "MAX_FRAMES", 2)
    out = run_tool("pdf.from-images", [tif]).outputs[0]
    with pikepdf.open(out) as pdf:
        assert len(pdf.pages) == 2


def test_open_image_errors(tmp_path):
    bad = tmp_path / "x.png"
    bad.write_bytes(b"nope")
    with pytest.raises(errors.QSError, match="「x.png」不是可辨識的圖片。"):
        common.open_image(bad)


# ------------------------------------------------------------------ 開啟的文件一定會關閉


def _fake_ocr(monkeypatch, tmp_path: Path, fail_on: int | None):
    layer = make_pdf(tmp_path / "layer.pdf", pages=1)
    monkeypatch.setattr(ocr, "render_for_ocr", lambda *a, **k: (tmp_path / "none.png", 300.0))
    line = ocr.OCRLine("字", 0, 0, 10, 10)
    monkeypatch.setattr(ocr, "recognize", lambda *a, **k: ([line], layer))
    monkeypatch.setattr(ocr, "_open_layer", lambda p: Tracked(pikepdf.open(p)))
    real_place = ocr.place_form
    count = {"n": 0}

    def place(doc, page, layer_page, *a, **k):
        count["n"] += 1
        if fail_on is not None and count["n"] == fail_on:
            raise RuntimeError("疊加失敗")
        return real_place(doc, page, layer_page, *a, **k)

    monkeypatch.setattr(ocr, "place_form", place)


def test_ocr_layers_closed_on_error_midway(tmp_path, monkeypatch, tracked):
    src = make_pdf(tmp_path / "s.pdf", pages=3, text=False)
    _fake_ocr(monkeypatch, tmp_path, fail_on=2)
    with pytest.raises(RuntimeError):
        run_tool("pdf.ocr", [src])
    assert len(tracked.instances) == 2
    assert all(t.closed for t in tracked.instances)


def test_ocr_layers_closed_on_success(tmp_path, monkeypatch, tracked):
    src = make_pdf(tmp_path / "s.pdf", pages=3, text=False)
    _fake_ocr(monkeypatch, tmp_path, fail_on=None)
    run = run_tool("pdf.ocr", [src])
    assert run.summary == "已辨識 3 頁、3 行文字"
    assert len(tracked.instances) == 3 and all(t.closed for t in tracked.instances)


@pytest.fixture
def tracked_open(monkeypatch, tracked):
    real = security._open
    monkeypatch.setattr(security, "_open", lambda f, pw="": Tracked(real(f, pw)))
    return tracked


@pytest.mark.parametrize(
    "case,password,error",
    [
        ("plain", "x", "沒有加密，不需要解鎖"),
        ("limited", "wrong", "設有權限限制"),
        ("limited", "o1", None),
        ("locked", "nope", "密碼不正確"),
        ("locked", "u1", None),
    ],
)
def test_unlock_closes_every_opened_document(
    tmp_path, pdf_file, tracked_open, case, password, error
):
    files = {
        "plain": pdf_file,
        "limited": encrypt(pdf_file, tmp_path / "限.pdf", owner="o1", extract=False),
        "locked": encrypt(pdf_file, tmp_path / "鎖.pdf", user="u1", owner="o1"),
    }
    if error:
        with pytest.raises(errors.QSError, match=error):
            run_tool("pdf.unlock", [files[case]], {"password": password})
    else:
        run_tool("pdf.unlock", [files[case]], {"password": password})
    assert all(t.closed for t in tracked_open.instances)
    if case != "locked" or error is None:
        assert tracked_open.instances  # 確實有開啟過文件
