"""文字解碼（對應原版 QSText）：BOM、UTF-8、UTF-16 猜測，以及 Big5／GB18030／Shift-JIS 等舊編碼偵測。"""

from __future__ import annotations

import logging
from dataclasses import dataclass

log = logging.getLogger(__name__)

BINARY_SAMPLE = 16384
UTF16_SAMPLE = 2048
UTF16_MIN = 16

_LE = "utf-16-le"
_BE = "utf-16-be"
_ALLOWED_CONTROL = {9, 10, 12, 13, 27}
_CONTROL = {c for c in range(0x20) if c not in _ALLOWED_CONTROL}
_NOT_CONTROL = bytes(c for c in range(256) if c not in _CONTROL)

# 統計式偵測只在這幾種舊編碼裡挑（與原版的候選清單相同）
LEGACY_LABELS: dict[str, str] = {
    "big5": "繁體中文（Big 5）",
    "gb18030": "簡體中文（GB 18030）",
    "cp932": "日文（Shift JIS）",
    "euc_jp": "日文（EUC）",
    "euc_kr": "韓文（EUC）",
    "cp1252": "西歐語系（Windows Latin 1）",
    "mac_roman": "西歐語系（Mac OS Roman）",
}


@dataclass(frozen=True)
class DecodedText:
    text: str  # 已統一為 LF 換行
    encoding: str  # 顯示用名稱
    line_ending: str  # LF / CRLF / CR / 混合
    truncated: bool


def _utf16_guess(data: bytes) -> str | None:
    head = data[:UTF16_SAMPLE]
    if len(head) >= 2:
        if head[0] == 0xFF and head[1] == 0xFE:
            return _LE
        if head[0] == 0xFE and head[1] == 0xFF:
            return _BE
    if len(head) < UTF16_MIN:
        return None
    even_zero = head[0::2].count(0)
    odd_zero = head[1::2].count(0)
    half = len(head) // 2
    if odd_zero > half * 7 // 10 and even_zero < half // 10:
        return _LE
    if even_zero > half * 7 // 10 and odd_zero < half // 10:
        return _BE
    return None


def looks_binary(data: bytes) -> bool:
    """看起來是不是二進位（含 NUL 或大量控制字元）；UTF-16 另外處理，不算二進位。"""
    if not data:
        return False
    if _utf16_guess(data) is not None:
        return False
    sample = data[:BINARY_SAMPLE]
    if b"\x00" in sample:
        return True
    control = len(sample.translate(None, _NOT_CONTROL))
    return control * 100 > len(sample) * 2


def _strict(data: bytes, encoding: str) -> str | None:
    try:
        return data.decode(encoding)
    except (UnicodeDecodeError, LookupError):
        return None


def _utf8(data: bytes, allow_trim: bool) -> str | None:
    """UTF-8 解碼；檔案被截斷時，容許結尾有被切開的多位元組字元。"""
    s = _strict(data, "utf-8")
    if s is not None or not allow_trim:
        return s
    for cut in range(1, 4):
        if len(data) > cut:
            s = _strict(data[:-cut], "utf-8")
            if s is not None:
                return s
    return None


def _legacy(data: bytes) -> tuple[str, str]:
    """統計式偵測舊編碼；都不像就當 ISO Latin-1。"""
    try:
        import charset_normalizer  # noqa: PLC0415 — 只有少數檔案需要

        best = charset_normalizer.from_bytes(data, cp_isolation=list(LEGACY_LABELS)).best()
    except Exception:  # noqa: BLE001 — 偵測失敗就退回 Latin-1
        log.debug("編碼偵測失敗", exc_info=True)
        best = None
    if best is not None:
        name = best.encoding
        s = _strict(data, name)
        if s is not None and name in LEGACY_LABELS:
            return s, LEGACY_LABELS[name]
    return data.decode("latin-1"), "ISO Latin-1"


def _utf16(data: bytes, encoding: str) -> tuple[str | None, str]:
    d = data
    if d.startswith((b"\xff\xfe", b"\xfe\xff")):
        d = d[2:]
    if len(d) % 2 == 1:
        d = d[:-1]
    return _strict(d, encoding), ("UTF-16 LE" if encoding == _LE else "UTF-16 BE")


def _decode_raw(data: bytes, truncated: bool) -> tuple[str | None, str]:
    if data.startswith(b"\xef\xbb\xbf"):
        return _utf8(data[3:], truncated), "UTF-8（BOM）"
    if data.startswith(b"\xff\xfe\x00\x00"):
        return _strict(data[4:], "utf-32-le"), "UTF-32 LE"
    if data.startswith(b"\x00\x00\xfe\xff"):
        return _strict(data[4:], "utf-32-be"), "UTF-32 BE"
    guess = _utf16_guess(data)
    if guess is not None:
        return _utf16(data, guess)
    if looks_binary(data):
        return None, ""
    s = _utf8(data, truncated)
    if s is not None:
        return s, ("ASCII" if data.isascii() else "UTF-8")
    return _legacy(data)


def decode(raw: bytes, truncated: bool) -> DecodedText | None:
    """解碼成文字；看起來是二進位或解不開時回傳 None。"""
    s, name = _decode_raw(raw, truncated)
    if s is None:
        return None
    normalized, ending = normalize_newlines(s)
    return DecodedText(normalized, name, ending, truncated)


def normalize_newlines(s: str) -> tuple[str, str]:
    """統一成 LF，並回傳原本的換行種類（沒有換行時算 LF）。"""
    crlf = s.count("\r\n")
    cr = s.count("\r") - crlf
    lf = s.count("\n") - crlf
    kinds = sum(1 for k in (crlf, cr, lf) if k > 0)
    if kinds > 1:
        ending = "混合"
    elif crlf:
        ending = "CRLF"
    elif cr:
        ending = "CR"
    else:
        ending = "LF"
    if not crlf and not cr:
        return s, ending
    out = s.replace("\r\n", "\n")
    if cr:
        out = out.replace("\r", "\n")
    return out, ending
