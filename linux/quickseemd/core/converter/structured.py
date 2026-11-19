"""保留鍵順序的 JSON 解析／輸出與 YAML 輸出（對應原版 CVStructuredData）。

值的表示法：
- None（null）、bool、Num（保留原始數字字串，不損失精度）、str
- tuple（陣列）、Obj（物件；成員是保留順序、可重複鍵的 (鍵, 值) 序列）
"""

from __future__ import annotations

import math
import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from typing import Any, Union

from .. import errors

MAX_DEPTH = 512
# 512 層巢狀時解析與輸出都會遞迴，每層約 2 個呼叫框，需要比預設 1000 更大的上限
_RECURSION_LIMIT = 8 * MAX_DEPTH + 2000


@contextmanager
def deep_recursion() -> Iterator[None]:
    """暫時放寬遞迴上限（處理深層巢狀資料）。"""
    old = sys.getrecursionlimit()
    if old < _RECURSION_LIMIT:
        sys.setrecursionlimit(_RECURSION_LIMIT)
    try:
        yield
    finally:
        if old < _RECURSION_LIMIT:
            sys.setrecursionlimit(old)


@dataclass(frozen=True)
class Num:
    text: str


@dataclass(frozen=True)
class Obj:
    members: tuple[tuple[str, Any], ...] = ()

    def get(self, key: str, default: Any = None) -> Any:
        for k, v in self.members:
            if k == key:
                return v
        return default


Value = Union[None, bool, Num, str, tuple, Obj]


def cell_text(value: Value) -> str:
    """給表格儲存格用的文字。"""
    if value is None:
        return ""
    if value is True:
        return "true"
    if value is False:
        return "false"
    if isinstance(value, Num):
        return value.text
    if isinstance(value, str):
        return value
    return write_json(value, pretty=False)


# ---------------------------------------------------------------- Swift 的 Double(String)

_DEC_FLOAT = re.compile(r"[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")
_HEX_FLOAT = re.compile(
    r"[+-]?0[xX](?:[0-9a-fA-F]+\.?[0-9a-fA-F]*|\.[0-9a-fA-F]+)(?:[pP][+-]?[0-9]+)?"
)
_SPECIAL_FLOAT = re.compile(r"[+-]?(?:inf|infinity|nan)", re.IGNORECASE)


def swift_double(s: str) -> float | None:
    """模擬 Swift 的 Double(s)：整個字串必須是十進位、十六進位浮點數或 inf／nan。"""
    if _DEC_FLOAT.fullmatch(s):
        return float(s)
    if _SPECIAL_FLOAT.fullmatch(s):
        return float(s)
    if _HEX_FLOAT.fullmatch(s):
        body = s.lstrip("+-")
        if "p" not in body.lower():
            body += "p0"
        value = float.fromhex(body)
        return -value if s.startswith("-") else value
    return None


# ---------------------------------------------------------------- JSON 解析

_WS = re.compile(r"[ \t\r]*")
_STRING_CHUNK = re.compile(r'[^"\\\n]+')
_NUMBER = re.compile(r"[+-]?[0-9.eE+-]*")
_HEX4 = re.compile(r"[0-9a-fA-F]{4}")
_ESCAPES = {'"': '"', "\\": "\\", "/": "/", "b": "\b", "f": "\f", "n": "\n", "r": "\r", "t": "\t"}


class _Parser:
    """寬鬆的 JSON 解析器：支援 // 與 /* */ 註解、結尾逗號、數字前的 +。"""

    def __init__(self, text: str) -> None:
        self.s = text
        self.n = len(text)
        self.pos = 1 if text.startswith("\N{ZERO WIDTH NO-BREAK SPACE}") else 0
        self.line = 1

    def error(self, message: str) -> errors.QSError:
        return errors.invalid_data(f"JSON 第 {self.line} 行：{message}")

    def skip_ws(self) -> None:
        s, n = self.s, self.n
        while self.pos < n:
            self.pos = _WS.match(s, self.pos).end()
            if self.pos >= n:
                return
            c = s[self.pos]
            if c == "\n":
                self.line += 1
                self.pos += 1
            elif s.startswith("//", self.pos):
                end = s.find("\n", self.pos)
                self.pos = n if end < 0 else end
            elif s.startswith("/*", self.pos):
                end = s.find("*/", self.pos + 2)
                stop = n if end < 0 else end
                self.line += s.count("\n", self.pos, stop)
                self.pos = stop + 2
            else:
                return

    def peek(self) -> str:
        return self.s[self.pos] if self.pos < self.n else ""

    def value(self, depth: int) -> Value:
        if depth >= MAX_DEPTH:
            raise self.error("巢狀層數太深。")
        self.skip_ws()
        if self.pos >= self.n:
            raise self.error("內容意外結束。")
        c = self.s[self.pos]
        if c == "{":
            return self.obj(depth)
        if c == "[":
            return self.arr(depth)
        if c == '"':
            return self.string()
        for word, val in (("true", True), ("false", False), ("null", None)):
            if c == word[0]:
                if not self.s.startswith(word, self.pos):
                    raise self.error("無法辨識的值。")
                self.pos += len(word)
                return val
        return self.number()

    def obj(self, depth: int) -> Obj:
        self.pos += 1
        members: list[tuple[str, Value]] = []
        while True:
            self.skip_ws()
            if self.peek() == "}":
                self.pos += 1
                return Obj(tuple(members))
            if self.peek() != '"':
                raise self.error("物件的鍵必須是字串。")
            key = self.string()
            self.skip_ws()
            if self.peek() != ":":
                raise self.error("缺少冒號。")
            self.pos += 1
            members.append((key, self.value(depth + 1)))
            self.skip_ws()
            if self.pos >= self.n:
                raise self.error("物件沒有結束。")
            c = self.s[self.pos]
            self.pos += 1
            if c == "}":
                return Obj(tuple(members))
            if c != ",":
                self.pos -= 1
                raise self.error("物件中缺少逗號。")

    def arr(self, depth: int) -> tuple:
        self.pos += 1
        items: list[Value] = []
        while True:
            self.skip_ws()
            if self.peek() == "]":
                self.pos += 1
                return tuple(items)
            items.append(self.value(depth + 1))
            self.skip_ws()
            if self.pos >= self.n:
                raise self.error("陣列沒有結束。")
            c = self.s[self.pos]
            self.pos += 1
            if c == "]":
                return tuple(items)
            if c != ",":
                self.pos -= 1
                raise self.error("陣列中缺少逗號。")

    def number(self) -> Num:
        m = _NUMBER.match(self.s, self.pos)
        text = m.group(0)
        if not text or swift_double(text) is None:
            raise self.error("無法辨識的值。")
        self.pos = m.end()
        return Num(text[1:] if text.startswith("+") else text)

    def hex4(self) -> int:
        m = _HEX4.match(self.s, self.pos)
        if not m:
            raise self.error("不正確的 \\u 跳脫。")
        self.pos = m.end()
        return int(m.group(0), 16)

    def unicode_escape(self) -> str:
        code = self.hex4()
        if 0xD800 <= code <= 0xDBFF and self.s.startswith("\\u", self.pos):
            self.pos += 2
            low = self.hex4()
            if 0xDC00 <= low <= 0xDFFF:
                code = 0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00)
        if 0xD800 <= code <= 0xDFFF:
            return "�"
        return chr(code)

    def string(self) -> str:
        self.pos += 1
        out: list[str] = []
        s, n = self.s, self.n
        while self.pos < n:
            m = _STRING_CHUNK.match(s, self.pos)
            if m:
                out.append(m.group(0))
                self.pos = m.end()
                continue
            c = s[self.pos]
            self.pos += 1
            if c == '"':
                return "".join(out)
            if c == "\n":
                self.line += 1
                out.append(c)
                continue
            if self.pos >= n:
                break
            e = s[self.pos]
            self.pos += 1
            if e == "u":
                out.append(self.unicode_escape())
            elif e in _ESCAPES:
                out.append(_ESCAPES[e])
            else:
                raise self.error("不正確的跳脫字元。")
        raise self.error("字串沒有結束。")


def parse_json(text: str) -> Value:
    parser = _Parser(text)
    with deep_recursion():
        value = parser.value(0)
    parser.skip_ws()
    if parser.pos < parser.n:
        raise errors.invalid_data(f"JSON 第 {parser.line} 行有多餘的內容。")
    return value


# ---------------------------------------------------------------- JSON 輸出

# JSON 字串要跳脫的字元：引號、反斜線、控制字元（含 C1）、U+2028／U+2029、BOM、
# 單獨的代理字元與非字元 U+FFFE／U+FFFF（原版只跳脫前三類；其餘是為了安全與可讀性補上）
_QUOTE_SPECIAL = re.compile(
    '["\\\\\x00-\x1f\x7f-\x9f\N{LINE SEPARATOR}\N{PARAGRAPH SEPARATOR}\N{ZERO WIDTH NO-BREAK SPACE}\ud800-\udfff\ufffe\uffff]'
)
_QUOTE_MAP = {'"': '\\"', "\\": "\\\\", "\n": "\\n", "\r": "\\r", "\t": "\\t", "\b": "\\b"}
_QUOTE_MAP["\f"] = "\\f"


def _quote_char(m: re.Match[str]) -> str:
    c = m.group(0)
    if c in _QUOTE_MAP:
        return _QUOTE_MAP[c]
    if ord(c) == 0x7F:
        return c
    return f"\\u{ord(c):04x}"


def quote(s: str) -> str:
    return '"' + _QUOTE_SPECIAL.sub(_quote_char, s) + '"'


# JSON 數字文法；不符合的數字字串一律不原樣寫出（避免注入額外內容）
_JSON_NUMBER = re.compile(r"-?(?:0|[1-9][0-9]*)(?:\.[0-9]+)?(?:[eE][+-]?[0-9]+)?")
_PLAIN_INT = re.compile(r"[+-]?[0-9]+")


def safe_number_text(text: str) -> str | None:
    """可以原樣寫進 JSON／YAML／plist 的數字文字；寬鬆寫法會正規化，無法表示時回傳 None。"""
    if _JSON_NUMBER.fullmatch(text):
        return text
    if _PLAIN_INT.fullmatch(text):
        return str(int(text))
    value = swift_double(text)
    if value is None or not math.isfinite(value):
        return None
    return repr(value)


def _emit(value: Value, pretty: bool, indent: str, out: list[str]) -> None:
    if value is None:
        out.append("null")
    elif value is True or value is False:
        out.append("true" if value else "false")
    elif isinstance(value, Num):
        safe = safe_number_text(value.text)
        out.append(safe if safe is not None else quote(value.text))
    elif isinstance(value, str):
        out.append(quote(value))
    elif isinstance(value, tuple):
        _emit_items(value, pretty, indent, out)
    else:
        _emit_members(value, pretty, indent, out)


def _emit_items(items: tuple, pretty: bool, indent: str, out: list[str]) -> None:
    if not items:
        out.append("[]")
        return
    out.append("[")
    inner = indent + "  "
    for i, item in enumerate(items):
        if i:
            out.append(",")
        if pretty:
            out.append("\n" + inner)
        _emit(item, pretty, inner, out)
    if pretty:
        out.append("\n" + indent)
    out.append("]")


def _emit_members(obj: Obj, pretty: bool, indent: str, out: list[str]) -> None:
    if not obj.members:
        out.append("{}")
        return
    out.append("{")
    inner = indent + "  "
    sep = ": " if pretty else ":"
    for i, (k, v) in enumerate(obj.members):
        if i:
            out.append(",")
        if pretty:
            out.append("\n" + inner)
        out.append(quote(k) + sep)
        _emit(v, pretty, inner, out)
    if pretty:
        out.append("\n" + indent)
    out.append("}")


def write_json(value: Value, pretty: bool) -> str:
    out: list[str] = []
    with deep_recursion():
        _emit(value, pretty, "", out)
    return "".join(out)


# ---------------------------------------------------------------- YAML 輸出

_YAML_RESERVED = frozenset(
    {"true", "false", "yes", "no", "on", "off", "null", "~", "y", "n", ".nan", ".inf", "-.inf"}
)
_YAML_LEAD = set("-?:,[]{}#&*!|>'\"%@`")
# YAML 可列印字元以外的字元（含 C1 控制字元、代理字元、U+FFFE／U+FFFF），
# 以及雖可列印但會被當成換行或 BOM 的 U+0085、U+2028、U+2029、U+FEFF：一律加引號
_YAML_UNSAFE = re.compile(
    "[\x00-\x1f\x7f-\x9f\N{LINE SEPARATOR}\N{PARAGRAPH SEPARATOR}\N{ZERO WIDTH NO-BREAK SPACE}\ud800-\udfff\ufffe\uffff]"
)
_YAML_ESCAPE = re.compile(
    '["\\\\\x00-\x1f\x7f-\x9f\N{LINE SEPARATOR}\N{PARAGRAPH SEPARATOR}\N{ZERO WIDTH NO-BREAK SPACE}\ud800-\udfff\ufffe\uffff]'
)
_YAML_DATE = re.compile(r"\d{4}-\d{2}-\d{2}")


def _yaml_needs_quote(s: str) -> bool:
    if s.lower() in _YAML_RESERVED:
        return True
    if swift_double(s) is not None or s.startswith(("0x", "0o")):
        return True
    if s[0] == " " or s[-1] == " " or s[0] in _YAML_LEAD:
        return True
    if ": " in s or " #" in s or s.endswith(":"):
        return True
    return bool(_YAML_UNSAFE.search(s) or _YAML_DATE.match(s))


def _yaml_escape_char(m: re.Match[str]) -> str:
    c = m.group(0)
    if c in _QUOTE_MAP:
        return _QUOTE_MAP[c]
    code = ord(c)
    if code < 0x20:
        return f"\\u{code:04x}"
    if code <= 0xFF:
        return f"\\x{code:02x}"
    return f"\\u{code:04x}"


def yaml_quote(s: str) -> str:
    """YAML 雙引號字串（跳脫規則相容 JSON，另外以 \\xNN／\\uNNNN 表示不可列印字元）。"""
    return '"' + _YAML_ESCAPE.sub(_yaml_escape_char, s) + '"'


def yaml_quote_if_needed(s: str) -> str:
    if not s:
        return '""'
    return yaml_quote(s) if _yaml_needs_quote(s) else s


def _yaml_scalar(value: Value) -> str:
    if value is None:
        return "null"
    if value is True or value is False:
        return "true" if value else "false"
    if isinstance(value, Num):
        safe = safe_number_text(value.text)
        return safe if safe is not None else yaml_quote(value.text)
    if isinstance(value, str):
        return yaml_quote_if_needed(value)
    return "[]" if isinstance(value, tuple) else "{}"


def _is_nonempty_container(value: Value) -> bool:
    return (isinstance(value, tuple) and bool(value)) or (
        isinstance(value, Obj) and bool(value.members)
    )


def _yaml_container(value: Value, indent: int, lines: list[str]) -> None:
    if isinstance(value, Obj):
        _yaml_object(value, indent, lines)
    else:
        _yaml_array(value, indent, lines)


def _yaml_object(obj: Obj, indent: int, lines: list[str]) -> None:
    pad = " " * indent
    for k, v in obj.members:
        key = yaml_quote_if_needed(k)
        if _is_nonempty_container(v):
            lines.append(pad + key + ":")
            _yaml_container(v, indent + 2, lines)
        else:
            lines.append(pad + key + ": " + _yaml_scalar(v))


def _yaml_array(items: tuple, indent: int, lines: list[str]) -> None:
    pad = " " * indent
    for item in items:
        if _is_nonempty_container(item):
            sub: list[str] = []
            _yaml_container(item, indent + 2, sub)
            lines.append(pad + "- " + sub[0][indent + 2 :])
            lines.extend(sub[1:])
        else:
            lines.append(pad + "- " + _yaml_scalar(item))


def write_yaml(value: Value) -> str:
    lines: list[str] = []
    if _is_nonempty_container(value):
        with deep_recursion():
            _yaml_container(value, 0, lines)
    else:
        lines.append(_yaml_scalar(value))
    return "\n".join(lines) + "\n"
