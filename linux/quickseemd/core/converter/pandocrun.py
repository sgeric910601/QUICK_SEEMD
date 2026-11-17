"""用 pandoc 轉檔（對應原版 CVExternalTools 的 pandoc 部分），分兩段以兼顧安全與功能。

這台的 pandoc 3.1.3 在 --sandbox 下找不到自己的資料檔，DOCX／ODT／EPUB 都寫不出來，所以：
1. 讀取（--sandbox）：輸入 → pandoc 內部格式（JSON），圖片抽到暫存資料夾。
2. 清理：拿掉網址、絕對路徑或指到外面的圖片，以及會讀取檔案的中繼資料（封面、樣式表…）。
3. 寫出：JSON → 目標格式（只用清理過的內容，不會連網或讀到其他檔案）。
"""

from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

from .. import backends, errors
from .external import pandoc_arguments

EMBEDS_MEDIA = frozenset({"docx", "odt", "epub"})
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_RAW_REF = re.compile(
    r"\b(?:src|href|data|poster|srcset)\s*=\s*[\"']?\s*(?:[A-Za-z][A-Za-z0-9+.-]*:|/)",
    re.IGNORECASE,
)
_FILE_META = frozenset(
    {"cover-image", "epub-cover-image", "css", "stylesheet", "epub-fonts", "epub-embed-font"}
    | {"bibliography", "csl", "citation-abbreviations"}
)


def _allowed(url: str, roots: list[str], base: str) -> bool:
    if url.startswith("data:"):
        return True
    if _SCHEME.match(url) or url.startswith("//"):
        return False
    real = os.path.realpath(os.path.join(base, url))
    return any(real == r or real.startswith(r + os.sep) for r in roots)


def sanitize(node: Any, roots: list[str], base: str) -> Any:
    """回傳清理過的 AST（不修改傳入的物件）。"""
    if isinstance(node, list):
        return [sanitize(n, roots, base) for n in node]
    if not isinstance(node, dict):
        return node
    kind = node.get("t")
    if kind == "Image":
        attr, alt, (url, _title) = node["c"]
        if not _allowed(url, roots, base):
            return {"t": "Span", "c": [attr, sanitize(alt, roots, base)]}
    if kind in ("RawInline", "RawBlock"):
        fmt, text = node["c"]
        if str(fmt).lower().startswith("html") and _RAW_REF.search(text):
            return {"t": "Str", "c": ""} if kind == "RawInline" else {"t": "Plain", "c": []}
    return {k: sanitize(v, roots, base) for k, v in node.items()}


def clean_document(doc: dict, roots: list[str], base: str) -> dict:
    meta = {k: v for k, v in (doc.get("meta") or {}).items() if k not in _FILE_META}
    return {
        **doc,
        "meta": sanitize(meta, roots, base),
        "blocks": sanitize(doc["blocks"], roots, base),
    }


def convert(
    path: Path, reader: str | None, fid: str, out: Path, work: Path, ctx
) -> None:  # noqa: ANN001
    tool = backends.require("pandoc")
    scratch = Path(tempfile.mkdtemp(prefix=".pandoc-", dir=work))
    ast_path = scratch / "document.json"
    media = scratch / "media"
    read = [tool, str(path)] + (["-f", reader] if reader else [])
    read += ["-t", "json", "--sandbox", "-o", str(ast_path)]
    if fid in EMBEDS_MEDIA:
        read.append(f"--extract-media={media}")
    ctx.run_process(read, "pandoc", cwd=path.parent)
    try:
        doc = json.loads(ast_path.read_text("utf-8"))
    except (OSError, ValueError) as exc:
        raise errors.tool_failed("pandoc", str(exc)) from exc
    roots = [os.path.realpath(path.parent), os.path.realpath(media)]
    with_structure = json.dumps(clean_document(doc, roots, str(path.parent)), ensure_ascii=False)
    clean_path = scratch / "clean.json"
    clean_path.write_text(with_structure, "utf-8")
    write = pandoc_arguments(str(clean_path), str(out), "json", fid)
    write = [a for a in write if a != "--sandbox"]
    write.insert(-2, f"--resource-path={path.parent}")
    ctx.run_process([tool, *write], "pandoc", cwd=path.parent)
