"""萬用轉換的格式目錄（對應原版 ConversionFormat.swift，順序與文字照抄）。"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FormatCategory:
    id: str
    title: str
    order: int
    icon: str  # 原版 SF Symbol


IMAGE = FormatCategory("image", "圖片", 0, "photo")
DOCUMENT = FormatCategory("document", "文件", 1, "doc.text")
VIDEO = FormatCategory("video", "影片", 2, "film")
AUDIO = FormatCategory("audio", "音訊", 3, "waveform")
DATA = FormatCategory("data", "資料", 4, "tablecells")
TEXT = FormatCategory("text", "文字編碼與換行", 5, "character.textbox")
ARCHIVE = FormatCategory("archive", "壓縮與封存", 6, "archivebox")
FORMAT_CATEGORIES = (IMAGE, DOCUMENT, VIDEO, AUDIO, DATA, TEXT, ARCHIVE)


@dataclass(frozen=True)
class Format:
    id: str
    label: str
    ext: str  # 空字串＝沿用原始檔名（文字編碼）或輸出資料夾（解壓縮）
    category: FormatCategory

    @property
    def display_name(self) -> str:
        return self.label


def _f(id_: str, label: str, ext: str, cat: FormatCategory) -> Format:
    return Format(id_, label, ext, cat)


FORMATS: tuple[Format, ...] = (
    # 圖片
    _f("png", "PNG", "png", IMAGE),
    _f("jpeg", "JPEG", "jpg", IMAGE),
    _f("heic", "HEIC", "heic", IMAGE),
    _f("avif", "AVIF", "avif", IMAGE),
    _f("webp", "WebP", "webp", IMAGE),
    _f("jxl", "JPEG XL", "jxl", IMAGE),
    _f("tiff", "TIFF", "tiff", IMAGE),
    _f("gif", "GIF", "gif", IMAGE),
    _f("bmp", "BMP", "bmp", IMAGE),
    _f("jp2", "JPEG 2000", "jp2", IMAGE),
    _f("ico", "ICO 圖示", "ico", IMAGE),
    _f("icns", "ICNS 圖示", "icns", IMAGE),
    _f("psd", "PSD", "psd", IMAGE),
    _f("tga", "TGA", "tga", IMAGE),
    _f("anim-gif", "GIF 動畫", "gif", IMAGE),
    # 文件
    _f("pdf", "PDF", "pdf", DOCUMENT),
    _f("docx", "Word (DOCX)", "docx", DOCUMENT),
    _f("doc", "Word 97 (DOC)", "doc", DOCUMENT),
    _f("odt", "OpenDocument (ODT)", "odt", DOCUMENT),
    _f("rtf", "RTF", "rtf", DOCUMENT),
    _f("rtfd", "RTFD（含圖片）", "rtfd", DOCUMENT),
    _f("html", "HTML", "html", DOCUMENT),
    _f("webarchive", "Web 封存檔", "webarchive", DOCUMENT),
    _f("txt", "純文字 (TXT)", "txt", DOCUMENT),
    _f("ocr-txt", "文字辨識 (OCR)", "txt", DOCUMENT),
    _f("md", "Markdown", "md", DOCUMENT),
    _f("epub", "EPUB 電子書", "epub", DOCUMENT),
    _f("tex", "LaTeX", "tex", DOCUMENT),
    _f("pptx", "PowerPoint (PPTX)", "pptx", DOCUMENT),
    _f("odp", "簡報 (ODP)", "odp", DOCUMENT),
    # 影片
    _f("mp4", "MP4 (H.264)", "mp4", VIDEO),
    _f("mp4-hevc", "MP4 (HEVC)", "mp4", VIDEO),
    _f("mov", "MOV (H.264)", "mov", VIDEO),
    _f("mov-hevc", "MOV (HEVC)", "mov", VIDEO),
    _f("prores", "MOV (ProRes 422)", "mov", VIDEO),
    _f("m4v", "M4V", "m4v", VIDEO),
    _f("webm", "WebM (VP9)", "webm", VIDEO),
    _f("mkv", "MKV", "mkv", VIDEO),
    _f("avi", "AVI", "avi", VIDEO),
    # 音訊
    _f("m4a", "M4A (AAC)", "m4a", AUDIO),
    _f("alac", "M4A (ALAC 無損)", "m4a", AUDIO),
    _f("aac", "AAC (ADTS)", "aac", AUDIO),
    _f("wav", "WAV", "wav", AUDIO),
    _f("aiff", "AIFF", "aiff", AUDIO),
    _f("caf", "CAF", "caf", AUDIO),
    _f("flac", "FLAC", "flac", AUDIO),
    _f("mp3", "MP3", "mp3", AUDIO),
    _f("ogg", "OGG Vorbis", "ogg", AUDIO),
    _f("opus", "Opus", "opus", AUDIO),
    # 資料
    _f("json", "JSON", "json", DATA),
    _f("json-pretty", "JSON（排版）", "json", DATA),
    _f("json-min", "JSON（壓縮）", "json", DATA),
    _f("plist", "Plist (XML)", "plist", DATA),
    _f("bplist", "Plist（二進位）", "plist", DATA),
    _f("yaml", "YAML", "yaml", DATA),
    _f("csv", "CSV", "csv", DATA),
    _f("tsv", "TSV", "tsv", DATA),
    _f("xlsx", "Excel (XLSX)", "xlsx", DATA),
    _f("ods", "試算表 (ODS)", "ods", DATA),
    _f("html-table", "HTML 表格", "html", DATA),
    # 文字編碼與換行
    _f("utf8", "UTF-8", "", TEXT),
    _f("utf8-bom", "UTF-8（含 BOM）", "", TEXT),
    _f("utf16", "UTF-16", "", TEXT),
    _f("big5", "Big5", "", TEXT),
    _f("gb18030", "GB18030", "", TEXT),
    _f("lf", "LF 換行（macOS／Linux）", "", TEXT),
    _f("crlf", "CRLF 換行（Windows）", "", TEXT),
    # 壓縮與封存
    _f("zip", "ZIP", "zip", ARCHIVE),
    _f("tgz", "TAR.GZ", "tar.gz", ARCHIVE),
    _f("7z", "7Z", "7z", ARCHIVE),
    _f("extract", "解壓縮到資料夾", "", ARCHIVE),
)

BY_ID: dict[str, Format] = {f.id: f for f in FORMATS}

ALIASES: dict[str, str] = {
    "jpg": "jpeg",
    "tif": "tiff",
    "htm": "html",
    "yml": "yaml",
    "tar.gz": "tgz",
    "targz": "tgz",
    "gz": "tgz",
    "aif": "aiff",
    "j2k": "jp2",
    "text": "txt",
    "markdown": "md",
    "ocr": "ocr-txt",
    "unzip": "extract",
    "folder": "extract",
    "hevc": "mp4-hevc",
    "gif-anim": "anim-gif",
    "xml-plist": "plist",
}

LOSSY_IMAGE_IDS = frozenset({"jpeg", "heic", "avif", "webp", "jxl", "jp2"})

# 輪盤的轉換格子排序（原版 WheelCatalog）
WHEEL_SORT: tuple[str, ...] = (
    "pdf",
    "docx",
    "jpeg",
    "png",
    "heic",
    "webp",
    "mp4",
    "anim-gif",
    "mov",
    "m4a",
    "mp3",
    "wav",
    "flac",
    "xlsx",
    "csv",
    "pptx",
    "txt",
    "html",
    "md",
    "json",
    "yaml",
    "avif",
    "tiff",
    "ico",
    "icns",
    "aiff",
    "zip",
    "tgz",
)


def format_by_id(format_id: str) -> Format | None:
    fid = format_id.lower()
    return BY_ID.get(ALIASES.get(fid, fid))
