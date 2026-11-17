"""畫面測試的共用設定：隔離的 HOME、只認得測試服務的私有 D-Bus、在 dbus-run-session 裡跑驅動程式。

D-Bus 設定檔只列出測試自己的服務資料夾，所以私有工作階段不會啟動任何系統服務；
畫面一律用 QS_TEST_DISPLAY（:120 以上的隔離畫面）。
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

import conftest

DRIVER = Path(__file__).with_name("previewer_gui_driver.py")
SHOT_DIR_ENV = "QS_GUI_SHOT_DIR"  # 設定的話，截圖另外複製一份到這裡（方便人工檢查）

BUS_CONFIG = """<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <type>session</type>
  <listen>unix:tmpdir={runtime}</listen>
  <auth>EXTERNAL</auth>
  <servicedir>{services}</servicedir>
  <policy context="default">
    <allow send_destination="*" eavesdrop="true"/>
    <allow eavesdrop="true"/>
    <allow own="*"/>
  </policy>
</busconfig>
"""


@dataclass(frozen=True)
class Session:
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
    def services(self) -> Path:
        return self.root / "services"

    @property
    def runtime(self) -> Path:
        return self.root / "run"

    @property
    def bus_config(self) -> Path:
        return self.root / "session.conf"

    @property
    def shots(self) -> Path:
        return self.root / "shots"

    def env(self) -> dict[str, str]:
        env = {
            "DISPLAY": os.environ["QS_TEST_DISPLAY"],
            "HOME": str(self.home),
            "XDG_CONFIG_HOME": str(self.config),
            "XDG_DATA_HOME": str(self.data),
            "XDG_CACHE_HOME": str(self.home / ".cache"),
            "XDG_RUNTIME_DIR": str(self.runtime),
            "GSETTINGS_BACKEND": "memory",
            "NO_AT_BRIDGE": "1",
            "GIO_USE_VFS": "local",
            "LANG": "zh_TW.UTF-8",
            "PATH": "/usr/bin:/bin",
            "PYTHONPATH": str(conftest.LINUX_DIR),
        }
        # 讓預覽服務在子程序裡也記錄覆蓋率（pytest-cov 透過這些變數設定）
        env.update({k: v for k, v in os.environ.items() if k.startswith("COV_CORE_")})
        return env


def make_session(root: Path) -> Session:
    s = Session(root)
    for d in (s.config, s.data, s.home / ".cache", s.services, s.shots):
        d.mkdir(parents=True, exist_ok=True)
    s.runtime.mkdir(mode=0o700)
    s.bus_config.write_text(BUS_CONFIG.format(runtime=s.runtime, services=s.services), "utf-8")
    return s


def write_service(s: Session, name: str, exec_line: str) -> None:
    text = f"[D-BUS Service]\nName={name}\nExec={exec_line}\n"
    (s.services / f"{name}.service").write_text(text, "utf-8")


def run_driver(s: Session, scenario: str, cfg: dict, timeout: int = 300) -> dict:
    report = s.root / f"report-{scenario}.json"
    cfg = dict(cfg, outdir=str(s.shots), report=str(report))
    cfg_path = s.root / f"config-{scenario}.json"
    cfg_path.write_text(json.dumps(cfg, ensure_ascii=False), "utf-8")
    log = s.root / f"driver-{scenario}.log"
    with open(log, "w", encoding="utf-8") as out:
        subprocess.run(
            [
                "/usr/bin/dbus-run-session",
                f"--config-file={s.bus_config}",
                "--",
                "/usr/bin/python3",
                str(DRIVER),
                scenario,
                str(cfg_path),
            ],
            env=s.env(),
            stdout=out,
            stderr=subprocess.STDOUT,
            timeout=timeout,
            check=False,
        )
    _copy_shots(s)
    if not report.exists():
        raise AssertionError("驅動程式沒有產生報告：\n" + log.read_text("utf-8")[-4000:])
    data = json.loads(report.read_text("utf-8"))
    data["_log"] = str(log)
    return data


def _copy_shots(s: Session) -> None:
    target = os.environ.get(SHOT_DIR_ENV)
    if not target:
        return
    dest = Path(target)
    dest.mkdir(parents=True, exist_ok=True)
    for png in s.shots.glob("*.png"):
        shutil.copyfile(png, dest / png.name)
