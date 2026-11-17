"""用外部工具解開 Python 讀不了的格式（7z、RAR、ISO、xar、cpio、tar.zst…）。

- 優先用 bsdtar 把壓縮檔「轉存」成標準 tar（bsdtar -cf 新檔 @壓縮檔）。
  這一步不會在磁碟上建立任何解出來的檔案；轉存的 tar 再交給 Python 的安全解壓縮逐項檢查。
- 沒有 bsdtar 或它讀不懂時改用 7z：先列出內容檢查每個路徑，再解到暫存資料夾。
  7-Zip 本身也會拒絕指向外面的連結；解完後清掉 macOS 中繼資料，最後由呼叫端再檢查整個資料夾。
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path

from ... import backends, process
from ...model import ToolContext
from .archivekind import Corrupt, Encrypted, UnsafeEntry, is_junk, safe_parts

log = logging.getLogger(__name__)

ExtractTar = Callable[[Path, Path, ToolContext], None]

_ENV = {"LC_ALL": "C.UTF-8"}
_PASSWORD_HINTS = ("ncrypt", "assphrase", "assword")
_DANGEROUS = re.compile(r"Dangerous link path was ignored : (.+?) : ")
_UNIX_MODE = re.compile(r"^[-dlcbps][-rwxsStT]{9}$")
_SPECIAL_TYPES = "cbps"


def _mentions_password(text: str) -> bool:
    return any(hint in text for hint in _PASSWORD_HINTS)


def extract(
    archive: Path, staging: Path, work: Path, ctx: ToolContext, extract_tar: ExtractTar
) -> None:
    """解到 staging；兩個工具都沒有時丟出「找不到 bsdtar…」。"""
    bsdtar = backends.find("bsdtar")
    sevenzip = backends.find("7z")
    if bsdtar is None and sevenzip is None:
        backends.require("bsdtar")
    if bsdtar is not None:
        converted = _convert_with_bsdtar(bsdtar, archive, work, ctx)
        if converted is not None:
            ctx.progress(0.5)
            extract_tar(converted, staging, ctx)
            return
    if sevenzip is None:
        raise Corrupt
    _extract_with_7z(sevenzip, archive, staging, ctx)


# ---------------------------------------------------------------- bsdtar


def _convert_with_bsdtar(bsdtar: str, archive: Path, work: Path, ctx: ToolContext) -> Path | None:
    """轉存成 pax 格式的 tar；bsdtar 讀不懂時回傳 None（交給 7z 試）。"""
    out = Path(tempfile.mkdtemp(prefix=".convert-", dir=work)) / "converted.tar"
    args = [bsdtar, "-c", "-f", str(out), "--format", "pax", "@" + str(archive.resolve())]
    res = process.run(args, cancel=ctx.cancel_event, env=_ENV)
    if res.returncode == 0:
        return out
    if _mentions_password(res.stderr):
        raise Encrypted
    log.info("bsdtar 讀不了 %s：%s", archive.name, process.last_lines(res.stderr))
    return None


# ---------------------------------------------------------------- 7z


def _listing_entries(text: str) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for block in text.split("\n\n"):
        fields = dict(line.split(" = ", 1) for line in block.splitlines() if " = " in line)
        if fields.get("Path"):
            entries.append(fields)
    return entries


def check_listing(text: str) -> None:
    """檢查「7z l -slt」列出的每個項目：路徑、裝置檔、加密。"""
    for fields in _listing_entries(text):
        path = fields["Path"]
        safe_parts(path)
        if fields.get("Encrypted") == "+":
            raise Encrypted
        mode = fields.get("Attributes", "").split(" ")[-1]
        if _UNIX_MODE.match(mode) and mode[0] in _SPECIAL_TYPES:
            raise UnsafeEntry(path)


def _extract_with_7z(sevenzip: str, archive: Path, staging: Path, ctx: ToolContext) -> None:
    target = str(archive.resolve())
    listing = process.run(
        [sevenzip, "l", "-slt", "-ba", "-sccUTF-8", "--", target], cancel=ctx.cancel_event, env=_ENV
    )
    if listing.returncode != 0:
        if _mentions_password(listing.stdout + listing.stderr):
            raise Encrypted
        raise Corrupt
    check_listing(listing.stdout)
    ctx.progress(0.2)
    res = process.run(
        [sevenzip, "x", "-y", "-bd", "-sccUTF-8", f"-o{staging}", "--", target],
        cancel=ctx.cancel_event,
        env=_ENV,
    )
    output = res.stdout + res.stderr
    dangerous = _DANGEROUS.search(output)
    if dangerous:
        raise UnsafeEntry(dangerous.group(1))
    if res.returncode not in (0, 1):
        if _mentions_password(output):
            raise Encrypted
        raise Corrupt
    remove_junk(staging)


def remove_junk(root: Path) -> None:
    """清掉暫存資料夾裡的 macOS 中繼資料（只動自己剛解出來的東西）。"""
    for dirpath, dirnames, filenames in os.walk(root):
        rel = Path(dirpath).relative_to(root).parts
        for name in list(dirnames):
            if is_junk((*rel, name)):
                full = os.path.join(dirpath, name)
                if os.path.islink(full):
                    os.unlink(full)
                else:
                    shutil.rmtree(full)
                dirnames.remove(name)
        for name in filenames:
            if is_junk((*rel, name)):
                os.unlink(os.path.join(dirpath, name))
