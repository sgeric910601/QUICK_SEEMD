"""萬用轉換用的文字編碼（對應原版 CVTextCodec）。

解碼、偵測、換行與編碼都直接用共用的 tools/other/textcodec；這裡只補轉換需要的部分：
選項 source_encoding（也接受原版命令列的寫法）對照到 Python 編碼名稱，以及編碼家族的判斷。
Big5 輸出一律是 CP950（見 text.py）。
"""

from __future__ import annotations

import codecs
from pathlib import Path

from ..tools.other import textcodec as _shared

LineEnding = _shared.LineEnding
Decoded = _shared.Decoded
display_name = _shared.display_name
detect_line_ending = _shared.detect_line_ending
normalize_line_endings = _shared.normalize_line_endings
encode = _shared.encode

# 轉換選項 source_encoding（也接受原版命令列的寫法）→ Python 編碼名稱
_FORCED: dict[str, str | None] = {
    "auto": None,
    "utf-8": "utf-8",
    "utf8": "utf-8",
    "utf-16": "utf-16",
    "utf16": "utf-16",
    "big5": _shared.BIG5,
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


def decode_bytes(data: bytes, forced: str | None = None) -> Decoded:
    return _shared.decode(data, forced)


def decode_file(path: Path, forced: str | None = None) -> Decoded:
    return _shared.decode_file(path, forced)


def forced_codec(option: str | None) -> str | None:
    """選項 source_encoding → 要強制使用的編碼；auto 或不認得的值回傳 None（自動偵測）。"""
    if not option:
        return None
    return _FORCED.get(option.strip().lower())


def normalized(codec: str) -> str:
    """Python 編碼的正式名稱（utf-8 → utf_8），查不到時原樣小寫。"""
    try:
        return codecs.lookup(codec).name.replace("-", "_")
    except LookupError:
        return codec.lower().replace("-", "_")


def is_utf8(codec: str) -> bool:
    return normalized(codec) == "utf_8"


def is_utf16(codec: str) -> bool:
    return normalized(codec) in ("utf_16", "utf_16_le", "utf_16_be")


def is_utf32(codec: str) -> bool:
    return normalized(codec) in ("utf_32", "utf_32_le", "utf_32_be")


def is_big5(codec: str) -> bool:
    return normalized(codec) in ("big5hkscs", "big5", "cp950")
