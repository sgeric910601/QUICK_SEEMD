"""壓縮 PDF：只重新壓縮與縮小圖片，文字與向量保持原樣。"""

from __future__ import annotations

import io
import time
import zlib
from dataclasses import dataclass
from pathlib import Path

import cairo
import pikepdf
import pytest
from pikepdf import Name
from PIL import Image

from conftest import make_pdf
from quickseemd.core import errors
from quickseemd.core.tools.pdf import compress
from test_pdf_helpers import digest, pdftotext, run_tool, tool


def _noisy_jpeg(size=(2400, 1800), quality=95) -> bytes:
    import numpy as np

    rng = np.random.default_rng(1)
    w, h = size
    x = np.linspace(0, 255, w, dtype=np.float32)[None, :]
    y = np.linspace(0, 255, h, dtype=np.float32)[:, None]
    base = np.stack([x + 0 * y, y + 0 * x, (x + y) / 2], axis=2)
    noise = rng.normal(0, 25, (h, w, 3))
    arr = np.clip(base + noise, 0, 255).astype("uint8")
    buf = io.BytesIO()
    Image.fromarray(arr).save(buf, "JPEG", quality=quality)
    return buf.getvalue()


def _photo_pdf(path: Path, jpeg: bytes, shown=(300, 225), extra=None) -> Path:
    """第 1 頁有中文文字，再加一張以 shown 大小（pt）顯示的大照片。"""
    make_pdf(path, pages=1)
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        im = Image.open(io.BytesIO(jpeg))
        img = pikepdf.Stream(pdf, jpeg)
        img.Type, img.Subtype = Name.XObject, Name.Image
        img.Width, img.Height = im.width, im.height
        img.ColorSpace, img.BitsPerComponent, img.Filter = Name.DeviceRGB, 8, Name.DCTDecode
        page = pdf.pages[0]
        name = page.add_resource(img, Name.XObject, prefix="Photo")
        cmd = f"q {shown[0]} 0 0 {shown[1]} 100 400 cm {name} Do Q\n".encode()
        page.contents_add(pdf.make_stream(cmd))
        if extra:
            extra(pdf, page)
        pdf.save(path)
    return path


@dataclass(frozen=True)
class Img:
    Width: int
    Height: int
    Filter: object
    ColorSpace: object

    def get(self, key: str):
        return getattr(self, key.lstrip("/"))


def _images(path: Path) -> list[Img]:
    with pikepdf.open(path) as pdf:
        return [
            Img(int(o.Width), int(o.Height), o.get("/Filter"), o.get("/ColorSpace"))
            for o in pdf.objects
            if isinstance(o, pikepdf.Stream) and o.get("/Subtype") == Name.Image
        ]


def test_compress_recommended_shrinks_photo(tmp_path):
    src = _photo_pdf(tmp_path / "大.pdf", _noisy_jpeg())
    before = digest(src)
    t = tool("pdf.compress")
    assert t.presets["pdf.compress-recommended"] == {"level": "recommended"}
    run = run_tool("pdf.compress", [src], t.values_for_preset("pdf.compress-recommended"))
    out = run.outputs[0]
    assert out.name == "大-已壓縮.pdf"
    assert out.stat().st_size < src.stat().st_size * 0.5
    assert "第 1 頁" in pdftotext(out)
    imgs = _images(out)
    assert len(imgs) == 1
    # 300 pt 寬、150 dpi → 625 px
    assert int(imgs[0].Width) == 625 and int(imgs[0].Height) == 469
    assert imgs[0].Filter == Name.DCTDecode
    assert run.summary.startswith("已壓縮：") and "（減少 " in run.summary
    assert digest(src) == before


def test_compress_levels_differ(tmp_path):
    src = _photo_pdf(tmp_path / "p.pdf", _noisy_jpeg())
    extreme = run_tool("pdf.compress", [src], {"level": "extreme"}).outputs[0]
    low = run_tool("pdf.compress", [src], {"level": "low"}).outputs[0]
    assert int(_images(extreme)[0].Width) == 400  # 96 dpi
    assert int(_images(low)[0].Width) == 917  # 220 dpi
    assert extreme.stat().st_size < low.stat().st_size


def test_compress_keeps_original_when_not_smaller(tmp_path):
    tiny = tmp_path / "小.pdf"
    pdf = pikepdf.new()
    pdf.add_blank_page(page_size=(200, 200))
    pdf.save(tiny, object_stream_mode=pikepdf.ObjectStreamMode.generate)
    run = run_tool("pdf.compress", [tiny])
    out = run.outputs[0]
    assert out.name == "小-已壓縮.pdf"
    assert digest(out) == digest(tiny)
    assert run.summary.startswith("「小.pdf」已經很精簡，壓縮後沒有變小，保留原始內容（")


def test_skip_rules(tmp_path):
    def extra(pdf, page):
        small = pikepdf.Stream(pdf, zlib.compress(bytes(16 * 16 * 3)))
        small.Type, small.Subtype, small.Width, small.Height = Name.XObject, Name.Image, 16, 16
        small.ColorSpace, small.BitsPerComponent, small.Filter = Name.DeviceRGB, 8, Name.FlateDecode
        mask = pikepdf.Stream(pdf, zlib.compress(bytes(200 * 200 // 8)))
        mask.Type, mask.Subtype, mask.Width, mask.Height = Name.XObject, Name.Image, 200, 200
        mask.ImageMask, mask.Filter = True, Name.FlateDecode
        dec = pikepdf.Stream(pdf, zlib.compress(bytes(300 * 300)))
        dec.Type, dec.Subtype, dec.Width, dec.Height = Name.XObject, Name.Image, 300, 300
        dec.ColorSpace, dec.BitsPerComponent, dec.Filter = Name.DeviceGray, 8, Name.FlateDecode
        dec.Decode = [1, 0]
        for im in (small, mask, dec):
            n = page.add_resource(im, Name.XObject, prefix="Skip")
            page.contents_add(pdf.make_stream(f"q 50 0 0 50 0 0 cm {n} Do Q\n".encode()))

    src = _photo_pdf(tmp_path / "s.pdf", _noisy_jpeg(), extra=extra)
    out = run_tool("pdf.compress", [src]).outputs[0]
    sizes = sorted((int(i.Width), int(i.Height)) for i in _images(out))
    assert (16, 16) in sizes and (200, 200) in sizes and (300, 300) in sizes


def test_display_size_through_forms_and_cycles(tmp_path, pdf_file):
    with pikepdf.open(pdf_file) as pdf:
        img = pikepdf.Stream(pdf, bytes(100 * 100 * 3))
        img.Type, img.Subtype, img.Width, img.Height = Name.XObject, Name.Image, 100, 100
        img.ColorSpace, img.BitsPerComponent = Name.DeviceRGB, 8
        mask = pikepdf.Stream(pdf, bytes(100 * 100))
        mask.Type, mask.Subtype, mask.Width, mask.Height = Name.XObject, Name.Image, 100, 100
        mask.ColorSpace, mask.BitsPerComponent = Name.DeviceGray, 8
        img.SMask = mask
        form = pikepdf.Stream(pdf, b"q 1 0 0 1 5 5 cm /Im0 Do Q /Fm0 Do")
        form.Type, form.Subtype = Name.XObject, Name.Form
        form.BBox, form.Matrix = [0, 0, 1, 1], [2, 0, 0, 2, 0, 0]
        form.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im0=img))
        form.Resources.XObject.Fm0 = form  # 自己畫自己：不能卡住
        page = pdf.pages[0]
        name = page.add_resource(form, Name.XObject, prefix="Fm")
        page.contents_add(pdf.make_stream(f"q 100 0 0 50 0 0 cm {name} Do Q".encode()))
        started = time.monotonic()
        sizes, masks = compress.scan_display_sizes(pdf)
        assert time.monotonic() - started < 2
        assert sizes[img.objgen] == pytest.approx((200, 100))
        assert sizes[mask.objgen] == pytest.approx((200, 100))
        assert masks == {mask.objgen}


def test_soft_mask_stays_lossless(tmp_path):
    path = tmp_path / "a.pdf"
    surface = cairo.PDFSurface(str(path), 400, 400)
    cr = cairo.Context(surface)
    img = cairo.ImageSurface(cairo.FORMAT_ARGB32, 1600, 1600)
    ic = cairo.Context(img)
    grad = cairo.RadialGradient(800, 800, 10, 800, 800, 800)
    grad.add_color_stop_rgba(0, 1, 0, 0, 1)
    grad.add_color_stop_rgba(1, 0, 0, 1, 0)
    ic.set_source(grad)
    ic.paint()
    import numpy as np

    data = np.ndarray(shape=(1600, 1600, 4), dtype=np.uint8, buffer=img.get_data())
    data[..., :3] = (
        np.random.default_rng(2).integers(0, 255, (1600, 1600, 3), dtype=np.uint8) & data[..., 3:4]
    )
    img.mark_dirty()
    cr.scale(0.25, 0.25)
    cr.set_source_surface(img, 0, 0)
    cr.paint()
    surface.finish()
    out = run_tool("pdf.compress", [path]).outputs[0]
    imgs = _images(out)
    smasks = [
        i
        for i in imgs
        if i.get("/ColorSpace") == Name.DeviceGray and i.get("/Filter") == Name.FlateDecode
    ]
    colors = [i for i in imgs if i.get("/Filter") == Name.DCTDecode]
    assert smasks and colors
    assert int(colors[0].Width) == 833  # 400 pt × 150 dpi / 72 ≈ 833.3


def test_ghostscript_fallback(tmp_path, monkeypatch):
    src = _photo_pdf(tmp_path / "g.pdf", _noisy_jpeg())

    def boom(*a, **k):
        raise RuntimeError("意外")

    monkeypatch.setattr(compress, "_compress_with_pikepdf", boom)
    run = run_tool("pdf.compress", [src])
    out = run.outputs[0]
    assert out.stat().st_size < src.stat().st_size
    assert "第 1 頁" in pdftotext(out)


def test_fallback_failure_message(tmp_path, monkeypatch):
    src = _photo_pdf(tmp_path / "g.pdf", _noisy_jpeg((400, 300)))
    monkeypatch.setattr(compress, "_compress_with_pikepdf", lambda *a, **k: 1 / 0)
    monkeypatch.setattr(compress, "_compress_with_gs", lambda *a, **k: False)
    with pytest.raises(errors.QSError, match="無法壓縮「g.pdf」。"):
        run_tool("pdf.compress", [src])


def test_compress_definition(pdf_file):
    t = tool("pdf.compress")
    assert [c.value for c in t.option("level").choices] == ["extreme", "recommended", "low"]
    assert t.option("level").choices[1].badge == "推薦"
    size_note = t.option("_compress_size").note
    assert size_note({}, {"total_size": 2_100_000}) == ("目前大小：2.1 MB", False)
    assert size_note({}, {}) is None
    assert compress.LEVELS["low"].min_saving == 0.9
