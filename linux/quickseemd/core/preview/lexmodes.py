"""標記語言、鍵值設定檔、diff 三種模式（對應原版 QSLexer.runMarkup／runKeyValue／runDiff）。"""

from __future__ import annotations

import re

from .langbase import Language
from .languages import BY_ID, value_language
from .lexer import CODE, Tokens, run_generic

_MARKUP_NEXT = re.compile(rb"[<&]")
_TAG_NAME = re.compile(rb"[A-Za-z0-9_\x80-\xff\-:.]*")
_ATTR = re.compile(rb"""(?:[^ \t\r\n=>"'/]|/(?!>))*""")
_ENTITY = re.compile(rb"[A-Za-z0-9#]{0,11}")
_SPACES_TABS = re.compile(rb"[ \t]*")
_ALPHA = frozenset(range(65, 91)) | frozenset(range(97, 123))
_SPACE = frozenset(b" \t\r\n")
DIFF_META: tuple[bytes, ...] = tuple(
    s.encode()
    for s in (
        "diff ",
        "index ",
        "--- ",
        "+++ ",
        "new file",
        "deleted file",
        "similarity",
        "rename ",
        "old mode",
        "new mode",
        "Binary files",
    )
)


def lex(b: bytes, lang: Language, start: int, end: int, toks: Tokens) -> None:
    """依語言的模式分析 b[start:end]。"""
    mode = lang.mode
    if mode == "plain" or start >= end:
        return
    if mode == "markup":
        run_markup(b, lang, start, end, toks)
    elif mode == "keyValue":
        run_key_value(b, lang, start, end, toks)
    elif mode == "diff":
        run_diff(b, start, end, toks)
    else:
        run_generic(b, lang, start, end, toks)


def _add(toks: Tokens, s: int, e: int, t: str) -> None:
    if s < e:
        toks.extend((s, e, CODE[t]))


def _scan_to(b: bytes, pattern: bytes, start: int, end: int) -> int:
    k = b.find(pattern, start, end)
    return end if k < 0 else k + len(pattern)


# ---------------------------------------------------------------- 標記語言


def run_markup(b: bytes, lang: Language, start: int, end: int, toks: Tokens) -> None:
    i = start
    while i < end:
        m = _MARKUP_NEXT.search(b, i, end)
        if m is None:
            return
        i = m.start()
        j = _markup_lt(b, lang, i, end, toks) if b[i] == 60 else _entity(b, i, end, toks)
        i = j if j >= 0 else i + 1


def _entity(b: bytes, i: int, end: int, toks: Tokens) -> int:
    j = _ENTITY.match(b, i + 1, end).end()
    if j < end and b[j] == 59 and j > i + 1:
        _add(toks, i, j + 1, "lit")
        return j + 1
    return -1


def _markup_lt(b: bytes, lang: Language, i: int, end: int, toks: Tokens) -> int:
    if b.startswith(b"<!--", i, end):
        j = _scan_to(b, b"-->", i + 4, end)
        _add(toks, i, j, "c")
        return j
    if b.startswith(b"<![CDATA[", i, end):
        j = _scan_to(b, b"]]>", i + 9, end)
        _add(toks, i, j, "s")
        return j
    nxt = b[i + 1] if i + 1 < end else -1
    if nxt in (63, 33):
        j = _scan_to(b, b"?>" if nxt == 63 else b">", i + 2, end)
        _add(toks, i, j, "p")
        return j
    if nxt in _ALPHA or nxt == 47:
        return _tag(b, lang, i, end, toks)
    return -1


def _tag(b: bytes, lang: Language, i: int, end: int, toks: Tokens) -> int:
    j = i + 1
    closing = b[j] == 47
    if closing:
        j += 1
    ns = j
    j = _TAG_NAME.match(b, j, end).end()
    _add(toks, ns, j, "g")
    tag = b[ns:j].lower()
    j, self_closing = _attributes(b, j, end, toks)
    if j < end and b[j] == 62:
        j += 1
    embedded = dict(lang.embedded).get(tag.decode("utf-8", "replace"))
    if closing or self_closing or embedded is None or embedded not in BY_ID:
        return j
    close = re.compile(re.escape(b"</" + tag), re.IGNORECASE).search(b, j, end)
    k = close.start() if close else end
    lex(b, BY_ID[embedded], j, k, toks)
    return k


def _attributes(b: bytes, j: int, end: int, toks: Tokens) -> tuple[int, bool]:
    self_closing = False
    after_eq = False
    while j < end and b[j] != 62:
        d = b[j]
        if d in (34, 39):
            k = b.find(bytes((d,)), j + 1, end)
            k = end if k < 0 else min(k + 1, end)
            _add(toks, j, k, "s")
            j, after_eq = k, False
            continue
        if d == 47 and j + 1 < end and b[j + 1] == 62:
            self_closing = True
            j += 1
            continue
        if d == 61:
            after_eq = True
            j += 1
            continue
        if d == 60:
            break
        if d in _SPACE:
            j += 1
            continue
        s = j
        j = _ATTR.match(b, j, end).end()
        if j == s:
            j += 1
            continue
        _add(toks, s, j, "s" if after_eq else "a")
        after_eq = False
    return j, self_closing


# ---------------------------------------------------------------- 鍵值設定檔


def run_key_value(b: bytes, lang: Language, start: int, end: int, toks: Tokens) -> None:
    i = start
    while i < end:
        le = b.find(b"\n", i, end)
        le = end if le < 0 else le
        s = _SPACES_TABS.match(b, i, le).end()
        if s < le:
            _key_value_line(b, lang, i, s, le, toks)
        i = le + 1


def _key_value_line(b: bytes, lang: Language, i: int, s: int, le: int, toks: Tokens) -> None:
    value = value_language(lang.id)
    is_yaml = lang.id == "yaml"
    c = b[s]
    if any(b.startswith(m, s, le) for m in lang.line_comments):
        _add(toks, s, le, "c")
    elif is_yaml and s == i and (b.startswith(b"---", s, le) or b.startswith(b"...", s, le)):
        _add(toks, s, le, "k")
    elif not is_yaml and c == 91:
        j = b.find(b"]", s, le)
        j = min((le if j < 0 else j) + 1, le)
        if j < le and b[j] == 93:
            j += 1
        _add(toks, s, j, "hd")
        run_generic(b, value, j, le, toks)
    else:
        ks = s
        if is_yaml and c == 45 and s + 1 < le and b[s + 1] == 32:
            _add(toks, s, s + 2, "p")
            ks = s + 2
        if lang.id == "dotenv" and b.startswith(b"export ", ks, le):
            _add(toks, ks, ks + 6, "k")  # 原版只標 6 個字元
            ks += 6
        sep = _find_separator(b, lang, ks, le)
        if sep > ks:
            _add(toks, ks, sep, "y")
            run_generic(b, value, sep + 1, le, toks)
        else:
            run_generic(b, value, ks, le, toks)


_SEP_CACHE: dict[tuple[bytes, bool], re.Pattern[bytes]] = {}


def _separator_regex(seps: bytes, is_yaml: bool) -> re.Pattern[bytes]:
    """找分隔符號的正規表示式；YAML 的分隔符號後面要接空白或行尾，「 #」會停止搜尋。"""
    key = (seps, is_yaml)
    rx = _SEP_CACHE.get(key)
    if rx is None:
        cls = b"[" + re.escape(seps) + b"]"
        if is_yaml:
            rx = re.compile(b"(?P<sep>" + cls + rb"(?=[ \t]|\Z))|(?<= )(?P<hash>#)")
        else:
            rx = re.compile(b"(?P<sep>" + cls + b")")
        _SEP_CACHE[key] = rx
    return rx


def _find_separator(b: bytes, lang: Language, ks: int, le: int) -> int:
    """鍵與值之間的分隔符號位置（略過開頭的引號字串；YAML 的「 #」會停止搜尋）。"""
    is_yaml = lang.id == "yaml"
    j = ks
    if b[j] in (34, 39):  # 只有鍵開頭的引號算數，其他位置的引號照原版忽略
        close = b.find(bytes((b[j],)), j + 1, le)
        if close < 0:
            return -1
        j = close + 1
    rx = _separator_regex(lang.key_separators or b"=", is_yaml)
    while True:
        m = rx.search(b, j, le)
        if m is None:
            return -1
        if m.lastgroup == "sep":
            return m.start()
        if m.start() > ks:
            return -1
        j = m.start() + 1


# ---------------------------------------------------------------- diff


def run_diff(b: bytes, start: int, end: int, toks: Tokens) -> None:
    i = start
    while i < end:
        le = b.find(b"\n", i, end)
        le = end if le < 0 else le
        t = None
        if any(b.startswith(m, i, le) for m in DIFF_META):
            t = "dm"
        elif b.startswith(b"@@", i, le):
            t = "dh"
        elif i < le and b[i] == 43:
            t = "da"
        elif i < le and b[i] == 45:
            t = "dd"
        if t is not None:
            _add(toks, i, le, t)
        i = le + 1
