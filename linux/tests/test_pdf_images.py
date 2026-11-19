"""PDF 轉圖片（每頁一張、擷取內嵌圖片）與圖片轉 PDF。"""

from __future__ import annotations

import io
import subprocess
import zlib
from pathlib import Path

import pikepdf
import pytest
from pikepdf import Name
from PIL import Image

from conftest import make_image, make_pdf
from quickseemd.core import errors
from quickseemd.core.tools.pdf import fromimages
from test_pdf_helpers import digest, page_count, run_tool, tool

# ------------------------------------------------------------------ PDF 轉圖片


def test_to_jpg_preset_folder(tmp_path, pdf_file):
    t = tool("pdf.to-images")
    assert t.presets["pdf.to-jpg"] == {"mode": "pages", "format": "jpg"}
    before = digest(pdf_file)
    run = run_tool("pdf.to-images", [pdf_file], t.values_for_preset("pdf.to-jpg"))
    folder = run.outputs[0]
    assert folder.name == "文件-JPG" and folder.is_dir()
    names = sorted(p.name for p in folder.iterdir())
    assert names == ["文件-01.jpg", "文件-02.jpg", "文件-03.jpg"]
    with Image.open(folder / "文件-01.jpg") as im:
        assert im.size == (1240, 1754)
        assert round(im.info["dpi"][0]) == 150
    assert run.summary == "已轉出 3 張 JPG 圖片"
    assert digest(pdf_file) == before


def test_single_page_png_white(tmp_path, pdf_file):
    run = run_tool("pdf.to-images", [pdf_file], {"format": "png", "pages": "2", "dpi": 72})
    out = run.outputs[0]
    assert out.name == "文件-第2頁.png"
    with Image.open(out) as im:
        assert im.mode == "RGB"
        assert im.getpixel((0, 0)) == (255, 255, 255)
        assert im.size == (595, 842)


def test_tiff_and_rotation(tmp_path):
    src = make_pdf(tmp_path / "r.pdf", pages=2, size=(200, 100))
    with pikepdf.open(src, allow_overwriting_input=True) as pdf:
        pdf.pages[0].obj.Rotate = 90
        pdf.save(src)
    run = run_tool("pdf.to-images", [src], {"format": "tiff", "dpi": 72})
    folder = run.outputs[0]
    assert folder.name == "r-TIFF"
    with Image.open(folder / "r-01.tiff") as im:
        assert im.size == (100, 200)
    assert run.summary == "已轉出 2 張 TIFF 圖片"


def test_long_side_cap(tmp_path):
    src = make_pdf(tmp_path / "w.pdf", pages=1, size=(14400, 200), text=False)
    out = run_tool("pdf.to-images", [src], {"format": "png", "dpi": 300}).outputs[0]
    with Image.open(out) as im:
        assert im.size[0] == 12000


@pytest.mark.slow
def test_heic_output(tmp_path):
    src = make_pdf(tmp_path / "h.pdf", pages=1)
    out = run_tool("pdf.to-images", [src], {"format": "heic", "dpi": 72, "quality": 60}).outputs[0]
    assert out.name == "h-第1頁.heic"
    assert out.read_bytes()[4:12] == b"ftypheic"


def test_to_images_validation_and_options(pdf_file):
    t = tool("pdf.to-images")
    info = t.inspect([pdf_file])
    assert t.validate([pdf_file], {**t.defaults(), "pages": "4"}, info) == "頁碼範圍不正確"
    q = t.option("quality")
    assert q.is_visible(t.defaults(), info)
    assert not q.is_visible({**t.defaults(), "format": "png"}, info)
    assert not q.is_visible({**t.defaults(), "mode": "embedded"}, info)
    assert q.display(85) == "85%"
    hint = t.option("dpi").note
    assert hint({"dpi": 72}, {}) == ("螢幕瀏覽、檔案最小", False)
    assert hint({"dpi": 150}, {}) == ("清晰且大小適中", False)
    assert hint({"dpi": 300}, {}) == ("列印品質，檔案較大", False)


def _embedded_pdf(path: Path, jpeg: bytes) -> Path:
    make_pdf(path, pages=2)
    with pikepdf.open(path, allow_overwriting_input=True) as pdf:
        j = pikepdf.Stream(pdf, jpeg)
        j.Type, j.Subtype, j.Width, j.Height = Name.XObject, Name.Image, 64, 48
        j.ColorSpace, j.BitsPerComponent, j.Filter = Name.DeviceRGB, 8, Name.DCTDecode
        raw = bytes(range(256)) * (40 * 30 * 3 // 256) + bytes(40 * 30 * 3 % 256)
        f = pikepdf.Stream(pdf, zlib.compress(raw))
        f.Type, f.Subtype, f.Width, f.Height = Name.XObject, Name.Image, 40, 30
        f.ColorSpace, f.BitsPerComponent, f.Filter = Name.DeviceRGB, 8, Name.FlateDecode
        m = pikepdf.Stream(pdf, zlib.compress(bytes([128]) * (40 * 30)))
        m.Type, m.Subtype, m.Width, m.Height = Name.XObject, Name.Image, 40, 30
        m.ColorSpace, m.BitsPerComponent, m.Filter = Name.DeviceGray, 8, Name.FlateDecode
        f.SMask = m
        tiny = pikepdf.Stream(pdf, bytes(4 * 4))
        tiny.Type, tiny.Subtype, tiny.Width, tiny.Height = Name.XObject, Name.Image, 4, 4
        tiny.ColorSpace, tiny.BitsPerComponent = Name.DeviceGray, 8
        form = pikepdf.Stream(pdf, b"/Inner Do")
        form.Type, form.Subtype, form.BBox = Name.XObject, Name.Form, [0, 0, 1, 1]
        form.Resources = pikepdf.Dictionary(XObject=pikepdf.Dictionary(Inner=f, Self=form))
        p1 = pdf.pages[0]
        for im in (j, tiny, form):
            p1.add_resource(im, Name.XObject, prefix="X")
        pdf.pages[1].add_resource(j, Name.XObject, prefix="Again")  # 重複出現只取一次
        pdf.save(path)
    return path


def test_extract_embedded_images(tmp_path, jpg_file):
    jpeg_buf = io.BytesIO()
    Image.new("RGB", (64, 48), (10, 200, 30)).save(jpeg_buf, "JPEG")
    src = _embedded_pdf(tmp_path / "嵌.pdf", jpeg_buf.getvalue())
    run = run_tool("pdf.to-images", [src], {"mode": "embedded"})
    folder = run.outputs[0]
    assert folder.name == "嵌-內嵌圖片"
    files = sorted(p.name for p in folder.iterdir())
    assert sorted(f.rsplit(".", 1)[1] for f in files) == ["jpg", "png"]
    assert all(f.startswith(("嵌-第01頁-1.", "嵌-第01頁-2.")) for f in files)
    jpg = next(folder.glob("*.jpg"))
    assert jpg.read_bytes() == jpeg_buf.getvalue()
    with Image.open(next(folder.glob("*.png"))) as im:
        assert im.mode == "RGBA" and im.getpixel((0, 0))[3] == 128
    assert run.summary == "已取出 2 張圖片"


def test_extract_without_images(pdf_file):
    with pytest.raises(
        errors.QSError, match="檔案沒有可轉換的內容：「文件.pdf」的指定頁面中沒有內嵌圖片。"
    ):
        run_tool("pdf.to-images", [pdf_file], {"mode": "embedded"})


# ------------------------------------------------------------------ 圖片轉 PDF


def _image_streams(path: Path) -> list[tuple[bytes, str]]:
    with pikepdf.open(path) as pdf:
        out = []
        for page in pdf.pages:
            for _k, x in page.obj.Resources.XObject.items():
                out.append((x.read_raw_bytes(), str(x.get("/Filter"))))
        return out


def test_images_to_pdf_merge_raw_jpeg(tmp_path):
    a = make_image(tmp_path / "相片.jpg", quality=90)
    b = make_image(tmp_path / "第二張.jpg", size=(300, 600), quality=90)
    t = tool("pdf.from-images")
    assert t.presets["pdf.images-to-pdf"] == {"output": "merge"}
    run = run_tool("pdf.from-images", [a, b], t.values_for_preset("pdf.images-to-pdf"))
    out = run.outputs[0]
    assert out.name == "相片-已合併.pdf"
    streams = _image_streams(out)
    assert streams[0] == (a.read_bytes(), "/DCTDecode")
    assert streams[1][0] == b.read_bytes()
    with pikepdf.open(out) as pdf:
        # 640×480 → 自動橫向 A4；300×600 → 直向 A4
        assert [round(float(v), 2) for v in pdf.pages[0].mediabox] == [0, 0, 841.89, 595.28]
        assert [round(float(v), 2) for v in pdf.pages[1].mediabox] == [0, 0, 595.28, 841.89]


def test_single_image_and_separate(tmp_path, jpg_file, png_file):
    one = run_tool("pdf.from-images", [jpg_file]).outputs[0]
    assert one.name == "相片.pdf"
    sep = run_tool("pdf.from-images", [jpg_file, png_file], {"output": "separate"}).outputs
    assert sorted(p.name for p in sep) == ["相片 (1).pdf", "透明.pdf"]
    with pikepdf.open(sep[1]) as pdf:
        im = [x for _k, x in pdf.pages[0].obj.Resources.XObject.items()][0]
        assert "/SMask" in im


def test_fit_page_with_margin(tmp_path, jpg_file):
    out = run_tool("pdf.from-images", [jpg_file], {"paper": "fit", "margin": 20}).outputs[0]
    with pikepdf.open(out) as pdf:
        assert [float(v) for v in pdf.pages[0].mediabox] == [0, 0, 680, 520]


def test_orientation_forced_portrait(tmp_path, jpg_file):
    out = run_tool(
        "pdf.from-images", [jpg_file], {"paper": "letter", "orientation": "portrait"}
    ).outputs[0]
    with pikepdf.open(out) as pdf:
        assert [float(v) for v in pdf.pages[0].mediabox] == [0, 0, 612, 792]


def _oriented_jpeg(path: Path, orientation: int) -> Path:
    """400×200 的圖，存檔方向的左上角是紅色，其他是白色。"""
    img = Image.new("RGB", (400, 200), "white")
    img.paste((255, 0, 0), (0, 0, 80, 60))
    exif = Image.Exif()
    exif[0x0112] = orientation
    img.save(path, "JPEG", quality=95, exif=exif.tobytes())
    return path


def _red_corner(pdf_path: Path) -> str:
    base = pdf_path.with_suffix("")
    subprocess.run(
        ["/usr/bin/pdftocairo", "-png", "-singlefile", "-r", "72", str(pdf_path), str(base)],
        check=True,
    )
    with Image.open(str(base) + ".png") as im:
        im = im.convert("RGB")
        w, h = im.size
        corners = {"tl": (5, 5), "tr": (w - 6, 5), "bl": (5, h - 6), "br": (w - 6, h - 6)}
        reds = [
            k for k, xy in corners.items() if im.getpixel(xy)[0] > 200 and im.getpixel(xy)[1] < 80
        ]
    assert len(reds) == 1, reds
    return reds[0]


@pytest.mark.parametrize(
    "orientation,corner,portrait",
    [
        (1, "tl", False),
        (2, "tr", False),
        (3, "br", False),
        (4, "bl", False),
        (5, "tl", True),
        (6, "tr", True),
        (7, "br", True),
        (8, "bl", True),
    ],
)
def test_exif_orientation(tmp_path, orientation, corner, portrait):
    src = _oriented_jpeg(tmp_path / f"o{orientation}.jpg", orientation)
    out = run_tool("pdf.from-images", [src], {"paper": "fit"}).outputs[0]
    with pikepdf.open(out) as pdf:
        box = [float(v) for v in pdf.pages[0].mediabox]
        assert box == ([0, 0, 200, 400] if portrait else [0, 0, 400, 200])
    assert _image_streams(out)[0][0] == src.read_bytes()  # 原始 JPEG，沒有重新壓縮
    assert _red_corner(out) == corner


def test_multipage_tiff_and_webp(tmp_path):
    frames = [Image.new("RGB", (100, 80), c) for c in ("red", "blue")]
    tif = tmp_path / "多頁.tiff"
    frames[0].save(tif, save_all=True, append_images=frames[1:])
    webp = make_image(tmp_path / "w.webp", size=(120, 90))
    out = run_tool("pdf.from-images", [tif, webp]).outputs[0]
    assert page_count(out) == 3
    filters = [f for _d, f in _image_streams(out)]
    assert filters[2] == "/DCTDecode"  # WebP（無透明）轉成 JPEG


@pytest.mark.slow
def test_heic_input(tmp_path, png_file):
    heic = tmp_path / "手機.heic"
    rgb = tmp_path / "rgb.png"
    Image.open(png_file).convert("RGB").save(rgb)
    subprocess.run(
        ["/usr/bin/heif-enc", "-q", "80", "-o", str(heic), str(rgb)],
        check=True,
        capture_output=True,
    )
    out = run_tool("pdf.from-images", [heic]).outputs[0]
    assert out.name == "手機.pdf"
    assert _image_streams(out)[0][1] == "/DCTDecode"


def test_from_images_errors(tmp_path):
    bad = tmp_path / "假.jpg"
    bad.write_bytes(b"not an image")
    with pytest.raises(errors.QSError, match="無法讀取檔案內容：「假.jpg」不是可辨識的圖片。"):
        run_tool("pdf.from-images", [bad])
    with pytest.raises(errors.QSError, match="請選擇至少一張圖片。"):
        run_tool("pdf.from-images", [])


def test_from_images_definition(jpg_file):
    t = tool("pdf.from-images")
    assert t.reorderable and t.input_types == ("image/*",)
    assert t.title_for_action({"output": "merge"}, {"file_count": 3}) == "轉成 PDF"
    assert t.title_for_action({"output": "separate"}, {"file_count": 3}) == "轉成 3 個 PDF"
    assert t.title_for_action({"output": "separate"}, {"file_count": 1}) == "轉成 PDF"
    assert not t.option("orientation").is_visible({"paper": "fit"}, {})
    assert t.inspect([jpg_file])["file_count"] == 1
    assert fromimages.page_layout((640, 480), "a4", "auto", 0)[0] == pytest.approx((841.89, 595.28))
