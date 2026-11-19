"""資料夾摘要卡與「不是一般檔案」的資訊頁（Linux 版補充，原版的預覽不會收到這些）。

- 資料夾：統計底下的資料夾與檔案數、總大小（不跟隨符號連結；最多掃 MAX_SCAN 項、SCAN_SECONDS 秒）。
- 管線、通訊端、裝置、失效的連結、不存在的路徑：只用 lstat 取資訊，絕不打開。
"""

from __future__ import annotations

import logging
import os
import stat
import time
from dataclasses import dataclass
from pathlib import Path

from . import Document, fmt
from .binary import info_panel
from .icons import FOLDER_MIME
from .page import Chip, FileInfo, Notice, Page, build, from_timestamp

log = logging.getLogger(__name__)

MAX_SCAN = 20_000
SCAN_SECONDS = 1.0
SPECIAL_NOTICE = "這不是一般檔案，預覽不會打開它"
MISSING_NOTICE = "檔案不存在或無法存取"
PARTIAL_NOTICE = "項目太多，數字只計算了一部分"


@dataclass
class _Scan:
    folders: int = 0
    files: int = 0
    total: int = 0
    top_level: int = 0
    partial: bool = False


def _scan_dir(path: str, scan: _Scan, deadline: float, stack: list[str]) -> None:
    try:
        with os.scandir(path) as it:
            for entry in it:
                if scan.folders + scan.files >= MAX_SCAN or time.monotonic() > deadline:
                    scan.partial = True
                    return
                if entry.is_dir(follow_symlinks=False):
                    scan.folders += 1
                    stack.append(entry.path)
                    continue
                scan.files += 1
                if entry.is_file(follow_symlinks=False):
                    scan.total += entry.stat(follow_symlinks=False).st_size
    except OSError:
        log.debug("讀不到資料夾：%s", path, exc_info=True)


def scan_folder(root: Path) -> _Scan:
    """逐層統計（不用遞迴、不跟隨連結）。"""
    scan = _Scan()
    try:
        with os.scandir(root) as it:
            scan.top_level = sum(1 for _ in zip(it, range(MAX_SCAN)))
    except OSError:
        log.debug("讀不到資料夾：%s", root, exc_info=True)
    deadline = time.monotonic() + SCAN_SECONDS
    stack = [str(root)]
    while stack and not scan.partial:
        _scan_dir(stack.pop(), scan, deadline, stack)
    return scan


def _times_rows(info: FileInfo) -> list[tuple[str, str]]:
    return [
        ("建立時間", fmt.date_long(info.created)),
        ("修改時間", fmt.date_long(info.modified)),
        ("權限", fmt.permissions(info.mode)),
    ]


def folder_document(path: Path) -> Document:
    info = FileInfo.of(path)
    scan = scan_folder(path)
    rows = [
        ("內容", f"{fmt.number(scan.folders)} 個資料夾、{fmt.number(scan.files)} 個檔案"),
        ("第一層", f"{fmt.number(scan.top_level)} 個項目"),
        ("總大小", f"{fmt.bytes_label(scan.total)}（{fmt.number(scan.total)} 位元組）"),
        *_times_rows(info),
    ]
    panel, atts = info_panel(info, rows, "資料夾", FOLDER_MIME)
    page = Page(
        kind="資料夾", show_icon=False, size=(760, 480), size_label=fmt.bytes_label(scan.total)
    )
    page.chips = [Chip(f"{fmt.number(scan.top_level)} 個項目")]
    if scan.partial:
        page.notices.append(Notice(PARTIAL_NOTICE, warn=True))
    page.body = panel
    page.attachments = atts
    return build(page, info)


def _special_kind(st: os.stat_result | None, is_link: bool) -> tuple[str, str]:
    """（類型名稱, 圖示用的 MIME）。"""
    if st is None:
        return "找不到檔案", "inode/x-missing"
    mode = st.st_mode
    if is_link:
        return "失效的連結", "inode/symlink"
    if stat.S_ISFIFO(mode):
        return "具名管線（FIFO）", "inode/fifo"
    if stat.S_ISSOCK(mode):
        return "通訊端（Socket）", "inode/socket"
    if stat.S_ISCHR(mode):
        return "字元裝置", "inode/chardevice"
    if stat.S_ISBLK(mode):
        return "區塊裝置", "inode/blockdevice"
    return "特殊檔案", "inode/x-special"


def special_document(path: Path) -> Document:
    """不打開檔案，只用 lstat／readlink 取得資訊。"""
    try:
        lst = os.lstat(path)
    except OSError:
        lst = None
    try:
        target = os.stat(path)  # 連結指向的東西（管線、裝置…）
    except OSError:
        target = None
    dangling = lst is not None and target is None and stat.S_ISLNK(lst.st_mode)
    label, mime = _special_kind(target or lst, dangling)
    info = FileInfo(
        path, path.name, "", 0, lst.st_mode if lst else None,
        from_timestamp(lst.st_mtime) if lst else None, False,
    )  # fmt: skip
    rows = [("類型", label)]
    if dangling:
        try:
            rows.append(("連結目標", os.readlink(path)))
        except OSError:
            log.debug("讀不到連結目標：%s", path, exc_info=True)
    rows += [("修改時間", fmt.date_long(info.modified)), ("權限", fmt.permissions(info.mode))]
    panel, atts = info_panel(info, rows, label, mime)
    page = Page(kind=label, show_icon=False, size=(640, 420), size_label="—")
    page.notices.append(Notice(MISSING_NOTICE if lst is None else SPECIAL_NOTICE))
    page.body = panel
    page.attachments = atts
    return build(page, info)
