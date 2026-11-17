"""通用模式的詞法分析（對應原版 QSLexer.runGeneric，逐條照搬判斷順序）。

原版逐位元組掃描；這裡改成「依開頭位元組查表」＋預先編譯的正規表示式找出每個詞的結尾，
大量的空白與標點一次跳過，行為與原版相同但快得多。輸出是 (起點, 終點, 類別) 的清單，
沒有上色的文字不記錄。各位置依序檢查：
日誌時間戳記 → 前置處理指令 → Rust 屬性 → 區塊註解 → 行註解 → 字串 → 變數符號 →
@裝飾器 → CSS → 組合語言指令 → :符號 → 數字 → 識別字。
"""

from __future__ import annotations

import re
from collections.abc import Callable, Iterable

from .langbase import Language, StringRule

Token = tuple[int, int, str]

_ALPHA = frozenset(range(65, 91)) | frozenset(range(97, 123))
_DIGITS = frozenset(range(48, 58))
_HIGH = frozenset(range(0x80, 0x100))
_HEX = _DIGITS | frozenset(b"abcdefABCDEF")
_SPACE = frozenset(b" \t\r\n")
TYPE_DECL = frozenset(
    (
        b"class struct enum protocol interface trait extension union object record actor type "
        b"typealias impl module namespace data newtype mixin message service contract library "
        b"defmodule"
    ).split()
)
FN_DECL = frozenset(b"func def function fn fun sub proc defn defp macro rpc method".split())
_TS_CHARS = b"[0123456789-:./,TZ+"
_CI = bytes((c | 0x20) & 0xFF for c in range(256))  # 原版的大小寫比對：位元組 | 0x20

_WS_RUN = re.compile(rb"[ \t\r]+")
_TS_BODY = re.compile(rb"(?: (?=[0-9])|[0-9\-:./,TZ+]){0,40}")
_TO_BRACE = re.compile(rb"[^}\n]*")
_SPACES_TABS = re.compile(rb"[ \t]*")
_HEX_RUN = re.compile(rb"[0-9A-Fa-f]*")
_IMPORTANT = b"!important"


def char_class(values: Iterable[int]) -> bytes:
    """位元組集合 → 正規表示式字元類別（[...]）。"""
    vals = sorted(set(values))
    parts: list[bytes] = []
    i = 0
    while i < len(vals):
        j = i
        while j + 1 < len(vals) and vals[j + 1] == vals[j] + 1:
            j += 1
        parts.append(b"\\x%02x" % vals[i])
        if j > i:
            parts.append(b"-\\x%02x" % vals[j])
        i = j + 1
    return b"[" + b"".join(parts) + b"]" if parts else rb"[^\x00-\xff]"


def _string_regex(r: StringRule) -> re.Pattern[bytes]:
    """字串規則 → 正規表示式；close 群組有值表示字串有結束。"""
    c0 = r.close[:1]
    excluded = {c0[0]}
    if r.escape:
        excluded.add(92)
    if not r.multiline:
        excluded.add(10)
    alts = [b"[^" + char_class(excluded)[1:-1] + b"]"]
    if r.escape:
        alts.append(rb"\\[\s\S]?")
    if len(r.close) > 1:
        alts.append(re.escape(c0) + b"(?!" + re.escape(r.close[1:]) + b")")
    body = b"(?:" + b"|".join(alts) + b")*"
    return re.compile(re.escape(r.open) + body + b"(?P<close>" + re.escape(r.close) + b")?")


class Spec:
    """一個語言預先算好的查表與正規表示式。"""

    def __init__(self, lang: Language) -> None:
        self.lang = lang
        self.ident_start = _ALPHA | _HIGH | {95} | frozenset(lang.ident_start_extra)
        self.ident = _ALPHA | _DIGITS | _HIGH | {95} | frozenset(lang.ident_extra)
        ident_cls = char_class(self.ident)
        self.word_re = re.compile(char_class(self.ident_start) + ident_cls + b"*")
        self.ident_run = re.compile(ident_cls + b"*")
        self.dotted_run = re.compile(b"(?:" + ident_cls + rb"|\.)*")
        number = (
            rb"0[xX][0-9A-Fa-f_]*[A-Za-z0-9]*"
            rb"|(?:[0-9_]|\.(?=[0-9])|[eE][0-9]|[eE][+\-](?=[0-9]))+[A-Za-z0-9]*"
        )
        self.number_re = re.compile(b"(?:" + number + b")" + (b"%?" if lang.mode == "css" else b""))
        self.strings = tuple((r, _string_regex(r)) for r in lang.strings)
        self.line_comments = tuple((c, c.translate(_CI)) for c in lang.line_comments)
        self.key_separators = frozenset(lang.key_separators)
        self.table = self._build_table()
        inert = [c for c in range(256) if not self.table[c] and c not in _SPACE]
        self.inert_re = re.compile(char_class(inert) + b"+")

    def _starts(self) -> list[tuple[Callable[..., int], set[int]]]:
        lang = self.lang
        line_starts: set[int] = set()
        for c in lang.line_comments:
            if lang.case_insensitive:
                line_starts |= {x for x in range(256) if _CI[x] == _CI[c[0]]}
            else:
                line_starts.add(c[0])
        return [
            (GenericLexer.h_timestamp, set(_TS_CHARS) if lang.mode == "log" else set()),
            (GenericLexer.h_preproc, {35} if lang.preprocessor else set()),
            (GenericLexer.h_hash_attr, {35} if lang.hash_attributes else set()),
            (GenericLexer.h_block, {o[0] for o, _c in lang.block_comments}),
            (GenericLexer.h_line, line_starts),
            (GenericLexer.h_string, {r.open[0] for r in lang.strings}),
            (GenericLexer.h_sigil, set(lang.sigils)),
            (GenericLexer.h_at, {64} if lang.decorators or lang.at_keywords else set()),
            (GenericLexer.h_css, set(b"{}#.!") if lang.mode == "css" else set()),
            (GenericLexer.h_dot_directive, {46} if lang.dot_directives else set()),
            (GenericLexer.h_symbol, {58} if lang.symbols else set()),
            (GenericLexer.h_number, set(_DIGITS) | {46}),
            (GenericLexer.h_ident, set(self.ident_start)),
        ]

    def _build_table(self) -> list[tuple[Callable[..., int], ...]]:
        starts = self._starts()
        return [tuple(h for h, chars in starts if c in chars) for c in range(256)]


_SPECS: dict[str, Spec] = {}


def spec_for(lang: Language) -> Spec:
    spec = _SPECS.get(lang.id)
    if spec is None or spec.lang is not lang:
        spec = Spec(lang)
        _SPECS[lang.id] = spec
    return spec


class GenericLexer:
    """通用／CSS／日誌模式的狀態機。"""

    def __init__(self, b: bytes, spec: Spec, toks: list[Token], start: int, end: int) -> None:
        self.b = b
        self.spec = spec
        self.lang = spec.lang
        self.toks = toks
        self.start = start
        self.end = end
        self.line_start = True
        self.pending: str | None = None
        self.depth = 0

    def tok(self, s: int, e: int, t: str | None) -> None:
        if t is not None and s < e:
            self.toks.append((s, e, t))

    def eol(self, i: int) -> int:
        j = self.b.find(b"\n", i, self.end)
        return self.end if j < 0 else j

    def run(self) -> None:
        b, end, table = self.b, self.end, self.spec.table
        ws, inert = _WS_RUN.match, self.spec.inert_re.match
        i = self.start
        while i < end:
            c = b[i]
            if c == 10:
                self.line_start = True
                i += 1
                continue
            if c == 32 or c == 9 or c == 13:
                i = ws(b, i, end).end()
                continue
            handlers = table[c]
            if not handlers:
                j = inert(b, i, end).end()
                if self.pending is not None and b[i:j].translate(None, b"*&"):
                    self.pending = None
                self.line_start = False
                i = j
                continue
            for h in handlers:
                j = h(self, i, c)
                if j >= 0:
                    i = j
                    break
            else:
                if c != 42 and c != 38:  # `func *`、`&` 之類不清除
                    self.pending = None
                self.line_start = False
                i += 1

    # ---------------------------------------------------------------- 各種開頭

    def h_timestamp(self, i: int, c: int) -> int:
        if not self.line_start:
            return -1
        b, end = self.b, self.end
        bracket = c == 91
        s = i + 1 if bracket else i
        j = _TS_BODY.match(b, s, end).end()
        seg = b[s:j]
        if j - s < 8 or len(seg) - len(seg.translate(None, b"0123456789")) < 6:
            return -1
        if bracket and j < end and b[j] == 93:
            j += 1
        self.tok(i, j, "lt")
        self.line_start = False
        return j

    def h_preproc(self, i: int, c: int) -> int:
        if not self.line_start:
            return -1
        b = self.b
        j = self.eol(i)
        while i < j < self.end and b[j - 1] == 92:
            j = self.eol(j + 1)
        self.tok(i, j, "p")
        self.line_start = False
        return j

    def h_hash_attr(self, i: int, c: int) -> int:
        b, end = self.b, self.end
        nxt = b[i + 1] if i + 1 < end else -1
        if not (nxt == 91 or (nxt == 33 and i + 2 < end and b[i + 2] == 91)):
            return -1
        j, d = i + 1, 0
        while j < end:
            x = b[j]
            if x == 91:
                d += 1
            elif x == 93:
                d -= 1
                if d == 0:
                    j += 1
                    break
            elif x == 10:
                break
            j += 1
        self.tok(i, j, "a")
        self.line_start = False
        return j

    def h_block(self, i: int, c: int) -> int:
        b, end = self.b, self.end
        for opener, closer in self.lang.block_comments:
            if b.startswith(opener, i, end):
                k = b.find(closer, i + len(opener), end)
                j = k + len(closer) if k >= 0 else end
                self.tok(i, j, "c")
                self.line_start = False
                return j
        return -1

    def h_line(self, i: int, c: int) -> int:
        b, end, lang = self.b, self.end, self.lang
        for marker, folded in self.spec.line_comments:
            if lang.case_insensitive:
                hit = i + len(marker) <= end and b[i : i + len(marker)].translate(_CI) == folded
            else:
                hit = b.startswith(marker, i, end)
            if not hit:
                continue
            if (
                marker == b"#"
                and lang.hash_comment_needs_space
                and i > self.start
                and b[i - 1] not in _SPACE
                and b[i - 1] != 59
            ):
                continue
            j = self.eol(i)
            self.tok(i, j, "c")
            return j
        return -1

    def h_string(self, i: int, c: int) -> int:
        b, end = self.b, self.end
        for r, rx in self.spec.strings:
            if not b.startswith(r.open, i, end):
                continue
            m = rx.match(b, i, end)
            k = m.end()
            closed = m.group("close") is not None
            if r.max_length > 0 and (not closed or k - i > r.max_length):
                continue
            t = "s"
            if self.spec.key_separators:
                n = _SPACES_TABS.match(b, k, end).end()
                if n < end and b[n] in self.spec.key_separators:
                    t = "y"
            self.tok(i, k, t)
            self.line_start = False
            self.pending = None
            return k
        return -1

    def h_sigil(self, i: int, c: int) -> int:
        b, end = self.b, self.end
        if i + 1 >= end:
            return -1
        n = b[i + 1]
        j = i + 1
        if n == 123:
            j = _TO_BRACE.match(b, i + 1, end).end()
            if j < end and b[j] == 125:
                j += 1
        elif n in self.spec.ident_start or n in _DIGITS:
            j = self.spec.ident_run.match(b, i + 1, end).end()
            if c == 37 and j < end and b[j] == 37:  # %VAR%
                j += 1
        elif c == 36 and n in b"@?#$!*-(":
            j = i + 2
        if j <= i + 1:
            return -1
        self.tok(i, j, self.lang.sigil_tok)
        self.line_start = False
        return j

    def h_at(self, i: int, c: int) -> int:
        b, end = self.b, self.end
        if i + 1 >= end or b[i + 1] not in self.spec.ident_start:
            return -1
        j = self.spec.dotted_run.match(b, i + 1, end).end()
        self.tok(i, j, "k" if self.lang.at_keywords else "a")
        self.line_start = False
        return j

    def h_css(self, i: int, c: int) -> int:
        b, end = self.b, self.end
        if c == 123:
            self.depth += 1
        elif c == 125:
            self.depth = max(0, self.depth - 1)
        nxt = b[i + 1] if i + 1 < end else -1
        if c == 35 and nxt in _HEX and self.depth > 0:
            j = _HEX_RUN.match(b, i + 1, end).end()
            self.tok(i, j, "n")
            return j
        if c in (46, 35) and self.depth == 0 and nxt in self.spec.ident_start:
            j = self.spec.ident_run.match(b, i + 1, end).end()
            self.tok(i, j, "t" if c == 46 else "a")
            return j
        if c == 33 and i + 10 <= end and b[i : i + 10].translate(_CI) == _IMPORTANT:
            self.tok(i, i + 10, "k")
            return i + 10
        return -1

    def h_dot_directive(self, i: int, c: int) -> int:
        b, end = self.b, self.end
        if i + 1 >= end or b[i + 1] not in _ALPHA:
            return -1
        if i != self.start and b[i - 1] in self.spec.ident:
            return -1
        j = self.spec.ident_run.match(b, i + 1, end).end()
        self.tok(i, j, "p")
        self.line_start = False
        return j

    def h_symbol(self, i: int, c: int) -> int:
        b, end = self.b, self.end
        if i + 1 >= end or b[i + 1] not in self.spec.ident_start:
            return -1
        if i != self.start and (b[i - 1] == 58 or b[i - 1] in self.spec.ident):
            return -1
        j = self.spec.ident_run.match(b, i + 1, end).end()
        self.tok(i, j, "lit")
        self.line_start = False
        return j

    def h_number(self, i: int, c: int) -> int:
        b, end = self.b, self.end
        if c == 46:
            if i + 1 >= end or b[i + 1] not in _DIGITS:
                return -1
            if i != self.start and b[i - 1] in self.spec.ident:
                return -1
        j = self.spec.number_re.match(b, i, end).end()
        self.tok(i, j, "n")
        self.line_start = False
        self.pending = None
        return j

    def h_ident(self, i: int, c: int) -> int:
        b, end = self.b, self.end
        j = self.spec.word_re.match(b, i, end).end()
        word = b[i:j]
        key = word.lower() if self.lang.case_insensitive else word
        t, j = self._classify(key, c, j)
        self.tok(i, j, t)
        self.line_start = False
        return j

    def _classify(self, key: bytes, c: int, j: int) -> tuple[str | None, int]:
        lang, b, end = self.lang, self.b, self.end
        n = b[j] if j < end else 0
        word_tok = lang.word_toks.get(key)
        if word_tok is not None:
            return word_tok, j
        if key in lang.keywords:
            if key in TYPE_DECL:
                self.pending = "t"
            elif key in FN_DECL:
                self.pending = "f"
            return "k", j
        if self.pending is not None:
            t, self.pending = self.pending, None
            return t, j
        if key in lang.literals:
            return "lit", j
        if key in lang.types:
            return "t", j
        if lang.macro_bang and n == 33 and not (j + 1 < end and b[j + 1] == 61):
            return "p", j + 1
        if lang.mode == "css":
            return self._css_word(n, j), j
        if n == 40:
            return "f", j
        if lang.line_labels and self.line_start and n == 58:
            if not (j + 1 < end and b[j + 1] in (61, 58)):
                return "f", j
        if key in lang.builtins:
            return "f", j
        if lang.capitalized_types and 65 <= c <= 90:
            return "t", j
        return None, j

    def _css_word(self, n: int, j: int) -> str | None:
        nn = _SPACES_TABS.match(self.b, j, self.end).end()
        if self.depth > 0 and nn < self.end and self.b[nn] == 58:
            return "y"
        if self.depth == 0:
            return "g"
        return "f" if n == 40 else None


def run_generic(b: bytes, lang: Language, start: int, end: int, toks: list[Token]) -> None:
    """以通用模式分析 b[start:end]，把上色的詞加進 toks。"""
    GenericLexer(b, spec_for(lang), toks, start, end).run()
