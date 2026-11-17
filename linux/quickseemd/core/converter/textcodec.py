"""文字解碼／編碼（對應原版 CVTextCodec）。

自動偵測順序照原版：BOM → 指定的編碼 → 空檔 → UTF-8 → 沒有 BOM 的 UTF-16 →
統計偵測（偏好繁體中文）→ 依序嘗試 Big5、GB18030、Shift-JIS、EUC-KR、Windows-1252。
統計偵測原版用系統的 NSString 偵測器，這裡改用 chardet（以字元頻率判斷中日韓編碼）。
"""

from __future__ import annotations

import codecs
import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from .. import errors

log = logging.getLogger(__name__)

# 統計偵測的候選與後備嘗試順序（原版 suggestions）
CANDIDATES: tuple[str, ...] = ("big5hkscs", "gb18030", "cp932", "euc_kr", "cp1252")
# 只拿檔頭做統計偵測，避免大檔太慢（之後仍以整個檔案嚴格解碼確認）
DETECT_SAMPLE_BYTES = 256 * 1024
LINE_SCAN_LIMIT = 2_000_000
UTF16_SAMPLE_BYTES = 4096

# 轉換選項 source_encoding（也接受原版命令列的寫法）→ Python 編碼名稱
_FORCED: dict[str, str | None] = {
    "auto": None,
    "utf-8": "utf-8",
    "utf8": "utf-8",
    "utf-16": "utf-16",
    "utf16": "utf-16",
    "big5": "big5hkscs",
    "gb18030": "gb18030",
    "shift_jis": "cp932",
    "shiftjis": "cp932",
    "euc-kr": "euc_kr",
    "euckr": "euc_kr",
    "cp1252": "cp1252",
    "windows1252": "cp1252",
    "mac_roman": "mac_roman",
    "macroman": "mac_roman",
}

# chardet 回報的名稱 → 候選編碼
_CHARDET_MAP: dict[str, str] = {
    "big5": "big5hkscs",
    "gb2312": "gb18030",
    "gbk": "gb18030",
    "gb18030": "gb18030",
    "shift_jis": "cp932",
    "cp932": "cp932",
    "euc-kr": "euc_kr",
    "cp949": "euc_kr",
    "windows-1252": "cp1252",
    "iso-8859-1": "cp1252",
}

_DISPLAY: dict[str, str] = {
    "utf_8": "UTF-8",
    "utf_16": "UTF-16",
    "utf_16_le": "UTF-16",
    "utf_16_be": "UTF-16",
    "utf_32": "UTF-32",
    "utf_32_le": "UTF-32",
    "utf_32_be": "UTF-32",
    "big5hkscs": "Big5",
    "big5": "Big5",
    "cp950": "Big5",
    "gb18030": "GB18030",
    "cp932": "Shift-JIS",
    "shift_jis": "Shift-JIS",
    "euc_kr": "EUC-KR",
    "cp949": "EUC-KR",
    "cp1252": "Windows-1252",
    "mac_roman": "Mac Roman",
    "latin_1": "ISO-8859-1",
    "iso8859_1": "ISO-8859-1",
    "ascii": "ASCII",
}

_UTF16_FAMILY = frozenset({"utf_16", "utf_16_le", "utf_16_be"})
_UTF32_FAMILY = frozenset({"utf_32", "utf_32_le", "utf_32_be"})


class LineEnding(str, Enum):
    LF = "LF"
    CRLF = "CRLF"
    CR = "CR"
    MIXED = "混合"
    NONE = "—"


def normalized(codec: str) -> str:
    """Python 編碼的正式名稱（utf-8 → utf_8），查不到時原樣小寫。"""
    try:
        return codecs.lookup(codec).name.replace("-", "_")
    except LookupError:
        return codec.lower().replace("-", "_")


def display_name(codec: str) -> str:
    """給人看的編碼名稱（UTF-8、Big5…）；其他編碼用大寫的正式名稱。"""
    key = normalized(codec)
    return _DISPLAY.get(key, key.replace("_", "-").upper())


def is_utf16(codec: str) -> bool:
    return normalized(codec) in _UTF16_FAMILY


def is_utf32(codec: str) -> bool:
    return normalized(codec) in _UTF32_FAMILY


def is_utf8(codec: str) -> bool:
    return normalized(codec) == "utf_8"


def is_big5(codec: str) -> bool:
    return normalized(codec) in ("big5hkscs", "big5", "cp950")


def forced_codec(option: str | None) -> str | None:
    """選項 source_encoding → 要強制使用的編碼；auto 或不認得的值回傳 None（自動偵測）。"""
    if not option:
        return None
    return _FORCED.get(option.strip().lower())


@dataclass(frozen=True)
class Decoded:
    text: str
    encoding: str  # Python 編碼名稱
    had_bom: bool

    @property
    def encoding_name(self) -> str:
        return display_name(self.encoding)

    @property
    def line_ending(self) -> LineEnding:
        return detect_line_ending(self.text)


def _try(data: bytes, codec: str) -> str | None:
    try:
        return data.decode(codec)
    except (UnicodeDecodeError, LookupError):
        return None


def _bom(data: bytes) -> Decoded | None:
    if data.startswith(codecs.BOM_UTF8):
        text = _try(data[3:], "utf-8")
        if text is not None:
            return Decoded(text, "utf-8", True)
    if data.startswith((b"\xff\xfe\x00\x00", b"\x00\x00\xfe\xff")):
        text = _try(data, "utf-32")
        if text is not None:
            return Decoded(text, "utf-32", True)
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = _try(data, "utf-16")
        if text is not None:
            return Decoded(text, "utf-16", True)
    return None


def _bomless_utf16(data: bytes) -> Decoded | None:
    """大量 0x00 交錯出現：沒有 BOM 的 UTF-16。"""
    if len(data) < 4:
        return None
    sample = data[:UTF16_SAMPLE_BYTES]
    even_zeros = sum(1 for b in sample[0::2] if b == 0)
    odd_zeros = sum(1 for b in sample[1::2] if b == 0)
    n = len(sample)
    if odd_zeros > n // 4 and even_zeros < n // 50:
        text = _try(data, "utf-16-le")
        if text is not None:
            return Decoded(text, "utf-16-le", False)
    if even_zeros > n // 4 and odd_zeros < n // 50:
        text = _try(data, "utf-16-be")
        if text is not None:
            return Decoded(text, "utf-16-be", False)
    return None


def _looks_cjk(text: str) -> bool:
    """解碼結果全是 ASCII、漢字與全形標點（用來避免短的 Big5 被誤判成西歐文字）。"""
    for ch in text:
        o = ord(ch)
        if o < 0x80 or 0x4E00 <= o <= 0x9FFF or 0x3000 <= o <= 0x303F or 0xFF00 <= o <= 0xFFEF:
            continue
        return False
    return True


def _statistical(data: bytes) -> str | None:
    """統計偵測（偏好繁體中文）；沒有把握時回傳 None 交給後備順序。"""
    try:
        import chardet  # noqa: PLC0415 — 只有遇到非 UTF-8 檔案才載入
    except ImportError:
        log.warning("沒有 chardet，略過統計偵測")
        return None
    sample = data[:DETECT_SAMPLE_BYTES]
    for result in chardet.detect_all(sample):
        name = (result.get("encoding") or "").lower()
        codec = _CHARDET_MAP.get(name)
        if codec is None or (result.get("confidence") or 0) < 0.5:
            continue
        if _try(data, codec) is None:
            continue
        if codec == "cp1252":
            big5 = _try(data, "big5hkscs")
            if big5 is not None and _looks_cjk(big5):
                return None
        return codec
    return None


def decode_bytes(data: bytes, forced: str | None = None) -> Decoded:
    """依原版順序解碼；forced 是 Python 編碼名稱（見 forced_codec）。"""
    found = _bom(data)
    if found is not None:
        return found
    if forced:
        text = _try(data, forced)
        if text is None:
            raise errors.unreadable(
                f"無法以 {display_name(forced)} 解讀這個檔案，請改用其他來源編碼。"
            )
        return Decoded(text, forced, False)
    if not data:
        return Decoded("", "utf-8", False)
    text = _try(data, "utf-8")
    if text is not None:
        return Decoded(text, "utf-8", False)
    found = _bomless_utf16(data)
    if found is not None:
        return found
    codec = _statistical(data)
    if codec is not None:
        return Decoded(data.decode(codec), codec, False)
    for codec in CANDIDATES:
        text = _try(data, codec)
        if text is not None:
            return Decoded(text, codec, False)
    raise errors.unreadable("無法辨識文字編碼，請在選項中手動指定來源編碼。")


def decode_file(path: Path, forced: str | None = None) -> Decoded:
    try:
        data = Path(path).read_bytes()
    except OSError as exc:
        raise errors.unreadable(exc.strerror or str(exc)) from exc
    return decode_bytes(data, forced)


def detect_line_ending(text: str) -> LineEnding:
    """只看前 2,000,000 個字元。"""
    sample = text[:LINE_SCAN_LIMIT]
    crlf = sample.count("\r\n")
    cr = sample.count("\r") - crlf
    lf = sample.count("\n") - crlf
    kinds = sum(1 for n in (crlf, lf, cr) if n > 0)
    if kinds == 0:
        return LineEnding.NONE
    if kinds > 1:
        return LineEnding.MIXED
    if crlf:
        return LineEnding.CRLF
    return LineEnding.LF if lf else LineEnding.CR


def normalize_line_endings(text: str, ending: LineEnding) -> str:
    lf = text.replace("\r\n", "\n").replace("\r", "\n")
    if ending is LineEnding.CRLF:
        return lf.replace("\n", "\r\n")
    if ending is LineEnding.CR:
        return lf.replace("\n", "\r")
    return lf


def encode(text: str, codec: str, bom: bool) -> bytes:
    """編碼成指定編碼；UTF-16 一律輸出 Windows 習慣的 UTF-16 LE＋BOM。"""
    if is_utf8(codec):
        data = text.encode("utf-8", "surrogatepass")
        return codecs.BOM_UTF8 + data if bom else data
    if is_utf16(codec):
        return codecs.BOM_UTF16_LE + text.encode("utf-16-le", "surrogatepass")
    try:
        data = text.encode(codec)
    except UnicodeEncodeError as exc:
        bad = exc.object[exc.start : exc.end][:1] or "?"
        raise errors.encoding_failed(display_name(codec), bad) from exc
    return data
