"""輸入檔分析（對應原版 CVInputProfile／CVInputInspector）。

同一個檔案可能同時有多種身分（CSV 也是純文字、GIF 也可以是動畫）。原版用 UTType，
這裡用 Gio 的 MIME 類型加上副檔名表（副檔名表照抄原版），影音能否解碼改用 ffprobe 判斷。
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, replace
from pathlib import Path

from .. import backends, filetypes, process
from . import capabilities

log = logging.getLogger(__name__)

MARKDOWN_EXTS = frozenset({"md", "markdown", "mdown", "mkd", "mkdn", "mdwn", "mdtxt"})
FFMPEG_AUDIO_EXTS = frozenset(
    {"wma", "ogg", "oga", "opus", "ape", "mka", "dts", "ac3", "amr", "wv", "tta", "spx"}
)
FFMPEG_VIDEO_EXTS = frozenset(
    {"mkv", "webm", "flv", "wmv", "ogv", "avi", "mpg", "mpeg", "ts", "m2ts", "mts", "3gp"}
    | {"vob", "rm", "rmvb", "asf", "divx", "f4v", "mod"}
)
# 原版的 ImageMagick 專用副檔名；eps／ps／ai 在 Linux 會被 ImageMagick 的安全政策擋下，
# jxr／wdp 要靠外部解碼程式（delegate），我們的政策一律禁止，所以也不列入
MAGICK_ONLY_EXTS = frozenset({"xcf", "pcx", "ppm", "pgm", "xpm", "xbm", "qoi"})
POSTSCRIPT_EXTS = frozenset({"eps", "ps", "ai", "epsf", "epsi"})
HEIF_EXTS = frozenset({"heic", "heif", "hif", "avif"})
SVG_EXTS = frozenset({"svg", "svgz"})
# 這些副檔名本質上是 zip，但不該當成一般壓縮檔
ZIP_BASED_DOCS = frozenset(
    {"docx", "xlsx", "pptx", "odt", "ods", "odp", "epub", "ipa", "jar", "apk"}
    | {"pages", "numbers", "key"}
)
COMPOUND_TAR = ("tar.gz", "tar.bz2", "tar.xz", "tar.zst", "tar.lz")
PANDOC_READERS = {
    "docx": "docx",
    "odt": "odt",
    "html": "html",
    "htm": "html",
    "xhtml": "html",
    "epub": "epub",
    "tex": "latex",
    "latex": "latex",
    "rst": "rst",
    "org": "org",
    "ipynb": "ipynb",
    "rtf": "rtf",
    "textile": "textile",
    "fb2": "fb2",
    "opml": "opml",
    "dbk": "docbook",
    "typ": "typst",
}
OFFICE_KINDS = {
    **dict.fromkeys(("doc", "docx", "docm", "odt", "rtf", "wpd"), "word"),
    **dict.fromkeys(("xls", "xlsx", "xlsm", "ods", "csv"), "spreadsheet"),
    **dict.fromkeys(("ppt", "pptx", "pps", "ppsx", "odp"), "presentation"),
    **dict.fromkeys(("pages", "numbers", "key"), "iWork"),
}
# Pillow 格式名稱 → 圖片格式代碼（判斷「與來源相同」用）
PILLOW_IDS = {
    "PNG": "png",
    "JPEG": "jpeg",
    "MPO": "jpeg",
    "WEBP": "webp",
    "TIFF": "tiff",
    "GIF": "gif",
    "BMP": "bmp",
    "DIB": "bmp",
    "JPEG2000": "jp2",
    "ICO": "ico",
    "ICNS": "icns",
    "PSD": "psd",
    "TGA": "tga",
}
ANIMATABLE = frozenset({"GIF", "PNG", "WEBP"})
# Pillow 不該拿來開的格式（MPEG 外掛會把影片當成圖片；EPS 會呼叫 Ghostscript）
PILLOW_REFUSED = frozenset({"MPEG", "EPS"})
GENERIC_MIMES = frozenset({"application/octet-stream", "application/x-zerosize", ""})
_CODE_EXCLUDED = frozenset(
    {"text/plain", "text/markdown", "text/x-markdown", "text/csv"}
    | {"text/tab-separated-values", "text/x-log"}
)
PROBE_TIMEOUT = 30.0


@dataclass(frozen=True)
class MediaInfo:
    has_video: bool
    has_audio: bool
    duration: float | None


@dataclass(frozen=True)
class Profile:
    path: Path
    mime: str
    ext: str
    is_dir: bool = False
    is_image: bool = False
    is_svg: bool = False
    is_animated: bool = False
    frame_count: int = 0
    image_id: str | None = None  # 來源圖片格式代碼（png、jpeg…）
    is_pdf: bool = False
    document_kind: str | None = None  # plain、rtf、html、doc、docx、odt
    is_markdown: bool = False
    is_plain_text: bool = False
    is_source_code: bool = False
    data_kind: str | None = None  # json、plist、csv、tsv、xlsx、yaml
    is_audio: bool = False
    is_video: bool = False
    is_av_readable: bool = False
    has_audio_track: bool = True
    duration: float | None = None
    archive_kind: str | None = None  # zip、tar、sevenZip、rar、xar、cpio、iso、gzip、bzip2、xz
    office_kind: str | None = None  # word、spreadsheet、presentation、iWork
    pandoc_reader: str | None = None
    is_ffmpeg_candidate: bool = False
    is_magick_candidate: bool = False

    @property
    def name(self) -> str:
        return self.path.name

    @property
    def kind_label(self) -> str:
        """主要身分的簡短說明。"""
        if self.is_dir:
            return "資料夾"
        if self.mime not in GENERIC_MIMES:
            desc = filetypes.describe(self.mime)
            if desc and desc != self.mime:
                return desc
        return f"{self.ext.upper()} 檔案" if self.ext else "檔案"


# ---------------------------------------------------------------- 小工具


def _gio():
    try:
        from gi.repository import Gio  # noqa: PLC0415

        return Gio
    except (ImportError, ValueError):
        return None


def _is_a(mime: str, parent: str) -> bool:
    gio = _gio()
    if gio is None:
        return mime == parent
    return mime == parent or bool(gio.content_type_is_a(mime, parent))


def detect_mime(path: Path) -> str:
    """Gio 的 MIME；空檔或判斷不出時改用副檔名猜（原版：動態類型改用副檔名的類型）。"""
    mime = filetypes.mime_of(path)
    if mime in GENERIC_MIMES:
        gio = _gio()
        if gio is not None:
            guessed, _uncertain = gio.content_type_guess(path.name, None)
            guessed = gio.content_type_get_mime_type(guessed) or guessed
            if guessed and guessed not in GENERIC_MIMES:
                return guessed
    return mime


def file_ext(path: Path) -> str:
    return path.suffix[1:].lower() if path.suffix else ""


def read_head(path: Path, count: int) -> bytes | None:
    try:
        with open(path, "rb") as fh:
            return fh.read(count)
    except OSError:
        return None


def looks_like_text(path: Path) -> bool:
    """看前 8192 位元組：空檔或 UTF-16 BOM 是文字；有 NUL 是二進位；控制字元要少於 1%。"""
    head = read_head(path, 8192)
    if head is None:
        return False
    if not head:
        return True
    if head.startswith((b"\xff\xfe", b"\xfe\xff")):
        return True
    if b"\x00" in head:
        return False
    control = sum(1 for b in head if b < 0x09 or (0x0D < b < 0x20 and b != 0x1B))
    return control * 100 < len(head)


def json_is_array(path: Path) -> bool:
    """JSON 最外層是否為陣列（略過空白與 BOM 位元組）。"""
    for b in read_head(path, 4096) or b"":
        if b in (0x20, 0x09, 0x0A, 0x0D, 0xEF, 0xBB, 0xBF):
            continue
        return b == ord("[")
    return False


# ---------------------------------------------------------------- 各種身分


def _pillow_image(path: Path) -> dict | None:
    """用 Pillow 開檔確認真的是圖片（只讀檔頭與影格數）。"""
    try:
        from PIL import Image  # noqa: PLC0415
    except ImportError:
        return None
    Image.init()
    allowed = [f for f in Image.OPEN if f not in PILLOW_REFUSED]
    try:
        with Image.open(path, formats=allowed) as img:
            frames = int(getattr(img, "n_frames", 1) or 1)
            fmt = img.format or ""
    except Exception as exc:  # noqa: BLE001 — 打不開就不是（可用的）圖片
        log.debug("Pillow 無法開啟 %s：%s", path, exc)
        return None
    if frames < 1:
        return None
    return {
        "is_image": True,
        "frame_count": frames,
        "image_id": PILLOW_IDS.get(fmt),
        "is_animated": frames > 1 and fmt in ANIMATABLE,
    }


def _heif_image(path: Path) -> dict | None:
    head = read_head(path, 32) or b""
    if head[4:8] != b"ftyp" or not capabilities.heif_readable():
        return None
    brand = head[8:12]
    image_id = "avif" if brand in (b"avif", b"avis") else "heic"
    return {"is_image": True, "frame_count": 1, "image_id": image_id}


def _image_fields(path: Path, mime: str, ext: str) -> dict:
    fields: dict = {}
    is_svg = mime == "image/svg+xml" or ext in SVG_EXTS
    if mime.startswith("image/") or ext in HEIF_EXTS or is_svg:
        if is_svg:
            ok = capabilities.svg_renderable()
            fields.update(is_svg=ok, is_image=ok)
        elif ext in HEIF_EXTS or mime in ("image/heif", "image/heic", "image/avif"):
            fields.update(_heif_image(path) or {})
        elif ext not in POSTSCRIPT_EXTS:
            fields.update(_pillow_image(path) or {})
        fields["is_magick_candidate"] = not is_svg and ext not in POSTSCRIPT_EXTS
    if ext in MAGICK_ONLY_EXTS:
        fields["is_magick_candidate"] = True
    return fields


def _document_kind(mime: str, ext: str) -> str | None:
    if mime in ("application/rtf", "text/rtf") or ext == "rtf":
        return "rtf"
    if mime in ("text/html", "application/xhtml+xml") or ext in ("html", "htm", "xhtml"):
        return "html"
    word = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    if ext in ("docx", "docm", "dotx") or mime == word:
        return "docx"
    if ext in ("doc", "dot") or mime == "application/msword":
        return "doc"
    if ext in ("odt", "ott") or mime == "application/vnd.oasis.opendocument.text":
        return "odt"
    return None


def _data_kind(mime: str, ext: str) -> str | None:
    if _is_a(mime, "application/json") or ext in ("json", "jsonc", "json5", "geojson", "ipynb"):
        return "json"
    if ext == "plist" or mime in ("application/x-plist", "application/x-bplist"):
        return "plist"
    if ext == "csv" or mime == "text/csv":
        return "csv"
    if ext == "tsv" or mime == "text/tab-separated-values":
        return "tsv"
    sheet = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
    if ext in ("xlsx", "xlsm") or mime == sheet:
        return "xlsx"
    if ext in ("yaml", "yml") or mime in ("application/yaml", "application/x-yaml"):
        return "yaml"
    return None


def _textual(mime: str, is_markdown: bool) -> bool:
    if is_markdown:
        return True
    if mime.startswith("image/") and mime != "image/svg+xml":
        return False
    return filetypes.is_text_mime(mime)


def _plain_text(path: Path, mime: str, ext: str, fields: dict) -> bool:
    if _textual(mime, fields.get("is_markdown", False)):
        plain = True
    else:
        unknown = mime in GENERIC_MIMES or not ext or mime == "text/x-log"
        media = ext in FFMPEG_VIDEO_EXTS or ext in FFMPEG_AUDIO_EXTS
        media = media or mime.startswith(("audio/", "video/")) or mime in filetypes.ARCHIVE_MIMES
        plain = unknown and not media and looks_like_text(path)
    if fields.get("data_kind") == "plist" and (read_head(path, 8) or b"").startswith(b"bplist"):
        plain = False
    return plain


def _archive_kind(path: Path, mime: str, ext: str) -> str | None:
    if ext in ZIP_BASED_DOCS:
        return None
    lower = path.name.lower()
    if ext == "zip" or _is_a(mime, "application/zip"):
        return "zip"
    if ext in ("tar", "tgz", "tbz", "tbz2", "txz") or lower.endswith(
        tuple("." + c for c in COMPOUND_TAR)
    ):
        return "tar"
    simple = {"7z": "sevenZip", "rar": "rar", "xar": "xar", "pkg": "xar", "cpio": "cpio"}
    simple.update({"iso": "iso", "gz": "gzip", "bz2": "bzip2", "xz": "xz"})
    return simple.get(ext)


def _media_fields(mime: str, ext: str) -> dict:
    fields: dict = {}
    if mime.startswith("audio/") or ext in FFMPEG_AUDIO_EXTS:
        fields["is_audio"] = True
    if mime.startswith("video/") or ext in FFMPEG_VIDEO_EXTS:
        fields.update(is_video=True, is_audio=False)
    if fields.get("is_audio") or fields.get("is_video"):
        fields["is_ffmpeg_candidate"] = True
    return fields


def inspect(path: Path) -> Profile:
    """快速分析：只看類型與少量檔頭（不呼叫 ffprobe）。"""
    path = Path(path)
    if path.is_dir():
        return Profile(path=path, mime=filetypes.FOLDER_MIME, ext=file_ext(path), is_dir=True)
    mime = detect_mime(path)
    ext = file_ext(path)
    fields: dict = {"is_pdf": mime == "application/pdf" or ext == "pdf"}
    if not fields["is_pdf"]:
        fields.update(_image_fields(path, mime, ext))
    fields["document_kind"] = _document_kind(mime, ext)
    fields["data_kind"] = _data_kind(mime, ext)
    fields["is_markdown"] = ext in MARKDOWN_EXTS or mime in ("text/markdown", "text/x-markdown")
    if fields["document_kind"] is None and not fields.get("is_image"):
        if _plain_text(path, mime, ext, fields):
            fields.update(is_plain_text=True, document_kind="plain")
    fields["is_source_code"] = bool(
        fields.get("is_plain_text")
        and fields["data_kind"] is None
        and not fields["is_markdown"]
        and mime not in _CODE_EXCLUDED
    )
    fields.update(_media_fields(mime, ext))
    fields["archive_kind"] = _archive_kind(path, mime, ext)
    fields["office_kind"] = OFFICE_KINDS.get(ext)
    fields["pandoc_reader"] = "markdown" if fields["is_markdown"] else PANDOC_READERS.get(ext)
    return Profile(path=path, mime=mime, ext=ext, **fields)


# ---------------------------------------------------------------- 完整分析（含影音軌）


def probe_media(path: Path) -> MediaInfo | None:
    """用 ffprobe 讀影音軌與長度；讀不懂（或沒有 ffmpeg）時回傳 None。"""
    if not capabilities.ffmpeg_ready():
        return None
    args = [
        backends.require("ffprobe"),
        "-v",
        "error",
        "-protocol_whitelist",
        "file,pipe",
        "-show_entries",
        "stream=codec_type:stream_disposition=attached_pic:format=duration",
        "-of",
        "json",
        os.path.abspath(path),
    ]
    try:
        res = process.run(args, timeout=PROBE_TIMEOUT)
        data = json.loads(res.stdout or "{}") if res.returncode == 0 else None
    except (ValueError, OSError) as exc:
        log.debug("ffprobe 失敗 %s：%s", path, exc)
        return None
    if not isinstance(data, dict):
        return None
    return _media_info(data)


def _media_info(data: dict) -> MediaInfo | None:
    streams = data.get("streams") or []
    video = audio = False
    for st in streams:
        kind = st.get("codec_type")
        cover = (st.get("disposition") or {}).get("attached_pic") == 1
        video = video or (kind == "video" and not cover)
        audio = audio or kind == "audio"
    if not (video or audio):
        return None
    try:
        duration = float((data.get("format") or {}).get("duration"))
    except (TypeError, ValueError):
        duration = None
    return MediaInfo(video, audio, duration if duration and duration > 0 else None)


def analyze(path: Path) -> Profile:
    """完整分析：影音另外用 ffprobe 確認能否解碼、有沒有影像軌與音軌。"""
    p = inspect(path)
    if not (p.is_video or p.is_audio):
        return p
    info = probe_media(p.path)
    if info is None:
        return p
    if p.is_video:
        if not info.has_video:
            return replace(
                p, is_video=False, is_audio=True, is_av_readable=True, duration=info.duration
            )
        return replace(
            p, is_av_readable=True, has_audio_track=info.has_audio, duration=info.duration
        )
    return replace(p, is_av_readable=info.has_audio, duration=info.duration)
