"""LibreOffice（無介面模式）的安全設定檔與指令參數。

暫代版本：轉檔工作線會提供共用的 quickseemd.core.soffice；合併後 office.py 改從那裡匯入，
這個檔案就可以拿掉。對外只用 prepare_profile() 與 convert_args() 兩個函式。

每次轉換都用全新的設定檔資料夾，事先寫好 registrymodifications.xcu：
- BlockUntrustedRefererLinks=true：不抓文件裡連結的遠端資源（圖片、樣式…），預覽不能連網。
- 巨集安全性最高、停用巨集：預覽的文件不執行任何巨集。
"""

from __future__ import annotations

from pathlib import Path

REGISTRY_NAME = "registrymodifications.xcu"

_ITEMS = (
    ("/org.openoffice.Office.Common/Security/Scripting", "BlockUntrustedRefererLinks", "true"),
    ("/org.openoffice.Office.Common/Security/Scripting", "DisableMacrosExecution", "true"),
    ("/org.openoffice.Office.Common/Security/Scripting", "MacroSecurityLevel", "3"),
)


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
