"""預覽內容的準備（在背景執行緒做）：分類、交給預覽核心、圖片與 PDF 載入、各種錯誤頁。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

import conftest
from quickseemd.core import backends, errors
from quickseemd.core import preview as core_preview
from quickseemd.core.preview import Document
from quickseemd.previewer import content, fallback, soffice_local
from quickseemd.previewer.uri import Target, TargetKind


class FakeEngine:
    """預覽核心的替身：依副檔名決定類型，記錄被呼叫的情形。"""

    KINDS = {".txt": "html", ".ttf": "font", ".png": "image", ".jpg": "image", ".pdf": "pdf"}

    def __init__(self, fail: Exception | None = None) -> None:
        self.calls: list[tuple[str, Path]] = []
        self.fail = fail

    def classify(self, path: Path) -> str:
        self.calls.append(("classify", path))
        if path.is_dir():
            return "folder"
        return self.KINDS.get(path.suffix, "html")

    def render(self, path: Path) -> Document:
        self.calls.append(("render", path))
        if self.fail is not None:
            raise self.fail
        return Document(html=f"<p>{path.name}</p>", kind_label="純文字", preferred_size=(820, 680))

    def render_font(self, path: Path) -> Document:
        self.calls.append(("render_font", path))
        return Document(html="<p>字型</p>", kind_label="字型", preferred_size=(900, 640))


@pytest.fixture
def engine(monkeypatch: pytest.MonkeyPatch) -> FakeEngine:
    fake = FakeEngine()
    monkeypatch.setattr(core_preview, "_engine", lambda: fake)
    return fake


@pytest.fixture
def no_engine(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing():
        raise NotImplementedError("預覽核心尚未實作")

    monkeypatch.setattr(core_preview, "_engine", missing)


def _file(path: Path) -> Target:
    return Target(TargetKind.FILE, path.as_uri(), path.name, path)


# ------------------------------------------------------------------ 網頁類


def test_text_file_uses_engine(tmp_path: Path, engine: FakeEngine):
    f = tmp_path / "筆記.txt"
    f.write_text("hello", "utf-8")
    c = content.load(_file(f))
    assert c.view == content.PAGE
    assert c.document is not None and "筆記.txt" in c.document.html
    assert c.title == "筆記.txt"
    assert c.subtitle == "純文字"
    assert ("render", f) in engine.calls


def test_folder_uses_engine_render(tmp_path: Path, engine: FakeEngine):
    t = Target(TargetKind.FOLDER, tmp_path.as_uri(), tmp_path.name, tmp_path)
    c = content.load(t)
    assert c.view == content.PAGE
    assert ("render", tmp_path) in engine.calls


def test_font_uses_render_font(tmp_path: Path, engine: FakeEngine):
    f = tmp_path / "字型.ttf"
    f.write_bytes(b"\0\1\0\0")
    c = content.load(_file(f))
    assert c.view == content.PAGE and c.subtitle == "字型"
    assert ("render_font", f) in engine.calls


def test_engine_missing_shows_fallback_page(tmp_path: Path, no_engine: None):
    f = tmp_path / "<script>alert(1)<x>.txt"
    f.write_text("x", "utf-8")
    c = content.load(_file(f))
    assert c.view == content.PAGE
    html = c.document.html
    assert "<script>" not in html
    assert "&lt;script&gt;" in html


def test_engine_unexpected_error_shows_error_page(tmp_path: Path, monkeypatch):
    fake = FakeEngine(fail=RuntimeError("壞掉了"))
    monkeypatch.setattr(core_preview, "_engine", lambda: fake)
    f = tmp_path / "a.txt"
    f.write_text("x", "utf-8")
    c = content.load(_file(f))
    assert c.view == content.PAGE
    assert "無法預覽" in c.document.html


def test_engine_user_error_message_is_shown(tmp_path: Path, monkeypatch):
    fake = FakeEngine(fail=errors.QSError("檔案太奇怪<b>"))
    monkeypatch.setattr(core_preview, "_engine", lambda: fake)
    f = tmp_path / "a.txt"
    f.write_text("x", "utf-8")
    c = content.load(_file(f))
    assert "檔案太奇怪&lt;b&gt;" in c.document.html


def test_unknown_kind_falls_back_to_page(tmp_path: Path, monkeypatch):
    fake = FakeEngine()
    fake.classify = lambda p: "something-new"  # type: ignore[method-assign]
    monkeypatch.setattr(core_preview, "_engine", lambda: fake)
    f = tmp_path / "a.xyz"
    f.write_text("x", "utf-8")
    assert content.load(_file(f)).view == content.PAGE


# ------------------------------------------------------------------ 不能打開的目標


def test_special_file_never_reaches_engine(tmp_path: Path, engine: FakeEngine):
    t = Target(TargetKind.SPECIAL, "file:///x/管線", "管線", tmp_path / "管線", "具名管線（FIFO）")
    c = content.load(t)
    assert c.view == content.PAGE
    assert "具名管線（FIFO）" in c.document.html
    assert engine.calls == []
    assert not c.can_open


@pytest.mark.parametrize(
    ("kind", "words"),
    [
        (TargetKind.MISSING, "找不到"),
        (TargetKind.REMOTE, "無法預覽這個位置"),
        (TargetKind.INVALID, "無法預覽"),
    ],
)
def test_unavailable_targets(kind: TargetKind, words: str, engine: FakeEngine):
    c = content.load(Target(kind, "sftp://h/a&b.txt", "a&b.txt"))
    assert c.view == content.PAGE
    assert words in c.document.html
    assert "a&amp;b.txt" in c.document.html
    assert engine.calls == []


def test_unsupported_locations_cannot_be_opened_either():
    """不支援的位置連「用預設程式開啟」都停用，完全不碰那個網址。"""
    assert not content.load(Target(TargetKind.REMOTE, "http://h/a.txt", "a.txt")).can_open
    assert not content.load(Target(TargetKind.INVALID, "", "")).can_open


# ------------------------------------------------------------------ 圖片


def test_image_loads_pixbuf(jpg_file: Path, engine: FakeEngine):
    c = content.load(_file(jpg_file))
    assert c.view == content.IMAGE
    assert c.natural_size == (640, 480)
    assert c.payload.pixbuf is not None
    assert "640 × 480" in c.subtitle


def test_image_exif_orientation_is_applied(tmp_path: Path, engine: FakeEngine):
    from PIL import Image  # noqa: PLC0415

    p = tmp_path / "直的.jpg"
    conftest.make_image(p, size=(200, 100))
    img = Image.open(p)
    exif = img.getexif()
    exif[0x0112] = 6  # 向右轉 90°
    img.save(p, exif=exif)
    c = content.load(_file(p))
    assert c.natural_size == (100, 200)


def test_animated_gif(tmp_path: Path, engine: FakeEngine, monkeypatch):
    from PIL import Image  # noqa: PLC0415

    p = tmp_path / "動畫.gif"
    frames = [Image.new("RGB", (60, 40), c) for c in ("red", "green", "blue")]
    frames[0].save(p, save_all=True, append_images=frames[1:], duration=100, loop=0)
    fake = FakeEngine()
    fake.KINDS = {".gif": "image"}
    monkeypatch.setattr(core_preview, "_engine", lambda: fake)
    c = content.load(_file(p))
    assert c.view == content.IMAGE
    assert c.payload.animation is not None
    assert c.natural_size == (60, 40)


def test_broken_image_shows_error_page(tmp_path: Path, engine: FakeEngine):
    p = tmp_path / "壞圖.png"
    p.write_bytes(b"\x89PNG\r\n\x1a\nnot really")
    c = content.load(_file(p))
    assert c.view == content.PAGE
    assert "無法預覽" in c.document.html


# ------------------------------------------------------------------ PDF


def test_pdf_loads_document(pdf_file: Path, engine: FakeEngine):
    c = content.load(_file(pdf_file))
    assert c.view == content.PDF
    assert c.payload.get_n_pages() == 3
    w, h = c.natural_size
    assert (round(w), round(h)) == (595, 842)
    assert "3 頁" in c.subtitle


def test_encrypted_pdf_shows_message(tmp_path: Path, pdf_file: Path, engine: FakeEngine):
    import pikepdf  # noqa: PLC0415

    locked = tmp_path / "上鎖.pdf"
    with pikepdf.open(pdf_file) as doc:
        doc.save(locked, encryption=pikepdf.Encryption(owner="o", user="u", R=6))
    c = content.load(_file(locked))
    assert c.view == content.PAGE
    assert "密碼" in c.document.html


def test_broken_pdf_shows_error(tmp_path: Path, engine: FakeEngine):
    p = tmp_path / "壞.pdf"
    p.write_bytes(b"%PDF-1.4 garbage")
    c = content.load(_file(p))
    assert c.view == content.PAGE
    assert "無法預覽" in c.document.html


# ------------------------------------------------------------------ 影音與 Office（不靠預覽核心的分類）


def test_video_and_audio(tmp_path: Path, no_engine: None):
    v = tmp_path / "片.mp4"
    v.write_bytes(b"\0\0\0\x18ftypmp42")
    a = tmp_path / "聲.wav"
    a.write_bytes(b"RIFF\0\0\0\0WAVEfmt ")
    cv = content.load(_file(v))
    ca = content.load(_file(a))
    assert (cv.view, cv.path) == (content.VIDEO, v)
    assert (ca.view, ca.path) == (content.AUDIO, a)


FAKE_SOFFICE_COPY = """#!/usr/bin/python3
import os, shutil, sys
args = sys.argv[1:]
outdir = args[args.index("--outdir") + 1]
stem = os.path.basename(args[-1]).rsplit(".", 1)[0]
if os.environ.get("FAKE_PDF"):
    shutil.copyfile(os.environ["FAKE_PDF"], os.path.join(outdir, stem + ".pdf"))
else:
    sys.exit(3)
"""


@pytest.fixture
def office_env(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, pdf_file: Path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    exe = bindir / "soffice"
    exe.write_text(FAKE_SOFFICE_COPY, "utf-8")
    exe.chmod(0o755)
    monkeypatch.setenv("QUICKSEEMD_TOOL_PATH", str(bindir))
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    monkeypatch.setenv("FAKE_PDF", str(pdf_file))
    # 假的 soffice 要讀 /tmp 底下的測試檔，不包 bwrap
    monkeypatch.setattr(soffice_local, "sandbox_available", lambda: False)
    backends.refresh()
    yield monkeypatch
    backends.refresh()


def test_office_document_is_converted(tmp_path: Path, no_engine: None, office_env):
    doc = tmp_path / "簡報.pptx"
    doc.write_bytes(b"PK\x03\x04")
    c = content.load(_file(doc))
    assert c.view == content.OFFICE
    done = content.finish_office(c)
    assert done.view == content.PDF
    assert done.payload.get_n_pages() == 3
    assert done.title == "簡報.pptx"
    assert done.target == c.target


def test_office_conversion_failure_page(tmp_path: Path, no_engine: None, office_env):
    office_env.delenv("FAKE_PDF")
    doc = tmp_path / "文件.docx"
    doc.write_bytes(b"PK\x03\x04")
    done = content.finish_office(content.load(_file(doc)))
    assert done.view == content.PAGE
    assert "LibreOffice" in done.document.html


# ------------------------------------------------------------------ 備援分類（預覽核心還沒合併時）


@pytest.mark.parametrize(
    ("name", "data", "kind"),
    [
        ("a.png", None, "image"),
        ("a.mp4", b"\0\0\0\x18ftypmp42", "video"),
        ("a.wav", b"RIFF\0\0\0\0WAVEfmt ", "audio"),
        ("a.pdf", b"%PDF-1.4\n", "pdf"),
        ("a.docx", b"PK\x03\x04", "office"),
        ("a.xlsx", b"PK\x03\x04", "office"),
        ("a.ttf", b"\0\1\0\0\0", "font"),
        ("a.txt", b"hello", "html"),
        ("a.bin", bytes(range(256)), "html"),
    ],
)
def test_fallback_classify(tmp_path: Path, name: str, data: bytes | None, kind: str):
    p = tmp_path / name
    if data is None:
        conftest.make_image(p)
    else:
        p.write_bytes(data)
    assert fallback.classify(p) == kind


def test_fallback_classify_folder_and_special(tmp_path: Path):
    assert fallback.classify(tmp_path) == "folder"
    fifo = tmp_path / "p"
    os.mkfifo(fifo)
    assert fallback.classify(fifo) == "special"


def test_info_page_escapes_everything():
    doc = fallback.info_page("<b>標題</b>", [("類型", "<i>x</i>")], note="注意 & 小心")
    assert "<b>標題</b>" not in doc.html
    assert "&lt;b&gt;標題&lt;/b&gt;" in doc.html
    assert "&lt;i&gt;x&lt;/i&gt;" in doc.html
    assert "注意 &amp; 小心" in doc.html
    assert "<script" not in doc.html


def test_file_page_rows(tmp_path: Path):
    f = tmp_path / "資料.bin"
    f.write_bytes(b"x" * 2048)
    doc = fallback.file_page(f)
    assert "資料.bin" in doc.html
    assert "大小" in doc.html


def test_info_page_size_is_compact():
    doc = fallback.info_page("t", [])
    assert doc.preferred_size[0] <= 920 and doc.preferred_size[1] <= 700
