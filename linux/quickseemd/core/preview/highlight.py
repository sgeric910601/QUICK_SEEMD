"""程式碼檢視的 HTML（對應原版 QSHighlighter＋QSWriter）。

三種版面：
- 每行一列（可換行）：語言預設換行（或指定 wrap）且換行數 ≤ 4000；跨行的 span 在行尾關閉、下一行重開。
- 固定行號欄＋不換行：其他情況。
- 純文字自動換行：最長一行 > 8000 位元組且全文 > 64 KiB，不上色也沒有行號。
上色器只跳脫 & < >（引號保留原樣，與原版相同）。
"""

from __future__ import annotations

from .langbase import Language
from .lexer import Token
from .lexmodes import lex

MAX_WRAPPED_LINES = 4000
MAX_HIGHLIGHTED_LINE = 8000
PLAIN_THRESHOLD = 64 * 1024

TOKENS = "k s c n t f a p y g lit hd da dd dh dm le lw li ld lt".split()
OPEN: dict[str, bytes] = {t: f'<span class="{t}">'.encode() for t in TOKENS}
CLOSE = b"</span>"
ROW_OPEN = b'<div class="l">'
ROW_CLOSE = b"</div>"
_ROW_BREAK = ROW_CLOSE + ROW_OPEN


def _esc(x: bytes) -> bytes:
    return x.replace(b"&", b"&amp;").replace(b"<", b"&lt;").replace(b">", b"&gt;")


# 標記用的位元組：從這些控制字元裡挑兩個文字中沒出現的，用來暫時代表 span 的開頭與結尾，
# 整段一次跳脫後再換成真正的標籤（比逐段跳脫快很多）
_MARK_CANDIDATES = bytes(x for x in range(1, 32) if x not in (9, 10, 12, 13, 27))
_CODES: dict[str, bytes] = {t: bytes((0x41 + i,)) for i, t in enumerate(TOKENS)}


def _free_marks(b: bytes) -> tuple[bytes, bytes] | None:
    found = [bytes((x,)) for x in _MARK_CANDIDATES if bytes((x,)) not in b][:2]
    return (found[0], found[1]) if len(found) == 2 else None


def _render_marked(b: bytes, toks: list[Token], rows: bool, marks: tuple[bytes, bytes]) -> bytes:
    mo, mc = marks
    openers = {t: mo + code for t, code in _CODES.items()}
    out: list[bytes] = []
    append = out.append
    prev = 0
    for s, e, t in toks:
        append(b[prev:s])
        opener = openers[t]
        append(opener)
        piece = b[s:e]
        if rows and 10 in piece:
            piece = piece.replace(b"\n", mc + b"\n" + opener)
        append(piece)
        append(mc)
        prev = e
    append(b[prev:])
    body = _esc(b"".join(out))
    for t, code in _CODES.items():
        body = body.replace(mo + code, OPEN[t])
    body = body.replace(mc, CLOSE)
    if rows:
        body = ROW_OPEN + body.replace(b"\n", _ROW_BREAK)
    return body


def _render_pieces(b: bytes, toks: list[Token], rows: bool) -> bytes:
    """備用：文字裡找不到可用的標記位元組時逐段跳脫。"""
    out: list[bytes] = [ROW_OPEN] if rows else []
    append = out.append
    brk = _ROW_BREAK if rows else b"\n"
    prev = 0
    for s, e, t in toks:
        if prev < s:
            append(_esc(b[prev:s]).replace(b"\n", brk))
        opener = OPEN[t]
        append(opener)
        append(_esc(b[s:e]).replace(b"\n", CLOSE + brk + opener if rows else b"\n"))
        append(CLOSE)
        prev = e
    if prev < len(b):
        append(_esc(b[prev:]).replace(b"\n", brk))
    return b"".join(out)


def render(
    b: bytes, toks: list[Token], rows: bool, marks: tuple[bytes, bytes] | None = None
) -> bytes:
    """把詞的清單轉成 HTML（rows=True 時每行包成一個 div.l）。"""
    marks = marks if marks is not None else _free_marks(b)
    if marks is None:
        return _render_pieces(b, toks, rows)
    return _render_marked(b, toks, rows, marks)


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


def tokens(b: bytes, lang: Language) -> list[Token]:
    toks: list[Token] = []
    lex(b, lang, 0, len(b), toks)
    return toks


def code_view(
    text: str, lang: Language, wrap: bool | None = None, gutter: bool = True
) -> tuple[str, int]:
    """上色後的程式碼檢視 HTML 與行數。"""
    b = text.encode("utf-8", "replace")
    newlines = b.count(b"\n")
    ends_nl = b.endswith(b"\n")
    longest = max(map(len, b.split(b"\n"))) if b else 0
    if longest > MAX_HIGHLIGHTED_LINE and len(b) > PLAIN_THRESHOLD:
        body, lines = _finish(_esc(b), False, newlines, ends_nl)
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
    gut = "\n".join(map(str, range(1, max(1, lines) + 1))) if gutter else ""
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
