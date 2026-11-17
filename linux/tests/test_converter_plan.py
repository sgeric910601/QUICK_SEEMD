"""萬用轉換：每種輸入能轉成哪些格式（對應原版 ConversionEngine.plan／unavailable）。

期望值是依原版規則逐條推出來的（Linux 版拿掉 RTFD、Web 封存檔、DMG，補上 GB18030，
單一 .xz 可以解壓）。測試機器上裝了 ffmpeg、pandoc、LibreOffice、tesseract、heif-enc，
沒有 ImageMagick。
"""

from __future__ import annotations

import bz2
import lzma
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from conftest import make_audio, make_image, make_video

from quickseemd.core import backends, process
from quickseemd.core.converter import capabilities, targets_for, unavailable

DOC_PLAIN = ["pdf", "docx", "doc", "odt", "rtf", "html"]
TEXT_UTF8_LF = ["utf8-bom", "utf16", "big5", "gb18030", "crlf"]
ARCHIVE = ["zip", "tgz", "7z"]
PLAIN_FILE = DOC_PLAIN + TEXT_UTF8_LF + ARCHIVE
IMAGES = ["png", "jpeg", "heic", "avif", "webp", "tiff", "gif", "bmp", "jp2", "ico", "icns", "tga"]
VIDEO_NATIVE = ["mp4", "mp4-hevc", "mov", "m4v", "mov-hevc", "prores"]
AUDIO_NATIVE = ["m4a", "alac", "aac", "wav", "aiff", "caf", "flac"]

SAMPLE_MATRIX: dict[str, list[str]] = {
    "Cargo.toml": PLAIN_FILE,
    "Dockerfile": PLAIN_FILE,
    "Makefile": PLAIN_FILE,
    "README.md": ["html", "pdf", "docx", "rtf", "odt", "doc", "epub", "tex"]
    + TEXT_UTF8_LF
    + ARCHIVE,
    "analysis.ipynb": ["docx", "html", "odt", "md", "epub", "tex"]
    + ["json-pretty", "json-min", "yaml", "plist", "bplist"]
    + TEXT_UTF8_LF
    + ARCHIVE,
    "app.log": PLAIN_FILE,
    "app.log.gz": ["extract"],
    "app.py": PLAIN_FILE,
    "archive.tar.gz": ["extract", "zip", "7z"],
    "archive.tar.xz": ["extract", "zip", "7z"],
    "archive.zip": ["extract", "tgz", "7z"],
    "big5.txt": DOC_PLAIN + ["utf8", "utf8-bom", "utf16", "gb18030", "crlf"] + ARCHIVE,
    "broken.json": ["json-pretty", "json-min", "yaml", "plist", "bplist"] + TEXT_UTF8_LF + ARCHIVE,
    "change.diff": PLAIN_FILE,
    "config.yaml": TEXT_UTF8_LF + ARCHIVE,
    "data.csv": ["xlsx", "json", "yaml", "html-table", "tsv"]
    + TEXT_UTF8_LF
    + ["pdf", "ods"]
    + ARCHIVE,
    "data.tsv": ["xlsx", "json", "yaml", "html-table", "csv"] + TEXT_UTF8_LF + ARCHIVE,
    "deploy": PLAIN_FILE,
    "empty.txt": PLAIN_FILE,
    "hello-binary": ARCHIVE,
    "hello.swift": PLAIN_FILE,
    "icon.png": IMAGES + ["pdf", "ocr-txt"] + ARCHIVE,
    "index.tsx": PLAIN_FILE,
    "main.rs": PLAIN_FILE,
    "minified.json": ["json-pretty", "json-min", "yaml", "plist", "bplist"]
    + TEXT_UTF8_LF
    + ARCHIVE,
    "query.sql": PLAIN_FILE,
    "random.dat": ARCHIVE,
    "sample.env": PLAIN_FILE,
    "server.go": PLAIN_FILE,
    "settings.ini": PLAIN_FILE,
    "settings.plist": ["json", "yaml", "plist", "bplist"] + ARCHIVE,
    "shop.sqlite": ARCHIVE,
    "styles.css": PLAIN_FILE,
    "utf16.txt": DOC_PLAIN + ["utf8", "utf8-bom", "big5", "gb18030", "crlf"] + ARCHIVE,
}


def ids(path: Path) -> list[str]:
    return [t.id for t in targets_for(path)]


def by_id(path: Path) -> dict:
    return {t.id: t for t in targets_for(path)}


def test_every_sample_is_covered(samples: Path) -> None:
    assert sorted(p.name for p in samples.iterdir()) == sorted(SAMPLE_MATRIX)


@pytest.mark.parametrize("name", sorted(SAMPLE_MATRIX))
def test_sample_matrix(samples: Path, name: str) -> None:
    assert ids(samples / name) == SAMPLE_MATRIX[name]


def test_sample_notes_and_backends(samples: Path) -> None:
    csv = by_id(samples / "data.csv")
    assert csv["utf8-bom"].note == "Windows Excel 開啟不亂碼"
    assert csv["pdf"].backend == "soffice" and csv["pdf"].route == "external"
    assert csv["xlsx"].route == "data" and csv["xlsx"].backend is None
    png = by_id(samples / "icon.png")
    assert png["png"].note == "重新壓縮或縮小"
    assert png["jpeg"].note is None
    assert png["ocr-txt"].note == "辨識圖片中的文字"
    assert png["pdf"].route == "imageToPDF"
    tar = by_id(samples / "archive.tar.gz")
    assert tar["zip"].note == "重新封裝" and tar["extract"].note is None
    readme = by_id(samples / "README.md")
    assert readme["epub"].backend == "pandoc"
    assert readme["html"].route == "document"
    nb = by_id(samples / "analysis.ipynb")
    assert nb["md"].backend == "pandoc"
    assert by_id(samples / "big5.txt")["utf8"].route == "text"


def test_sample_unavailable(samples: Path) -> None:
    png = unavailable(samples / "icon.png")
    assert [(u.formats, u.backend) for u in png] == [("JPEG XL", "magick")]
    assert png[0].reason == (
        "這台電腦無法寫入這些格式，需要安裝 ImageMagick（sudo apt install imagemagick）。"
    )
    yaml = unavailable(samples / "config.yaml")
    assert [(u.formats, u.reason) for u in yaml] == [
        ("JSON、Plist", "目前不支援讀取 YAML（只能輸出 YAML）。")
    ]
    assert unavailable(samples / "archive.tar.xz") == []
    assert unavailable(samples / "data.csv") == []


# ------------------------------------------------------------------ 產生的檔案


def test_jpeg_and_static_gif(jpg_file: Path, tmp_path: Path) -> None:
    t = by_id(jpg_file)
    assert list(t) == IMAGES + ["pdf", "ocr-txt"] + ARCHIVE
    assert t["jpeg"].note == "重新壓縮或縮小"
    gif = make_image(tmp_path / "s.gif")
    assert by_id(gif)["gif"].note == "重新壓縮或縮小"


def _animated(path: Path, fmt: str) -> Path:
    from PIL import Image

    frames = [Image.new("RGB", (40, 30), c) for c in ("red", "green", "blue")]
    frames[0].save(path, format=fmt, save_all=True, append_images=frames[1:], duration=80, loop=0)
    return path


def test_animated_gif(tmp_path: Path) -> None:
    t = by_id(_animated(tmp_path / "a.gif", "GIF"))
    assert list(t) == IMAGES + ["mp4", "mov", "pdf", "ocr-txt"] + ARCHIVE
    first = {i: "只取第一格" for i in IMAGES}
    first.update(tiff=None, gif="保留動畫")
    assert {i: t[i].note for i in IMAGES} == first
    assert t["mp4"].route == "animatedToVideo" and t["mp4"].note == "動畫轉影片"


def test_animated_png_keeps_recompress_note(tmp_path: Path) -> None:
    t = by_id(_animated(tmp_path / "a.png", "PNG"))
    assert t["png"].note == "重新壓縮或縮小"
    assert t["gif"].note == "保留動畫"
    assert "mp4" in t


def test_pdf(pdf_file: Path) -> None:
    t = by_id(pdf_file)
    assert list(t) == ["png", "jpeg", "tiff", "heic", "txt", "docx", "rtf", "html", "odt"] + ARCHIVE
    assert t["png"].note == "每頁一張" and t["png"].route == "pdfToImages"
    assert t["txt"].note == "掃描檔自動 OCR"
    assert t["docx"].note == "僅文字與基本格式"
    assert [u.formats for u in unavailable(pdf_file)] == ["可編輯的 Word 版面"]


def test_video_with_audio(video_file: Path) -> None:
    t = by_id(video_file)
    expected = VIDEO_NATIVE + ["anim-gif", "png", "jpeg"] + AUDIO_NATIVE
    expected += ["webm", "mkv", "avi", "mp3", "ogg", "opus"] + ARCHIVE
    assert list(t) == expected
    assert t["png"].note == "擷取單一畫面" and t["png"].route == "videoFrame"
    assert t["anim-gif"].route == "videoToGIF"
    assert t["m4a"].note == "擷取音軌" and t["m4a"].route == "audio"
    assert t["webm"].backend == "ffmpeg"
    assert t["mp3"].note == "擷取音軌"


def test_video_without_audio(tmp_path: Path) -> None:
    v = make_video(tmp_path / "mute.mp4", seconds=1, audio=False)
    expected = VIDEO_NATIVE + ["anim-gif", "png", "jpeg", "webm", "mkv", "avi"] + ARCHIVE
    assert ids(v) == expected


def test_mkv_skips_its_own_extension(tmp_path: Path) -> None:
    v = make_video(tmp_path / "clip.mkv", seconds=1)
    expected = VIDEO_NATIVE + ["anim-gif", "png", "jpeg"] + AUDIO_NATIVE
    expected += ["webm", "avi", "mp3", "ogg", "opus"] + ARCHIVE
    assert ids(v) == expected


def test_audio_only_mp4_is_reclassified(tmp_path: Path) -> None:
    a = make_audio(tmp_path / "voice.mp4", seconds=1)
    assert ids(a) == AUDIO_NATIVE + ["mp3", "ogg", "opus"] + ARCHIVE


def test_wav(audio_file: Path) -> None:
    assert (
        ids(audio_file)
        == [f for f in AUDIO_NATIVE if f != "wav"]
        + [
            "mp3",
            "ogg",
            "opus",
        ]
        + ARCHIVE
    )


def test_docx(tmp_path: Path) -> None:
    import docx

    d = docx.Document()
    d.add_paragraph("內容")
    d.save(tmp_path / "w.docx")
    assert (
        ids(tmp_path / "w.docx")
        == [
            "pdf",
            "doc",
            "odt",
            "rtf",
            "html",
            "txt",
            "md",
            "epub",
            "tex",
        ]
        + ARCHIVE
    )


def test_rtf_and_html(tmp_path: Path) -> None:
    (tmp_path / "r.rtf").write_text("{\\rtf1 hi}")
    assert (
        ids(tmp_path / "r.rtf")
        == [
            "pdf",
            "docx",
            "doc",
            "odt",
            "html",
            "txt",
            "md",
            "epub",
            "tex",
        ]
        + ARCHIVE
    )
    (tmp_path / "p.html").write_text("<html><body><p>x</p></body></html>")
    assert (
        ids(tmp_path / "p.html")
        == [
            "pdf",
            "docx",
            "doc",
            "odt",
            "rtf",
            "txt",
            "md",
            "epub",
            "tex",
        ]
        + ARCHIVE
    )


def test_xlsx_pptx_epub(tmp_path: Path) -> None:
    from quickseemd.core.converter import tables, xlsx

    xlsx.write_xlsx([tables.Table("a", (("x",),))], tmp_path / "s.xlsx")
    t = by_id(tmp_path / "s.xlsx")
    assert list(t) == ["csv", "json", "tsv", "html-table", "yaml", "pdf", "ods"] + ARCHIVE
    assert t["csv"].note == "多個工作表各存一檔" and t["tsv"].note == "多個工作表各存一檔"
    assert t["ods"].backend == "soffice"
    for name in ("p.pptx", "b.epub"):
        with zipfile.ZipFile(tmp_path / name, "w") as z:
            z.writestr("mimetype", "x")
    assert ids(tmp_path / "p.pptx") == ["pdf", "odp"] + ARCHIVE
    assert ids(tmp_path / "b.epub") == ["docx", "html", "odt", "md", "tex"] + ARCHIVE


def test_folder_and_json_array(tmp_path: Path) -> None:
    folder = tmp_path / "資料夾"
    folder.mkdir()
    assert ids(folder) == ARCHIVE
    arr = tmp_path / "rows.json"
    arr.write_text('﻿ [{"a": 1}]\n' if False else ' [{"a": 1}]\n')
    assert (
        ids(arr)
        == [
            "json-pretty",
            "json-min",
            "yaml",
            "plist",
            "bplist",
            "csv",
            "xlsx",
            "tsv",
            "html-table",
        ]
        + TEXT_UTF8_LF
        + ARCHIVE
    )


def test_single_compressed_files(tmp_path: Path) -> None:
    (tmp_path / "a.txt.xz").write_bytes(lzma.compress(b"hello"))
    (tmp_path / "a.txt.bz2").write_bytes(bz2.compress(b"hello"))
    assert ids(tmp_path / "a.txt.xz") == ["extract"]
    assert ids(tmp_path / "a.txt.bz2") == ["extract"]
    assert unavailable(tmp_path / "a.txt.xz") == []


def test_magick_only_input_without_imagemagick(tmp_path: Path) -> None:
    xcf = tmp_path / "art.xcf"
    xcf.write_bytes(b"gimp xcf v011\x00" + b"\x00" * 64)
    assert ids(xcf) == ARCHIVE
    hints = unavailable(xcf)
    assert [(u.formats, u.reason) for u in hints] == [
        (
            "所有圖片格式",
            "這台電腦無法讀取 .xcf，需要安裝 ImageMagick（sudo apt install imagemagick）。",
        )
    ]


def test_svg_is_an_image(tmp_path: Path) -> None:
    svg = tmp_path / "v.svg"
    svg.write_text('<svg xmlns="http://www.w3.org/2000/svg" width="10" height="10"/>')
    assert ids(svg) == IMAGES + ["pdf", "ocr-txt"] + ARCHIVE


def test_heic_input(tmp_path: Path) -> None:
    png = make_image(tmp_path / "src.png", size=(64, 48))
    heic = tmp_path / "photo.heic"
    process.run_checked(
        [backends.require("heif-enc"), "-q", "50", "-o", str(heic), str(png)], "heif-enc"
    )
    t = by_id(heic)
    assert t["heic"].note == "重新壓縮或縮小"
    assert t["png"].note is None


# ------------------------------------------------------------------ 缺少外部工具


@pytest.fixture
def no_tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[None]:
    _hide_tools(tmp_path, monkeypatch)
    yield
    monkeypatch.undo()
    backends.refresh()
    capabilities.refresh()


def _hide_tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    empty = tmp_path / "no-tools"
    empty.mkdir(exist_ok=True)
    monkeypatch.setenv("QUICKSEEMD_TOOL_PATH", str(empty))
    backends.refresh()
    capabilities.refresh()


def test_without_tools_media_hints(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    video = make_video(tmp_path / "v.mp4", seconds=1)
    audio = make_audio(tmp_path / "a.wav", seconds=1)
    _hide_tools(tmp_path, monkeypatch)
    try:
        assert ids(video) == ["zip", "tgz"]
        v = unavailable(video)
        assert [(u.formats, u.reason, u.backend) for u in v] == [
            (
                "所有影片與音訊格式",
                "這台電腦無法解碼 .mp4 影片，需要安裝 ffmpeg（sudo apt install ffmpeg）。",
                "ffmpeg",
            )
        ]
        a = unavailable(audio)
        assert a[0].formats == "所有音訊格式"
        assert (
            a[0].reason
            == "這台電腦無法解碼 .wav 音訊，需要安裝 ffmpeg（sudo apt install ffmpeg）。"
        )
    finally:
        monkeypatch.undo()
        backends.refresh()
        capabilities.refresh()


def test_without_tools_document_hints(samples: Path, png_file: Path, no_tools: None) -> None:
    md = unavailable(samples / "README.md")
    assert ("EPUB、LaTeX", "需要安裝 Pandoc（sudo apt install pandoc）。") in [
        (u.formats, u.reason) for u in md
    ]
    assert (
        "Word、ODT、RTF",
        "文件格式轉換需要 LibreOffice（sudo apt install libreoffice-writer-nogui）。",
    ) in [(u.formats, u.reason) for u in md]
    assert ids(samples / "README.md")[:2] == ["html", "pdf"]
    nb = [u.formats for u in unavailable(samples / "analysis.ipynb")]
    assert "Markdown、EPUB、LaTeX" in nb
    png = [(u.formats, u.backend) for u in unavailable(png_file)]
    assert ("文字辨識 (OCR)", "tesseract") in png
    assert "ocr-txt" not in ids(png_file)
    assert "7z" not in ids(png_file)


def test_without_libreoffice_office_hints(tmp_path: Path, no_tools: None) -> None:
    from quickseemd.core.converter import tables, xlsx

    xlsx.write_xlsx([tables.Table("a", (("x",),))], tmp_path / "s.xlsx")
    with zipfile.ZipFile(tmp_path / "p.pptx", "w") as z:
        z.writestr("mimetype", "x")
    (tmp_path / "k.key").write_bytes(b"x")
    hints = {u.formats: u.reason for u in unavailable(tmp_path / "s.xlsx")}
    assert hints == {
        "PDF、ODS": "試算表排版需要 LibreOffice（sudo apt install libreoffice-calc-nogui）。"
    }
    hints = {u.formats: u.reason for u in unavailable(tmp_path / "p.pptx")}
    assert hints == {
        "PDF、PPTX、ODP": "簡報需要 LibreOffice 才能轉換（sudo apt install libreoffice-impress-nogui）。"
    }
    hints = {u.formats: u.reason for u in unavailable(tmp_path / "k.key")}
    assert hints == {
        "PDF、Office 格式": "Pages／Numbers／Keynote 需要 LibreOffice（sudo apt install libreoffice-writer-nogui）。"
    }


def test_pdf_without_poppler(pdf_file: Path, no_tools: None) -> None:
    assert ids(pdf_file) == ["zip", "tgz"]
