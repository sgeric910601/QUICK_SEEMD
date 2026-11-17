"""用外部工具解開 Python 讀不了的格式（7z、RAR、ISO、xar、cpio、tar.zst…）。

- 優先用 bsdtar 把壓縮檔「轉存」成標準 tar（bsdtar -cf 新檔 @壓縮檔）。
  這一步不會在磁碟上建立任何解出來的檔案；轉存的 tar 再交給 Python 的安全解壓縮逐項檢查。
- 沒有 bsdtar 或它讀不懂時改用 7z：先列出內容檢查每個路徑與宣告大小，再解到暫存資料夾。
  7-Zip 本身也會拒絕指向外面的連結；解完後清掉 macOS 中繼資料，最後由呼叫端再檢查整個資料夾。
- 外部程式寫檔時由 SizeWatch 在背景量大小，超過額度（extractlimits）就結束它。
"""

from __future__ import annotations

import logging
import os
import re
import shutil
import tempfile
from collections.abc import Callable
from pathlib import Path

from ... import backends, errors, process
from ...model import ToolContext
from .archivekind import Corrupt, Encrypted, UnsafeEntry, is_junk, safe_parts
from .extractlimits import Budget, SizeWatch, file_size, tree_size

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
    archive: Path,
    staging: Path,
    work: Path,
    ctx: ToolContext,
    budget: Budget,
    extract_tar: ExtractTar,
    *,
    allow_7z: bool = True,
) -> None:
    """解到 staging；兩個工具都沒有時丟出「找不到 bsdtar…」。

    allow_7z=False 用於 tar 系列：7z 解 .tar.gz 只會得到裡面的 .tar，而且大小取自
    gzip 結尾（壞檔時是亂數），所以 bsdtar 讀不了就當成壞檔。
    """
    bsdtar = backends.find("bsdtar")
    sevenzip = backends.find("7z") if allow_7z else None
    if bsdtar is None and sevenzip is None:
        backends.require("bsdtar")
    if bsdtar is not None:
        converted = _convert_with_bsdtar(bsdtar, archive, work, ctx, budget)
        if converted is not None:
            ctx.progress(0.5)
            extract_tar(converted, staging, ctx)
            _drop_converted(converted, budget)
            return
    if sevenzip is None:
        raise Corrupt
    _extract_with_7z(sevenzip, archive, staging, ctx, budget)


def _run_watched(
    args: list[str], measure: Callable[[], int], ctx: ToolContext, budget: Budget
) -> process.Result:
    """執行外部程式，同時在背景量它寫出的大小；超過額度就結束程式並丟 TooLarge。"""
    with SizeWatch(measure, budget, ctx.cancel_event) as watch:
        try:
            return process.run(args, cancel=watch.stop, env=_ENV)
        except errors.Cancelled:
            watch.raise_if_exceeded()
            raise


# ---------------------------------------------------------------- bsdtar


def _convert_with_bsdtar(
    bsdtar: str, archive: Path, work: Path, ctx: ToolContext, budget: Budget
) -> Path | None:
    """轉存成 pax 格式的 tar；bsdtar 讀不懂時回傳 None（交給 7z 試）。"""
    out = Path(tempfile.mkdtemp(prefix=".convert-", dir=work)) / "converted.tar"
    args = [bsdtar, "-c", "-f", str(out), "--format", "pax", "@" + str(archive.resolve())]
    res = _run_watched(args, lambda: file_size(out), ctx, budget)
    size = file_size(out)
    if res.returncode == 0:
        budget.check(extra_disk=size, extra_content=size)
        budget.charge(size, content=False)
        return out
    _drop_converted(out, None)
    if _mentions_password(res.stderr):
        raise Encrypted
    log.info("bsdtar 讀不了 %s：%s", archive.name, process.last_lines(res.stderr))
    return None


def _drop_converted(path: Path, budget: Budget | None) -> None:
    """刪掉自己轉存的中間 tar（立刻釋放磁碟空間並退回額度）。"""
    size = file_size(path)
    folder = path.parent
    if not folder.name.startswith(".convert-"):
        return
    shutil.rmtree(folder, onexc=lambda _f, p, e: log.warning("刪不掉暫存檔 %s：%s", p, e))
    if budget is not None:
        budget.release(size)


# ---------------------------------------------------------------- 7z


def _listing_entries(text: str) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for block in text.split("\n\n"):
        fields = dict(line.split(" = ", 1) for line in block.splitlines() if " = " in line)
        if fields.get("Path"):
            entries.append(fields)
    return entries


def check_listing(text: str) -> int:
    """檢查「7z l -slt」列出的每個項目（路徑、裝置檔、加密），回傳宣告的總大小。"""
    total = 0
    for fields in _listing_entries(text):
        path = fields["Path"]
        safe_parts(path)
        if fields.get("Encrypted") == "+":
            raise Encrypted
        mode = fields.get("Attributes", "").split(" ")[-1]
        if _UNIX_MODE.match(mode) and mode[0] in _SPECIAL_TYPES:
            raise UnsafeEntry(path)
        size = fields.get("Size", "").strip()
        total += int(size) if size.isdigit() else 0
    return total


def _extract_with_7z(
    sevenzip: str, archive: Path, staging: Path, ctx: ToolContext, budget: Budget
) -> None:
    target = str(archive.resolve())
    listing = process.run(
        [sevenzip, "l", "-slt", "-ba", "-sccUTF-8", "--", target], cancel=ctx.cancel_event, env=_ENV
    )
    if listing.returncode != 0:
        if _mentions_password(listing.stdout + listing.stderr):
            raise Encrypted
        raise Corrupt
    budget.plan(check_listing(listing.stdout))
    ctx.progress(0.2)
    args = [sevenzip, "x", "-y", "-bd", "-sccUTF-8", f"-o{staging}", "--", target]
    res = _run_watched(args, lambda: tree_size(staging), ctx, budget)
    output = res.stdout + res.stderr
    dangerous = _DANGEROUS.search(output)
    if dangerous:
        raise UnsafeEntry(dangerous.group(1))
    if res.returncode not in (0, 1):
        if _mentions_password(output):
            raise Encrypted
        raise Corrupt
    budget.charge(tree_size(staging))
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
