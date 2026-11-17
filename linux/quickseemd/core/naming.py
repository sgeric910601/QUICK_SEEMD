"""輸出檔名與「絕不覆蓋」的寫入（對應原版 CVFileNaming）。

規則：
- 雙副檔名（.tar.gz 等）視為一個副檔名。
- 同名時依序試「名稱 (1).ext」「名稱 (2).ext」…，最多 1000 次。
- 寫入一律是不可覆蓋的原子操作：renameat2(RENAME_NOREPLACE) → 硬連結 → 獨佔建立。
"""

from __future__ import annotations

import ctypes
import ctypes.util
import errno
import os
import shutil
import tempfile
from collections.abc import Iterator
from pathlib import Path

from . import errors

COMPOUND_EXTS: tuple[str, ...] = ("tar.gz", "tar.bz2", "tar.xz", "tar.zst", "tar.lz")
MAX_ATTEMPTS = 1000
UNTITLED = "未命名"
WORK_PREFIX = ".quickseemd-"

_RENAME_NOREPLACE = 1
_AT_FDCWD = -100


def split_name(name: str) -> tuple[str, str]:
    """把檔名拆成（主檔名, 副檔名）；副檔名不含點，雙副檔名算一個。"""
    lower = name.lower()
    for ext in COMPOUND_EXTS:
        suffix = "." + ext
        if lower.endswith(suffix) and len(name) > len(suffix):
            return name[: -len(suffix)], name[-len(ext) :]
    dot = name.rfind(".")
    if dot <= 0 or dot == len(name) - 1:
        # 沒有副檔名、隱藏檔（.bashrc）、或以點結尾
        return name, ""
    return name[:dot], name[dot + 1 :]


def base_name(path: str | os.PathLike[str]) -> str:
    """去掉副檔名的檔名；拿掉後變空字串時（隱藏檔）回傳完整檔名。"""
    return split_name(Path(path).name)[0]


def sanitize(name: str) -> str:
    """清理成合法檔名：/ 和 : 換成 -、去頭尾空白、開頭的點換成 _、空字串變「未命名」。"""
    cleaned = name.replace("/", "-").replace(":", "-").replace("\0", "")
    cleaned = cleaned.strip()
    if cleaned.startswith("."):
        cleaned = "_" + cleaned[1:]
    return cleaned or UNTITLED


def join_name(stem: str, ext: str) -> str:
    return f"{stem}.{ext}" if ext else stem


def output_name(input_path: str | os.PathLike[str], tag: str | None, ext: str) -> str:
    """「{主檔名}-{標記}.{副檔名}」，沒有標記時是「{主檔名}.{副檔名}」。"""
    stem = base_name(input_path)
    if tag:
        stem = f"{stem}-{tag}"
    return join_name(sanitize(stem), ext)


def candidate_names(name: str) -> Iterator[str]:
    """name、name (1)、name (2)…（保留雙副檔名）。"""
    stem, ext = split_name(name)
    yield name
    for i in range(1, MAX_ATTEMPTS):
        yield join_name(f"{stem} ({i})", ext)


def unique_path(directory: Path, name: str) -> Path:
    """第一個不存在的路徑（壞掉的符號連結也算已存在）。只供顯示用；寫入請用 commit。"""
    for cand in candidate_names(name):
        target = directory / cand
        if not os.path.lexists(target):
            return target
    raise errors.write_failed("找不到可用的檔名。")


# ---------------------------------------------------------------- 低階搬移


def _libc() -> ctypes.CDLL | None:
    path = ctypes.util.find_library("c")
    if not path:
        return None
    try:
        return ctypes.CDLL(path, use_errno=True)
    except OSError:
        return None


_LIBC = _libc()


def _rename_noreplace(src: Path, dst: Path) -> None:
    """不覆蓋的原子改名；目的地存在時丟 FileExistsError。"""
    if _LIBC is None or not hasattr(_LIBC, "renameat2"):
        raise OSError(errno.ENOSYS, "renameat2 不可用")
    res = _LIBC.renameat2(
        _AT_FDCWD, os.fsencode(src), _AT_FDCWD, os.fsencode(dst), _RENAME_NOREPLACE
    )
    if res != 0:
        err = ctypes.get_errno()
        if err in (errno.EEXIST, errno.ENOTEMPTY):
            raise FileExistsError(err, os.strerror(err), str(dst))
        raise OSError(err, os.strerror(err), str(dst))


def _copy_file_exclusive(src: Path, dst: Path) -> None:
    with open(src, "rb") as fin, open(dst, "xb") as fout:
        shutil.copyfileobj(fin, fout, 4 * 1024 * 1024)
    shutil.copystat(src, dst, follow_symlinks=False)
    if os.path.getsize(src) != os.path.getsize(dst):
        raise OSError(errno.EIO, "複製後大小不符", str(dst))


def _tree_signature(root: Path) -> tuple[int, int]:
    """（檔案數, 總大小），用來確認整個資料夾複製完整。"""
    count = total = 0
    for dirpath, _dirs, files in os.walk(root):
        for f in files:
            count += 1
            total += os.lstat(os.path.join(dirpath, f)).st_size
    return count, total


def _copy_tree_exclusive(src: Path, dst: Path) -> None:
    os.mkdir(dst)  # 目的地已存在時丟 FileExistsError
    shutil.copytree(src, dst, symlinks=True, dirs_exist_ok=True)
    if _tree_signature(src) != _tree_signature(dst):
        raise OSError(errno.EIO, "複製後內容不符", str(dst))


def _move_children(src: Path, dst: Path) -> None:
    """把 src 資料夾的內容逐一搬進全新的 dst（dst 是剛獨佔建立的空資料夾）。"""
    for child in os.listdir(src):
        _rename_noreplace_or_link(src / child, dst / child)
    os.rmdir(src)


def _rename_noreplace_or_link(src: Path, dst: Path) -> None:
    try:
        _rename_noreplace(src, dst)
        return
    except FileExistsError:
        raise
    except OSError as exc:
        if exc.errno not in (errno.EINVAL, errno.ENOSYS, errno.EOPNOTSUPP, errno.ENOTSUP):
            raise
    # 檔案系統不支援 RENAME_NOREPLACE（網路磁碟、FUSE…）
    if src.is_dir() and not src.is_symlink():
        os.mkdir(dst)
        _move_children(src, dst)
        return
    try:
        os.link(src, dst, follow_symlinks=False)
        os.unlink(src)
        return
    except FileExistsError:
        raise
    except OSError as exc:
        if exc.errno not in (errno.EPERM, errno.EOPNOTSUPP, errno.ENOTSUP, errno.EXDEV):
            raise
    # 連硬連結都不行（exFAT 等）：獨佔建立後複製
    _copy_file_exclusive(src, dst)
    os.unlink(src)


def _move_one(src: Path, dst: Path) -> None:
    """不覆蓋地把 src 搬到 dst；dst 存在時丟 FileExistsError。"""
    try:
        _rename_noreplace_or_link(src, dst)
        return
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise
    # 跨磁碟：先獨佔建立並複製，確認完整後才刪來源（來源是我們自己的暫存檔）
    if src.is_dir() and not src.is_symlink():
        _copy_tree_exclusive(src, dst)
        shutil.rmtree(src)
    else:
        _copy_file_exclusive(src, dst)
        os.unlink(src)


def commit(src: Path, directory: Path, name: str) -> Path:
    """把暫存的 src（檔案或資料夾）以 name 放進 directory，同名就加編號，絕不覆蓋。"""
    ensure_writable(directory)
    for cand in candidate_names(name):
        target = directory / cand
        if os.path.lexists(target):
            continue
        try:
            _move_one(Path(src), target)
            return target
        except FileExistsError:
            continue
        except OSError as exc:
            raise errors.write_failed(exc.strerror or str(exc)) from exc
    raise errors.write_failed("找不到可用的檔名。")


def move_no_overwrite(src: Path, directory: Path) -> Path:
    """「移動到…」用：把使用者的檔案搬進 directory，同名加編號；跨磁碟時確認複製完整才刪來源。"""
    return commit(Path(src), directory, Path(src).name)


def ensure_writable(directory: Path) -> None:
    d = Path(directory)
    if not d.is_dir() or not os.access(d, os.W_OK | os.X_OK):
        raise errors.output_folder_unavailable(str(d))


def _cache_root() -> Path:
    base = os.environ.get("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return Path(base) / "quickseemd" / "work"


def make_work_dir(near: Path) -> Path:
    """建立暫存資料夾，盡量和輸出位置在同一顆磁碟（之後改名才是原子操作）。

    與快取資料夾同一顆磁碟時放快取；否則在輸出資料夾裡建隱藏的 .quickseemd-* 資料夾。
    呼叫端負責在結束時用 remove_work_dir 清掉。
    """
    near = Path(near)
    cache = _cache_root()
    try:
        cache.mkdir(parents=True, exist_ok=True)
        if os.stat(cache).st_dev == os.stat(near).st_dev:
            return Path(tempfile.mkdtemp(prefix="job-", dir=cache))
    except OSError:
        pass
    try:
        outside = Path(tempfile.mkdtemp(prefix=WORK_PREFIX, dir=near))
    except OSError:
        cache.mkdir(parents=True, exist_ok=True)
        return Path(tempfile.mkdtemp(prefix="job-", dir=cache))
    _remember_outside(outside)
    return outside


def remove_work_dir(path: Path) -> None:
    """只刪 make_work_dir 建出來的暫存資料夾。"""
    p = Path(path)
    if p.name.startswith(("job-", WORK_PREFIX)) and p.is_dir() and not p.is_symlink():
        shutil.rmtree(p, ignore_errors=True)


# ---------------------------------------------------------------- 清掉被中斷工作留下的暫存資料夾

STALE_SECONDS = 24 * 3600
_OUTSIDE_LIST = "outside-dirs.txt"


def _outside_list() -> Path:
    return _cache_root() / _OUTSIDE_LIST


def _remember_outside(path: Path) -> None:
    """記下建在輸出資料夾裡的隱藏暫存資料夾，工作被強制結束時之後還能找到並清掉。"""
    try:
        _cache_root().mkdir(parents=True, exist_ok=True)
        with open(_outside_list(), "a", encoding="utf-8") as fh:
            fh.write(os.fspath(path) + "\n")
    except OSError:
        pass


def _is_stale(p: Path, now: float, max_age: float) -> bool:
    try:
        st = os.lstat(p)
    except OSError:
        return False
    return now - st.st_mtime > max_age


def sweep_stale_work_dirs(max_age: float = STALE_SECONDS) -> int:
    """刪掉超過 max_age 秒的暫存資料夾（工作程序被強制結束時留下的）；回傳刪了幾個。

    只碰快取裡的 job-* 與清單上記錄過、名稱是 .quickseemd-* 的資料夾，不會掃描使用者的資料夾。
    """
    import time  # noqa: PLC0415

    now = time.time()
    removed = 0
    cache = _cache_root()
    if cache.is_dir():
        for child in cache.iterdir():
            if child.name.startswith("job-") and _is_stale(child, now, max_age):
                remove_work_dir(child)
                removed += 1
    keep: list[str] = []
    try:
        listed = _outside_list().read_text("utf-8").splitlines()
    except OSError:
        listed = []
    for line in listed:
        p = Path(line)
        if not p.name.startswith(WORK_PREFIX) or not p.is_dir():
            continue
        if _is_stale(p, now, max_age):
            remove_work_dir(p)
            removed += 1
        else:
            keep.append(line)
    if listed:
        _rewrite_outside_list(keep)
    return removed


def sweep_in_background() -> None:
    """程式啟動時在背景清一次（不拖慢啟動）。"""
    import logging  # noqa: PLC0415
    import threading  # noqa: PLC0415

    def work() -> None:
        try:
            count = sweep_stale_work_dirs()
        except OSError:
            logging.getLogger(__name__).exception("清理暫存資料夾失敗")
            return
        if count:
            logging.getLogger(__name__).info("清掉 %d 個過期的暫存資料夾", count)

    threading.Thread(target=work, name="qs-sweep", daemon=True).start()


def _rewrite_outside_list(lines: list[str]) -> None:
    target = _outside_list()
    try:
        fd, tmp = tempfile.mkstemp(prefix=".outside-", dir=target.parent)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            fh.write("".join(f"{line}\n" for line in lines))
        os.replace(tmp, target)  # 我們自己的清單檔
    except OSError:
        pass
