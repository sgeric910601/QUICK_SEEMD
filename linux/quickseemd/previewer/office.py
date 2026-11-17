"""Office 文件預覽：用 LibreOffice（無介面模式）轉成 PDF，放在快取資料夾。

- 快取檔名：sha1(路徑, 修改時間, 大小).pdf，檔案改過就會重新轉換。
- 轉檔交給共用的 core.soffice：全新的設定檔（不抓連結的遠端資源、不更新連結、不重算、
  不執行巨集）、能用 bwrap 時放進沒有網路的隔離環境、同一時間只跑一個。
- 每次轉換用全新的暫存資料夾，結束後清掉。
- 轉好的 PDF 以「獨佔建立」放進快取（硬連結），絕不覆蓋已存在的檔案；原始檔完全不動。
"""

from __future__ import annotations

import hashlib
import logging
import os
import tempfile
from pathlib import Path

from ..core import backends, errors, naming, soffice

log = logging.getLogger(__name__)

CONVERT_TIMEOUT = 60  # 秒
CACHE_KEEP = 40  # 快取最多留幾份
TOOL_TITLE = "LibreOffice"


def cache_folder() -> Path:
    return backends.cache_dir() / "preview"


def cache_key(path: Path) -> str:
    st = os.stat(path)
    raw = b"\0".join(
        (os.fsencode(os.path.abspath(path)), str(st.st_mtime_ns).encode(), str(st.st_size).encode())
    )
    return hashlib.sha1(raw, usedforsecurity=False).hexdigest()


def cached_pdf_path(path: Path) -> Path:
    return cache_folder() / f"{cache_key(path)}.pdf"


def _commit(produced: Path, target: Path) -> None:
    """獨佔建立快取檔；已經有了（別的轉換剛好完成）就沿用。"""
    try:
        os.link(produced, target)
    except FileExistsError:
        log.info("快取已經存在，沿用：%s", target)


def _convert_in(work: Path, src: Path, target: Path) -> None:
    produced = soffice.convert(src, "pdf", work, timeout=CONVERT_TIMEOUT)
    if produced.suffix.lower() != ".pdf":
        raise errors.tool_failed(TOOL_TITLE, "沒有產生 PDF。")
    _commit(produced, target)


def convert_to_pdf(path: Path) -> Path:
    """回傳轉好的 PDF（在快取裡）；失敗時丟 QSError。"""
    src = Path(os.path.abspath(path))
    target = cached_pdf_path(src)
    if target.is_file():
        return target
    folder = target.parent
    folder.mkdir(parents=True, exist_ok=True)
    work = Path(tempfile.mkdtemp(prefix="job-", dir=folder))
    try:
        _convert_in(work, src, target)
    finally:
        naming.remove_work_dir(work)
    prune_cache()
    return target


def prune_cache(keep: int = CACHE_KEEP) -> None:
    """只留最新的幾份轉好的 PDF（只動快取資料夾裡我們自己命名的檔案）。"""
    folder = cache_folder()
    try:
        pdfs = [
            p
            for p in folder.iterdir()
            if p.suffix == ".pdf" and len(p.stem) == 40 and p.is_file() and not p.is_symlink()
        ]
    except OSError:
        return
    pdfs.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    for old in pdfs[keep:]:
        try:
            old.unlink()
        except OSError as exc:
            log.warning("無法清除舊的預覽快取 %s：%s", old, exc)
