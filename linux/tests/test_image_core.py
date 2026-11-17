"""圖片工具共用核心：格式、顏色、讀取、寫出、流程。"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import make_image
from PIL import Image
from test_image_kit import CAMERA_TAGS, exiftool_json, tag_names, with_metadata

from quickseemd.core import backends, errors
from quickseemd.core.model import ToolContext
from quickseemd.core.tools.image import colors, formats, loader, pipeline, writer

# ------------------------------------------------------------------ 格式與顏色


def test_format_table_matches_original():
    ids = [f.id for f in formats.FORMATS.values()]
    assert ids == [
        "jpeg",
        "png",
        "heic",
        "avif",
        "tiff",
        "gif",
        "bmp",
        "webp",
        "ico",
        "icns",
        "pdf",
    ]
    assert formats.get("jpeg").ext == "jpg"
    assert formats.get("jpeg").title == "JPG"
    assert formats.get("webp").title == "WebP"
    assert formats.get("jpeg").lossy and not formats.get("png").lossy
    assert not formats.get("bmp").alpha and formats.get("png").alpha
    assert not formats.get("ico").bitmap and formats.get("gif").bitmap
    assert formats.HINTS["tiff"] == "無損（LZW 壓縮），適合印刷與後製。"


def test_writable_formats_follow_heif_encoder(monkeypatch):
    assert formats.is_writable("jpeg") and formats.is_writable("pdf")
    monkeypatch.setattr(backends, "find", lambda key: None)
    assert not formats.is_writable("heic")
    assert "heic" not in formats.writable_ids()
    assert "avif" not in formats.writable_ids()


def test_color_parsing():
    assert colors.parse("#ffffff") == colors.WHITE
    assert colors.parse("transparent").a == 0
    assert colors.parse("custom", "#ff0000").to8() == (255, 0, 0, 255)
    assert colors.parse("custom", None, colors.Rgba(0.5, 0.5, 0.5)).to8()[:3] == (128, 128, 128)
    assert colors.parse("#00000080").to8() == (0, 0, 0, 128)
    assert colors.parse("bogus") == colors.WHITE
    assert colors.parse("custom", [1, 0, 0]).to8() == (255, 0, 0, 255)
    assert colors.hex_of(colors.Rgba(0.85, 0.92, 1)) == "#d9ebff"
    assert colors.Rgba(1, 1, 1).is_light and not colors.BLACK.is_light


# ------------------------------------------------------------------ 讀取


def test_load_applies_exif_orientation(tmp_path):
    src = make_image(tmp_path / "a.jpg", size=(200, 100), quality=95)
    rotated = with_metadata(src, tmp_path / "o6.jpg", "-Orientation=6", "-n")
    s = loader.load(rotated)
    assert (s.width, s.height) == (100, 200)
    assert s.orientation == 6
    assert s.format == "jpeg"
    assert loader.pixel_size(rotated) == (100, 200)


def test_load_png_alpha_and_preferred_format(png_file):
    s = loader.load(png_file)
    assert s.has_alpha and s.image.mode == "RGBA"
    assert s.preferred_format() == "png"
    assert s.output_extension("png") == "png"
    assert s.output_extension("jpeg") == "jpg"


def test_output_extension_keeps_jpeg_spelling(tmp_path):
    f = make_image(tmp_path / "x.JPEG", quality=90)
    s = loader.load(f)
    assert s.output_extension("jpeg") == "jpeg"


def test_preferred_format_for_unwritable_source(tmp_path):
    ppm = make_image(tmp_path / "x.ppm")
    s = loader.load(ppm)
    assert s.format is None
    assert s.preferred_format() == "jpeg"
    ico = tmp_path / "x.ico"
    Image.new("RGBA", (32, 32), (255, 0, 0, 128)).save(ico)
    assert loader.load(ico).preferred_format() == "png"


def test_load_gray_and_cmyk_become_rgb(tmp_path):
    g = tmp_path / "g.png"
    Image.new("L", (10, 10), 128).save(g)
    assert loader.load(g).image.mode == "RGB"
    c = tmp_path / "c.jpg"
    Image.new("CMYK", (10, 10), (0, 255, 255, 0)).save(c)
    img = loader.load(c).image
    assert img.mode == "RGB"
    r, g_, b = img.getpixel((5, 5))
    assert r > 200 and g_ < 80 and b < 80


def test_load_16bit_gray_scales_down(tmp_path):
    import numpy as np

    p = tmp_path / "deep.png"
    Image.fromarray(np.full((8, 8), 40000, dtype=np.uint16)).save(p)
    img = loader.load(p).image
    assert img.mode == "RGB"
    assert abs(img.getpixel((1, 1))[0] - 40000 // 257) <= 1


def test_load_gif_frames(tmp_path):
    p = tmp_path / "anim.gif"
    frames = [Image.new("RGB", (20, 20), c) for c in ("red", "green", "blue")]
    frames[0].save(p, save_all=True, append_images=frames[1:], duration=[100, 200, 300], loop=0)
    one = loader.load(p)
    assert len(one.frames) == 1
    allf = loader.load(p, all_frames=True)
    assert len(allf.frames) == 3
    assert allf.loop == 0
    assert [round(f.delay, 2) for f in allf.frames] == [0.1, 0.2, 0.3]


def test_load_heic_via_heif_convert(tmp_path):
    if not backends.available("heif-enc"):
        pytest.skip("沒有 heif-enc")
    png = make_image(tmp_path / "a.png", size=(64, 48))
    heic = tmp_path / "a.heic"
    from quickseemd.core import process

    process.run_checked([backends.require("heif-enc"), "-q", "80", "-o", str(heic), str(png)], "x")
    s = loader.load(heic)
    assert s.format == "heic"
    assert s.size == (64, 48)
    assert loader.pixel_size(heic) == (64, 48)


def test_load_svg_rasterised(tmp_path):
    svg = tmp_path / "a.svg"
    svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="40" height="30">'
        '<rect width="40" height="30" fill="red"/></svg>'
    )
    s = loader.load(svg)
    assert s.size == (40, 30)
    assert s.format is None and s.has_alpha


def test_load_errors(tmp_path):
    with pytest.raises(errors.QSError, match="找不到檔案"):
        loader.load(tmp_path / "nope.png")
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"not an image at all")
    with pytest.raises(errors.QSError, match="不是可辨識的圖片"):
        loader.load(bad)
    assert loader.pixel_size(bad) is None


# ------------------------------------------------------------------ 寫出


@pytest.mark.parametrize(
    "fmt,pil",
    [
        ("jpeg", "JPEG"),
        ("png", "PNG"),
        ("tiff", "TIFF"),
        ("gif", "GIF"),
        ("bmp", "BMP"),
        ("webp", "WEBP"),
        ("ico", "ICO"),
        ("icns", "ICNS"),
        ("pdf", None),
    ],
)
def test_write_every_pillow_format(tmp_path, png_file, fmt, pil):
    s = loader.load(png_file)
    out = tmp_path / f"o.{formats.get(fmt).ext}"
    writer.write(s.frames, s, out, writer.Output(fmt))
    assert out.stat().st_size > 0
    if pil:
        with Image.open(out) as im:
            assert im.format == pil
    else:
        assert out.read_bytes().startswith(b"%PDF")


def test_write_tiff_uses_lzw(tmp_path, jpg_file):
    s = loader.load(jpg_file)
    out = tmp_path / "o.tiff"
    writer.write(s.frames, s, out, writer.Output("tiff"))
    with Image.open(out) as im:
        assert im.info.get("compression") == "tiff_lzw"


def test_write_jpeg_flattens_on_background(tmp_path):
    p = tmp_path / "t.png"
    Image.new("RGBA", (10, 10), (0, 0, 0, 0)).save(p)
    s = loader.load(p)
    out = tmp_path / "o.jpg"
    writer.write(s.frames, s, out, writer.Output("jpeg", background=colors.parse("#000000")))
    with Image.open(out) as im:
        assert im.mode == "RGB" and max(im.getpixel((5, 5))) < 10
    out2 = tmp_path / "o2.jpg"
    writer.write(s.frames, s, out2, writer.Output("jpeg"))
    with Image.open(out2) as im:
        assert min(im.getpixel((5, 5))) > 245


def test_write_animated_gif_keeps_frames(tmp_path):
    p = tmp_path / "anim.gif"
    frames = [Image.new("RGB", (20, 20), c) for c in ("red", "green", "blue")]
    frames[0].save(p, save_all=True, append_images=frames[1:], duration=150, loop=0)
    s = loader.load(p, all_frames=True)
    out = tmp_path / "o.gif"
    writer.write(s.frames, s, out, writer.Output("gif"))
    with Image.open(out) as im:
        assert im.n_frames == 3
        assert im.info.get("loop") == 0
        assert im.info.get("duration") == 150


def test_write_pdf_page_size_uses_dpi(tmp_path):
    p = tmp_path / "a.png"
    Image.new("RGB", (300, 150), "white").save(p, dpi=(150, 150))
    s = loader.load(p)
    assert s.dpi == pytest.approx(150, abs=0.5)
    out = tmp_path / "o.pdf"
    writer.write(s.frames, s, out, writer.Output("pdf"))
    import pikepdf

    with pikepdf.open(out) as pdf:
        assert [float(v) for v in pdf.pages[0].MediaBox] == [0, 0, 144, 72]


@pytest.mark.parametrize("fmt", ["heic", "avif"])
def test_write_heif_formats(tmp_path, png_file, fmt):
    if not formats.is_writable(fmt):
        pytest.skip("沒有 heif-enc")
    s = loader.load(png_file)
    out = tmp_path / f"o.{fmt}"
    writer.write(s.frames, s, out, writer.Output(fmt, quality=0.6))
    head = out.read_bytes()[:32]
    assert b"ftyp" in head
    assert (b"avif" in head) == (fmt == "avif")
    back = loader.load(out)
    assert back.size == s.size and back.has_alpha


def test_write_metadata_modes(tmp_path):
    src = make_image(tmp_path / "a.jpg", size=(200, 100), quality=95)
    meta = with_metadata(src, tmp_path / "m.jpg", *CAMERA_TAGS, "-Orientation=6", "-n")
    s = loader.load(meta)
    keep = tmp_path / "keep.jpg"
    writer.write(s.frames, s, keep, writer.Output("jpeg", metadata="keep"))
    tags = exiftool_json(keep)
    assert tags["EXIF:Orientation"] == "Horizontal (normal)"
    assert tags["EXIF:Model"] == "Cam 1"
    assert "EXIF:GPSLatitude" in tags
    noloc = tmp_path / "noloc.jpg"
    writer.write(s.frames, s, noloc, writer.Output("jpeg", metadata="remove-location"))
    names = tag_names(noloc)
    assert "Model" in names and not any(n.startswith("GPS") for n in names)
    none = tmp_path / "none.png"
    writer.write(s.frames, s, none, writer.Output("png", metadata="remove-all"))
    names = tag_names(none)
    assert "Model" not in names and "DateTimeOriginal" not in names


PRIVATE_TAGS = (
    "-GPSLatitude=25.03",
    "-GPSLatitudeRef=N",
    "-XMP-exif:GPSLatitude=10",
    "-XMP-exif:GPSLongitude=20",
    "-XMP-photoshop:City=Taipei",
    "-XMP-iptcCore:Location=Here",
    "-IPTC:City=Taipei",
    "-Model=Cam 1",
)


def _private_leaks(path: Path, mode: str) -> list[str]:
    """輸出檔裡不該出現的標籤（exiftool -j -G）。"""
    tags = exiftool_json(path)
    bad = []
    for key, value in tags.items():
        group, _sep, name = key.partition(":")
        if group in ("File", "System", "ExifTool", "Composite", "SourceFile"):
            continue
        location = name.startswith("GPS") or name in ("City", "Location", "Sub-location")
        private = (
            group in ("XMP", "IPTC", "Photoshop") or "Comment" in name or "secret" in str(value)
        )
        if location or private or (mode == writer.REMOVE_ALL and name == "Model"):
            bad.append(key)
    return bad


@pytest.mark.parametrize("src_ext", ["jpg", "png"])
@pytest.mark.parametrize("fmt", ["jpeg", "png", "webp", "tiff", "gif"])
@pytest.mark.parametrize("mode", [writer.REMOVE_LOCATION, writer.REMOVE_ALL])
def test_removed_metadata_never_leaks(tmp_path, src_ext, fmt, mode):
    base = make_image(tmp_path / f"a.{src_ext}", size=(64, 48))
    comment = "-Comment=secret note" if src_ext == "jpg" else "-PNG:Comment=secret note"
    leaky = with_metadata(base, tmp_path / f"leaky.{src_ext}", *PRIVATE_TAGS, comment)
    assert "secret" in str(exiftool_json(leaky))  # 測試資料真的帶著私人資訊
    s = loader.load(leaky)
    out = tmp_path / f"o.{formats.get(fmt).ext}"
    writer.write(s.frames, s, out, writer.Output(fmt, metadata=mode))
    assert _private_leaks(out, mode) == []
    if mode == writer.REMOVE_LOCATION and fmt in ("jpeg", "png", "webp") and src_ext == "jpg":
        assert "Model" in tag_names(out)  # 只移除位置時相機資訊還在


def test_loaded_frames_carry_no_info(tmp_path):
    base = make_image(tmp_path / "a.png", size=(32, 32))
    leaky = with_metadata(base, tmp_path / "leaky.png", *PRIVATE_TAGS, "-PNG:Comment=secret")
    s = loader.load(leaky)
    assert all(f.image.info == {} for f in s.frames)


def test_writer_ignores_info_on_given_images(tmp_path):
    img = Image.new("RGB", (16, 16), "red")
    img.info.update({"comment": b"secret", "xmp": b"<x:xmpmeta>secret</x:xmpmeta>"})
    for fmt in ("jpeg", "png", "webp", "gif"):
        out = tmp_path / f"o.{formats.get(fmt).ext}"
        writer.write_image(img, None, out, writer.Output(fmt, metadata=writer.REMOVE_ALL))
        assert b"secret" not in out.read_bytes()
    assert img.info["comment"] == b"secret"  # 呼叫端的圖不會被改到


def test_write_keeps_icc_profile(tmp_path):
    from PIL import ImageCms

    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("LAB")).tobytes()
    p = tmp_path / "icc.png"
    Image.new("RGB", (8, 8), "red").save(p, icc_profile=icc)
    s = loader.load(p)
    assert s.icc == icc
    out = tmp_path / "o.jpg"
    writer.write(s.frames, s, out, writer.Output("jpeg"))
    with Image.open(out) as im:
        assert im.info.get("icc_profile") == icc


def test_write_unwritable_format_errors(tmp_path, png_file, monkeypatch):
    s = loader.load(png_file)
    monkeypatch.setattr(formats, "is_writable", lambda fid: False)
    with pytest.raises(errors.QSError, match="無法寫入"):
        writer.write(s.frames, s, tmp_path / "o.heic", writer.Output("heic"))


# ------------------------------------------------------------------ 流程


def test_pipeline_run_and_never_overwrite(tmp_path, jpg_file):
    before = jpg_file.read_bytes()
    ctx = ToolContext()
    outs = [
        pipeline.run_file(
            jpg_file, ctx, "測試", lambda s: writer.Output(s.preferred_format()), lambda im, s: im
        )
        for _ in range(2)
    ]
    ctx.cleanup()
    assert [o.name for o in outs] == ["相片-測試.jpg", "相片-測試 (1).jpg"]
    assert jpg_file.read_bytes() == before
    assert ctx.last_message == "已儲存：相片-測試 (1).jpg"


def test_each_file_collects_skips(tmp_path, jpg_file, png_file):
    ctx = ToolContext()

    def body(f: Path, sub: ToolContext) -> list[Path]:
        if f.suffix == ".png":
            raise errors.SkipFile("略過原因")
        return [f]

    outs, skipped = pipeline.each_file([jpg_file, png_file], ctx, body)
    assert outs == [jpg_file]
    assert skipped == ["透明.png：略過原因"]
    with pytest.raises(errors.SkipFile):
        pipeline.each_file([png_file], ToolContext(), body)


def test_image_info(tmp_path, jpg_file, png_file):
    info = pipeline.image_info([jpg_file, png_file])
    assert info["count"] == 2
    assert info["sizes"] == [[640, 480], [640, 480]]
    assert info["has_alpha"] is True
    assert info["has_png"] is True
    assert info["total_size"] == jpg_file.stat().st_size + png_file.stat().st_size
    assert pipeline.image_info([]) == {
        "count": 0,
        "sizes": [],
        "has_alpha": False,
        "has_png": False,
        "total_size": 0,
    }
