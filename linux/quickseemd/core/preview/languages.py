"""語法上色的語言表（對應原版 QSLanguages）：全部語言與「值」用的小型語言。"""

from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
from types import MappingProxyType

from .langbase import DQ, SQ, Language, StringRule, lc, rule, words
from .langtable import table

ALL: tuple[Language, ...] = table()
BY_ID: Mapping[str, Language] = MappingProxyType({lang.id: lang for lang in ALL})


@lru_cache(maxsize=32)
def value_language(lang_id: str) -> Language:
    """鍵值設定檔的「值」所用的小型語言（YAML 有較多字面值與錨點符號）。"""
    is_yaml = lang_id == "yaml"
    literals = "true false null yes no on off True False Null ~" if is_yaml else "true false"
    if is_yaml:
        sigils = b"&*"
    elif lang_id == "dotenv":
        sigils = b"$"
    else:
        sigils = b""
    return Language(
        f"value-{lang_id}",
        "",
        literals=words(literals),
        strings=(DQ, SQ),
        line_comments=() if lang_id in ("ini", "properties") else lc("#"),
        hash_comment_needs_space=True,
        sigils=sigils,
    )


__all__ = ["ALL", "BY_ID", "Language", "StringRule", "rule", "value_language"]
