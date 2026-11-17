"""Nemo 傳來的網址 → 要預覽的目標。

- 只接受 file://、trash://、recent://；其他網址（http、sftp、smb…）一律不碰，
  連 Gio 都不交給它（GVfs 會連網），只顯示「無法預覽這個位置」。
- file://：直接用路徑。
- trash://、recent://：查 standard::target-uri 找到真正的檔案；指向的不是本機檔案也一樣不碰。
- 不是一般檔案（管線、裝置、通訊端）只用 stat 判斷，絕不打開（打開管線會卡住）。
"""

from __future__ import annotations

import os
import stat
from collections.abc import Callable
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any

from gi.repository import Gio, GLib

MAX_URI_LENGTH = 65536
ALLOWED_SCHEMES = frozenset({"file", "trash", "recent"})
TARGET_URI_ATTRIBUTE = "standard::target-uri"
_QUERY_ATTRIBUTES = f"{TARGET_URI_ATTRIBUTE},standard::display-name"

_SPECIAL_LABELS: tuple[tuple[Callable[[int], bool], str], ...] = (
    (stat.S_ISFIFO, "具名管線（FIFO）"),
    (stat.S_ISCHR, "字元裝置"),
    (stat.S_ISBLK, "區塊裝置"),
    (stat.S_ISSOCK, "通訊端（socket）"),
)


class TargetKind(Enum):
    FILE = "file"  # 一般檔案
    FOLDER = "folder"  # 資料夾
    SPECIAL = "special"  # 管線、裝置等，只顯示資訊
    MISSING = "missing"  # 檔案不存在
    REMOTE = "remote"  # 不支援的位置（其他網址、或指向網路的項目），完全不碰
    INVALID = "invalid"  # 網址格式不對


@dataclass(frozen=True)
class Target:
    kind: TargetKind
    uri: str  # Nemo 給的原始網址
    name: str  # 顯示用的檔名
    path: Path | None = None
    detail: str = ""  # 特殊檔案的種類名稱

    @property
    def local_uri(self) -> str:
        """「用預設程式開啟」用的網址：有本機路徑就用 file://。"""
        if self.path is not None:
            return Gio.File.new_for_path(str(self.path)).get_uri()
        return self.uri


FileFactory = Callable[[str], Any]


def _valid(uri: object) -> bool:
    if not isinstance(uri, str) or not uri or len(uri) > MAX_URI_LENGTH:
        return False
    if any(ch in uri for ch in ("\0", "\n", "\r")):
        return False
    return GLib.uri_parse_scheme(uri) is not None


def display_name(uri: str) -> str:
    """只從網址文字取出顯示用的名稱（不碰檔案系統或網路）。"""
    tail = uri.rstrip("/").rsplit("/", 1)[-1]
    return GLib.uri_unescape_string(tail, None) or tail or uri


def _local_path(gfile: Any) -> tuple[str | None, str | None]:
    """（本機路徑, 顯示名稱）；查不到本機路徑時路徑是 None。"""
    path = gfile.get_path()
    if path:
        return path, None
    info = gfile.query_info(_QUERY_ATTRIBUTES, Gio.FileQueryInfoFlags.NONE, None)
    display = info.get_display_name()
    target = info.get_attribute_string(TARGET_URI_ATTRIBUTE)
    if target and GLib.uri_parse_scheme(target) == "file":
        return Gio.File.new_for_uri(target).get_path(), display
    return None, display


def _classify_path(uri: str, path: Path, name: str) -> Target:
    """只用 stat（跟著符號連結）判斷，不打開檔案。"""
    try:
        st = os.stat(path)
    except OSError:
        return Target(TargetKind.MISSING, uri, name, path)
    if stat.S_ISDIR(st.st_mode):
        return Target(TargetKind.FOLDER, uri, name, path)
    if stat.S_ISREG(st.st_mode):
        return Target(TargetKind.FILE, uri, name, path)
    detail = next((label for test, label in _SPECIAL_LABELS if test(st.st_mode)), "特殊檔案")
    return Target(TargetKind.SPECIAL, uri, name, path, detail)


def resolve(uri: str, *, file_for_uri: FileFactory | None = None) -> Target:
    """把網址解析成預覽目標；任何錯誤都變成可以顯示的目標，不丟例外。"""
    if not _valid(uri):
        return Target(TargetKind.INVALID, uri if isinstance(uri, str) else "", "")
    if (GLib.uri_parse_scheme(uri) or "").lower() not in ALLOWED_SCHEMES:
        return Target(TargetKind.REMOTE, uri, display_name(uri))
    factory = file_for_uri or Gio.File.new_for_uri
    gfile = factory(uri)
    try:
        raw_path, display = _local_path(gfile)
    except GLib.Error:
        raw_path, display = None, None
    if raw_path is None:
        name = display or gfile.get_basename() or display_name(uri)
        return Target(TargetKind.REMOTE, uri, name)
    path = Path(raw_path)
    return _classify_path(uri, path, display or path.name or raw_path)
