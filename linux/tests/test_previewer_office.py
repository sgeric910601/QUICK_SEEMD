"""Office 文件預覽：LibreOffice 轉成 PDF 後快取。"""

from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

import pytest

from quickseemd.core import backends, errors
from quickseemd.previewer import office

FAKE_SOFFICE = """#!/usr/bin/python3
import json, os, sys
args = sys.argv[1:]
with open(os.environ["FAKE_SOFFICE_LOG"], "a", encoding="utf-8") as fh:
    fh.write(json.dumps(args, ensure_ascii=False) + "\\n")
copy_to = os.environ.get("FAKE_SOFFICE_COPY_PROFILE")
if copy_to:
    prof = next(a for a in args if a.startswith("-env:UserInstallation=file://"))
    prof = prof[len("-env:UserInstallation=file://"):]
    with open(os.path.join(prof, "user", "registrymodifications.xcu"), encoding="utf-8") as src:
        with open(copy_to, "w", encoding="utf-8") as dst:
            dst.write(src.read())
mode = os.environ.get("FAKE_SOFFICE_MODE", "ok")
if mode == "fail":
    sys.stderr.write("Error: source file could not be loaded\\n")
    sys.exit(1)
outdir = args[args.index("--outdir") + 1]
src = args[-1]
stem = os.path.basename(src).rsplit(".", 1)[0]
if mode != "silent":
    with open(os.path.join(outdir, stem + ".pdf"), "xb") as out:
        out.write(b"%PDF-1.4 fake")
"""


@pytest.fixture(autouse=True)
def own_cache(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """每個測試用自己的快取資料夾，互不干擾。"""
    cache = tmp_path / "cache"
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache))
    return cache


@pytest.fixture
def fake_soffice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    bindir = tmp_path / "fakebin"
    bindir.mkdir()
    exe = bindir / "soffice"
    exe.write_text(FAKE_SOFFICE, "utf-8")
    exe.chmod(0o755)
    log = tmp_path / "soffice.log"
    monkeypatch.setenv("QUICKSEEMD_TOOL_PATH", str(bindir))
    monkeypatch.setenv("FAKE_SOFFICE_LOG", str(log))
    backends.refresh()
    yield log
    backends.refresh()


def _calls(log: Path) -> list[list[str]]:
    if not log.exists():
        return []
    return [json.loads(line) for line in log.read_text("utf-8").splitlines()]


def _docx(tmp_path: Path, name: str = "報告.docx") -> Path:
    p = tmp_path / name
    p.write_bytes(b"PK\x03\x04 not really a docx")
    return p


def test_cache_key_is_sha1_of_path_mtime_size(tmp_path: Path):
    f = _docx(tmp_path)
    k1 = office.cache_key(f)
    assert re.fullmatch(r"[0-9a-f]{40}", k1)
    assert office.cache_key(f) == k1
    other = _docx(tmp_path, "另一份.docx")
    assert office.cache_key(other) != k1


def test_cache_key_changes_with_content(tmp_path: Path):
    f = _docx(tmp_path)
    k1 = office.cache_key(f)
    f.write_bytes(b"PK\x03\x04 changed and longer")
    st = f.stat()
    os.utime(f, ns=(st.st_atime_ns, st.st_mtime_ns + 5_000_000_000))
    assert office.cache_key(f) != k1


def test_cached_pdf_path_location(tmp_path: Path):
    f = _docx(tmp_path)
    p = office.cached_pdf_path(f)
    assert p.parent == backends.cache_dir() / "preview"
    assert p.name == office.cache_key(f) + ".pdf"


def test_convert_creates_cached_pdf(tmp_path: Path, fake_soffice: Path):
    src = _docx(tmp_path)
    before = src.read_bytes()
    pdf = office.convert_to_pdf(src)
    assert pdf == office.cached_pdf_path(src)
    assert pdf.read_bytes().startswith(b"%PDF")
    assert src.read_bytes() == before
    # 原始檔旁邊沒有多出任何檔案
    assert sorted(p.name for p in tmp_path.iterdir()) == sorted(
        ["cache", "fakebin", "soffice.log", src.name]
    )
    # 快取資料夾只剩轉好的 PDF（暫存的設定檔與輸出資料夾都清掉）
    assert [p.name for p in pdf.parent.iterdir()] == [pdf.name]


def test_convert_arguments(tmp_path: Path, fake_soffice: Path):
    src = _docx(tmp_path)
    office.convert_to_pdf(src)
    (args,) = _calls(fake_soffice)
    assert any(a.startswith("-env:UserInstallation=file://") for a in args)
    assert "--headless" in args
    assert args[args.index("--convert-to") + 1] == "pdf"
    outdir = args[args.index("--outdir") + 1]
    assert os.path.isabs(outdir)
    assert args[-1] == str(src)
    profile = next(a for a in args if a.startswith("-env:UserInstallation="))
    # 設定檔資料夾與輸出資料夾是這次專用的暫存資料夾
    assert Path(outdir).parent.name.startswith("job-")
    assert "job-" in profile


def test_second_convert_uses_cache(tmp_path: Path, fake_soffice: Path):
    src = _docx(tmp_path)
    first = office.convert_to_pdf(src)
    second = office.convert_to_pdf(src)
    assert first == second
    assert len(_calls(fake_soffice)) == 1


def test_existing_cache_file_is_not_overwritten(tmp_path: Path, fake_soffice: Path):
    src = _docx(tmp_path)
    target = office.cached_pdf_path(src)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_bytes(b"%PDF-1.4 already here")
    assert office.convert_to_pdf(src) == target
    assert target.read_bytes() == b"%PDF-1.4 already here"
    assert _calls(fake_soffice) == []


def test_failed_conversion_raises_and_leaves_nothing(
    tmp_path: Path, fake_soffice: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("FAKE_SOFFICE_MODE", "fail")
    src = _docx(tmp_path)
    with pytest.raises(errors.QSError) as info:
        office.convert_to_pdf(src)
    assert "LibreOffice" in str(info.value)
    assert not office.cached_pdf_path(src).exists()
    assert list(office.cached_pdf_path(src).parent.iterdir()) == []


def test_conversion_without_output_raises(
    tmp_path: Path, fake_soffice: Path, monkeypatch: pytest.MonkeyPatch
):
    monkeypatch.setenv("FAKE_SOFFICE_MODE", "silent")
    with pytest.raises(errors.QSError):
        office.convert_to_pdf(_docx(tmp_path))


def test_missing_soffice(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    empty = tmp_path / "empty-bin"
    empty.mkdir()
    monkeypatch.setenv("QUICKSEEMD_TOOL_PATH", str(empty))
    backends.refresh()
    try:
        with pytest.raises(errors.QSError) as info:
            office.convert_to_pdf(_docx(tmp_path))
        assert str(info.value) == "找不到 LibreOffice，這個格式需要先安裝它。"
    finally:
        backends.refresh()


def test_timeout_is_passed(tmp_path: Path, fake_soffice: Path, monkeypatch: pytest.MonkeyPatch):
    seen: dict = {}
    real_run = office.process.run

    def spy(args, **kw):  # noqa: ANN001, ANN003
        seen.update(kw)
        return real_run(args, **kw)

    monkeypatch.setattr(office.process, "run", spy)
    office.convert_to_pdf(_docx(tmp_path))
    assert seen["timeout"] == office.CONVERT_TIMEOUT == 60


def test_prune_keeps_newest(tmp_path: Path):
    cache = office.cache_folder()
    cache.mkdir(parents=True, exist_ok=True)
    now = time.time()
    for i in range(6):
        p = cache / f"{i:040x}.pdf"
        p.write_bytes(b"%PDF")
        os.utime(p, (now - 100 + i, now - 100 + i))
    other = cache / "keep-me.txt"
    other.write_text("不是快取", "utf-8")
    office.prune_cache(keep=3)
    names = sorted(p.name for p in cache.iterdir())
    assert names == sorted([f"{i:040x}.pdf" for i in (3, 4, 5)] + ["keep-me.txt"])


@pytest.mark.slow
def test_real_libreoffice_conversion(tmp_path: Path):
    if backends.find("soffice") is None:
        pytest.skip("沒有安裝 LibreOffice")
    import docx  # noqa: PLC0415
    import pikepdf  # noqa: PLC0415

    d = docx.Document()
    d.add_heading("預覽測試", 0)
    d.add_paragraph("這是一份用來測試 Office 預覽的文件。")
    src = tmp_path / "真的文件.docx"
    d.save(src)
    pdf = office.convert_to_pdf(src)
    with pikepdf.open(pdf) as doc:
        assert len(doc.pages) >= 1


# ------------------------------------------------------------------ 不能抓取文件裡連結的遠端資源


def test_profile_blocks_untrusted_links(tmp_path: Path, fake_soffice: Path, monkeypatch):
    """每次轉換的設定檔都要先寫好 BlockUntrustedRefererLinks=true。"""
    seen = tmp_path / "profile-copy.xcu"
    monkeypatch.setenv("FAKE_SOFFICE_COPY_PROFILE", str(seen))
    office.convert_to_pdf(_docx(tmp_path))
    text = seen.read_text("utf-8")
    assert 'oor:path="/org.openoffice.Office.Common/Security/Scripting"' in text
    assert '<prop oor:name="BlockUntrustedRefererLinks" oor:op="fuse"><value>true</value>' in text


def test_profile_helper_never_overwrites(tmp_path: Path):
    from quickseemd.previewer import soffice_local  # noqa: PLC0415

    profile = tmp_path / "profile"
    soffice_local.prepare_profile(profile)
    xcu = profile / "user" / "registrymodifications.xcu"
    assert xcu.is_file()
    with pytest.raises(FileExistsError):
        soffice_local.prepare_profile(profile)


def _odt_with_remote_image(path: Path, url: str) -> Path:
    import zipfile  # noqa: PLC0415

    content = f"""<?xml version="1.0" encoding="UTF-8"?>
<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0"
 xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0"
 xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0"
 xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0"
 xmlns:xlink="http://www.w3.org/1999/xlink" office:version="1.2">
<office:body><office:text><text:p>連結的圖片：
<draw:frame draw:name="img" svg:width="4cm" svg:height="3cm" text:anchor-type="as-char">
<draw:image xlink:href="{url}" xlink:type="simple" xlink:show="embed" xlink:actuate="onLoad"/>
</draw:frame></text:p></office:text></office:body></office:document-content>"""
    manifest = """<?xml version="1.0" encoding="UTF-8"?>
<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0">
<manifest:file-entry manifest:full-path="/" manifest:media-type="application/vnd.oasis.opendocument.text"/>
<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>
</manifest:manifest>"""
    with zipfile.ZipFile(path, "w") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), "application/vnd.oasis.opendocument.text")
        z.writestr("META-INF/manifest.xml", manifest, compress_type=zipfile.ZIP_DEFLATED)
        z.writestr("content.xml", content, compress_type=zipfile.ZIP_DEFLATED)
    return path


@pytest.mark.slow
def test_real_libreoffice_does_not_fetch_linked_images(tmp_path: Path):
    if backends.find("soffice") is None:
        pytest.skip("沒有安裝 LibreOffice")
    from local_http import RecordingServer  # noqa: PLC0415

    with RecordingServer() as server:
        doc = _odt_with_remote_image(tmp_path / "連結.odt", server.url("/image.png"))
        pdf = office.convert_to_pdf(doc)
        assert pdf.is_file()
        assert server.requests == []
