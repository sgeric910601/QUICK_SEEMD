"""讀取 QR Code（other.read-qr）。"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np
import pytest
from PIL import Image

from quickseemd.core import errors
from quickseemd.core.model import ToolContext
from quickseemd.core.tools.other import TOOLS
from quickseemd.core.tools.other.readqr import clipboard_payload

NOT_FOUND = "檔案沒有可轉換的內容：在圖片中找不到 QR Code 或條碼。"


def _qr(text: str, scale: int = 8, border: int = 4) -> np.ndarray:
    """OpenCV 產生的 QR（灰階 0／255），放大並加白邊。"""
    img = cv2.QRCodeEncoder_create().encode(text)
    img = cv2.resize(img, None, fx=scale, fy=scale, interpolation=cv2.INTER_NEAREST)
    pad = border * scale
    return cv2.copyMakeBorder(img, pad, pad, pad, pad, cv2.BORDER_CONSTANT, value=255)


def _save(arr: np.ndarray, path: Path) -> Path:
    Image.fromarray(arr).save(path)
    return path


def _tool():
    return next(t for t in TOOLS if t.id == "other.read-qr")


def _run(files, out=None):
    t = _tool()
    ctx = ToolContext(out)
    try:
        return t.run([Path(f) for f in files], t.defaults(), ctx), ctx
    finally:
        ctx.cleanup()


def test_definition(tmp_path):
    t = _tool()
    assert t.title == "讀取 QR Code"
    assert t.badge == "QR" and t.action_title == "讀取" and t.input_label == "圖片"
    assert t.options == ()
    assert t.presets == {"other.read-qr": {}}
    png = _save(_qr("x"), tmp_path / "a.png")
    assert t.accepts(png)


def test_single_image(tmp_path):
    img = _save(_qr("https://example.com/測試?a=1"), tmp_path / "截圖.png")
    [out], ctx = _run([img])
    assert out == tmp_path / "截圖-QR內容.txt"
    assert out.read_text("utf-8") == "https://example.com/測試?a=1\n"
    assert ctx.last_message == "找到 1 個，已複製到剪貼簿"


def test_several_codes_deduplicated(tmp_path, outdir):
    a, b = _qr("第一個"), _qr("second-code")
    h = max(a.shape[0], b.shape[0])
    a = cv2.copyMakeBorder(a, 0, h - a.shape[0], 0, 0, cv2.BORDER_CONSTANT, value=255)
    b = cv2.copyMakeBorder(b, 0, h - b.shape[0], 0, 0, cv2.BORDER_CONSTANT, value=255)
    sheet = np.hstack([a, b, a])
    img = _save(sheet, tmp_path / "sheet.png")
    [out], ctx = _run([img], outdir)
    lines = out.read_text("utf-8").splitlines()
    assert sorted(lines) == ["second-code", "第一個"]
    assert ctx.last_message == "找到 2 個，已複製到剪貼簿"


def test_several_files_have_headers(tmp_path, outdir):
    a = _save(_qr("AAA"), tmp_path / "a.png")
    nothing = _save(np.full((200, 200), 255, np.uint8), tmp_path / "blank.png")
    b = _save(_qr("BBB"), tmp_path / "b.jpg")
    [out], ctx = _run([a, nothing, b], outdir)
    assert out == outdir / "a-QR內容.txt"
    assert out.read_text("utf-8") == "# a.png\nAAA\n\n# b.jpg\nBBB\n"
    assert ctx.last_message == "找到 2 個，已複製到剪貼簿"
    assert clipboard_payload(out.read_text("utf-8")) == "AAA\nBBB"


def test_clipboard_payload_single():
    assert clipboard_payload("one\ntwo\n") == "one\ntwo"


def test_transparent_background(tmp_path):
    arr = _qr("透明背景")
    rgba = np.zeros((*arr.shape, 4), np.uint8)
    rgba[..., 3] = np.where(arr == 0, 255, 0)  # 黑色模組不透明，其他全透明
    img = tmp_path / "t.png"
    Image.fromarray(rgba, "RGBA").save(img)
    [out], _ = _run([img])
    assert out.read_text("utf-8") == "透明背景\n"


def test_tiny_code_is_upscaled(tmp_path):
    img = _save(_qr("tiny", scale=1, border=0), tmp_path / "tiny.png")
    [out], _ = _run([img])
    assert out.read_text("utf-8") == "tiny\n"


def test_pdf_pages(tmp_path):
    import cairo

    qr_png = _save(_qr("PDF 裡的 QR", scale=4), tmp_path / "q.png")
    pdf = tmp_path / "文件.pdf"
    surface = cairo.PDFSurface(str(pdf), 300, 300)
    cr = cairo.Context(surface)
    cr.show_page()  # 第 1 頁空白
    img = cairo.ImageSurface.create_from_png(str(qr_png))
    cr.set_source_surface(img, 20, 20)
    cr.paint()
    cr.show_page()
    surface.finish()
    [out], ctx = _run([pdf])
    assert out.name == "文件-QR內容.txt"
    assert out.read_text("utf-8") == "PDF 裡的 QR\n"
    assert ctx.last_message == "找到 1 個，已複製到剪貼簿"


def test_nothing_found(tmp_path):
    img = _save(np.full((300, 300), 255, np.uint8), tmp_path / "white.png")
    with pytest.raises(errors.QSError) as exc:
        _run([img])
    assert str(exc.value) == NOT_FOUND
    assert sorted(p.name for p in tmp_path.iterdir()) == ["white.png"]


def test_unreadable_image(tmp_path):
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not an image")
    with pytest.raises(errors.QSError) as exc:
        _run([bad])
    assert str(exc.value) == "無法讀取檔案內容：bad.png"


def test_decode_payload_bytes():
    from quickseemd.core.tools.other.readqr import decode_payload

    assert decode_payload("中文".encode()) == "中文"
    assert decode_payload("日本語のテキストです。今日は晴れ。".encode("cp932")) == (
        "日本語のテキストです。今日は晴れ。"
    )
    assert decode_payload(b"\x81\x20\x81\x20") == "\x81 \x81 "


def test_heic_input(tmp_path):
    from quickseemd.core import backends, process

    png = _save(cv2.cvtColor(_qr("HEIC 相片"), cv2.COLOR_GRAY2RGB), tmp_path / "src.png")
    heic = tmp_path / "photo.heic"
    process.run_checked(
        [backends.require("heif-enc"), "-q", "90", "-o", str(heic), str(png)], "heif-enc"
    )
    [out], _ = _run([heic])
    assert out.read_text("utf-8") == "HEIC 相片\n"


def test_broken_pdf(tmp_path):
    bad = tmp_path / "bad.pdf"
    bad.write_bytes(b"%PDF-1.4\nnot really a pdf")
    with pytest.raises(errors.QSError) as exc:
        _run([bad])
    assert str(exc.value) == "無法讀取檔案內容：bad.pdf"


def test_zbar_public_api_fallback(tmp_path, monkeypatch):
    from quickseemd.core.tools.other import readqr

    def broken(_img):
        raise AttributeError("內部介面不見了")

    monkeypatch.setattr(readqr, "_zbar_binary", broken)
    img = _save(_qr("fallback"), tmp_path / "f.png")
    [out], _ = _run([img])
    assert out.read_text("utf-8") == "fallback\n"


def test_opencv_fallback_and_failure(tmp_path, monkeypatch):
    from quickseemd.core.tools.other import readqr

    monkeypatch.setattr(readqr, "_zbar_scan", lambda _img: [])
    img = _save(_qr("opencv"), tmp_path / "o.png")
    [out], _ = _run([img])
    assert out.read_text("utf-8") == "opencv\n"

    def boom():
        raise cv2.error("壞了")

    monkeypatch.setattr(cv2, "QRCodeDetector", boom)
    with pytest.raises(errors.QSError) as exc:
        _run([img])
    assert str(exc.value) == NOT_FOUND


@pytest.mark.parametrize("kind", ["tool", "action"])
def test_runner_reports_clipboard_text(tmp_path, kind):
    """同原版：所有內容以換行連接放進剪貼簿，不含「# 檔名」標題；文字檔照樣產生。"""
    from quickseemd.core.runner import JobSpec, run_spec

    a = _save(_qr("第一個 QR"), tmp_path / "a.png")
    b = _save(_qr("BBB"), tmp_path / "b.png")
    result = run_spec(JobSpec(kind, "other.read-qr", [str(a), str(b)]))
    assert result.error is None
    assert result.clipboard == "第一個 QR\nBBB"
    assert result.to_json()["clipboard"] == "第一個 QR\nBBB"
    assert result.summary == "找到 2 個，已複製到剪貼簿"
    [out] = result.outputs
    assert out.read_text("utf-8") == "# a.png\n第一個 QR\n\n# b.png\nBBB\n"


def test_runner_no_clipboard_when_nothing_found(tmp_path):
    from quickseemd.core.runner import JobSpec, run_spec

    img = _save(np.full((200, 200), 255, np.uint8), tmp_path / "white.png")
    result = run_spec(JobSpec("tool", "other.read-qr", [str(img)]))
    assert result.error == NOT_FOUND
    assert result.clipboard is None
    assert "clipboard" not in result.to_json()


# ------------------------------------------------------------------ PDF 頁面大小上限


def _qr_pdf(path: Path, pages: list[tuple[float, float, bool]], tmp_path: Path) -> Path:
    """依序產生頁面（寬, 高, 是否放 QR）；QR 畫在左上角。"""
    import cairo

    qr_png = _save(_qr("大頁面 QR", scale=6), tmp_path / f"{path.stem}-qr.png")
    surface = cairo.PDFSurface(str(path), *pages[0][:2])
    cr = cairo.Context(surface)
    for w, h, with_qr in pages:
        surface.set_size(w, h)
        if with_qr:
            img = cairo.ImageSurface.create_from_png(str(qr_png))
            scale = max(w, h) / 1500  # 讓 QR 在縮小後的畫面裡仍夠大
            cr.save()
            cr.scale(scale, scale)
            cr.set_source_surface(img, 20, 20)
            cr.paint()
            cr.restore()
        cr.show_page()
    surface.finish()
    return path


def test_render_size_is_capped():
    from quickseemd.core.tools.other import readqr

    assert readqr.render_long_side(595, 842) == 1684  # 2 倍
    assert readqr.render_long_side(5000, 3000) == readqr.MAX_RENDER_PX
    assert readqr.page_is_sane((0, 0, 595, 842))
    assert not readqr.page_is_sane((0, 0, 20000, 20000))
    assert not readqr.page_is_sane((0, 0, 0, 842))


def test_huge_mediabox_page_is_skipped(tmp_path, monkeypatch):
    from quickseemd.core.tools.other import readqr

    rendered: list[str] = []
    real_run = readqr.process.run

    def spy(args, **kw):
        if "pdftocairo" in args[0]:
            rendered.append(" ".join(str(a) for a in args))
        return real_run(args, **kw)

    monkeypatch.setattr(readqr.process, "run", spy)
    pdf = _qr_pdf(tmp_path / "big.pdf", [(20000, 20000, True), (600, 800, True)], tmp_path)
    [out], _ = _run([pdf])
    assert out.read_text("utf-8") == "大頁面 QR\n"
    assert len(rendered) == 1 and "-f 2 -l 2" in rendered[0]


def test_large_page_rendered_within_cap(tmp_path, monkeypatch):
    from quickseemd.core.tools.other import readqr

    sizes: list[tuple[int, int]] = []
    real_open = readqr._open_image

    def spy(path):
        img = real_open(path)
        sizes.append(img.size)
        return img

    monkeypatch.setattr(readqr, "_open_image", spy)
    pdf = _qr_pdf(tmp_path / "wide.pdf", [(9000, 6000, True)], tmp_path)
    [out], _ = _run([pdf])
    assert out.read_text("utf-8") == "大頁面 QR\n"
    assert max(sizes[0]) <= readqr.MAX_RENDER_PX


def test_pdf_with_only_absurd_pages_finds_nothing(tmp_path):
    pdf = _qr_pdf(tmp_path / "absurd.pdf", [(30000, 30000, True)], tmp_path)
    with pytest.raises(errors.QSError) as exc:
        _run([pdf])
    assert str(exc.value) == NOT_FOUND
