"""quickseemd render <檔案...> <輸出資料夾>（對應原版 qs-render）。

每個檔案輸出「<檔名（空白換成 _）>.html」，附件存成「<檔名>.<附件名>.<副檔名>」並把 HTML 裡的
cid:附件名 換成那個檔名；每處理一個檔案印一行「{毫秒}ms\\t{KB}KB\\t{附件數} att\\t{輸出檔名}」。
Linux 版補充：絕不覆蓋既有檔案，撞名時改用「<檔名>_(1)」這樣的名稱（同一組輸出一起換）。
"""

from __future__ import annotations

import logging
import mimetypes
import re
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from . import Document, engine

log = logging.getLogger(__name__)

USAGE = "用法：quickseemd render <檔案...> <輸出資料夾>"
MAX_ATTEMPTS = 1000
_CID = re.compile(r"cid:([A-Za-z0-9._-]+)")
_EXTS = {
    "image/png": "png",
    "image/jpeg": "jpeg",
    "image/gif": "gif",
    "image/svg+xml": "svg",
    "image/webp": "webp",
    "font/ttf": "ttf",
    "font/otf": "otf",
    "font/woff": "woff",
    "font/woff2": "woff2",
    "font/collection": "ttc",
}


def _ext_for(mime: str) -> str:
    if mime in _EXTS:
        return _EXTS[mime]
    guessed = mimetypes.guess_extension(mime)
    return guessed.lstrip(".") if guessed else "bin"


def _plan(doc: Document, base: str) -> tuple[str, dict[str, str]]:
    names = {name: f"{base}.{name}.{_ext_for(att.mime)}" for name, att in doc.attachments.items()}
    return f"{base}.html", names


def _write_new(path: Path, data: bytes) -> None:
    with open(path, "xb") as fh:  # 獨佔建立：已存在就丟 FileExistsError，絕不覆蓋
        fh.write(data)


def write_document(doc: Document, out_dir: Path, base: str) -> tuple[Path, str]:
    """寫出 HTML 與附件；回傳（HTML 路徑, 換好附件名稱的 HTML）。"""
    for attempt in range(MAX_ATTEMPTS):
        stem = base if attempt == 0 else f"{base}_({attempt})"
        html_name, att_names = _plan(doc, stem)
        targets = [out_dir / html_name] + [out_dir / n for n in att_names.values()]
        if any(t.exists() or t.is_symlink() for t in targets):
            continue
        html = _CID.sub(lambda m: att_names.get(m.group(1), m.group(0)), doc.html)
        try:
            for name, att in doc.attachments.items():
                _write_new(out_dir / att_names[name], att.data)
            _write_new(out_dir / html_name, html.encode("utf-8"))
        except FileExistsError:
            continue  # 剛好被別人搶先建立：換下一個名稱
        return out_dir / html_name, html
    raise FileExistsError(f"找不到可用的檔名：{base}")


def _render_one(path: Path, out_dir: Path) -> str:
    start = time.perf_counter()
    doc = engine.render(path)
    ms = int((time.perf_counter() - start) * 1000)
    out, html = write_document(doc, out_dir, path.name.replace(" ", "_"))
    kb = len(html.encode("utf-8")) // 1024
    return f"{ms}ms\t{kb}KB\t{len(doc.attachments)} att\t{out.name}"


def main(argv: Sequence[str]) -> int:
    args = list(argv)
    if len(args) < 2:
        sys.stderr.write(USAGE + "\n")
        return 1
    out_dir = Path(args[-1]).expanduser()
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        sys.stderr.write(f"錯誤：無法建立輸出資料夾：{out_dir}（{exc.strerror}）\n")
        return 1
    status = 0
    for arg in args[:-1]:
        try:
            line = _render_one(Path(arg).expanduser(), out_dir)
        except (OSError, ValueError) as exc:
            log.debug("輸出失敗：%s", arg, exc_info=True)
            sys.stderr.write(f"錯誤：{arg}：{exc}\n")
            status = 1
            continue
        sys.stdout.write(line + "\n")
        sys.stdout.flush()
    return status
