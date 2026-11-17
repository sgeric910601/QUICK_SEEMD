"""JSON 工具（對應原版 QSJSON）：驗證與摘要、判斷是否壓縮過、自動排版。

排版保留原文的數字寫法與鍵的順序（不經過解析再輸出），以正規表示式逐詞處理。
錯誤訊息是 Linux 版自己的中文說明（原版直接顯示系統的英文訊息）。
"""

from __future__ import annotations

import json
import re

from . import fmt

MINIFIED_MIN_BYTES = 200
MINIFIED_BYTES_PER_LINE = 400

_TOKEN = re.compile(
    r'"[^"\\]*(?:\\[\s\S]?[^"\\]*)*"?'  # 字串（沒結束就到結尾）
    r"|[{}\[\],:]"
    r"|[ \t\n\r]+"
    r'|[^"{}\[\],: \t\n\r]+'
)
_WS = re.compile(r"[ \t\n\r]*")

_MESSAGES = {
    "Expecting value": "這裡應該是一個值",
    "Expecting property name enclosed in double quotes": "物件的鍵要用雙引號括起來",
    "Expecting ',' delimiter": "缺少逗號",
    "Expecting ':' delimiter": "缺少冒號",
    "Extra data": "結尾有多餘的內容",
    "Unterminated string starting at": "字串沒有結束",
    "Invalid control character at": "字串裡有不允許的控制字元",
    "Invalid \\escape": "不正確的跳脫字元",
    "Invalid \\uXXXX escape": "不正確的 \\u 跳脫字元",
    "Illegal trailing comma before end of object": "物件結尾多了逗號",
    "Illegal trailing comma before end of array": "陣列結尾多了逗號",
}


def looks_minified(text: str) -> bool:
    n = len(text.encode("utf-8", "replace"))
    if n <= MINIFIED_MIN_BYTES:
        return False
    newlines = text.count("\n")
    return newlines < 3 or n // max(newlines, 1) > MINIFIED_BYTES_PER_LINE


def pretty_print(text: str) -> str:
    """縮排兩格；空物件／空陣列保持「{}」「[]」。"""
    out: list[str] = []
    indent = 0
    i, n = 0, len(text)
    while i < n:
        m = _TOKEN.match(text, i)
        tok = m.group()
        i = m.end()
        c = tok[0]
        if c in "{[":
            j = _WS.match(text, i).end()
            if j < n and text[j] in "}]":
                out.append(c + text[j])
                i = j + 1
                continue
            indent += 1
            out.append(c + "\n" + "  " * indent)
        elif c in "}]":
            indent = max(0, indent - 1)
            out.append("\n" + "  " * indent + c)
        elif c == ",":
            out.append(",\n" + "  " * indent)
        elif c == ":":
            out.append(": ")
        elif c not in " \t\n\r":
            out.append(tok)
    return "".join(out)


def _reject_constant(name: str) -> None:
    raise ValueError(f"不支援 {name}")


def _message(exc: json.JSONDecodeError) -> str:
    detail = _MESSAGES.get(exc.msg, exc.msg)
    return f"第 {exc.lineno} 行第 {exc.colno} 欄：{detail}"


def inspect(text: str) -> tuple[str | None, str | None]:
    """（摘要, 錯誤訊息）。"""
    try:
        obj = json.loads(text, parse_constant=_reject_constant)
    except json.JSONDecodeError as exc:
        return None, _message(exc)
    except RecursionError:
        return None, "巢狀層數太深，無法驗證"
    except ValueError as exc:
        return None, str(exc)
    if isinstance(obj, dict):
        return f"物件 · {fmt.number(len(obj))} 個鍵", None
    if isinstance(obj, list):
        return f"陣列 · {fmt.number(len(obj))} 個項目", None
    return "單一值", None
