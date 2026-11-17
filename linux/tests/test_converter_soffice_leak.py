"""LibreOffice 不能把使用者資料夾裡的檔案嵌進輸出（SVG 的 <image href="file://…">）。

秘密圖片放在「像家目錄」的地方：測試檔所在的專案資料夾若在 /home、/mnt 等使用者資料根目錄底下就放那裡
（由測試自己建立的隱藏暫存資料夾，結束時自動清掉），否則放在 pytest 的暫存資料夾。
判斷方式：秘密圖片是一種特殊顏色，轉出的文件裡完全不能出現這個顏色。
"""

from __future__ import annotations

import base64
import tempfile
import zipfile
from collections.abc import Iterator
from pathlib import Path

import pytest
from PIL import Image

from quickseemd.core import backends, process, soffice
from quickseemd.core.converter import htmlsafe

SECRET_COLOR = (17, 211, 97)
USER_ROOTS = ("/home", "/root", "/media", "/mnt", "/srv")


@pytest.fixture
def secret_png(tmp_path: Path) -> Iterator[Path]:
    here = Path(__file__).resolve().parent
    parent = here if str(here).startswith(USER_ROOTS) else tmp_path
    with tempfile.TemporaryDirectory(prefix=".qs-secret-", dir=parent) as folder:
        path = Path(folder) / "secret.png"
        Image.new("RGB", (40, 40), SECRET_COLOR).save(path)
        yield path


def _svg(secret: Path) -> str:
    return (
        '<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" '
        'width="40" height="40">'
        f'<image href="{secret.as_uri()}" xlink:href="{secret.as_uri()}" width="40" height="40"/>'
        "</svg>"
    )


def _has_secret_color(pdf: Path, scratch: Path) -> bool:
    base = scratch / "render"
    args = [backends.require("pdftocairo"), "-png", "-r", "50", str(pdf), str(base)]
    process.run_checked(args, "pdftocairo")
    for page in scratch.glob("render*.png"):
        with Image.open(page) as img:
            colors = img.convert("RGB").getcolors(1 << 20) or []
            if any(c == SECRET_COLOR for _n, c in colors):
                return True
    return False


def _odt_with_svg(path: Path, secret: Path) -> Path:
    content = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<office:document-content xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
        'xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" '
        'xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0" '
        'xmlns:xlink="http://www.w3.org/1999/xlink" office:version="1.2">'
        "<office:body><office:text><text:p>圖：</text:p><text:p>"
        '<draw:frame svg:width="4cm" svg:height="4cm" text:anchor-type="as-char">'
        '<draw:image xlink:href="Pictures/leak.svg" xlink:type="simple" xlink:show="embed"/>'
        "</draw:frame></text:p></office:text></office:body></office:document-content>"
    )
    manifest = (
        '<?xml version="1.0" encoding="UTF-8"?>'
        '<manifest:manifest xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0" '
        'manifest:version="1.2">'
        '<manifest:file-entry manifest:full-path="/" '
        'manifest:media-type="application/vnd.oasis.opendocument.text"/>'
        '<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>'
        '<manifest:file-entry manifest:full-path="Pictures/leak.svg" '
        'manifest:media-type="image/svg+xml"/></manifest:manifest>'
    )
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("mimetype", "application/vnd.oasis.opendocument.text", zipfile.ZIP_STORED)
        z.writestr("content.xml", content)
        z.writestr("META-INF/manifest.xml", manifest)
        z.writestr("Pictures/leak.svg", _svg(secret))
    return path


# ------------------------------------------------------------------ htmlsafe


def test_htmlsafe_drops_svg_and_non_raster_data_uris(tmp_path: Path, secret_png: Path) -> None:
    svg_uri = "data:image/svg+xml;base64," + base64.b64encode(_svg(secret_png).encode()).decode()
    raster = "data:image/png;base64,iVBORw0KGgo="
    markup = (
        f'<p><img src="{svg_uri}"><img src="{raster}"><img src="data:text/html,<b>x</b>">'
        f'<img src="DATA:IMAGE/SVG+XML,<svg/>"><img src="data:image/jpeg;base64,/9j/">'
        f'<svg><image href="{svg_uri}"/><image href="{raster}"/>'
        f'<use xlink:href="{secret_png.as_uri()}"/><image href="leak.svg"/></svg></p>'
    )
    (tmp_path / "leak.svg").write_text(_svg(secret_png))
    clean = htmlsafe.sanitize(markup, tmp_path)
    assert "svg+xml" not in clean.lower() and "text/html" not in clean
    assert "file:" not in clean and "leak.svg" not in clean
    assert clean.count("data:image/png;base64,iVBORw0KGgo=") == 2
    assert "data:image/jpeg;base64,/9j/" in clean


# ------------------------------------------------------------------ LibreOffice 沙箱


def _bwrap_ready(tmp_path: Path) -> bool:
    return bool(soffice.sandbox_prefix(tmp_path))


@pytest.mark.slow
def test_odt_svg_cannot_embed_user_files(tmp_path: Path, secret_png: Path) -> None:
    if not _bwrap_ready(tmp_path):
        pytest.skip("這台沒有可用的 bwrap")
    odt = _odt_with_svg(tmp_path / "leak.odt", secret_png)
    work = tmp_path / "work"
    work.mkdir()
    pdf = soffice.convert(odt, "pdf", work)
    assert not _has_secret_color(pdf, tmp_path)


@pytest.mark.slow
def test_raw_html_with_svg_data_uri_cannot_embed_user_files(
    tmp_path: Path, secret_png: Path
) -> None:
    """就算 HTML 沒經過清理器直接交給 LibreOffice，沙箱也看不到使用者的資料夾。"""
    if not _bwrap_ready(tmp_path):
        pytest.skip("這台沒有可用的 bwrap")
    svg_uri = "data:image/svg+xml;base64," + base64.b64encode(_svg(secret_png).encode()).decode()
    page = tmp_path / "page.html"
    page.write_text(
        f'<html><body><p>x</p><img src="{svg_uri}" width="40" height="40"></body></html>'
    )
    work = tmp_path / "work"
    work.mkdir()
    pdf = soffice.convert(page, "pdf:writer_pdf_Export", work, infilter="HTML (StarWriter)")
    assert not _has_secret_color(pdf, tmp_path)


def test_sandbox_masks_user_data_roots(tmp_path: Path) -> None:
    if not _bwrap_ready(tmp_path):
        pytest.skip("這台沒有可用的 bwrap")
    prefix = soffice.sandbox_prefix(tmp_path)
    masked = [prefix[i + 1] for i, a in enumerate(prefix) if a == "--tmpfs"]
    for root in USER_ROOTS:
        if Path(root).exists():
            assert root in masked
    scratch_bind = prefix.index(str(tmp_path.resolve()))
    assert scratch_bind > max(prefix.index(r) for r in masked)  # 暫存資料夾在遮罩之後才掛回
    readable = soffice.with_readable(prefix, [tmp_path / "in.docx"])
    assert readable[-1] == "--" and str(tmp_path / "in.docx") in readable


def test_missing_bwrap_warns_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog
) -> None:  # noqa: ANN001
    import logging

    monkeypatch.setattr(soffice, "_warned_unsandboxed", False)
    monkeypatch.setattr(backends, "find", lambda key: None)
    with caplog.at_level(logging.WARNING, logger="quickseemd.core.soffice"):
        assert soffice.sandbox_prefix(tmp_path) == []
        assert soffice.sandbox_prefix(tmp_path) == []
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
