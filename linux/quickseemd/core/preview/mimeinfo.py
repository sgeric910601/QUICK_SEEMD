"""檔案類型的中文說明（取代原版 UTType.localizedDescription）。

順序：內建的常見類型表 → 系統 MIME 資料庫裡的 zh_TW 說明（必須真的有翻譯）→ 依副檔名「EXT 檔案」／「文件」。
"""

from __future__ import annotations

import logging
import re
from functools import lru_cache
from pathlib import Path

log = logging.getLogger(__name__)

MIME_DIR = Path("/usr/share/mime")
UNKNOWN_MIMES = frozenset(
    {"application/octet-stream", "application/x-zerosize", "inode/x-empty", "text/x-generic"}
)
_MIME_RE = re.compile(r"^[a-z0-9.+-]+/[A-Za-z0-9.+_-]+$")
_ZH_COMMENT = re.compile(r'<comment xml:lang="zh_TW">([^<]+)</comment>')

BUILTIN: dict[str, str] = {
    "application/x-executable": "執行檔",
    "application/x-pie-executable": "執行檔",
    "application/x-sharedlib": "共用程式庫",
    "application/x-object": "目的檔",
    "application/x-core": "核心傾印",
    "application/x-mach-binary": "Mach-O 二進位檔",
    "application/x-java": "Java 類別檔",
    "application/vnd.sqlite3": "SQLite 資料庫",
    "application/x-sqlite3": "SQLite 資料庫",
    "text/plain": "純文字文件",
    "inode/directory": "資料夾",
    "inode/fifo": "具名管線（FIFO）",
    "inode/socket": "通訊端（Socket）",
    "inode/chardevice": "字元裝置",
    "inode/blockdevice": "區塊裝置",
    "inode/symlink": "符號連結",
}


@lru_cache(maxsize=512)
def _system_zh(mime: str) -> str | None:
    if not _MIME_RE.match(mime):
        return None
    path = MIME_DIR / (mime + ".xml")
    try:
        text = path.read_text("utf-8")
    except (OSError, UnicodeDecodeError):
        return None
    m = _ZH_COMMENT.search(text)
    if m is None:
        return None
    label = m.group(1).strip()
    # 系統資料庫有些「翻譯」其實還是英文，不用
    return label if label and not label.isascii() else None


def describe(mime: str | None, ext: str) -> str:
    """給人看的類型名稱；未知類型時用「EXT 檔案」或「文件」。"""
    if mime and mime not in UNKNOWN_MIMES:
        label = BUILTIN.get(mime) or _system_zh(mime)
        if label:
            return label
    return f"{ext.upper()} 檔案" if ext else "文件"
