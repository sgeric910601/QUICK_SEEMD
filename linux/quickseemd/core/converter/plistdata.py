"""Plist 讀寫（對應原版 CVPlistBridge）。

- XML plist：自己輸出（Tab 縮排、保留鍵順序）；讀取用迷你 DOM 保留順序。
- 二進位 plist：用 plistlib；讀出的鍵依字母排序（同原版用系統 API 的結果）。
"""

from __future__ import annotations

import base64
import math
import plistlib
import re
from datetime import datetime, timezone
from typing import Any

from .. import errors
from . import xmldom
from .structured import Num, Obj, Value, deep_recursion, safe_number_text, swift_double

_INT64 = re.compile(r"[+-]?[0-9]+")
# plist 的 <real> 只接受十進位浮點數（數字文字之後會原樣寫進其他格式，不能夾帶別的內容）
_REAL = re.compile(r"[+-]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][+-]?[0-9]+)?")
_INT64_MIN = -(2**63)
_INT64_MAX = 2**63 - 1

MAX_XML_NODES = 5_000_000  # XML plist 的元素上限（避免惡意檔案吃光記憶體）

XML_HEADER = (
    '<?xml version="1.0" encoding="UTF-8"?>\n'
    '<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" '
    '"http://www.apple.com/DTDs/PropertyList-1.0.dtd">\n'
    '<plist version="1.0">\n'
)


def as_int64(text: str) -> int | None:
    """沒有小數點與指數、而且放得進 64 位元整數時回傳整數。"""
    if "." in text or "e" in text.lower() or not _INT64.fullmatch(text):
        return None
    value = int(text)
    return value if _INT64_MIN <= value <= _INT64_MAX else None


# ---------------------------------------------------------------- 輸出


def _emit(value: Value, indent: str, out: list[str]) -> None:
    if value is None:
        out.append(indent + "<string></string>\n")
    elif value is True or value is False:
        out.append(indent + ("<true/>" if value else "<false/>") + "\n")
    elif isinstance(value, Num):
        _emit_number(value, indent, out)
    elif isinstance(value, str):
        out.append(f"{indent}<string>{xmldom.escape(value)}</string>\n")
    elif isinstance(value, tuple):
        _emit_array(value, indent, out)
    else:
        _emit_dict(value, indent, out)


def _emit_number(value: Num, indent: str, out: list[str]) -> None:
    """數字先驗證；不是合法數字文字時當成字串輸出（避免注入）。"""
    safe = safe_number_text(value.text)
    if safe is None:
        out.append(f"{indent}<string>{xmldom.escape(value.text)}</string>\n")
        return
    tag = "integer" if as_int64(safe) is not None else "real"
    out.append(f"{indent}<{tag}>{xmldom.escape(safe)}</{tag}>\n")


def _emit_array(items: tuple, indent: str, out: list[str]) -> None:
    if not items:
        out.append(indent + "<array/>\n")
        return
    out.append(indent + "<array>\n")
    for item in items:
        _emit(item, indent + "\t", out)
    out.append(indent + "</array>\n")


def _emit_dict(obj: Obj, indent: str, out: list[str]) -> None:
    kept = [(k, v) for k, v in obj.members if v is not None]
    if not kept:
        out.append(indent + "<dict/>\n")
        return
    out.append(indent + "<dict>\n")
    for k, v in kept:
        out.append(f"{indent}\t<key>{xmldom.escape(k)}</key>\n")
        _emit(v, indent + "\t", out)
    out.append(indent + "</dict>\n")


def xml_plist(value: Value) -> str:
    """XML plist（保留鍵順序）；字典裡的 null 會略過，陣列裡的 null 變成空字串。"""
    out = [XML_HEADER]
    with deep_recursion():
        _emit(value, "", out)
    out.append("</plist>\n")
    return "".join(out)


def _foundation(value: Value) -> Any:
    if value is None:
        return ""
    if value is True or value is False or isinstance(value, str):
        return value
    if isinstance(value, Num):
        as_int = as_int64(value.text)
        if as_int is not None:
            return as_int
        return swift_double(value.text) or 0.0
    if isinstance(value, tuple):
        return [_foundation(v) for v in value]
    return {k: _foundation(v) for k, v in value.members if v is not None}


def binary_plist(value: Value) -> bytes:
    if value is None:
        raise errors.invalid_data("plist 不支援 null 作為根節點。")
    try:
        with deep_recursion():
            obj = _foundation(value)
        return plistlib.dumps(obj, fmt=plistlib.FMT_BINARY, sort_keys=False)
    except (OverflowError, TypeError, ValueError) as exc:
        raise errors.write_failed(str(exc)) from exc


# ---------------------------------------------------------------- 讀取


def _from_xml(node: xmldom.Node) -> Value:
    name = node.name
    if name == "dict":
        members: list[tuple[str, Value]] = []
        pending: str | None = None
        for child in node.children:
            if child.name == "key":
                pending = child.text
                continue
            if pending is not None:
                members.append((pending, _from_xml(child)))
                pending = None
        return Obj(tuple(members))
    if name == "array":
        return tuple(_from_xml(c) for c in node.children)
    return _from_xml_scalar(node)


def _from_xml_scalar(node: xmldom.Node) -> Value:
    name = node.name
    text = node.text.strip()
    if name == "string":
        return node.text
    if name == "integer":
        return Num(text) if _INT64.fullmatch(text) else text
    if name == "real":
        if not _REAL.fullmatch(text):
            return text
        number = swift_double(text)
        return Num(text) if number is not None and math.isfinite(number) else text
    if name in ("true", "false"):
        return name == "true"
    if name == "date":
        return text
    if name == "data":
        return "".join(node.text.split())
    raise errors.invalid_data(f"plist 中有無法辨識的元素 <{name}>。")


def _float_text(d: float) -> Value:
    if not math.isfinite(d):
        return str(d)
    if d == round(d) and abs(d) < 1e15:
        return Num(f"{int(d)}.0")
    return Num(repr(d))


def from_foundation(obj: Any) -> Value:
    """plistlib 讀出的物件 → 值（字典鍵排序，同原版）。"""
    if isinstance(obj, str):
        return obj
    if isinstance(obj, bool):
        return obj
    if isinstance(obj, int):
        return Num(str(obj))
    if isinstance(obj, float):
        return _float_text(obj)
    if isinstance(obj, datetime):
        moment = obj if obj.tzinfo else obj.replace(tzinfo=timezone.utc)
        return moment.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if isinstance(obj, (bytes, bytearray)):
        return base64.b64encode(bytes(obj)).decode("ascii")
    if isinstance(obj, (list, tuple)):
        return tuple(from_foundation(v) for v in obj)
    if isinstance(obj, dict):
        return Obj(tuple((k, from_foundation(obj[k])) for k in sorted(obj, key=str)))
    return str(obj)


def read_plist(data: bytes) -> Value:
    head = data[:64].decode("utf-8", "replace")
    if "<?xml" in head or "<plist" in head:
        try:
            root = xmldom.parse(data, MAX_XML_NODES)
        except xmldom.XMLTooLarge as exc:
            raise errors.invalid_data("plist 檔案太大，無法轉換。") from exc
        if root is not None:
            plist = root.first_child("plist") or (root if root.name == "plist" else None)
            if plist is not None and plist.children:
                with deep_recursion():
                    return _from_xml(plist.children[0])
    try:
        obj = plistlib.loads(data)
    except Exception as exc:  # noqa: BLE001 — plistlib 會丟各種例外，一律視為格式錯誤
        raise errors.invalid_data("不是正確的 plist 檔案。") from exc
    return from_foundation(obj)
