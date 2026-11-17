"""Nemo 整合的畫面測試：install.sh 裝進隔離的家目錄，在私有工作階段裡跑 Nemo。

- 在 Nemo 選第一個檔案按空白鍵 → 預覽視窗出現、掛在 Nemo 視窗上；再按一次關閉。
- 開右鍵選單 → 有「QUICK_SEEMD 轉換」→ 選第一個動作 → 背景程式收到 RunAction。
背景程式用測試的替身（只記錄收到的呼叫）。設定 QS_TEST_DISPLAY 才會執行。
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from install_support import INSTALL
from previewer_gui_support import DRIVER, make_session, run_driver, write_service

import conftest

pytestmark = [pytest.mark.gui, pytest.mark.slow]


@pytest.fixture(scope="module")
def report(tmp_path_factory: pytest.TempPathFactory) -> dict:
    s = make_session(tmp_path_factory.mktemp("nemo"))
    env = s.env()
    env["TMPDIR"] = str(s.root)
    res = subprocess.run(
        ["/bin/bash", str(INSTALL), "--no-autostart"],
        env=env,
        capture_output=True,
        text=True,
        timeout=180,
        check=False,
    )
    assert res.returncode == 0, res.stdout + res.stderr
    # 預覽服務用裝好的那一份（服務檔指向裝好的啟動檔）
    installed = s.data / "dbus-1" / "services" / "org.nemo.Preview.service"
    shutil.copyfile(installed, s.services / installed.name)
    agent_log = s.root / "agent-calls.jsonl"
    write_service(s, "dev.quickseemd.Agent", f"/usr/bin/python3 {DRIVER} fake-agent {agent_log}")
    folder = s.root / "測試資料夾"
    folder.mkdir()
    first = conftest.make_pdf(folder / "a-文件.pdf", pages=2)
    (folder / "b-說明.txt").write_text("說明\n", "utf-8")
    cfg = {
        "folder": str(folder),
        "first": str(first),
        "agent_log": str(agent_log),
        "nemo_log": str(s.root / "nemo.log"),
    }
    data = run_driver(s, "nemo", cfg)
    assert "error" not in data, data.get("error")
    data["_first"] = str(first)
    return data


def test_nemo_started(report):
    assert report["nemo_started"]


def test_space_opens_preview_on_top_of_nemo(report):
    assert report["preview_shown"]
    preview = report["preview"]
    assert preview["transient_for"] == report["nemo"]["id"]
    assert "_NET_WM_WINDOW_TYPE_DIALOG" in preview["types"]


def test_space_again_closes_preview(report):
    assert report["preview_closed"]


def test_context_menu_shows_quickseemd(report):
    assert report["menu_item_found"], report.get("menu_shot")


def test_menu_action_reaches_agent(report):
    calls = report["agent_calls"]
    assert ["RunAction", ["pdf.compress-recommended", [report["_first"]]]] in calls


def test_preview_service_stopped(report):
    assert report["service_stopped"]


def test_driver_file_exists():
    assert Path(DRIVER).is_file()
