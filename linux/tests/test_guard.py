"""證明測試防護真的有效（這個檔案失敗就不要跑其他測試）。"""

from __future__ import annotations

import os
import signal
import subprocess
from pathlib import Path

import pytest

import conftest
from quickseemd.core import backends, settings


def test_home_and_xdg_are_isolated():
    assert Path(os.environ["HOME"]).is_relative_to(conftest.TEST_ROOT)
    for key in ("XDG_CONFIG_HOME", "XDG_DATA_HOME", "XDG_CACHE_HOME", "XDG_RUNTIME_DIR"):
        assert Path(os.environ[key]).is_relative_to(conftest.TEST_ROOT)
    assert settings.settings_path().is_relative_to(conftest.TEST_ROOT)
    assert settings.autostart_path().is_relative_to(conftest.TEST_ROOT)


def test_dbus_points_nowhere():
    assert "/nonexistent/" in os.environ["DBUS_SESSION_BUS_ADDRESS"]
    from gi.repository import Gio, GLib

    with pytest.raises(GLib.Error):
        Gio.bus_get_sync(Gio.BusType.SESSION, None)


def test_display_is_unset_or_isolated():
    disp = os.environ.get("DISPLAY")
    if disp is not None:
        assert int(disp.lstrip(":").split(".")[0]) >= 120


@pytest.mark.parametrize("cmd", ["nemo", "systemctl", "gdbus", "gsettings", "xdotool"])
def test_dangerous_commands_are_blocked(cmd):
    path = backends.find(cmd)
    assert path is not None and Path(path).is_relative_to(conftest.TEST_ROOT)
    res = subprocess.run([path, "--version"], capture_output=True, check=False)
    assert res.returncode == conftest.BLOCK_EXIT


def test_signals_only_reach_own_children():
    # 用 posix_spawn 啟動一個不經過防護登記的程序：送訊號必須被擋下
    pid = os.posix_spawn("/usr/bin/sleep", ["sleep", "30"], os.environ)
    try:
        with pytest.raises(PermissionError):
            os.kill(pid, signal.SIGTERM)
        # 就算防護失效，這個群組 id 也只對到剛才的 sleep，不會波及其他程序
        with pytest.raises(PermissionError):
            os.killpg(pid, signal.SIGTERM)
    finally:
        conftest._real_kill(pid, signal.SIGKILL)
        os.waitpid(pid, 0)
    # 自己（經過防護）啟動的子程序可以結束
    proc = subprocess.Popen(["/usr/bin/sleep", "30"])
    proc.terminate()
    assert proc.wait(timeout=5) != 0
