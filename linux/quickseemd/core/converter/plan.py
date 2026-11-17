"""可轉換格式的規劃（對應原版 ConversionEngine.plan／unavailable）。

依檔案身分依序加入「格式＋路線」，同一個格式代碼只保留第一個（內建路線優先於外部引擎）。
Linux 版的差異：
- 「內建」影音靠 ffmpeg，文件靠 LibreOffice（Word 類）與 WeasyPrint（PDF）。
- 不提供 RTFD、Web 封存檔、DMG（Linux 沒有對應格式）。
- 單一 .xz 檔可以解壓縮（Python 內建 lzma）。
"""

from __future__ import annotations

from .. import backends, errors
from . import capabilities
from . import Target, Unavailable
from .formats import BY_ID
from .profile import Profile, json_is_array

DOCUMENT_OUTPUTS = ("pdf", "docx", "doc", "odt", "rtf", "html", "txt")
RICH_OUTPUTS = frozenset({"docx", "doc", "odt", "rtf"})
OFFICE_INPUT_KINDS = frozenset({"rtf", "docx", "doc", "odt"})
AF_READABLE_EXTS = frozenset(
    {"wav", "aif", "aiff", "caf", "m4a", "mp3", "aac", "flac", "au", "snd", "amr", "ac3"}
)
TEXT_SIZE_LIMIT = 8_000_000
EXTERNAL_TITLES = {
    "ffmpeg": "ffmpeg",
    "pandoc": "Pandoc",
    "soffice": "LibreOffice",
    "magick": "ImageMagick",
}


class _Plan:
    def __init__(self, p: Profile) -> None:
        self.p = p
        self.targets: list[Target] = []
        self.seen: set[str] = set()

    def add(self, fid: str, route: str, note: str | None = None, backend: str | None = None):
        if fid in self.seen or fid not in BY_ID:
            return
        self.seen.add(fid)
        self.targets.append(Target(BY_ID[fid], route, note, backend))

    def ext_of(self, fid: str) -> str:
        return BY_ID[fid].ext


def _tools() -> dict[str, bool]:
    return {
        "ffmpeg": capabilities.ffmpeg_ready(),
        "pandoc": backends.available("pandoc"),
        "soffice": backends.available("soffice"),
        "magick": backends.available("magick"),
        "tesseract": backends.available("tesseract"),
        "weasyprint": backends.has_module("weasyprint"),
        "pdftocairo": backends.available("pdftocairo"),
        "pdftotext": backends.available("pdftotext"),
        "bsdtar": backends.available("bsdtar"),
    }


# ---------------------------------------------------------------- 各類輸入


def _images(pl: _Plan, tools: dict[str, bool]) -> None:
    p = pl.p
    if p.is_image:
        for fid in capabilities.native_image_formats():
            if fid == p.image_id and not (p.is_animated and fid == "gif"):
                pl.add(fid, "image", "重新壓縮或縮小")
            elif p.is_animated and fid == "gif":
                pl.add(fid, "image", "保留動畫")
            else:
                pl.add(fid, "image", "只取第一格" if p.is_animated and fid != "tiff" else None)
        if p.is_animated and tools["ffmpeg"]:
            pl.add("mp4", "animatedToVideo", "動畫轉影片")
            pl.add("mov", "animatedToVideo", "動畫轉影片")
        pl.add("pdf", "imageToPDF")
        if tools["tesseract"]:
            pl.add("ocr-txt", "ocr", "辨識圖片中的文字")
        if tools["magick"]:
            writable = capabilities.magick_writable()
            for fid in ("webp", "jxl"):
                if fid in writable:
                    pl.add(fid, "external", backend="magick")
    elif p.is_magick_candidate and tools["magick"]:
        writable = capabilities.magick_writable()
        for fid in ("png", "jpeg", "tiff", "webp"):
            if fid in writable or fid == "jpeg" and "jpg" in writable:
                pl.add(fid, "external", backend="magick")


def _pdf(pl: _Plan, tools: dict[str, bool]) -> None:
    if not pl.p.is_pdf:
        return
    if tools["pdftocairo"]:
        heic = "heic" in capabilities.native_image_formats()
        for fid in ("png", "jpeg", "tiff", "heic"):
            if fid != "heic" or heic:
                pl.add(fid, "pdfToImages", "每頁一張")
    if tools["pdftotext"]:
        pl.add("txt", "pdfToText", "掃描檔自動 OCR")
        for fid in ("docx", "rtf", "html", "odt"):
            if fid == "html" or tools["soffice"]:
                pl.add(fid, "document", "僅文字與基本格式")


def _markdown(pl: _Plan, tools: dict[str, bool]) -> None:
    if not pl.p.is_markdown:
        return
    pl.add("html", "document")
    for fid in ("pdf", "docx", "rtf", "odt", "doc"):
        if (fid == "pdf" and tools["weasyprint"]) or (fid in RICH_OUTPUTS and tools["soffice"]):
            pl.add(fid, "document")
    if tools["pandoc"]:
        pl.add("epub", "external", backend="pandoc")
        pl.add("tex", "external", backend="pandoc")


def document_output_ok(kind: str, fid: str, tools: dict[str, bool]) -> bool:
    """Linux 上這種文件能不能輸出成這個格式（看 LibreOffice 與 WeasyPrint 在不在）。"""
    if kind in OFFICE_INPUT_KINDS:
        return tools["soffice"]
    if fid == "pdf":
        return tools["weasyprint"]
    if fid == "html":
        return True
    return tools["soffice"]


def _documents(pl: _Plan, tools: dict[str, bool]) -> None:
    p = pl.p
    kind = p.document_kind
    if kind is not None and not p.is_markdown and (p.data_kind is None or kind != "plain"):
        source = "txt" if kind == "plain" else kind
        for fid in DOCUMENT_OUTPUTS:
            if fid == source or (kind == "plain" and fid == "txt"):
                continue
            if document_output_ok(kind, fid, tools):
                pl.add(fid, "document")
        if tools["pandoc"] and p.pandoc_reader is not None:
            for fid in ("md", "epub", "tex"):
                pl.add(fid, "external", backend="pandoc")
    elif tools["pandoc"] and p.pandoc_reader is not None and not p.is_markdown:
        # pandoc 才讀得懂的格式（EPUB、LaTeX、rst…）
        for fid in ("docx", "html", "odt", "md", "epub", "tex"):
            if pl.ext_of(fid) != p.ext:
                pl.add(fid, "external", backend="pandoc")


def _data(pl: _Plan) -> None:
    kind = pl.p.data_kind
    if kind == "json":
        for fid in ("json-pretty", "json-min", "yaml", "plist", "bplist"):
            pl.add(fid, "data")
        if json_is_array(pl.p.path):
            for fid in ("csv", "xlsx", "tsv", "html-table"):
                pl.add(fid, "data")
    elif kind == "plist":
        for fid in ("json", "yaml", "plist", "bplist"):
            pl.add(fid, "data")
    elif kind in ("csv", "tsv"):
        for fid in ("xlsx", "json", "yaml", "html-table"):
            pl.add(fid, "data")
        pl.add("tsv" if kind == "csv" else "csv", "data")
    elif kind == "xlsx":
        for fid in ("csv", "json", "tsv", "html-table", "yaml"):
            pl.add(fid, "data", "多個工作表各存一檔" if fid in ("csv", "tsv") else None)


def _text(pl: _Plan) -> None:
    p = pl.p
    if not p.is_plain_text:
        return
    from . import textcodec  # noqa: PLC0415

    current = None
    try:
        if p.path.stat().st_size <= TEXT_SIZE_LIMIT:
            current = textcodec.decode_file(p.path)
    except (OSError, errors.QSError):
        current = None  # 解不出編碼時照原版列出全部選項
    enc = current.encoding if current else ""
    utf8 = bool(current) and textcodec.is_utf8(enc)
    if not (utf8 and not current.had_bom):
        pl.add("utf8", "text")
    if not (utf8 and current.had_bom):
        excel = p.data_kind in ("csv", "tsv")
        pl.add("utf8-bom", "text", "Windows Excel 開啟不亂碼" if excel else None)
    if not (current and textcodec.is_utf16(enc)):
        pl.add("utf16", "text")
    if not (current and textcodec.is_big5(enc)):
        pl.add("big5", "text")
    if not (current and textcodec.normalized(enc) == "gb18030"):
        pl.add("gb18030", "text")
    ending = current.line_ending if current else None
    if ending not in (textcodec.LineEnding.LF, textcodec.LineEnding.NONE):
        pl.add("lf", "text")
    if ending is not textcodec.LineEnding.CRLF:
        pl.add("crlf", "text")


def _video(pl: _Plan, tools: dict[str, bool]) -> None:
    p = pl.p
    if not p.is_video or not p.is_av_readable:
        return
    for fid in capabilities.native_video_formats():
        pl.add(fid, "video")
    pl.add("anim-gif", "videoToGIF")
    pl.add("png", "videoFrame", "擷取單一畫面")
    pl.add("jpeg", "videoFrame", "擷取單一畫面")
    if p.has_audio_track:
        for fid in capabilities.native_audio_formats():
            pl.add(fid, "audio", "擷取音軌")
    for fid in ("webm", "mkv", "avi", "mp4", "mov", "mp4-hevc", "anim-gif"):
        if pl.ext_of(fid) != p.ext or fid == "anim-gif":
            pl.add(fid, "external", backend="ffmpeg")
    if p.has_audio_track:
        for fid in ("mp3", "ogg", "opus", "m4a", "wav", "flac"):
            pl.add(fid, "external", "擷取音軌", backend="ffmpeg")


def _audio(pl: _Plan, tools: dict[str, bool]) -> None:
    p = pl.p
    if not p.is_audio:
        return
    if p.is_av_readable or p.ext in AF_READABLE_EXTS:
        for fid in capabilities.native_audio_formats():
            if pl.ext_of(fid) == p.ext and fid not in ("m4a", "alac"):
                continue
            if p.ext == "aif" and fid == "aiff":
                continue
            pl.add(fid, "audio")
    if tools["ffmpeg"] and p.is_av_readable:
        for fid in ("mp3", "ogg", "opus", "m4a", "flac", "wav", "aiff"):
            if pl.ext_of(fid) != p.ext:
                pl.add(fid, "external", backend="ffmpeg")


def _office(pl: _Plan, tools: dict[str, bool]) -> None:
    p = pl.p
    if not tools["soffice"] or p.office_kind is None:
        return
    lists = {
        "word": ("pdf", "docx", "odt"),
        "spreadsheet": ("pdf", "xlsx", "ods", "csv"),
        "presentation": ("pdf", "pptx", "odp"),
    }
    if p.office_kind in lists:
        for fid in lists[p.office_kind]:
            if pl.ext_of(fid) != p.ext:
                pl.add(fid, "external", backend="soffice")
        return
    pl.add("pdf", "external", backend="soffice")
    extra = {"pages": "docx", "numbers": "xlsx", "key": "pptx"}.get(p.ext)
    if extra:
        pl.add(extra, "external", backend="soffice")


NEEDS_BSDTAR = frozenset({"sevenZip", "rar", "xar", "cpio", "iso"})


def _archives(pl: _Plan, tools: dict[str, bool]) -> None:
    p = pl.p
    kind = p.archive_kind
    can_7z = tools["bsdtar"]
    if kind is not None:
        if kind in NEEDS_BSDTAR and not tools["bsdtar"]:
            return
        pl.add("extract", "archive")
        if kind not in ("gzip", "bzip2", "xz"):
            if kind != "zip":
                pl.add("zip", "archive", "重新封裝")
            if kind != "tar":
                pl.add("tgz", "archive", "重新封裝")
            if kind != "sevenZip" and can_7z:
                pl.add("7z", "archive", "重新封裝")
    elif p.path.exists():
        pl.add("zip", "archive")
        pl.add("tgz", "archive")
        if can_7z:
            pl.add("7z", "archive")


def plan(p: Profile) -> list[Target]:
    """依檔案身分規劃所有可行的格式與路線，同一格式只保留第一個（內建優先）。"""
    tools = _tools()
    pl = _Plan(p)
    if not p.is_dir:
        _images(pl, tools)
        _pdf(pl, tools)
        _markdown(pl, tools)
        _documents(pl, tools)
        _data(pl)
        _text(pl)
        _video(pl, tools)
        _audio(pl, tools)
        _office(pl, tools)
    _archives(pl, tools)
    return pl.targets


# ---------------------------------------------------------------- 目前做不到的格式


def _hint(key: str) -> str:
    return f"（{backends.install_hint(key)}）"


def _media_hints(p: Profile, tools: dict[str, bool]) -> list[Unavailable]:
    if tools["ffmpeg"] or p.is_av_readable:
        return []
    hint = _hint("ffmpeg")
    if p.is_video:
        reason = f"這台電腦無法解碼 .{p.ext} 影片，需要安裝 ffmpeg{hint}。"
        return [Unavailable("所有影片與音訊格式", reason, "ffmpeg")]
    if p.is_audio:
        reason = f"這台電腦無法解碼 .{p.ext} 音訊，需要安裝 ffmpeg{hint}。"
        return [Unavailable("所有音訊格式", reason, "ffmpeg")]
    return []


def _image_hints(p: Profile, tools: dict[str, bool]) -> list[Unavailable]:
    out: list[Unavailable] = []
    if p.is_image and not tools["magick"]:
        native = set(capabilities.native_image_formats())
        missing = [BY_ID[f].display_name for f in ("webp", "jxl") if f not in native]
        if missing:
            reason = f"這台電腦無法寫入這些格式，需要安裝 ImageMagick{_hint('magick')}。"
            out.append(Unavailable("、".join(missing), reason, "magick"))
    if p.is_image and not tools["tesseract"]:
        reason = f"需要安裝 Tesseract{_hint('tesseract')}。"
        out.append(Unavailable("文字辨識 (OCR)", reason, "tesseract"))
    if p.is_magick_candidate and not p.is_image and not tools["magick"]:
        reason = f"這台電腦無法讀取 .{p.ext}，需要安裝 ImageMagick{_hint('magick')}。"
        out.append(Unavailable("所有圖片格式", reason, "magick"))
    return out


def _office_hints(p: Profile, tools: dict[str, bool]) -> list[Unavailable]:
    if tools["soffice"]:
        return []
    out: list[Unavailable] = []
    kind = p.office_kind
    if kind == "spreadsheet" and p.data_kind != "csv":
        what = "PDF、ODS" if p.data_kind == "xlsx" else "PDF、XLSX、CSV"
        reason = "試算表排版需要 LibreOffice（sudo apt install libreoffice-calc-nogui）。"
        out.append(Unavailable(what, reason, "soffice"))
    elif kind == "presentation":
        reason = "簡報需要 LibreOffice 才能轉換（sudo apt install libreoffice-impress-nogui）。"
        out.append(Unavailable("PDF、PPTX、ODP", reason, "soffice"))
    elif kind == "iWork":
        reason = f"Pages／Numbers／Keynote 需要 LibreOffice{_hint('soffice')}。"
        out.append(Unavailable("PDF、Office 格式", reason, "soffice"))
    has_doc = p.document_kind is not None or p.is_markdown or p.is_pdf
    if has_doc and not (p.document_kind == "plain" and p.data_kind is not None):
        what = "所有文件格式" if p.document_kind in OFFICE_INPUT_KINDS else "Word、ODT、RTF"
        reason = f"文件格式轉換需要 LibreOffice{_hint('soffice')}。"
        out.append(Unavailable(what, reason, "soffice"))
    return out


def unavailable(p: Profile) -> list[Unavailable]:
    """這個檔案「想轉但目前不行」的格式與原因（只列出真的缺少的工具）。"""
    tools = _tools()
    out = _media_hints(p, tools) + _image_hints(p, tools)
    if p.is_pdf:
        reason = "PDF 轉 Word 只能保留文字與基本格式，無法重建原始版面。"
        out.append(Unavailable("可編輯的 Word 版面", reason, None))
    if (p.is_markdown or p.pandoc_reader is not None) and not tools["pandoc"]:
        what = "EPUB、LaTeX" if p.is_markdown else "Markdown、EPUB、LaTeX"
        out.append(Unavailable(what, f"需要安裝 Pandoc{_hint('pandoc')}。", "pandoc"))
    out += _office_hints(p, tools)
    if p.data_kind == "yaml":
        out.append(Unavailable("JSON、Plist", "目前不支援讀取 YAML（只能輸出 YAML）。", None))
    return out
