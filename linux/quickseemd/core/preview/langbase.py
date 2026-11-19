"""語言定義的資料型別（對應原版 QSLanguage／QSStringRule）。字詞一律用 bytes（UTF-8）。"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from types import MappingProxyType


@dataclass(frozen=True)
class StringRule:
    """字串規則；max_length > 0 時只有在這個長度內結束才算（字元常值用）。"""

    open: bytes
    close: bytes
    escape: bool = True
    multiline: bool = False
    max_length: int = 0


def rule(
    open_: str,
    close: str | None = None,
    *,
    escape: bool = True,
    multiline: bool = False,
    max_length: int = 0,
) -> StringRule:
    return StringRule(open_.encode(), (close or open_).encode(), escape, multiline, max_length)


DQ = rule('"')
SQ = rule("'")


def _empty_map() -> Mapping[bytes, str]:
    return MappingProxyType({})


@dataclass(frozen=True)
class Language:
    """一個語言的上色設定（純資料）。"""

    id: str
    name: str
    mode: str = "generic"  # generic / css / log / markup / keyValue / diff / plain
    keywords: frozenset[bytes] = frozenset()
    types: frozenset[bytes] = frozenset()
    literals: frozenset[bytes] = frozenset()
    builtins: frozenset[bytes] = frozenset()
    word_toks: Mapping[bytes, str] = field(default_factory=_empty_map)
    line_comments: tuple[bytes, ...] = ()
    block_comments: tuple[tuple[bytes, bytes], ...] = ()
    strings: tuple[StringRule, ...] = (DQ, SQ)
    case_insensitive: bool = False
    capitalized_types: bool = False
    preprocessor: bool = False
    sigils: bytes = b""
    sigil_tok: str = "a"
    decorators: bool = False
    at_keywords: bool = False
    ident_extra: bytes = b""
    ident_start_extra: bytes = b""
    key_separators: bytes = b""
    line_labels: bool = False
    hash_attributes: bool = False
    macro_bang: bool = False
    symbols: bool = False
    dot_directives: bool = False
    hash_comment_needs_space: bool = False
    wrap: bool = False
    embedded: tuple[tuple[str, str], ...] = ()

    def __hash__(self) -> int:  # word_toks 是唯讀對照表，以代碼識別即可
        return hash(self.id)


def words(s: str) -> frozenset[bytes]:
    return frozenset(w.encode() for w in s.split())


def lc(*items: str) -> tuple[bytes, ...]:
    return tuple(i.encode() for i in items)


C_BLOCK: tuple[tuple[bytes, bytes], ...] = ((b"/*", b"*/"),)
CSS_LITERALS = words(
    "auto none inherit initial unset block inline flex grid absolute relative fixed sticky "
    "solid transparent bold normal"
)


def log_tokens() -> Mapping[bytes, str]:
    """記錄檔的等級字（錯誤／警告／資訊／除錯）。"""
    groups = {
        "le": "ERROR ERR FATAL CRITICAL CRIT SEVERE PANIC FAILED FAILURE Error Exception error fatal",
        "lw": "WARN WARNING Warning warning warn",
        "li": "INFO NOTICE Info",
        "ld": "DEBUG TRACE VERBOSE Debug debug",
    }
    return MappingProxyType({w.encode(): tok for tok, ws in groups.items() for w in ws.split()})
