"""壓縮檔種類與安全檢查（對應原版 CVInputProfile.archiveKind，另加路徑穿越防護）。

安全規則（解壓縮前全部檢查，任何一項不合格就整個拒絕，什麼都不寫）：
- 絕對路徑、Windows 磁碟代號（C:）、含「..」的路徑
- 指向壓縮檔外面的符號連結與硬連結
- 裝置檔、管線、socket
解壓縮後還會再確認每個符號連結實際指向的位置都在解壓縮資料夾裡。
"""

from __future__ import annotations

import os
import re
import stat
from enum import Enum
from pathlib import Path

from ... import errors, naming


class ArchiveKind(str, Enum):
    ZIP = "zip"
    TAR = "tar"
    SEVEN_ZIP = "7z"
    RAR = "rar"
    XAR = "xar"
    CPIO = "cpio"
    ISO = "iso"
    GZIP = "gzip"
    BZIP2 = "bzip2"
    XZ = "xz"


# 單一檔案的壓縮（解開就是一個檔案）
SINGLE_FILE_KINDS = frozenset({ArchiveKind.GZIP, ArchiveKind.BZIP2, ArchiveKind.XZ})
# 本質是 ZIP 但不當壓縮檔處理的文件格式
ZIP_BASED_DOCUMENTS = frozenset(
    {
        "docx",
        "xlsx",
        "pptx",
        "odt",
        "ods",
        "odp",
        "epub",
        "ipa",
        "jar",
        "apk",
        "pages",
        "numbers",
        "key",
    }
)
_TAR_EXTS = frozenset({"tar", "tgz", "tbz", "tbz2", "txz"})
_BY_EXT = {
    "7z": ArchiveKind.SEVEN_ZIP,
    "rar": ArchiveKind.RAR,
    "xar": ArchiveKind.XAR,
    "pkg": ArchiveKind.XAR,
    "cpio": ArchiveKind.CPIO,
    "iso": ArchiveKind.ISO,
    "gz": ArchiveKind.GZIP,
    "bz2": ArchiveKind.BZIP2,
    "xz": ArchiveKind.XZ,
}
JUNK_NAMES = frozenset({".DS_Store"})
JUNK_DIRS = frozenset({"__MACOSX"})
_DRIVE = re.compile(r"^[A-Za-z]:")


def archive_kind(path: Path) -> ArchiveKind | None:
    """依檔名判斷壓縮檔種類（原版規則）；資料夾與 ZIP 格式的文件回傳 None。"""
    p = Path(path)
    if p.is_dir():
        return None
    lower = p.name.lower()
    ext = lower.rsplit(".", 1)[1] if "." in lower.lstrip(".") else ""
    if ext in ZIP_BASED_DOCUMENTS:
        return None
    if ext == "zip":
        return ArchiveKind.ZIP
    if ext in _TAR_EXTS or any(lower.endswith("." + c) for c in naming.COMPOUND_EXTS):
        return ArchiveKind.TAR
    return _BY_EXT.get(ext)


# ---------------------------------------------------------------- 錯誤


class UnsafeEntry(Exception):
    """壓縮檔裡有不安全的項目；entry 是它在壓縮檔裡的名稱。"""

    def __init__(self, entry: str) -> None:
        super().__init__(entry)
        self.entry = entry


class Encrypted(Exception):
    """壓縮檔有密碼保護。"""


class Corrupt(Exception):
    """讀不懂或已損毀。"""


class Conflict(Exception):
    """壓縮檔裡的項目彼此衝突（例如同一個名稱既是檔案又是資料夾）。"""

    def __init__(self, entry: str) -> None:
        super().__init__(entry)
        self.entry = entry


def unsafe_error(archive: Path, entry: str) -> errors.QSError:
    shown = entry.replace("\0", "") or "（空白）"
    return errors.QSError(
        f"「{Path(archive).name}」含有不安全的路徑「{shown}」，為了保護你的檔案，已停止解壓縮。"
    )


def encrypted_error(archive: Path) -> errors.QSError:
    return errors.QSError(f"「{Path(archive).name}」有密碼保護，目前無法解壓縮。")


def corrupt_error(archive: Path) -> errors.QSError:
    return errors.QSError(
        f"「{Path(archive).name}」無法解壓縮：檔案可能已損毀，或不是支援的壓縮格式。"
    )


def conflict_error(archive: Path, entry: str) -> errors.QSError:
    return errors.QSError(f"「{Path(archive).name}」裡有互相衝突的項目「{entry}」，無法解壓縮。")


# ---------------------------------------------------------------- 路徑檢查


def safe_parts(name: str, *, backslash: bool = False) -> tuple[str, ...]:
    """壓縮檔內的路徑 → 安全的路徑片段（去掉空白與「.」）；不安全時丟出 UnsafeEntry。

    backslash=True 時把反斜線也當成分隔符號（Windows 做的 ZIP）。
    """
    if "\0" in name:
        raise UnsafeEntry(name)
    text = name.replace("\\", "/") if backslash else name
    if text.startswith("/") or _DRIVE.match(text):
        raise UnsafeEntry(name)
    parts = tuple(p for p in text.split("/") if p not in ("", "."))
    if ".." in parts:
        raise UnsafeEntry(name)
    return parts


def link_stays_inside(entry_parts: tuple[str, ...], target: str) -> bool:
    """符號連結（相對於自己所在資料夾）在文字上是否一直留在壓縮檔範圍內。"""
    if not target or "\0" in target or target.startswith("/") or _DRIVE.match(target):
        return False
    depth = list(entry_parts[:-1])
    for part in target.split("/"):
        if part in ("", "."):
            continue
        if part == "..":
            if not depth:
                return False
            depth.pop()
        else:
            depth.append(part)
    return True


def is_junk(parts: tuple[str, ...]) -> bool:
    """macOS 的中繼資料：__MACOSX、.DS_Store、._ 開頭的 AppleDouble 檔。"""
    if not parts:
        return False
    if parts[0] in JUNK_DIRS:
        return True
    return any(p in JUNK_NAMES or p.startswith("._") for p in parts)


def _inside(root: str, path: str) -> bool:
    return path == root or path.startswith(root + os.sep)


def _target_resolves_inside(root: Path, parts: tuple[str, ...], target: str) -> bool:
    """連結（位在 root 裡的 parts）的目標逐步解析都留在 root 裡，而且中途不經過其他符號連結。

    中途經過連結再「..」的話，交付（搬移或改名）之後解析結果會改變，所以一律不允許。
    最後一段可以是另一個連結（例如 libx.so → libx.so.1），那個連結本身也會被同樣檢查。
    """
    if not target or "\0" in target or target.startswith("/") or _DRIVE.match(target):
        return False
    depth = list(parts[:-1])
    comps = [c for c in target.split("/") if c not in ("", ".")]
    for i, comp in enumerate(comps):
        if comp == "..":
            if not depth:
                return False
            depth.pop()
            continue
        depth.append(comp)
        if i < len(comps) - 1 and os.path.islink(root.joinpath(*depth)):
            return False
    return True


def _check_link(root: Path, real_root: str, full: str, rel: str) -> None:
    if not _target_resolves_inside(root, Path(rel).parts, os.readlink(full)):
        raise UnsafeEntry(rel)
    try:
        real = os.path.realpath(full, strict=True)
    except OSError as exc:  # 懸空的連結或連結循環
        raise UnsafeEntry(rel) from exc
    if not _inside(real_root, real):
        raise UnsafeEntry(rel)


def verify_tree(root: Path) -> None:
    """交付前的最後檢查，以「實際交付出去的項目」為根（單一項目時就是那個項目本身）。

    - 交付的項目本身不能是符號連結，也不能是特殊檔案。
    - 裡面每個符號連結：目標不是絕對路徑、逐步解析不離開 root、中途不經過其他連結、
      實際存在（不懸空、不循環），而且實際指向的位置在 root 裡。
    - 不能有管線、裝置檔、socket。
    """
    top = os.lstat(root).st_mode
    if stat.S_ISLNK(top) or not (stat.S_ISREG(top) or stat.S_ISDIR(top)):
        raise UnsafeEntry(Path(root).name)
    if stat.S_ISREG(top):
        return
    real_root = os.path.realpath(root)
    for dirpath, dirnames, filenames in os.walk(root):
        for name in (*dirnames, *filenames):
            full = os.path.join(dirpath, name)
            rel = os.path.relpath(full, root)
            mode = os.lstat(full).st_mode
            if stat.S_ISLNK(mode):
                _check_link(Path(root), real_root, full, rel)
            elif not (stat.S_ISREG(mode) or stat.S_ISDIR(mode)):
                raise UnsafeEntry(rel)
