"""文字類與單一壓縮檔的頁面（對應原版 QSPreviewEngine 的 codePage／markdownPage／
binaryPlistPage／gzipPage／xzPage）。"""

from __future__ import annotations

import unicodedata
from collections import Counter

from . import archive, archiveview, binary, detect, fmt, highlight, jsonview, markdown, plist
from .langbase import Language
from .languages import BY_ID
from .page import Chip, FileInfo, Notice, Page, escape
from .text import DecodedText, decode, looks_binary

TEXT_LIMIT = 2 * 1024 * 1024
MD_TRUNCATED = f"文件很長，只排版前 {fmt.number(markdown.MAX_LINES)} 行，其餘以純文字顯示"
MD_DEGRADED = "部分段落太長或太複雜，以純文字顯示"


def size_notice(info: FileInfo, decoded: DecodedText) -> Notice | None:
    if not decoded.truncated:
        return None
    return Notice(
        f"檔案很大（{fmt.bytes_label(info.size)}），只顯示前 {fmt.bytes_label(TEXT_LIMIT)}",
        warn=True,
    )


def _json_chips(
    page: Page, text: str, decoded: DecodedText, lang: Language
) -> tuple[str, list[Chip]]:
    chips: list[Chip] = []
    valid = True
    if not decoded.truncated and lang.id == "json":
        summary, error = jsonview.inspect(text)
        if summary:
            chips += [Chip("✓ 有效 JSON", "ok"), Chip(summary)]
        if error:
            valid = False
            chips.append(Chip("JSON 格式錯誤", "err"))
            page.notices.append(Notice(error, warn=True))
    if valid and jsonview.looks_minified(text):
        pretty = jsonview.pretty_print(text)
        if pretty is not None:
            text = pretty
            chips.append(Chip("已自動排版"))
    return text, chips


def code_page(
    info: FileInfo,
    decoded: DecodedText,
    language: Language | None = None,
    extra_chips: tuple[Chip, ...] = (),
) -> Page:
    """程式碼／文字頁：JSON 驗證與自動排版、行數、編碼、換行種類。"""
    text = decoded.text
    lang = language or detect.detect(info, text)
    page = Page(kind=lang.name)
    chips: list[Chip] = []
    if lang.id in ("json", "jsonc"):
        text, chips = _json_chips(page, text, decoded, lang)
    html, lines = highlight.code_view(text, lang)
    page.chips = (
        chips
        + list(extra_chips)
        + [
            Chip(f"{fmt.number(lines)} 行"),
            Chip(decoded.encoding),
            Chip(decoded.line_ending),
        ]
    )
    if not text:
        page.notices.append(Notice("這是空白檔案"))
    notice = size_notice(info, decoded)
    if notice:
        page.notices.append(notice)
    page.body = html
    page.size = (820, 680) if lang.wrap else (920, 700)
    return page


def _is_word_char(ch: str) -> bool:
    cat = unicodedata.category(ch)
    return cat[0] in ("L", "M") or cat == "Nd"


def letter_count(text: str) -> int:
    """字母（含附加符號）與十進位數字的數量；先統計不同字元再分類，大檔也快。"""
    return sum(n for ch, n in Counter(text).items() if _is_word_char(ch))


def markdown_page(info: FileInfo, decoded: DecodedText) -> Page:
    result = markdown.render(decoded.text, None if info.is_virtual else info.path.parent)
    page = Page(kind="Markdown", size=(900, 780))
    page.chips = [Chip(f"約 {fmt.number(letter_count(decoded.text))} 字"), Chip(decoded.encoding)]
    notice = size_notice(info, decoded)
    if notice:
        page.notices.append(notice)
    if result.truncated:
        page.notices.append(Notice(MD_TRUNCATED, warn=True))
    if result.degraded:
        page.notices.append(Notice(MD_DEGRADED, warn=True))
    page.body = f'<article class="md">{result.html}</article>'
    page.attachments = result.attachments
    return page


def binary_plist_page(info: FileInfo) -> Page | None:
    xml = plist.to_xml(info)
    if xml is None:
        return None
    decoded = DecodedText(xml, "UTF-8", "LF", False)
    page = code_page(info, decoded, BY_ID["xml"], (Chip("二進位 plist，已轉為 XML 顯示"),))
    page.kind = "屬性列表 (plist)"
    return page


def _inner_section(inner: bytes, inner_name: str) -> str:
    decoded = None if looks_binary(inner) else decode(inner, len(inner) >= TEXT_LIMIT)
    if decoded is not None:
        lang = detect.detect(FileInfo.virtual(inner_name), decoded.text)
        title = f'<div class="section-title">解壓縮後的內容 · {escape(lang.name)}</div>'
        return title + highlight.code_view(decoded.text, lang)[0]
    if inner:
        return binary.hex_section(inner[: binary.HEX_LIMIT], "解壓縮後的內容（十六進位）")
    return ""


def compressed_single_page(
    info: FileInfo, inner: bytes, inner_name: str, rows: list[tuple[str, str]], fmt_name: str
) -> Page:
    panel, atts = binary.info_panel(info, rows + binary.standard_rows(info), fmt_name)
    page = Page(kind=fmt_name, show_icon=False, size=(860, 680))
    page.attachments = atts
    page.body = panel + _inner_section(inner, inner_name)
    return page


def _strip_ext(name: str) -> str:
    dot = name.rfind(".")
    return name[:dot] if dot > 0 else name


def gzip_page(info: FileInfo) -> Page | None:
    gz = archive.gzip_info(info.path, info.size)
    if gz is None:
        return None
    inner = archive.decompress_head(info.path, "gzip", gz.header_length, TEXT_LIMIT)
    if archive.looks_like_tar(inner):
        listing = archive.read_compressed_tar(info.path, "gzip", gz.header_length, "TAR.GZ")
        if listing is not None:
            return archiveview.render(listing, info)
    inner_name = gz.original_name or _strip_ext(info.name)
    rows = [("壓縮格式", "gzip"), ("原始檔名", inner_name)]
    if gz.uncompressed_size is not None:
        rows.append(("原始大小", fmt.bytes_label(gz.uncompressed_size)))
        if gz.uncompressed_size > 0:
            ratio = int((1 - info.size / gz.uncompressed_size) * 100)
            rows.append(("壓縮率", f"{max(0, ratio)}%"))
    return compressed_single_page(info, inner, inner_name, rows, "gzip 壓縮檔")


def xz_page(info: FileInfo) -> Page | None:
    inner = archive.decompress_head(info.path, "xz", 0, TEXT_LIMIT)
    if not inner:
        return None
    if archive.looks_like_tar(inner):
        listing = archive.read_compressed_tar(info.path, "xz", 0, "TAR.XZ")
        if listing is not None:
            return archiveview.render(listing, info)
    inner_name = _strip_ext(info.name)
    rows = [("壓縮格式", "xz"), ("原始檔名", inner_name)]
    return compressed_single_page(info, inner, inner_name, rows, "xz 壓縮檔")
