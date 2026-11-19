"""壓縮、解壓縮與重新封裝（對應原版 CVArchiveConverter）。

- 解壓縮一律交給共用的安全解壓縮（tools/other/unpack.py）：所有項目先檢查、只寫進新的暫存資料夾、
  解完再確認每個符號連結實際指向的位置；本模組不直接執行 bsdtar -x。
- 只有一個最上層項目時交付它本身；但隱藏檔（例如 .bash_profile）一律包進以檔名命名的資料夾，
  免得解壓縮後「看不見」。
- 建立 ZIP、TAR.GZ 用 Python 內建模組；7z 用 bsdtar（成員一律以 ./ 開頭傳入，
  避免「@名稱」被當成另一個壓縮檔）。
"""

from __future__ import annotations

import os
import stat
import tarfile
import tempfile
import time
import zipfile
from collections.abc import Callable
from pathlib import Path

from .. import backends, errors, naming
from ..tools.other import unpack
from ..tools.other.archivekind import SINGLE_FILE_KINDS, ArchiveKind
from .formats import Format
from .profile import Profile

Check = Callable[[], None]

# profile.archive_kind → 共用解壓縮的種類
KINDS: dict[str, ArchiveKind] = {
    "zip": ArchiveKind.ZIP,
    "tar": ArchiveKind.TAR,
    "sevenZip": ArchiveKind.SEVEN_ZIP,
    "rar": ArchiveKind.RAR,
    "xar": ArchiveKind.XAR,
    "cpio": ArchiveKind.CPIO,
    "iso": ArchiveKind.ISO,
    "gzip": ArchiveKind.GZIP,
    "bzip2": ArchiveKind.BZIP2,
    "xz": ArchiveKind.XZ,
}


# ---------------------------------------------------------------- 建立壓縮檔


def _zip_symlink(z: zipfile.ZipFile, path: Path, arcname: str) -> None:
    info = zipfile.ZipInfo(arcname, time.localtime(path.lstat().st_mtime)[:6])
    info.create_system = 3
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    z.writestr(info, os.readlink(path))


def _zip_add(z: zipfile.ZipFile, path: Path, arcname: str, check: Check) -> None:
    check()
    if path.is_symlink():
        _zip_symlink(z, path, arcname)
    elif path.is_dir():
        z.write(path, arcname + "/")
        for child in sorted(path.iterdir(), key=lambda c: c.name):
            _zip_add(z, child, f"{arcname}/{child.name}", check)
    elif path.is_file():
        z.write(path, arcname)


def create_zip(src: Path, out: Path, check: Check) -> None:
    """壓成 ZIP，保留最上層名稱（同 ditto --keepParent）。"""
    with zipfile.ZipFile(out, "x", zipfile.ZIP_DEFLATED, strict_timestamps=False) as z:
        _zip_add(z, src, src.name, check)


def create_tgz(src: Path, out: Path, check: Check) -> None:
    def keep(info: tarfile.TarInfo) -> tarfile.TarInfo:
        check()
        return info

    with tarfile.open(out, "x:gz") as tar:
        tar.add(src, arcname=src.name, recursive=True, filter=keep)


def sevenzip_arguments(tool: str, src: Path, out: Path) -> list[str]:
    """bsdtar 參數；成員以 ./名稱 傳入，再用 -s 拿掉存進壓縮檔的「./」。"""
    args = [tool, "--format", "7zip", "-cf", str(out), "-C", str(src.parent)]
    return args + ["-s", ",^\\./,,", "--", f"./{src.name}"]


def create_7z(src: Path, out: Path, ctx) -> None:  # noqa: ANN001
    tool = backends.require("bsdtar")
    ctx.run_process(sevenzip_arguments(tool, src, out), "bsdtar")


# ---------------------------------------------------------------- 解壓縮


def _commit(item: Path, work: Path, name: str) -> Path:
    """用不覆蓋的方式把 item 改名成 name（放進全新的資料夾，名稱一定不會衝突）。"""
    holder = Path(tempfile.mkdtemp(prefix=".named-", dir=work))
    return naming.commit(item, holder, naming.sanitize(name))


def extract(path: Path, kind: str | None, work: Path, ctx) -> Path:  # noqa: ANN001
    """用共用的安全解壓縮解到 work 裡；回傳的路徑名稱就是要交付的名稱。"""
    archive_kind = KINDS.get(kind or "")
    if archive_kind is None:
        raise errors.unsupported_target(path.name, "解壓縮到資料夾")
    result = unpack.extract(path, archive_kind, work, ctx)
    single_hidden = result.path.name.startswith(".") and result.path.name == result.name
    if single_hidden and archive_kind not in SINGLE_FILE_KINDS:
        # 只有一個隱藏的最上層項目：連同所在的暫存資料夾包成以檔名命名的資料夾
        return _commit(result.path.parent, work, naming.base_name(path))
    if result.path.name != result.name:
        return _commit(result.path, work, result.name)
    return result.path


# ---------------------------------------------------------------- 路線入口


def convert(
    path: Path,
    profile: Profile,
    fmt: Format,
    options: dict,
    work: Path,
    ctx,  # noqa: ANN001, ARG001
) -> list[Path]:
    if fmt.id == "extract":
        result = extract(path, profile.archive_kind, work, ctx)
        ctx.progress(1)
        return [result]
    source = path
    if profile.archive_kind is not None:
        # 壓縮檔 → 另一種壓縮檔：先用安全解壓縮解開再重新打包
        source = extract(path, profile.archive_kind, work, ctx)
        ctx.progress(0.4)
    out = work / naming.join_name(naming.base_name(path), fmt.ext)
    if fmt.id == "zip":
        create_zip(source, out, ctx.check_cancelled)
    elif fmt.id == "tgz":
        create_tgz(source, out, ctx.check_cancelled)
    elif fmt.id == "7z":
        create_7z(source, out, ctx)
    else:
        raise errors.unsupported_target(path.name, fmt.display_name)
    ctx.progress(1)
    return [out]
