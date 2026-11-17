"""相片資訊的讀取與移除（對應原版 ITMetadataStripper）。

- 讀取：exiftool -j -G0（沒有 exiftool 時退回 Pillow 只讀 EXIF）。
- 移除：exiftool -o 寫出新檔（絕不就地修改、不留 _original 備份），影像資料不重新壓縮；
  只移除位置＝EXIF／XMP 的 GPS*，以及 IPTC 與 XMP 的城市、州省、國家、地點欄位；
  移除全部＝刪掉 EXIF、XMP、IPTC、註解等所有資訊，只把方向與色彩描述檔寫回去。
- 寫出後再讀一次確認；失敗或格式不支援時改用 Pillow 重新編碼（有損格式以二分搜尋
  找出不超過原檔 1.05 倍大小的最高品質）。
"""

from __future__ import annotations

import json
import logging
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from PIL import ExifTags, Image, UnidentifiedImageError

from ... import backends, errors, process
from . import formats, loader, writer

log = logging.getLogger(__name__)

LOCATION = "location"
ALL = "all"

# exiftool 可以不重新壓縮就改寫的格式
LOSSLESS_FORMATS = frozenset({"jpeg", "png", "tiff", "heic", "avif", "webp", "gif"})

LOCATION_DELETE: tuple[str, ...] = (
    "-GPS*=",
    "-XMP:GPS*=",
    "-IPTC:City=",
    "-IPTC:Sub-location=",
    "-IPTC:Province-State=",
    "-IPTC:Country-PrimaryLocationCode=",
    "-IPTC:Country-PrimaryLocationName=",
    "-XMP:City=",
    "-XMP:State=",
    "-XMP:Country=",
    "-XMP:CountryCode=",
    "-XMP:Location=",
    "-XMP:LocationCreated*=",
    "-XMP:LocationShown*=",
)
# TIFF 的第 0 層不能整個刪，描述性的標籤要一個一個指名
TIFF_IFD0_DELETE: tuple[str, ...] = (
    "-IFD0:Make=",
    "-IFD0:Model=",
    "-IFD0:Software=",
    "-IFD0:Artist=",
    "-IFD0:Copyright=",
    "-IFD0:ModifyDate=",
    "-IFD0:HostComputer=",
    "-IFD0:ImageDescription=",
    "-IFD0:DocumentName=",
)
KEEP_BACK: tuple[str, ...] = ("-tagsFromFile", "@", "-EXIF:Orientation", "-ICC_Profile")

_LOCATION_NAMES = frozenset(
    {
        "City",
        "Sub-location",
        "Province-State",
        "Country-PrimaryLocationName",
        "Country-PrimaryLocationCode",
        "State",
        "Country",
        "CountryCode",
        "Location",
        "Sublocation",
    }
)
_CATEGORIES: tuple[tuple[str, frozenset[str]], ...] = (
    (
        "相機與鏡頭",
        frozenset(
            {
                "Make",
                "Model",
                "LensModel",
                "LensMake",
                "SerialNumber",
                "BodySerialNumber",
                "InternalSerialNumber",
                "LensSerialNumber",
                "CameraSerialNumber",
            }
        ),
    ),
    (
        "拍攝時間",
        frozenset(
            {
                "DateTimeOriginal",
                "CreateDate",
                "DateTimeDigitized",
                "ModifyDate",
                "DateTime",
                "DateCreated",
                "DigitalCreationDate",
                "CreationTime",
                "TimeCreated",
            }
        ),
    ),
    (
        "拍攝參數",
        frozenset({"ExposureTime", "FNumber", "ISO", "ISOSpeedRatings", "FocalLength", "Flash"}),
    ),
    (
        "作者與版權",
        frozenset(
            {"Creator", "Artist", "Copyright", "Rights", "By-line", "CopyrightNotice", "Author"}
        ),
    ),
    ("編輯軟體", frozenset({"Software", "CreatorTool"})),
)
_METADATA_GROUPS = frozenset({"EXIF", "XMP", "IPTC", "MakerNotes", "Photoshop", "PNG", "GIF"})
_STRUCTURAL = frozenset(
    {
        "Orientation",
        "ExifImageWidth",
        "ExifImageHeight",
        "ColorSpace",
        "XResolution",
        "YResolution",
        "ResolutionUnit",
        "YCbCrPositioning",
        "ExifVersion",
        "FlashpixVersion",
        "ComponentsConfiguration",
        "ThumbnailOffset",
        "ThumbnailLength",
        "Compression",
        "InteropIndex",
        "InteropVersion",
        "XMPToolkit",
        "ImageWidth",
        "ImageHeight",
        "BitsPerSample",
        "PhotometricInterpretation",
        "SamplesPerPixel",
        "PlanarConfiguration",
        "StripOffsets",
        "StripByteCounts",
        "RowsPerStrip",
        "Predictor",
        "ExtraSamples",
        "SampleFormat",
        "BitDepth",
        "ColorType",
        "Filter",
        "Interlace",
        "PixelsPerUnitX",
        "PixelsPerUnitY",
        "PixelUnits",
        "SRGBRendering",
        "Gamma",
        "ProfileName",
        "Palette",
        "Transparency",
        "BackgroundColor",
        "SignificantBits",
        "WhitePointX",
        "WhitePointY",
        "RedX",
        "RedY",
        "GreenX",
        "GreenY",
        "BlueX",
        "BlueY",
        "GIFVersion",
        "ScreenDescriptor",
        "ColorResolutionDepth",
        "HasColorMap",
        "BackgroundColor",
        "AnimationIterations",
        "FrameCount",
        "Duration",
        "ImageSize",
        "ApplicationRecordVersion",
        "ExifOffset",
    }
)


@dataclass(frozen=True)
class StripReport:
    file: Path
    ext: str
    removed: tuple[str, ...]
    lossless: bool


# ---------------------------------------------------------------- 讀取


def read_tags(path: Path) -> dict[str, Any] | None:
    """「群組:名稱」→ 值；沒有 exiftool 時用 Pillow 只讀 EXIF；都讀不到回傳 None。"""
    exe = backends.find("exiftool")
    if exe is None:
        return _pillow_tags(path)
    try:
        res = process.run([exe, "-j", "-G0", "-a", str(Path(path).resolve())], timeout=60)
        data = json.loads(res.stdout or "[]")
    except (errors.QSError, json.JSONDecodeError, OSError) as exc:
        log.warning("無法讀取相片資訊：%s", exc)
        return None
    return data[0] if data and isinstance(data[0], dict) else None


def _pillow_tags(path: Path) -> dict[str, Any] | None:
    try:
        with loader.open_checked(path) as im:
            exif = im.getexif()
            out: dict[str, Any] = {}
            for tag, value in exif.items():
                out[f"EXIF:{ExifTags.TAGS.get(tag, str(tag))}"] = value
            for tag, value in exif.get_ifd(0x8769).items():
                out[f"EXIF:{ExifTags.TAGS.get(tag, str(tag))}"] = value
            for tag, value in exif.get_ifd(0x8825).items():
                out[f"EXIF:{ExifTags.GPSTAGS.get(tag, f'GPS{tag}')}"] = value
            return out
    except (errors.QSError, UnidentifiedImageError, OSError, ValueError, SyntaxError):
        return None


def _is_location(name: str) -> bool:
    return name.startswith(("GPS", "LocationCreated", "LocationShown")) or name in _LOCATION_NAMES


def _entries(tags: dict[str, Any]) -> list[tuple[str, str]]:
    out = []
    for key in tags:
        group, sep, name = key.partition(":")
        if sep and (group in _METADATA_GROUPS or name.endswith("Comment")):
            out.append((group, name))
    return out


def categories_from_tags(tags: dict[str, Any], mode: str) -> list[str]:
    entries = _entries(tags)
    names = {n for _g, n in entries}
    has_location = any(_is_location(n) for n in names)
    if mode == LOCATION:
        return ["位置（GPS）"] if has_location else []
    found = ["位置（GPS）"] if has_location else []
    found += [title for title, keys in _CATEGORIES if names & keys]
    if not found and any(n not in _STRUCTURAL for n in names):
        found.append("其他中繼資料")
    return found


def categories(path: Path, mode: str) -> list[str]:
    """檔案裡有哪些類別的資訊（依模式，給使用者看的文字）。"""
    tags = read_tags(Path(path))
    return categories_from_tags(tags, mode) if tags else []


def _clean(tags: dict[str, Any] | None, mode: str) -> bool:
    """輸出確實沒有該移除的資訊。"""
    if tags is None:
        return False
    for group, name in _entries(tags):
        if _is_location(name):
            return False
        if mode == ALL and (group in ("XMP", "IPTC") or name.endswith("Comment")):
            return False
    return not [c for c in categories_from_tags(tags, mode) if c != "其他中繼資料"]


# ---------------------------------------------------------------- 移除


def _source_format(path: Path) -> str | None:
    kind = loader.heif_kind(path)
    if kind:
        return kind
    try:
        with loader.open_checked(path) as im:  # 太大的圖直接以中文錯誤拒絕
            return formats.from_pillow(im.format)
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        return None


def _exiftool_args(exe: str, src: Path, dst: Path, mode: str, fmt: str) -> list[str]:
    args = [exe, "-m", "-q", "-q", "-o", str(dst)]
    if mode == LOCATION:
        args += list(LOCATION_DELETE)
    else:
        args += ["-all="]
        if fmt == "tiff":
            args += list(TIFF_IFD0_DELETE)
        args += list(KEEP_BACK)
    args.append(str(src))
    return args


def _strip_lossless(
    src: Path, out_base: Path, mode: str, cancel: threading.Event | None
) -> tuple[Path, str] | None:
    exe = backends.find("exiftool")
    fmt = _source_format(src)
    if exe is None or fmt not in LOSSLESS_FORMATS:
        return None
    ext = src.suffix.lstrip(".").lower() or formats.get(fmt).ext
    target = out_base.with_name(f"{out_base.name}.{ext}")
    res = process.run(_exiftool_args(exe, src.resolve(), target, mode, fmt), cancel=cancel)
    if res.returncode == 0 and target.is_file() and _clean(read_tags(target), mode):
        return target, ext
    log.warning("exiftool 無法移除「%s」的資訊，改用重新編碼：%s", src.name, res.stderr.strip())
    target.unlink(missing_ok=True)
    return None


def _write_q(source: loader.Source, target: Path, out: writer.Output, q: float) -> int:
    target.unlink(missing_ok=True)
    frames = source.frames if out.format == "gif" else source.frames[:1]
    writer.write(
        frames,
        source,
        target,
        writer.Output(out.format, q, out.metadata, keeps_alpha=out.keeps_alpha),
    )
    return target.stat().st_size


def reencode(src: Path, out_base: Path, mode: str) -> tuple[Path, str]:
    """重新編碼（像素轉正、不寫入該移除的資訊）。"""
    source = loader.load(src, all_frames=True)
    fid = source.preferred_format()
    ext = source.output_extension(fid)
    target = out_base.with_name(f"{out_base.name}.{ext}")
    meta = writer.REMOVE_ALL if mode == ALL else writer.REMOVE_LOCATION
    out = writer.Output(fid, 0.92, meta, keeps_alpha=source.has_alpha)
    budget = source.file_size + source.file_size // 20
    if _write_q(source, target, out, 0.92) > budget and formats.get(fid).lossy:
        lo, hi, best = 0.4, 0.92, None
        for _ in range(5):
            q = (lo + hi) / 2
            if _write_q(source, target, out, q) <= budget:
                best, lo = q, q
            else:
                hi = q
        _write_q(source, target, out, best if best is not None else lo)
    return target, ext


def strip(
    src: Path, mode: str, out_base: Path, cancel: threading.Event | None = None
) -> StripReport:
    """移除相片資訊；out_base 是不含副檔名的輸出路徑（依格式補上副檔名）。"""
    src = Path(src)
    if not src.is_file():
        raise errors.file_not_found(src.name)
    removed = tuple(categories(src, mode))
    done = _strip_lossless(src, Path(out_base), mode, cancel)
    if done is not None:
        return StripReport(done[0], done[1], removed, True)
    target, ext = reencode(src, Path(out_base), mode)
    return StripReport(target, ext, removed, False)
