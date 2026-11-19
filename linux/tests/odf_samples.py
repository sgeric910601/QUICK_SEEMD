"""測試用的 ODF 文件：內容會試著連到網址（連結的圖片、WEBSERVICE、外部參照、連結的工作表）。"""

from __future__ import annotations

import zipfile
from pathlib import Path
from xml.sax.saxutils import quoteattr

NS = (
    'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
    'xmlns:text="urn:oasis:names:tc:opendocument:xmlns:text:1.0" '
    'xmlns:table="urn:oasis:names:tc:opendocument:xmlns:table:1.0" '
    'xmlns:draw="urn:oasis:names:tc:opendocument:xmlns:drawing:1.0" '
    'xmlns:svg="urn:oasis:names:tc:opendocument:xmlns:svg-compatible:1.0" '
    'xmlns:xlink="http://www.w3.org/1999/xlink" '
    'xmlns:of="urn:oasis:names:tc:opendocument:xmlns:of:1.2" office:version="1.2"'
)
ODT = "application/vnd.oasis.opendocument.text"
ODS = "application/vnd.oasis.opendocument.spreadsheet"


def _settings(link_update_mode: int) -> str:
    """文件自己要求的連結更新方式（0 永不、1 手動、2 自動、3 跟隨全域設定）。"""
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<office:document-settings '
        'xmlns:office="urn:oasis:names:tc:opendocument:xmlns:office:1.0" '
        'xmlns:config="urn:oasis:names:tc:opendocument:xmlns:config:1.0" office:version="1.2">'
        '<office:settings><config:config-item-set config:name="ooo:configuration-settings">'
        '<config:config-item config:name="LinkUpdateMode" config:type="short">'
        f"{link_update_mode}</config:config-item></config:config-item-set></office:settings>"
        "</office:document-settings>"
    )


def _manifest(mime: str, with_settings: bool) -> str:
    entries = [
        f'<manifest:file-entry manifest:full-path="/" manifest:media-type="{mime}"/>',
        '<manifest:file-entry manifest:full-path="content.xml" manifest:media-type="text/xml"/>',
    ]
    if with_settings:
        entries.append(
            '<manifest:file-entry manifest:full-path="settings.xml" manifest:media-type="text/xml"/>'
        )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n<manifest:manifest '
        'xmlns:manifest="urn:oasis:names:tc:opendocument:xmlns:manifest:1.0">'
        + "".join(entries)
        + "</manifest:manifest>"
    )


def _write(path: Path, mime: str, body: str, link_update_mode: int | None = None) -> Path:
    content = f'<?xml version="1.0" encoding="UTF-8"?>\n<office:document-content {NS}>{body}'
    content += "</office:document-content>"
    deflate = zipfile.ZIP_DEFLATED
    with zipfile.ZipFile(path, "x") as z:
        z.writestr(zipfile.ZipInfo("mimetype"), mime)
        manifest = _manifest(mime, link_update_mode is not None)
        z.writestr("META-INF/manifest.xml", manifest, compress_type=deflate)
        z.writestr("content.xml", content, compress_type=deflate)
        if link_update_mode is not None:
            z.writestr("settings.xml", _settings(link_update_mode), compress_type=deflate)
    return path


def odt_with_remote_image(path: Path, url: str) -> Path:
    body = (
        "<office:body><office:text><text:p>連結的圖片："
        '<draw:frame draw:name="img" svg:width="4cm" svg:height="3cm" text:anchor-type="as-char">'
        f'<draw:image xlink:href={quoteattr(url)} xlink:type="simple" xlink:show="embed" '
        'xlink:actuate="onLoad"/></draw:frame></text:p></office:text></office:body>'
    )
    return _write(path, ODT, body)


def ods_with_formula(path: Path, formula: str, link_update_mode: int | None = None) -> Path:
    """一格公式、沒有存好的計算結果（載入或輸出時可能被重新計算）。"""
    cell = f"<table:table-cell table:formula={quoteattr(formula)}><text:p/></table:table-cell>"
    body = (
        '<office:body><office:spreadsheet><table:table table:name="Sheet1">'
        f"<table:table-row>{cell}</table:table-row></table:table>"
        "</office:spreadsheet></office:body>"
    )
    return _write(path, ODS, body, link_update_mode)


def ods_with_linked_sheet(path: Path, url: str, link_update_mode: int | None = None) -> Path:
    """整張工作表連結到網址上的另一份文件（載入時可能更新連結）。"""
    body = (
        '<office:body><office:spreadsheet><table:table table:name="Linked">'
        f'<table:table-source xlink:type="simple" xlink:href={quoteattr(url)} '
        'table:filter-name="calc8" table:mode="copy-results-only" table:table-name="Sheet1"/>'
        "<table:table-row><table:table-cell><text:p>x</text:p></table:table-cell>"
        "</table:table-row></table:table></office:spreadsheet></office:body>"
    )
    return _write(path, ODS, body, link_update_mode)
