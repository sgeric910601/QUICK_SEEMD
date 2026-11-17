"""這台電腦實際能輸出哪些格式（對應原版在執行時查詢 ImageIO、AudioToolbox、AVFoundation）。

- 圖片：Pillow 能寫的格式，加上 heif-enc 有編碼器的 HEIC／AVIF。
- 音訊／影片：ffmpeg 有對應的編碼器才列出。
結果依工具路徑快取（backends.refresh() 後路徑改變就會重新查詢）。
"""

from __future__ import annotations

import logging
import threading

from .. import backends, process

log = logging.getLogger(__name__)

# 原版 nativeWritableImageFormats 的順序
IMAGE_ORDER: tuple[str, ...] = (
    "png",
    "jpeg",
    "heic",
    "avif",
    "webp",
    "jxl",
    "tiff",
    "gif",
    "bmp",
    "jp2",
    "ico",
    "icns",
    "psd",
    "tga",
)
# 格式代碼 → Pillow 的存檔格式名稱
PILLOW_SAVE: dict[str, str] = {
    "png": "PNG",
    "jpeg": "JPEG",
    "webp": "WEBP",
    "tiff": "TIFF",
    "gif": "GIF",
    "bmp": "BMP",
    "jp2": "JPEG2000",
    "ico": "ICO",
    "icns": "ICNS",
    "tga": "TGA",
}
_PILLOW_FEATURE = {"webp": "webp", "jp2": "jpg_2000"}
HEIF_IDS = ("heic", "avif")

# 原版 nativeAudioFormats／nativeVideoFormats 的順序，以及各自需要的 ffmpeg 編碼器
AUDIO_ENCODERS: tuple[tuple[str, str], ...] = (
    ("m4a", "aac"),
    ("alac", "alac"),
    ("aac", "aac"),
    ("wav", "pcm_s16le"),
    ("aiff", "pcm_s16be"),
    ("caf", "pcm_s16le"),
    ("flac", "flac"),
)
VIDEO_ENCODERS: tuple[tuple[str, str], ...] = (
    ("mp4", "libx264"),
    ("mp4-hevc", "libx265"),
    ("mov", "libx264"),
    ("m4v", "libx264"),
    ("mov-hevc", "libx265"),
    ("prores", "prores_ks"),
)

_lock = threading.Lock()
_cache: dict[tuple[str, str], frozenset[str]] = {}
PROBE_TIMEOUT = 20.0


def _cached(kind: str, tool: str, compute) -> frozenset[str]:  # noqa: ANN001
    key = (kind, tool)
    with _lock:
        if key in _cache:
            return _cache[key]
    value = compute(tool)
    with _lock:
        _cache[key] = value
    return value


def _run_lines(args: list[str]) -> list[str]:
    try:
        res = process.run(args, timeout=PROBE_TIMEOUT)
    except Exception as exc:  # noqa: BLE001 — 偵測失敗就當成沒有這個能力
        log.warning("偵測 %s 失敗：%s", args[0], exc)
        return []
    return (res.stdout + "\n" + res.stderr).splitlines()


def _heif_encoders(tool: str) -> frozenset[str]:
    out: set[str] = set()
    section = ""
    for line in _run_lines([tool, "--list-encoders"]):
        stripped = line.strip()
        if stripped.endswith("encoders:"):
            section = stripped.split()[0].upper()
        elif stripped.startswith("- ") and section in ("HEIC", "AVIF"):
            out.add(section.lower())
    return frozenset(out)


def heif_writable() -> frozenset[str]:
    tool = backends.find("heif-enc")
    if tool is None:
        return frozenset()
    return _cached("heif", tool, _heif_encoders)


def heif_readable() -> bool:
    return backends.available("heif-convert")


def _pillow_ok(fid: str) -> bool:
    try:
        from PIL import Image, features  # noqa: PLC0415
    except ImportError:
        return False
    Image.init()
    if PILLOW_SAVE[fid] not in Image.SAVE:
        return False
    feature = _PILLOW_FEATURE.get(fid)
    return feature is None or bool(features.check(feature))


def native_image_formats() -> list[str]:
    """可直接輸出的圖片格式代碼（原版順序）。"""
    heif = heif_writable()
    out = []
    for fid in IMAGE_ORDER:
        if fid in PILLOW_SAVE and _pillow_ok(fid):
            out.append(fid)
        elif fid in HEIF_IDS and fid in heif:
            out.append(fid)
    return out


def _magick_formats(tool: str) -> frozenset[str]:
    """ImageMagick 可寫入的格式（convert -list format 的 rw 欄位）。"""
    out: set[str] = set()
    for line in _run_lines([tool, "-list", "format"]):
        parts = line.split()
        if len(parts) >= 3 and "w" in parts[2]:
            out.add(parts[0].rstrip("*").lower())
    return frozenset(out)


def magick_writable() -> frozenset[str]:
    tool = backends.find("magick")
    if tool is None:
        return frozenset()
    return _cached("magick", tool, _magick_formats)


def _ffmpeg_encoders(tool: str) -> frozenset[str]:
    out: set[str] = set()
    for line in _run_lines([tool, "-hide_banner", "-encoders"]):
        parts = line.split()
        if len(parts) >= 2 and len(parts[0]) == 6 and parts[0][0] in "VAS":
            out.add(parts[1])
    return frozenset(out)


def ffmpeg_ready() -> bool:
    """影音全部靠 ffmpeg／ffprobe；兩個都在才算能處理影音。"""
    return backends.available("ffmpeg") and backends.available("ffprobe")


def ffmpeg_encoders() -> frozenset[str]:
    tool = backends.find("ffmpeg")
    if tool is None:
        return frozenset()
    return _cached("ffmpeg", tool, _ffmpeg_encoders)


def native_audio_formats() -> list[str]:
    if not ffmpeg_ready():
        return []
    enc = ffmpeg_encoders()
    return [fid for fid, codec in AUDIO_ENCODERS if codec in enc]


def native_video_formats() -> list[str]:
    if not ffmpeg_ready():
        return []
    enc = ffmpeg_encoders()
    return [fid for fid, codec in VIDEO_ENCODERS if codec in enc]


def svg_renderable() -> bool:
    """GdkPixbuf 有 SVG 載入器（librsvg）才把 SVG 當圖片。"""
    try:
        import gi  # noqa: PLC0415

        gi.require_version("GdkPixbuf", "2.0")
        from gi.repository import GdkPixbuf  # noqa: PLC0415
    except (ImportError, ValueError):
        return False
    return any("svg" in (f.get_name() or "") for f in GdkPixbuf.Pixbuf.get_formats())


def refresh() -> None:
    with _lock:
        _cache.clear()
