"""安裝程式測試的共用工具：一律在暫存資料夾裡的假 HOME 執行，絕不碰真正的家目錄。"""

from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path

import conftest

LINUX_DIR = conftest.LINUX_DIR
REPO_DIR = conftest.REPO_DIR
INSTALL = LINUX_DIR / "install.sh"
UNINSTALL = LINUX_DIR / "uninstall.sh"
ICON_SIZES = (16, 24, 32, 48, 64, 128, 256, 512)
MODULES = {
    "quickseemd": "quickseemd",
    "quickseemd-agent": "quickseemd.agent",
    "quickseemd-preview": "quickseemd.previewer",
}
NEMO_HINT = "請執行 nemo -q 後重新打開 Nemo，右鍵選單才會出現"


@dataclass(frozen=True)
class Home:
    root: Path

    @property
    def home(self) -> Path:
        return self.root / "home"

    @property
    def data(self) -> Path:
        return self.home / ".local" / "share"

    @property
    def config(self) -> Path:
        return self.home / ".config"

    @property
    def cache(self) -> Path:
        return self.home / ".cache"

    @property
    def bin(self) -> Path:
        return self.home / ".local" / "bin"

    @property
    def qs(self) -> Path:
        return self.data / "quickseemd"

    @property
    def lib(self) -> Path:
        return self.qs / "lib"

    @property
    def manifest(self) -> Path:
        return self.qs / "install-manifest.txt"

    @property
    def hicolor(self) -> Path:
        return self.data / "icons" / "hicolor"

    @property
    def env(self) -> dict[str, str]:
        return {
            "HOME": str(self.home),
            "XDG_DATA_HOME": str(self.data),
            "XDG_CONFIG_HOME": str(self.config),
            "XDG_CACHE_HOME": str(self.cache),
            "PATH": "/usr/bin:/bin",
            "LANG": "C.UTF-8",
            "TMPDIR": str(self.root / "tmp"),
        }

    def manifest_entries(self) -> list[str]:
        return [ln for ln in self.manifest.read_text("utf-8").splitlines() if ln]


def make_home(root: Path) -> Home:
    h = Home(root)
    for d in (h.home, h.root / "tmp"):
        d.mkdir(parents=True, exist_ok=True)
    # 安全檢查：測試用的 HOME 一定在暫存資料夾裡
    assert h.home.resolve().is_relative_to(root.resolve())
    assert str(h.home) != os.path.expanduser("~") or "qs-test-" in str(h.home)
    return h


def run(
    h: Home, script: Path, *args: str, stdin: str = "", env: dict[str, str] | None = None
) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["/bin/bash", str(script), *args],
        input=stdin,
        capture_output=True,
        text=True,
        env=env or h.env,
        timeout=180,
        check=False,
    )


def run_sourced(h: Home, script: Path, code: str, stdin: str = "") -> subprocess.CompletedProcess:
    """載入腳本的函式（不執行 main）後跑一段 bash，測試個別步驟。"""
    # $0 故意設成別的名字：腳本靠「BASH_SOURCE 等於 $0」判斷要不要執行主程式
    return subprocess.run(
        ["/bin/bash", "-c", f'source "$1"\n{code}', "qs-test-shell", str(script)],
        input=stdin,
        capture_output=True,
        text=True,
        env=h.env,
        timeout=120,
        check=False,
    )


def all_files(root: Path) -> set[Path]:
    return {p for p in root.rglob("*") if p.is_file() or p.is_symlink()}
