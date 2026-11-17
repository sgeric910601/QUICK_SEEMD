"""LibreOffice（無介面模式）的安全設定檔與指令參數。

暫代版本：轉檔工作線會提供共用的 quickseemd.core.soffice；合併後 office.py 改從那裡匯入，
這個檔案就可以拿掉。對外只用 prepare_profile()、convert_args()、wrap() 三個函式。

每次轉換都用全新的設定檔資料夾，事先寫好 registrymodifications.xcu：
- BlockUntrustedRefererLinks=true：不抓文件裡連結的遠端資源（圖片、樣式…），預覽不能連網。
- 載入時不更新連結：全域設定的名稱是 Content/Update/Link（文件裡的 LinkUpdateMode 是另一回事）。
  注意兩邊數值意義不同：Calc 是 0 一律、1 永不、2 詢問；Writer 是 0 永不、1 手動、2 自動。
- 載入時不重新計算（OOXMLRecalcMode、ODFRecalcMode＝1 永不），WEBSERVICE() 之類不會連網。
- 巨集安全性最高、停用巨集：預覽的文件不執行任何巨集。

多一層防護：這台能用 bwrap 時，LibreOffice 在沒有網路的隔離環境裡執行，
整個系統唯讀，只有這次的設定檔與輸出資料夾可寫；bwrap 不能用就只靠設定檔，並記錄下來。
"""

from __future__ import annotations

import logging
import os
from collections.abc import Sequence
from functools import lru_cache
from pathlib import Path

from ..core import process

log = logging.getLogger(__name__)

REGISTRY_NAME = "registrymodifications.xcu"
BWRAP = "/usr/bin/bwrap"
PROBE_TIMEOUT = 10.0
_SCRIPTING = "/org.openoffice.Office.Common/Security/Scripting"
_ITEMS = (
    (_SCRIPTING, "BlockUntrustedRefererLinks", "true"),
    (_SCRIPTING, "DisableMacrosExecution", "true"),
    (_SCRIPTING, "MacroSecurityLevel", "3"),
    ("/org.openoffice.Office.Calc/Content/Update", "Link", "1"),  # Calc：1＝永不
    ("/org.openoffice.Office.Writer/Content/Update", "Link", "0"),  # Writer：0＝永不
    ("/org.openoffice.Office.Calc/Formula/Load", "OOXMLRecalcMode", "1"),  # 1＝永不重算
    ("/org.openoffice.Office.Calc/Formula/Load", "ODFRecalcMode", "1"),
)
# 隔離環境裡不給 LibreOffice 的環境變數（連到桌面工作階段的通道）
_HIDDEN_ENV = ("DBUS_SESSION_BUS_ADDRESS", "DISPLAY", "WAYLAND_DISPLAY", "XAUTHORITY")


def registry_xml() -> str:
    items = "".join(
        f'<item oor:path="{path}"><prop oor:name="{name}" oor:op="fuse">'
        f"<value>{value}</value></prop></item>\n"
        for path, name, value in _ITEMS
    )
    return (
        '<?xml version="1.0" encoding="UTF-8"?>\n'
        '<oor:items xmlns:oor="http://openoffice.org/2001/registry" '
        'xmlns:xs="http://www.w3.org/2001/XMLSchema" '
        'xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance">\n'
        f"{items}</oor:items>\n"
    )


def prepare_profile(profile: Path) -> None:
    """在全新的設定檔資料夾寫好安全設定（檔案已存在就丟 FileExistsError，絕不覆蓋）。"""
    user = Path(profile) / "user"
    user.mkdir(parents=True, exist_ok=True)
    with open(user / REGISTRY_NAME, "x", encoding="utf-8") as fh:
        fh.write(registry_xml())


def convert_args(
    soffice: str, src: Path, profile: Path, outdir: Path, target: str = "pdf"
) -> list[str]:
    return [
        soffice,
        f"-env:UserInstallation={Path(profile).as_uri()}",
        "--headless",
        "--invisible",
        "--norestore",
        "--nologo",
        "--nodefault",
        "--nolockcheck",
        "--convert-to",
        target,
        "--outdir",
        str(outdir),
        str(src),
    ]


# ---------------------------------------------------------------- bwrap 隔離


def _base_sandbox() -> list[str]:
    args = [BWRAP, "--unshare-net", "--die-with-parent", "--ro-bind", "/", "/"]
    args += ["--dev", "/dev", "--proc", "/proc", "--tmpfs", "/tmp", "--tmpfs", "/run"]
    for name in _HIDDEN_ENV:
        args += ["--unsetenv", name]
    return args + ["--setenv", "TMPDIR", "/tmp"]


def sandbox_prefix(writable: Sequence[Path], readable: Sequence[Path]) -> list[str]:
    """bwrap 的參數：沒有網路、系統唯讀；writable 可寫，readable（例如輸入檔）唯讀掛入。

    私有的 /tmp、/run 先掛，之後才掛這些路徑，放在 /tmp 底下的輸入檔也看得到。
    """
    args = _base_sandbox()
    for path in writable:
        args += ["--bind", str(path), str(path)]
    for path in readable:
        args += ["--ro-bind", str(path), str(path)]
    return args + ["--"]


@lru_cache(maxsize=1)
def probe_sandbox() -> bool:
    """試跑一次 bwrap（Ubuntu 會限制非特權的使用者命名空間）；結果在這個程序裡記住。"""
    if not os.access(BWRAP, os.X_OK):
        log.warning("找不到 bwrap，LibreOffice 預覽只靠設定檔防護")
        return False
    try:
        res = process.run(_base_sandbox() + ["--", "/bin/true"], timeout=PROBE_TIMEOUT)
    except Exception:  # noqa: BLE001 — 試不起來就退回只用設定檔，並記錄
        log.warning("bwrap 無法執行，LibreOffice 預覽只靠設定檔防護", exc_info=True)
        return False
    if res.returncode != 0:
        detail = process.last_lines(res.stderr)
        log.warning("bwrap 無法使用（%s），LibreOffice 預覽只靠設定檔防護", detail)
        return False
    return True


def sandbox_available() -> bool:
    return probe_sandbox()


def wrap(argv: list[str], writable: Sequence[Path], readable: Sequence[Path]) -> list[str]:
    """能用 bwrap 就包起來執行，否則原樣回傳。"""
    if sandbox_available():
        return sandbox_prefix(writable, readable) + list(argv)
    return list(argv)
