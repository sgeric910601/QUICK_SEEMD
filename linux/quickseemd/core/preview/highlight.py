"""程式碼檢視的 HTML（對應原版 QSHighlighter＋QSWriter）。

三種版面：
- 每行一列（可換行）：語言預設換行（或指定 wrap）且換行數 ≤ 4000；跨行的 span 在行尾關閉、下一行重開。
- 固定行號欄＋不換行：其他情況。
- 純文字自動換行：最長一行 > 8000 位元組且全文 > 64 KiB，不上色也沒有行號。
上色器只跳脫 & < >（引號保留原樣，與原版相同）。
"""

from __future__ import annotations

from .langbase import Language
from .lexer import TOKENS, Tokens, new_tokens
from .lexmodes import lex

MAX_WRAPPED_LINES = 4000
MAX_HIGHLIGHTED_LINE = 8000
PLAIN_THRESHOLD = 64 * 1024
GUTTER_CHUNK = 10000

OPEN: dict[str, bytes] = {t: f'<span class="{t}">'.encode() for t in TOKENS}
_OPEN_BY_CODE: list[bytes] = [OPEN[t] for t in TOKENS]
CLOSE = b"</span>"
ROW_OPEN = b'<div class="l">'
ROW_CLOSE = b"</div>"
_ROW_BREAK = ROW_CLOSE + ROW_OPEN


def _esc(x: bytes | bytearray) -> bytes | bytearray:
    return x.replace(b"&", b"&amp;").replace(b"<", b"&lt;").replace(b">", b"&gt;")


def _triples(toks: Tokens):  # noqa: ANN202 — (起點, 終點, 代碼) 的迭代器
    it = iter(toks)
    return zip(it, it, it)


# 標記用的位元組：從這些控制字元裡挑兩個文字中沒出現的，用來暫時代表 span 的開頭與結尾，
# 整段一次跳脫後再換成真正的標籤（比逐段跳脫快很多）
_MARK_CANDIDATES = bytes(x for x in range(1, 32) if x not in (9, 10, 12, 13, 27))
_CODE_BYTES: list[bytes] = [bytes((0x41 + i,)) for i in range(len(TOKENS))]


def _free_marks(b: bytes) -> tuple[bytes, bytes] | None:
    found = [bytes((x,)) for x in _MARK_CANDIDATES if bytes((x,)) not in b][:2]
    return (found[0], found[1]) if len(found) == 2 else None


def _render_marked(b: bytes, toks: Tokens, rows: bool, marks: tuple[bytes, bytes]) -> bytes:
    mo, mc = marks
    openers = [mo + code for code in _CODE_BYTES]
    mv = memoryview(b)
    out = bytearray()
    prev = 0
    for s, e, k in _triples(toks):
        out += mv[prev:s]
        opener = openers[k]
        out += opener
        if rows and b.find(b"\n", s, e) >= 0:
            out += b[s:e].replace(b"\n", mc + b"\n" + opener)
        else:
            out += mv[s:e]
        out += mc
        prev = e
    out += mv[prev:]
    body = _esc(out)
    for k, code in enumerate(_CODE_BYTES):
        if (mo + code) in body:
            body = body.replace(mo + code, _OPEN_BY_CODE[k])
    body = body.replace(mc, CLOSE)
    if rows:
        body = ROW_OPEN + body.replace(b"\n", _ROW_BREAK)
    return bytes(body)


def _render_pieces(b: bytes, toks: Tokens, rows: bool) -> bytes:
    """備用：文字裡找不到可用的標記位元組時逐段跳脫。"""
    out = bytearray(ROW_OPEN if rows else b"")
    brk = _ROW_BREAK if rows else b"\n"
    prev = 0
    for s, e, k in _triples(toks):
        if prev < s:
            out += _esc(b[prev:s]).replace(b"\n", brk)
        opener = _OPEN_BY_CODE[k]
        out += opener
        out += _esc(b[s:e]).replace(b"\n", CLOSE + brk + opener if rows else b"\n")
        out += CLOSE
        prev = e
    if prev < len(b):
        out += _esc(b[prev:]).replace(b"\n", brk)
    return bytes(out)


def render(b: bytes, toks: Tokens, rows: bool, marks: tuple[bytes, bytes] | None = None) -> bytes:
    """把詞的清單轉成 HTML（rows=True 時每行包成一個 div.l）。"""
    marks = marks if marks is not None else _free_marks(b)
    if marks is None:
        return _render_pieces(b, toks, rows)
    return _render_marked(b, toks, rows, marks)


def has_long_line(b: bytes, limit: int) -> bool:
    """有沒有超過 limit 位元組的一行。超過的行一定跨過某個 limit 的倍數位置，只要檢查那些點。"""
    for p in range(limit, len(b), limit):
        start = b.rfind(b"\n", 0, p) + 1
        end = b.find(b"\n", p)
        if (len(b) if end < 0 else end) - start > limit:
            return True
    return False


def _gutter(lines: int) -> str:
    """行號欄「1\n2\n…」；分段組合，避免一次產生上百萬個字串。"""
    last = max(1, lines)
    chunks = [
        "\n".join(map(str, range(start, min(start + GUTTER_CHUNK, last + 1))))
        for start in range(1, last + 1, GUTTER_CHUNK)
    ]
    return "\n".join(chunks)


def _finish(body: bytes, rows: bool, newlines: int, ends_with_newline: bool) -> tuple[bytes, int]:
    """收尾：結尾換行不算新的一行。"""
    lines = newlines + 1
    if ends_with_newline and newlines > 0:
        lines -= 1
        if rows and body.endswith(ROW_OPEN):
            body = body[: -len(ROW_OPEN)]
        elif rows:
            body += ROW_CLOSE
        elif body.endswith(b"\n"):
            body = body[:-1]
    elif rows:
        body += ROW_CLOSE
    return body, lines


def tokens(b: bytes, lang: Language) -> Tokens:
    toks = new_tokens()
    lex(b, lang, 0, len(b), toks)
    return toks


def code_view(
    text: str, lang: Language, wrap: bool | None = None, gutter: bool = True
) -> tuple[str, int]:
    """上色後的程式碼檢視 HTML 與行數。"""
    b = text.encode("utf-8", "replace")
    newlines = b.count(b"\n")
    ends_nl = b.endswith(b"\n")
    if len(b) > PLAIN_THRESHOLD and has_long_line(b, MAX_HIGHLIGHTED_LINE):
        body, lines = _finish(bytes(_esc(b)), False, newlines, ends_nl)
        html = body.decode("utf-8", "replace")
        return f'<div class="code plainwrap"><pre class="src">{html}</pre></div>', lines
    rows = (lang.wrap if wrap is None else wrap) and newlines <= MAX_WRAPPED_LINES
    toks = tokens(b, lang)
    rendered = render(b, toks, rows)
    body, lines = _finish(rendered, rows, newlines, ends_nl)
    if not b:
        lines = 0
    html = body.decode("utf-8", "replace")
    extra = "" if gutter else " nogutter"
    if rows:
        digits = max(2, len(str(lines)))
        return f'<div class="code wrap{extra}" style="--gw:{digits}ch">{html}</div>', lines
    gut = _gutter(lines) if gutter else ""
    return (
        f'<div class="code{extra}"><div class="gut">{gut}</div><pre class="src">{html}</pre></div>',
        lines,
    )


def inline(text: str, lang: Language) -> str:
    """只回傳上色後的內文（Markdown 程式碼區塊、SQLite 結構定義用）。"""
    b = text.encode("utf-8", "replace")
    body, _lines = _finish(
        render(b, tokens(b, lang), False), False, b.count(b"\n"), b.endswith(b"\n")
    )
    return body.decode("utf-8", "replace")
