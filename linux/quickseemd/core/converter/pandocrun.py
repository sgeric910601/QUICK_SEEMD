"""用 pandoc 轉檔（對應原版 CVExternalTools 的 pandoc 部分），分兩段以兼顧安全與功能。

這台的 pandoc 3.1.3 在 --sandbox 下找不到自己的資料檔，DOCX／ODT／EPUB 都寫不出來，所以：
1. 讀取（--sandbox）：輸入 → pandoc 內部格式（JSON），內嵌的圖片抽到暫存資料夾。
2. 清理：
   - 原始 HTML（RawInline／RawBlock）整段拿掉——裡面的 <img src> 等寫出時會被讀進 EPUB。
   - 每張圖片都用 realpath 解析，必須是輸入檔資料夾（或第 1 步抽出的資料夾）裡的一般檔案；
     網址、絕對路徑、「..」跑出去、指到外面的符號連結一律改成只留替代文字。
   - 要內嵌圖片的格式（DOCX／ODT／EPUB）：把通過檢查的圖片複製到專用的資源資料夾，改用新名稱。
   - 拿掉會讀取檔案的中繼資料（封面、樣式表、字型、書目）。
3. 寫出：JSON → 目標格式；工作資料夾與 --resource-path 都只指向第 2 步的資源資料夾。
"""

from __future__ import annotations

import json
import os
import re
import shutil
import stat
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import unquote

from .. import backends, errors
from .external import pandoc_arguments

EMBEDS_MEDIA = frozenset({"docx", "odt", "epub"})
MAX_IMAGE_BYTES = 50 * 1024 * 1024
KEEP_RAW_FORMATS = frozenset({"latex", "tex"})  # 只會原樣寫進 .tex，不會被讀取
_SCHEME = re.compile(r"^[A-Za-z][A-Za-z0-9+.-]*:")
_FILE_META = frozenset(
    {"cover-image", "epub-cover-image", "css", "stylesheet", "epub-fonts", "epub-embed-font"}
    | {"bibliography", "csl", "citation-abbreviations"}
)


def _inside(real: str, roots: list[str]) -> bool:
    return any(real == r or real.startswith(r + os.sep) for r in roots)


class Cleaner:
    """清理 pandoc 的 AST；embed 時把允許的圖片複製到 resources 並改寫網址。"""

    def __init__(self, roots: list[Path], base: Path, resources: Path | None) -> None:
        self.roots = [os.path.realpath(r) for r in roots]
        self.base = base
        self.resources = resources
        self.copied: dict[str, str] = {}

    def _local_file(self, url: str) -> str | None:
        """圖片網址 → 允許讀取的實際路徑；不允許時回傳 None。"""
        if not url or _SCHEME.match(url) or url.startswith(("//", "\\")):
            return None
        for candidate in (url, unquote(url)):
            real = os.path.realpath(os.path.join(self.base, candidate))
            if not _inside(real, self.roots):
                continue
            try:
                st = os.stat(real)
            except OSError:
                continue
            if stat.S_ISREG(st.st_mode) and st.st_size <= MAX_IMAGE_BYTES:
                return real
        return None

    def image_url(self, url: str) -> str | None:
        """回傳寫出時要用的網址；None＝拿掉這張圖。"""
        if url.startswith("data:"):
            return url
        real = self._local_file(url)
        if real is None:
            return None
        if self.resources is None:
            return url  # 不內嵌的格式（md、html、tex）保留原本的相對路徑
        if real not in self.copied:
            name = f"img-{len(self.copied) + 1}{Path(real).suffix.lower()}"
            shutil.copyfile(real, self.resources / name)
            self.copied[real] = name
        return self.copied[real]

    def clean(self, node: Any) -> Any:
        """回傳清理過的節點（不修改傳入的物件）。"""
        if isinstance(node, list):
            return [self.clean(n) for n in node]
        if not isinstance(node, dict):
            return node
        kind = node.get("t")
        if kind == "Image":
            attr, alt, (url, title) = node["c"]
            new_url = self.image_url(url)
            if new_url is None:
                return {"t": "Span", "c": [attr, self.clean(alt)]}
            return {"t": "Image", "c": [attr, self.clean(alt), [new_url, title]]}
        if kind in ("RawInline", "RawBlock"):
            if str(node["c"][0]).lower() not in KEEP_RAW_FORMATS:
                return {"t": "Str", "c": ""} if kind == "RawInline" else {"t": "Plain", "c": []}
        return {k: self.clean(v) for k, v in node.items()}

    def document(self, doc: dict) -> dict:
        meta = {k: v for k, v in (doc.get("meta") or {}).items() if k not in _FILE_META}
        return {**doc, "meta": self.clean(meta), "blocks": self.clean(doc["blocks"])}


def convert(
    path: Path, reader: str | None, fid: str, out: Path, work: Path, ctx  # noqa: ANN001
) -> None:
    tool = backends.require("pandoc")
    scratch = Path(tempfile.mkdtemp(prefix=".pandoc-", dir=work))
    ast_path = scratch / "document.json"
    media = scratch / "media"
    resources = scratch / "resources"
    resources.mkdir()
    read = [tool, str(path)] + (["-f", reader] if reader else [])
    read += ["-t", "json", "--sandbox", "-o", str(ast_path)]
    if fid in EMBEDS_MEDIA:
        read.append(f"--extract-media={media}")
    ctx.run_process(read, "pandoc", cwd=path.parent)
    try:
        doc = json.loads(ast_path.read_text("utf-8"))
    except (OSError, ValueError) as exc:
        raise errors.tool_failed("pandoc", str(exc)) from exc
    embed = resources if fid in EMBEDS_MEDIA else None
    cleaner = Cleaner([path.parent, media], path.parent, embed)
    clean_path = scratch / "clean.json"
    clean_path.write_text(json.dumps(cleaner.document(doc), ensure_ascii=False), "utf-8")
    args = pandoc_arguments(str(clean_path), str(out), "json", fid)
    write = [a for a in args if a != "--sandbox"]
    write.insert(-2, f"--resource-path={resources}")
    ctx.run_process([tool, *write], "pandoc", cwd=resources)
