"""萬用轉換：圖片、圖片轉 PDF、合併 PDF、PDF 轉圖片與文字、OCR。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from conftest import make_image, make_pdf
from PIL import Image

from quickseemd.core import errors
from quickseemd.core.converter import convert, merge_to_pdf
from quickseemd.core.model import ToolContext


def run(path: Path, fid: str, out: Path, **opts: Any) -> list[Path]:
    ctx = ToolContext(out)
    try:
        return convert(path, fid, ctx, opts)
    finally:
        ctx.cleanup()


def _animated(path: Path, fmt: str = "GIF") -> Path:
    frames = [Image.new("RGB", (40, 30), c) for c in ("red", "green", "blue")]
    frames[0].save(
        path, format=fmt, save_all=True, append_images=frames[1:], duration=[80, 120, 200], loop=0
    )
    return path


# ------------------------------------------------------------------ 圖片 → 圖片


def test_png_with_alpha_to_jpeg_is_flattened_on_white(png_file: Path, outdir: Path) -> None:
    [out] = run(png_file, "jpeg", outdir, image_quality=0.9)
    assert out.name == "透明.jpg"
    with Image.open(out) as img:
        assert img.format == "JPEG" and img.mode == "RGB"
        assert img.getpixel((5, img.height - 2))[0] > 240  # 透明處變白


def test_max_pixel_never_upscales(jpg_file: Path, outdir: Path) -> None:
    [small] = run(jpg_file, "png", outdir, image_max_pixel=320)
    with Image.open(small) as img:
        assert max(img.size) == 320
    [same] = run(jpg_file, "webp", outdir, image_max_pixel=4096)
    with Image.open(same) as img:
        assert img.size == (640, 480)


def test_exif_orientation_is_applied(tmp_path: Path, outdir: Path) -> None:
    src = tmp_path / "rot.jpg"
    img = Image.new("RGB", (60, 20), "red")
    exif = img.getexif()
    exif[0x0112] = 6  # 需要順時針轉 90°
    img.save(src, exif=exif.tobytes())
    [out] = run(src, "png", outdir)
    with Image.open(out) as res:
        assert res.size == (20, 60)
        assert res.getexif().get(0x0112, 1) == 1


@pytest.mark.parametrize("fid", ["bmp", "tiff", "jp2", "tga", "webp", "gif"])
def test_pillow_formats(jpg_file: Path, outdir: Path, fid: str) -> None:
    [out] = run(jpg_file, fid, outdir)
    with Image.open(out) as img:
        assert img.size == (640, 480)


def test_heic_and_avif_roundtrip(jpg_file: Path, outdir: Path, tmp_path: Path) -> None:
    [heic] = run(jpg_file, "heic", outdir)
    [avif] = run(jpg_file, "avif", outdir)
    assert heic.read_bytes()[4:8] == b"ftyp" and avif.read_bytes()[4:8] == b"ftyp"
    [back] = run(heic, "png", tmp_path)
    with Image.open(back) as img:
        assert img.size == (640, 480)


def test_icons(png_file: Path, outdir: Path) -> None:
    [ico] = run(png_file, "ico", outdir)
    with Image.open(ico) as img:
        assert {s for s in img.info["sizes"]} >= {(16, 16), (32, 32), (256, 256)}
    [icns] = run(png_file, "icns", outdir)
    assert icns.read_bytes()[:4] == b"icns"


def test_animated_gif_keeps_frames_and_delays(tmp_path: Path, outdir: Path) -> None:
    src = _animated(tmp_path / "a.gif")
    [gif] = run(src, "gif", outdir)
    with Image.open(gif) as img:
        assert img.n_frames == 3
        assert img.info.get("loop") == 0
        delays = []
        for i in range(3):
            img.seek(i)
            delays.append(img.info["duration"])
    assert delays == [80, 120, 200]
    [tiff] = run(src, "tiff", outdir)
    with Image.open(tiff) as img:
        assert img.n_frames == 3
    [png] = run(src, "png", outdir)
    with Image.open(png) as img:
        assert getattr(img, "n_frames", 1) == 1


def test_svg_rasterizes_to_at_least_1024(tmp_path: Path, outdir: Path) -> None:
    svg = tmp_path / "v.svg"
    svg.write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="100" height="50">'
        '<rect width="100" height="50" fill="red"/></svg>'
    )
    [out] = run(svg, "png", outdir)
    with Image.open(out) as img:
        assert img.size == (1024, 512)


def test_unreadable_image(tmp_path: Path, outdir: Path) -> None:
    bad = tmp_path / "bad.png"
    bad.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 10)
    with pytest.raises(errors.QSError):
        run(bad, "jpeg", outdir)


# ------------------------------------------------------------------ 圖片 → PDF、合併


def test_image_to_pdf_page_size_from_dpi(tmp_path: Path, outdir: Path) -> None:
    import pikepdf

    src = make_image(tmp_path / "p.png", size=(300, 150), dpi=(150, 150))
    [pdf] = run(src, "pdf", outdir)
    with pikepdf.open(pdf) as doc:
        box = [float(v) for v in doc.pages[0].mediabox]
        assert box[2:] == pytest.approx([144.0, 72.0])
        assert str(doc.docinfo["/Creator"]) == "QUICK_SEEMD"


def test_jpeg_is_embedded_without_recompression(jpg_file: Path, outdir: Path) -> None:
    import pikepdf

    [pdf] = run(jpg_file, "pdf", outdir)
    with pikepdf.open(pdf) as doc:
        images = list(doc.pages[0].images.values())
        assert images and images[0].Filter == pikepdf.Name.DCTDecode
        assert images[0].read_raw_bytes() == jpg_file.read_bytes()


def test_merge_images_and_pdfs_in_order(tmp_path: Path, jpg_file: Path, outdir: Path) -> None:
    import pikepdf

    pdf = make_pdf(tmp_path / "doc.pdf", pages=2)
    text = tmp_path / "skip.txt"
    text.write_text("x")
    messages: list[str | None] = []
    ctx = ToolContext(outdir, lambda f, m: messages.append(m))
    try:
        out = merge_to_pdf([jpg_file, text, pdf], ctx)
    finally:
        ctx.cleanup()
    assert out == outdir / "合併文件.pdf"
    assert messages[-1] == "已將 2 個檔案合併成「合併文件.pdf」"
    with pikepdf.open(out) as doc:
        assert len(doc.pages) == 3


def test_merge_without_usable_files(tmp_path: Path, outdir: Path) -> None:
    text = tmp_path / "a.txt"
    text.write_text("x")
    ctx = ToolContext(outdir)
    with pytest.raises(errors.QSError) as exc:
        merge_to_pdf([text], ctx)
    assert str(exc.value) == "檔案沒有可轉換的內容：沒有可合併的圖片或 PDF。"


def test_encrypted_pdf_is_reported(tmp_path: Path, outdir: Path) -> None:
    import pikepdf

    src = make_pdf(tmp_path / "plain.pdf", pages=1)
    locked = tmp_path / "locked.pdf"
    with pikepdf.open(src) as doc:
        doc.save(locked, encryption=pikepdf.Encryption(user="secret", owner="o"))
    with pytest.raises(errors.QSError) as exc:
        run(locked, "png", outdir)
    assert "受密碼保護" in str(exc.value)


# ------------------------------------------------------------------ PDF → 圖片與文字


def test_pdf_single_page_is_a_file(tmp_path: Path, outdir: Path) -> None:
    src = make_pdf(tmp_path / "one.pdf", pages=1)
    [png] = run(src, "png", outdir, pdf_dpi=72)
    assert png.name == "one.png"
    with Image.open(png) as img:
        assert img.size == (595, 842)


def test_pdf_multi_page_folder_naming(pdf_file: Path, outdir: Path) -> None:
    [folder] = run(pdf_file, "jpeg", outdir, pdf_dpi=72, image_max_pixel=512)
    assert folder.name == "文件-JPEG"
    names = sorted(p.name for p in folder.iterdir())
    assert names == ["文件-01.jpg", "文件-02.jpg", "文件-03.jpg"]
    with Image.open(folder / names[0]) as img:
        assert max(img.size) == 512 and img.format == "JPEG"


def test_pdf_tiff_and_heic_folders(pdf_file: Path, outdir: Path) -> None:
    [tiff] = run(pdf_file, "tiff", outdir, pdf_dpi=72)
    assert tiff.name == "文件-TIFF"
    [heic] = run(pdf_file, "heic", outdir, pdf_dpi=72)
    assert heic.name == "文件-HEIC" and len(list(heic.iterdir())) == 3


def test_pdf_to_text_joins_pages_with_form_feed(pdf_file: Path, outdir: Path) -> None:
    [txt] = run(pdf_file, "txt", outdir)
    text = txt.read_text("utf-8")
    assert text.count("\n\n\f\n") == 2
    assert "第 1 頁" in text and "Page 3" in text
    assert text.endswith("\n")


@pytest.mark.slow
def test_scanned_pdf_uses_ocr(tmp_path: Path, outdir: Path) -> None:
    src = make_pdf(tmp_path / "scan.pdf", pages=1, text=False)
    with pytest.raises(errors.QSError) as exc:
        run(src, "txt", outdir)
    assert str(exc.value) == "檔案沒有可轉換的內容：PDF 中沒有文字，OCR 也辨識不到內容。"


# ------------------------------------------------------------------ OCR


def _text_image(path: Path) -> Path:
    import cairo

    surface = cairo.ImageSurface(cairo.FORMAT_RGB24, 900, 260)
    cr = cairo.Context(surface)
    cr.set_source_rgb(1, 1, 1)
    cr.paint()
    cr.set_source_rgb(0, 0, 0)
    cr.select_font_face("DejaVu Sans")
    cr.set_font_size(48)
    cr.move_to(30, 90)
    cr.show_text("Hello QUICK")
    cr.move_to(30, 200)
    cr.show_text("Second line 42")
    surface.write_to_png(str(path))
    return path


@pytest.mark.slow
def test_ocr_lines(tmp_path: Path, outdir: Path) -> None:
    [txt] = run(_text_image(tmp_path / "words.png"), "ocr-txt", outdir)
    assert txt.name == "words.txt"
    lines = txt.read_text("utf-8").splitlines()
    assert "Hello" in lines[0] and "QUICK" in lines[0]
    assert "Second" in lines[1]


@pytest.mark.slow
def test_ocr_blank_image(tmp_path: Path, outdir: Path) -> None:
    blank = make_image(tmp_path / "blank.png", size=(200, 100))
    Image.new("RGB", (200, 100), "white").save(blank)
    with pytest.raises(errors.QSError) as exc:
        run(blank, "ocr-txt", outdir)
    assert "在圖片中找不到可辨識的文字。" in str(exc.value)


def test_ocr_arrangement_rules() -> None:
    from quickseemd.core.converter import ocr

    frags = [
        ocr.Fragment(300, 12, 50, 20, "right"),
        ocr.Fragment(10, 10, 50, 20, "left"),
        ocr.Fragment(10, 60, 50, 20, "next"),
    ]
    assert ocr.arrange(frags) == "left right\nnext"
    assert ocr.join_words(["中文", "字", "abc", "def"]) == "中文字 abc def"
    tsv = (
        "level\tpage_num\tblock_num\tpar_num\tline_num\tword_num\tleft\ttop\twidth\theight\tconf\ttext\n"
        "5\t1\t1\t1\t1\t1\t10\t10\t20\t10\t90\t你\n"
        "5\t1\t1\t1\t1\t2\t32\t10\t20\t10\t90\t好\n"
        "5\t1\t1\t1\t2\t1\t10\t40\t20\t10\t90\tok\n"
    )
    assert ocr.arrange(ocr.parse_tsv(tsv)) == "你好\nok"
