"""萬用轉換：選項區段的顯示規則（對應原版 ConverterModel.relevantOptionSections）。"""

from __future__ import annotations

from pathlib import Path

from quickseemd.core.converter import sections
from quickseemd.core.converter.profile import Profile


def prof(**kw: object) -> Profile:
    return Profile(path=Path("/x"), mime="", ext="", **kw)


def test_video_sections() -> None:
    v = prof(is_video=True)
    assert sections.option_sections([(v, "anim-gif")]) == ["gif"]
    assert sections.option_sections([(v, "jpeg")]) == [
        "image_quality",
        "image_max_pixel",
        "frame_time",
    ]
    assert sections.option_sections([(v, "png")]) == ["image_max_pixel", "frame_time"]
    assert sections.option_sections([(v, "webm")]) == ["video_size"]
    assert sections.option_sections([(v, "mp3"), (v, "flac")]) == ["audio_bitrate"]


def test_pdf_image_and_audio_sections() -> None:
    pdf = prof(is_pdf=True)
    assert sections.option_sections([(pdf, "jpeg")]) == [
        "image_quality",
        "image_max_pixel",
        "pdf_dpi",
    ]
    assert sections.option_sections([(pdf, "png")]) == ["image_max_pixel", "pdf_dpi"]
    img = prof(is_image=True)
    assert sections.option_sections([(img, "ico")]) == []
    assert sections.option_sections([(img, "webp")]) == ["image_quality", "image_max_pixel"]
    assert sections.option_sections([(img, "pdf")]) == ["image_max_pixel"]
    assert sections.option_sections([(img, "mp4")]) == ["image_max_pixel"]
    audio = prof(is_audio=True)
    assert sections.option_sections([(audio, "opus")]) == ["audio_bitrate"]
    assert sections.option_sections([(audio, "wav")]) == []


def test_text_sections_and_unknown() -> None:
    text = prof(is_plain_text=True)
    assert sections.option_sections([(text, "utf8"), (text, None), (text, "nope")]) == [
        "source_encoding"
    ]
    xlsx = prof(data_kind="xlsx")
    assert sections.option_sections([(xlsx, "csv")]) == ["source_encoding"]  # 原版的小毛病照留
    assert sections.option_sections([(prof(), "zip")]) == []


def test_detected_encoding() -> None:
    assert sections.detected_encoding(["UTF-8 · LF", None, "UTF-8 · LF"]) == "UTF-8 · LF"
    assert sections.detected_encoding(["UTF-8", "Big5"]) == "多種編碼"
    assert sections.detected_encoding([None]) is None
