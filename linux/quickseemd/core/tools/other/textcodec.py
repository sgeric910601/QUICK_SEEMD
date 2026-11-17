"""文字解碼與編碼（對應原版 CVTextCodec 與 QSText）。

兩種解碼方式：
- decode()：轉換用（原版 CVTextCodec）。記住原本的編碼與 BOM，認不出來就丟出中文錯誤。
  順序：BOM → 指定的編碼 → 空檔 → UTF-8 → 沒有 BOM 的 UTF-16 → 統計偵測（偏好繁體中文）→ 依序嘗試。
- decode_lenient()：顯示用（原版 QSText，字幕與預覽）。換行統一成 LF，二進位檔回傳 None。

編碼名稱一律用 Python 的編碼代碼（例如 "cp950"），顯示時用 display_name() 轉成原版的名稱。
"""

from __future__ import annotations

import codecs
import logging
from dataclasses import dataclass
from enum import Enum
from pathlib import Path

from ... import errors
from . import scriptfit

log = logging.getLogger(__name__)

UTF8 = "utf-8"
UTF16 = "utf-16"  # 有 BOM（任一位元組順序）
UTF16LE = "utf-16-le"  # 沒有 BOM 的小端序
UTF16BE = "utf-16-be"  # 沒有 BOM 的大端序
UTF32 = "utf-32"
BIG5 = "cp950"  # 台灣 Windows 的 Big5（輸出 Big5 一律用它）
BIG5HKSCS = "big5hkscs"
GB18030 = "gb18030"
SHIFT_JIS = "cp932"
EUC_JP = "euc_jp"
EUC_KR = "euc_kr"
CP1252 = "cp1252"
MAC_ROMAN = "mac_roman"
LATIN1 = "latin-1"

BOM_UTF8 = b"\xef\xbb\xbf"
BOM_UTF16_LE = b"\xff\xfe"
BOM_UTF16_BE = b"\xfe\xff"
BOM_UTF32_LE = b"\xff\xfe\x00\x00"
BOM_UTF32_BE = b"\x00\x00\xfe\xff"

# 轉換用的偵測候選（原版 CVTextCodec 的 suggestions，順序就是後備嘗試的順序）
DETECT_CANDIDATES: tuple[str, ...] = (BIG5, GB18030, SHIFT_JIS, EUC_KR, CP1252)
# 顯示用的偵測候選（原版 QSText）；Mac Roman 什麼位元組都能解，只在統計偵測時考慮
LENIENT_CANDIDATES: tuple[str, ...] = (BIG5, GB18030, SHIFT_JIS, EUC_JP, EUC_KR, CP1252, MAC_ROMAN)

LINE_SCAN_LIMIT = 2_000_000
_UTF16_SAMPLE = 4096
_LENIENT_UTF16_SAMPLE = 2048
_BINARY_SAMPLE = 16384
_TEXT_HEAD = 8192
_BIG5_DECODERS = (BIG5, BIG5HKSCS)

# charset_normalizer 的編碼名稱 ↔ 這裡的代碼
_TO_NORMALIZER = {
    BIG5: "big5",
    GB18030: "gb18030",
    SHIFT_JIS: "cp932",
    EUC_JP: "euc_jp",
    EUC_KR: "euc_kr",
    CP1252: "cp1252",
    MAC_ROMAN: "mac_roman",
}
_FROM_NORMALIZER = {v: k for k, v in _TO_NORMALIZER.items()}
# 各編碼對應的文字系統（scriptfit 用）
_FAMILY = {BIG5: "zh", GB18030: "zh", SHIFT_JIS: "ja", EUC_JP: "ja", EUC_KR: "ko"}

_DISPLAY = {
    "utf-8": "UTF-8",
    "utf-16": "UTF-16",
    "utf-16-le": "UTF-16",
    "utf-16-be": "UTF-16",
    "utf-32": "UTF-32",
    "utf-32-le": "UTF-32",
    "utf-32-be": "UTF-32",
    "big5": "Big5",
    "cp950": "Big5",
    "big5hkscs": "Big5",
    "gb18030": "GB18030",
    "cp932": "Shift-JIS",
    "shift_jis": "Shift-JIS",
    "euc_jp": "EUC-JP",
    "euc_kr": "EUC-KR",
    "cp1252": "Windows-1252",
    "mac-roman": "Mac Roman",
    "iso8859-1": "ISO-8859-1",
    "ascii": "ASCII",
}


class LineEnding(str, Enum):
    """換行種類；值是原版顯示的文字。"""

    LF = "LF"
    CRLF = "CRLF"
    CR = "CR"
    MIXED = "混合"
    NONE = "—"


@dataclass(frozen=True)
class Decoded:
    """轉換用的解碼結果。encoding 是 Python 編碼代碼。"""

    text: str
    encoding: str
    had_bom: bool

    @property
    def display_name(self) -> str:
        return display_name(self.encoding)

    @property
    def line_ending(self) -> LineEnding:
        return detect_line_ending(self.text)


@dataclass(frozen=True)
class LenientText:
    """顯示用的解碼結果：text 已統一成 LF；encoding 與 line_ending 是顯示文字。"""

    text: str
    encoding: str
    line_ending: str


def display_name(encoding: str) -> str:
    """原版的編碼顯示名稱（UTF-8、Big5、GB18030…），其他編碼用大寫的標準名稱。"""
    try:
        name = codecs.lookup(encoding).name
    except LookupError:
        return encoding.upper()
    return _DISPLAY.get(name, name.upper())


# ---------------------------------------------------------------- 共用小工具


def _strict(data: bytes, encoding: str) -> str | None:
    try:
        return data.decode(encoding)
    except (UnicodeDecodeError, LookupError):
        return None


def _try_decode(data: bytes, encoding: str) -> tuple[str, str] | None:
    """嚴格解碼；Big5 先試 CP950 再試 HKSCS。回傳（文字, 實際用的編碼）。"""
    for enc in _BIG5_DECODERS if encoding == BIG5 else (encoding,):
        text = _strict(data, enc)
        if text is not None:
            return text, enc
    return None


def _statistical_guess(data: bytes, candidates: tuple[str, ...]) -> str | None:
    """統計偵測（charset_normalizer），只考慮 candidates。

    排序：文字像不像該語言（scriptfit）減去亂碼程度，其次語言一致性，
    最後依 candidates 的順序（Big5 在最前面＝平手時偏好繁體中文，同原版）。
    """
    try:
        from charset_normalizer import CharsetMatch, from_bytes  # noqa: PLC0415 — 用到才載入
    except ImportError:
        log.warning("沒有 charset_normalizer，略過統計偵測")
        return None
    names = [_TO_NORMALIZER[c] for c in candidates if c in _TO_NORMALIZER]
    matches = [m for m in from_bytes(data, cp_isolation=names) if m.encoding in _FROM_NORMALIZER]
    if not matches:
        return None
    order = {c: i for i, c in enumerate(candidates)}

    def rank(match: CharsetMatch) -> tuple[float, float, int]:
        enc = _FROM_NORMALIZER[match.encoding]
        fit = scriptfit.plausibility(_FAMILY.get(enc, "latin"), str(match))
        return (-round(fit - 2 * match.chaos, 2), -match.coherence, order.get(enc, len(order)))

    return _FROM_NORMALIZER[min(matches, key=rank).encoding]


def _zero_counts(sample: bytes) -> tuple[int, int]:
    """（偶數位置的 0x00 數量, 奇數位置的 0x00 數量）。"""
    return sample[0::2].count(0), sample[1::2].count(0)


# ---------------------------------------------------------------- 轉換用解碼


def _decode_bom(data: bytes) -> Decoded | None:
    if data.startswith(BOM_UTF8):
        text = _strict(data[3:], UTF8)
        if text is not None:
            return Decoded(text, UTF8, True)
    if data.startswith((BOM_UTF32_LE, BOM_UTF32_BE)):
        text = _strict(data, UTF32)
        if text is not None:
            return Decoded(text, UTF32, True)
    if data.startswith((BOM_UTF16_LE, BOM_UTF16_BE)):
        text = _strict(data, UTF16)
        if text is not None:
            return Decoded(text, UTF16, True)
    return None


def _utf16_without_bom(data: bytes) -> Decoded | None:
    """沒有 BOM 的 UTF-16：前 4096 位元組裡 0x00 大量出現在奇數（LE）或偶數（BE）位置。"""
    if len(data) < 4:
        return None
    sample = data[:_UTF16_SAMPLE]
    n = len(sample)
    even, odd = _zero_counts(sample)
    if odd > n // 4 and even < n // 50:
        text = _strict(data, UTF16LE)
        if text is not None:
            return Decoded(text, UTF16LE, False)
    if even > n // 4 and odd < n // 50:
        text = _strict(data, UTF16BE)
        if text is not None:
            return Decoded(text, UTF16BE, False)
    return None


def _best_strict(data: bytes, candidates: tuple[str, ...]) -> tuple[str, str] | None:
    """統計偵測沒有結果時：在能嚴格解開的編碼裡挑文字最像該語言的（平手時照 candidates 順序）。"""
    best: tuple[float, tuple[str, str]] | None = None
    for enc in candidates:
        hit = _try_decode(data, enc)
        if hit is None:
            continue
        fit = scriptfit.plausibility(_FAMILY.get(enc, "latin"), hit[0])
        if best is None or fit > best[0]:
            best = (fit, hit)
    return best[1] if best else None


def _guess_and_decode(data: bytes, candidates: tuple[str, ...]) -> tuple[str, str] | None:
    guessed = _statistical_guess(data, candidates)
    if guessed is not None:
        hit = _try_decode(data, guessed)
        if hit is not None:
            return hit
    return _best_strict(data, tuple(c for c in candidates if c != MAC_ROMAN))


def _decode_legacy(data: bytes) -> Decoded:
    hit = _guess_and_decode(data, DETECT_CANDIDATES)
    if hit is None:
        raise errors.unreadable("無法辨識文字編碼，請在選項中手動指定來源編碼。")
    return Decoded(hit[0], hit[1], False)


def decode(data: bytes, forced: str | None = None) -> Decoded:
    """自動偵測編碼並解碼（forced 指定來源編碼時只用它，BOM 仍然優先）。"""
    bom = _decode_bom(data)
    if bom is not None:
        return bom
    if forced:
        hit = _try_decode(data, forced)
        if hit is None:
            name = display_name(forced)
            raise errors.unreadable(f"無法以 {name} 解讀這個檔案，請改用其他來源編碼。")
        return Decoded(hit[0], hit[1], False)
    if not data:
        return Decoded("", UTF8, False)
    text = _strict(data, UTF8)
    # 原版先認 UTF-8，純英文的 UTF-16（含 0x00）也算合法 UTF-8 而被誤判；有 0x00 時先看 UTF-16
    if text is not None and 0 not in data:
        return Decoded(text, UTF8, False)
    utf16 = _utf16_without_bom(data)
    if utf16 is not None:
        return utf16
    if text is not None:
        return Decoded(text, UTF8, False)
    return _decode_legacy(data)


_NAME_CANDIDATES: tuple[str, ...] = (BIG5, GB18030, SHIFT_JIS, EUC_KR)


def detect_legacy_encoding(data: bytes) -> str | None:
    """沒有標記編碼的位元組（例如舊式 ZIP 檔名）最可能的編碼；UTF-8 優先，認不出來回傳 None。"""
    if _strict(data, UTF8) is not None:
        return UTF8
    hit = _guess_and_decode(data, _NAME_CANDIDATES)
    return hit[1] if hit is not None else None


def read_error_text(path: Path, exc: OSError) -> str:
    """讀檔失敗的中文說明。"""
    name = Path(path).name
    if isinstance(exc, FileNotFoundError):
        return f"找不到「{name}」，可能已被移動或刪除。"
    if isinstance(exc, PermissionError):
        return f"沒有權限讀取「{name}」。"
    if isinstance(exc, IsADirectoryError):
        return f"「{name}」是資料夾，不是檔案。"
    return f"「{name}」：{exc.strerror or exc}"


def read_bytes(path: Path) -> bytes:
    """讀整個檔案；失敗時丟出「無法讀取檔案內容：…」。"""
    try:
        return Path(path).read_bytes()
    except OSError as exc:
        raise errors.unreadable(read_error_text(path, exc)) from exc


def decode_file(path: Path, forced: str | None = None) -> Decoded:
    return decode(read_bytes(path), forced)


# ---------------------------------------------------------------- 換行


def _newline_counts(text: str) -> tuple[int, int, int]:
    """（CRLF, 單獨 LF, 單獨 CR）的數量。"""
    crlf = text.count("\r\n")
    return crlf, text.count("\n") - crlf, text.count("\r") - crlf


def detect_line_ending(text: str) -> LineEnding:
    """只看前 200 萬個字元（同原版）。"""
    crlf, lf, cr = _newline_counts(text[:LINE_SCAN_LIMIT])
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


# ---------------------------------------------------------------- 編碼


def _first_unencodable(text: str, encoding: str) -> str:
    for ch in text:
        try:
            ch.encode(encoding)
        except UnicodeEncodeError:
            return ch
    return "?"


def encode(text: str, encoding: str, bom: bool) -> bytes:
    """編碼成指定編碼；UTF-16 一律是 Windows 習慣的 LE＋BOM。無法表示的字元丟出中文錯誤。"""
    try:
        if encoding == UTF8:
            data = text.encode(UTF8)
            return BOM_UTF8 + data if bom else data
        if encoding in (UTF16, UTF16LE):
            return BOM_UTF16_LE + text.encode(UTF16LE)
        return text.encode(encoding)
    except UnicodeEncodeError as exc:
        sample = _first_unencodable(text, encoding)
        raise errors.encoding_failed(display_name(encoding), sample) from exc


# ---------------------------------------------------------------- 顯示用（寬鬆）解碼


def utf16_guess(data: bytes) -> str | None:
    """原版 QSText 的 UTF-16 判斷：BOM，或前 2048 位元組的 0x00 分布。"""
    head = data[:_LENIENT_UTF16_SAMPLE]
    if head.startswith(BOM_UTF16_LE):
        return UTF16LE
    if head.startswith(BOM_UTF16_BE):
        return UTF16BE
    if len(head) < 16:
        return None
    even, odd = _zero_counts(head)
    half = len(head) // 2
    if odd > half * 7 // 10 and even < half // 10:
        return UTF16LE
    if even > half * 7 // 10 and odd < half // 10:
        return UTF16BE
    return None


_ALLOWED_CONTROL = frozenset({9, 10, 12, 13, 27})
_CONTROL_BYTES = bytes(b for b in range(0x20) if b not in _ALLOWED_CONTROL)


def looks_binary(data: bytes) -> bool:
    """含 NUL 或控制字元超過 2%（UTF-16 另外處理）。"""
    if not data or utf16_guess(data) is not None:
        return False
    sample = data[:_BINARY_SAMPLE]
    if 0 in sample:
        return True
    control = len(sample) - len(sample.translate(None, _CONTROL_BYTES))
    return control * 100 > len(sample) * 2


_TEXT_CONTROL_BYTES = bytes(b for b in range(0x20) if b < 0x09 or (0x0D < b and b != 0x1B))


def looks_like_text(head: bytes) -> bool:
    """原版 looksLikeText：前 8192 位元組沒有 NUL、控制字元少於 1%。"""
    head = head[:_TEXT_HEAD]
    if not head or head.startswith((BOM_UTF16_LE, BOM_UTF16_BE)):
        return True
    if 0 in head:
        return False
    control = len(head) - len(head.translate(None, _TEXT_CONTROL_BYTES))
    return control * 100 < len(head)


def normalize_newlines(text: str) -> tuple[str, str]:
    """換行統一成 LF，並回傳原本的換行種類（LF／CRLF／CR／混合）。"""
    crlf, lf, cr = _newline_counts(text)
    kinds = sum(1 for n in (crlf, lf, cr) if n > 0)
    if kinds > 1:
        ending = LineEnding.MIXED.value
    elif crlf:
        ending = LineEnding.CRLF.value
    elif cr:
        ending = LineEnding.CR.value
    else:
        ending = LineEnding.LF.value
    if not crlf and not cr:
        return text, ending
    return text.replace("\r\n", "\n").replace("\r", "\n"), ending


def _lenient_legacy(data: bytes) -> tuple[str, str]:
    hit = _guess_and_decode(data, LENIENT_CANDIDATES)
    if hit is not None:
        return hit[0], display_name(hit[1])
    return data.decode(LATIN1), "ISO Latin-1"


def _lenient_utf16(data: bytes, encoding: str) -> tuple[str | None, str]:
    body = data[2:] if data.startswith((BOM_UTF16_LE, BOM_UTF16_BE)) else data
    if len(body) % 2:
        body = body[:-1]
    return _strict(body, encoding), "UTF-16 LE" if encoding == UTF16LE else "UTF-16 BE"


def _lenient_text(data: bytes) -> tuple[str | None, str]:
    if data.startswith(BOM_UTF8):
        return _strict(data[3:], UTF8), "UTF-8（BOM）"
    if data.startswith(BOM_UTF32_LE):
        return _strict(data[4:], "utf-32-le"), "UTF-32 LE"
    if data.startswith(BOM_UTF32_BE):
        return _strict(data[4:], "utf-32-be"), "UTF-32 BE"
    utf16 = utf16_guess(data)
    if utf16 is not None:
        return _lenient_utf16(data, utf16)
    if looks_binary(data):
        return None, ""
    text = _strict(data, UTF8)
    if text is not None:
        return text, "ASCII" if data.isascii() else "UTF-8"
    return _lenient_legacy(data)


def decode_lenient(data: bytes) -> LenientText | None:
    """顯示用解碼（字幕、預覽）；二進位或解不開時回傳 None。"""
    text, name = _lenient_text(data)
    if text is None:
        return None
    normalized, ending = normalize_newlines(text)
    return LenientText(normalized, name, ending)
