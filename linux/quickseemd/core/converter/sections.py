"""選項畫面要顯示哪些區段（對應原版 ConverterModel.relevantOptionSections）。

給主程式的萬用轉換畫面用：依範圍內每個檔案已選的格式，決定顯示哪些選項，順序固定。
區段名稱就是 OPTION_DEFAULTS 的鍵（gif 一個區段含 gif_fps、gif_width、gif_seconds）。
"""

from __future__ import annotations

from collections.abc import Iterable

from .formats import BY_ID, LOSSY_IMAGE_IDS
from .profile import Profile

ORDER: tuple[str, ...] = (
    "image_quality",
    "image_max_pixel",
    "pdf_dpi",
    "video_size",
    "gif",
    "frame_time",
    "audio_bitrate",
    "source_encoding",
)
GIF_KEYS = ("gif_fps", "gif_width", "gif_seconds")
_BITRATE_IDS = frozenset({"m4a", "aac", "mp3", "opus"})


def is_textual(p: Profile) -> bool:
    return p.is_plain_text or p.is_markdown or p.data_kind is not None


def _video(fid: str, category: str, found: set[str]) -> None:
    if fid == "anim-gif":
        found.add("gif")
    elif fid in ("png", "jpeg"):
        found.update(("frame_time", "image_max_pixel"))
        if fid == "jpeg":
            found.add("image_quality")
    elif category == "video":
        found.add("video_size")
    elif category == "audio" and fid in _BITRATE_IDS:
        found.add("audio_bitrate")


def _one(p: Profile, fid: str, found: set[str]) -> None:
    fmt = BY_ID.get(fid)
    if fmt is None:
        return
    category = fmt.category.id
    image_out = category == "image" and fid != "anim-gif"
    if p.is_video:
        _video(fid, category, found)
        return
    if p.is_pdf and image_out:
        found.update(("pdf_dpi", "image_max_pixel"))
        if fid in LOSSY_IMAGE_IDS:
            found.add("image_quality")
        return
    if p.is_image:
        if (image_out and fid not in ("ico", "icns")) or fid in ("pdf", "mp4", "mov"):
            found.add("image_max_pixel")
        if fid in LOSSY_IMAGE_IDS or fid == "webp":
            found.add("image_quality")
    if p.is_audio and fid in _BITRATE_IDS:
        found.add("audio_bitrate")
    if is_textual(p) and category in ("text", "data", "document"):
        found.add("source_encoding")


def option_sections(jobs: Iterable[tuple[Profile, str | None]]) -> list[str]:
    """jobs：（檔案分析結果, 已選的格式代碼）；回傳要顯示的區段（固定順序）。"""
    found: set[str] = set()
    for profile, fid in jobs:
        if fid:
            _one(profile, fid, found)
    return [key for key in ORDER if key in found]


def detected_encoding(details: Iterable[str | None]) -> str | None:
    """「偵測到：X」的 X；各檔不同時是「多種編碼」。"""
    values = [d for d in details if d]
    if not values:
        return None
    return values[0] if all(v == values[0] for v in values) else "多種編碼"
