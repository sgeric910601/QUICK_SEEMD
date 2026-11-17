"""LibreOffice 無介面轉檔（萬用轉換與預覽共用）。

- 每次轉檔都用全新的設定檔資料夾（-env:UserInstallation），不碰使用者自己的 LibreOffice；
  設定檔預先寫好安全設定：封鎖文件裡指向外部的連結（不會去抓網路上的圖片）、不更新連結、
  開檔不重新計算（WEBSERVICE 不會連網）、停用巨集。
- 有 bubblewrap 時再加一層隔離：沒有網路、系統唯讀，只有這次的暫存資料夾可寫。
- 用檔案鎖讓同一時間只有一個轉檔在跑；等鎖、執行時都可以取消，執行有逾時。
- 輸出放進全新的暫存資料夾，確認真的有產生檔案才回傳。

API：convert(src, filter_spec, outdir_parent, timeout=…, cancel=…, infilter=…) -> Path
"""

from __future__ import annotations

import fcntl
import logging
import os
import tempfile
import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from xml.sax.saxutils import quoteattr

from . import backends, errors, process

log = logging.getLogger(__name__)

TIMEOUT_SECONDS = 300.0
LOCK_POLL_SECONDS = 0.2

# 預先寫進設定檔的安全設定：（設定路徑, 名稱, 值）
REGISTRY: tuple[tuple[str, str, str], ...] = (
    # 不信任的文件（所有輸入都是）裡的外部連結一律不載入：不會連網、也不讀其他本機檔案
    ("/org.openoffice.Office.Common/Security/Scripting", "BlockUntrustedRefererLinks", "true"),
    # 巨集：最高安全層級，並完全停用執行
    ("/org.openoffice.Office.Common/Security/Scripting", "MacroSecurityLevel", "3"),
    ("/org.openoffice.Office.Common/Security/Scripting", "DisableMacrosExecution", "true"),
    # 開檔時不更新連結。Writer 用 LinkUpdateModes（0 永不、1 詢問、2 一律）；
    # Calc 用 ScLkUpdMode（0 一律、1 永不、2 詢問）——兩者的「永不」數值不同
    ("/org.openoffice.Office.Writer/Content/Update", "Link", "0"),
    ("/org.openoffice.Office.Calc/Content/Update", "Link", "1"),
    # 開檔時絕不重新計算（WEBSERVICE() 等函式與外部參照就不會去抓資料）：0 一律、1 永不、2 詢問
    ("/org.openoffice.Office.Calc/Formula/Load", "OOXMLRecalcMode", "1"),
    ("/org.openoffice.Office.Calc/Formula/Load", "ODFRecalcMode", "1"),
)


def registry_xml() -> str:
    items = "\n".join(
        f'<item oor:path={quoteattr(path)}><prop oor:name={quoteattr(name)} oor:op="fuse">'
        f"<value>{value}</value></prop></item>"
        for path, name, value in REGISTRY
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<oor:items xmlns:oor="http://openoffice.org/2001/registry" '
        'xmlns:xs="http://www.w3.org/2001/XMLSchema" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">\n'
        f"{items}\n</oor:items>\n"
    )


def make_profile(parent: Path) -> Path:
    """在 parent 裡建立全新的設定檔資料夾（含安全設定），回傳它的路徑。"""
    profile = Path(parent) / "profile"
    user = profile / "user"
    user.mkdir(parents=True)
    (user / "registrymodifications.xcu").write_text(registry_xml(), "utf-8")
    return profile


def lock_path() -> Path:
    return backends.cache_dir() / "soffice.lock"


@contextmanager
def lock(cancel: threading.Event | None = None) -> Iterator[None]:
    """同一時間只讓一個 LibreOffice 轉檔執行；等待時仍可取消。"""
    path = lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT | os.O_CLOEXEC, 0o600)
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if cancel is not None and cancel.is_set():
                    raise errors.Cancelled() from None
                time.sleep(LOCK_POLL_SECONDS)
        yield
    finally:
        os.close(fd)  # 關閉即釋放鎖


_sandbox_lock = threading.Lock()
_sandbox_ok: dict[str, bool] = {}


def _bwrap_base(bwrap: str, writable: Path) -> list[str]:
    root = str(Path(writable).resolve())
    return [
        bwrap,
        "--ro-bind", "/", "/",
        "--dev", "/dev",
        "--proc", "/proc",
        "--tmpfs", "/var/tmp",  # LibreOffice 的單一執行個體管道需要可寫的 /tmp 或 /var/tmp
        "--bind", root, root,
        "--unshare-net",
        "--die-with-parent",
        "--",
    ]  # fmt: skip


def sandbox_prefix(writable: Path) -> list[str]:
    """用 bubblewrap 隔離：沒有網路、整個系統唯讀，只有 writable（設定檔與輸出）可寫。

    bwrap 不在或這台不能用（例如不允許非特權的使用者命名空間）時回傳空清單，只靠設定檔防護。
    """
    bwrap = backends.find("bwrap")
    if bwrap is None:
        log.info("沒有 bwrap，LibreOffice 只靠設定檔封鎖外部連結")
        return []
    with _sandbox_lock:
        ok = _sandbox_ok.get(bwrap)
    if ok is None:
        probe = process.run(_bwrap_base(bwrap, writable) + ["/bin/true"], timeout=30)
        ok = probe.returncode == 0
        if not ok:
            log.warning("bwrap 無法使用（%s），LibreOffice 只靠設定檔封鎖外部連結", probe.stderr)
        with _sandbox_lock:
            _sandbox_ok[bwrap] = ok
    return _bwrap_base(bwrap, writable) if ok else []


def arguments(
    src: Path, filter_spec: str, out_dir: Path, profile: Path, infilter: str | None
) -> list[str]:  # noqa: E501
    tool = backends.require("soffice")
    args = [tool, f"-env:UserInstallation={profile.resolve().as_uri()}", "--headless"]
    args += ["--norestore", "--nologo", "--nodefault", "--nolockcheck"]
    if infilter:
        args.append(f"--infilter={infilter}")
    args += ["--convert-to", filter_spec, "--outdir", str(out_dir), str(Path(src).resolve())]
    return args


def convert(
    src: Path,
    filter_spec: str,
    outdir_parent: Path,
    *,
    timeout: float = TIMEOUT_SECONDS,
    cancel: threading.Event | None = None,
    infilter: str | None = None,
) -> Path:
    """轉檔並回傳產生的檔案（在 outdir_parent 裡新建的暫存資料夾的 out/ 裡）。"""
    scratch = Path(tempfile.mkdtemp(prefix=".soffice-", dir=outdir_parent))
    out_dir = scratch / "out"
    out_dir.mkdir()
    profile = make_profile(scratch)
    tmp = scratch / "tmp"
    tmp.mkdir()
    args = sandbox_prefix(scratch) + arguments(Path(src), filter_spec, out_dir, profile, infilter)
    env = {"SAL_USE_VCLPLUGIN": "svp", "HOME": str(scratch), "TMPDIR": str(tmp)}
    with lock(cancel):
        process.run_checked(args, "LibreOffice", cancel=cancel, timeout=timeout, env=env)
    produced = sorted(p for p in out_dir.iterdir() if p.is_file())
    if not produced:
        raise errors.tool_failed("LibreOffice", "沒有產生輸出檔。")
    return produced[0]
