"""主程式和系統的接點：整合頁狀態、自動啟動檔、視窗大小、剪貼簿文字、工作程序、D-Bus。

D-Bus 測試用自己啟動的私有匯流排（沒有任何服務目錄，不會自動啟動別的程式）。
"""

from __future__ import annotations

import os
import subprocess
import time
from pathlib import Path

import pytest
from gi.repository import Gio, GLib

from quickseemd.app import dbus as qsdbus
from quickseemd.app import desktop, integration, jobrunner, winstate
from quickseemd.core import errors, ipc, registry, settings
from quickseemd.core.runner import JobSpec

# ---------------------------------------------------------------- 整合頁


def test_extension_and_service_status(tmp_path):
    assert not integration.nemo_extension_installed()
    assert not integration.preview_service_installed()
    ext = integration.nemo_extension_path()
    ext.parent.mkdir(parents=True)
    ext.write_text("# 擴充")
    svc = integration.preview_service_path()
    svc.parent.mkdir(parents=True)
    svc.write_text("[D-BUS Service]")
    assert integration.nemo_extension_installed() and integration.preview_service_installed()
    assert str(ext).endswith("nemo-python/extensions/quickseemd_nemo.py")
    assert str(svc).endswith("dbus-1/services/org.nemo.Preview.service")
    assert integration.nemo_status(True) == ("Nemo 右鍵選單已啟用", True)
    assert integration.nemo_status(False)[1] is False
    assert integration.preview_status(True) == ("預覽擴充已啟用", True)
    assert integration.preview_status(False)[0].startswith("預覽擴充尚未啟用")
    assert integration.agent_status(None) == ("檢查中…", None)
    assert integration.agent_status(True)[1] is True
    assert integration.agent_status(False)[1] is False
    ext.unlink()
    svc.unlink()


def test_wheel_conflict_and_titles():
    vals = dict(settings.DEFAULTS)
    assert not integration.wheel_conflict(vals)
    vals["wheel.key.tools"] = vals["wheel.key.convert"]
    assert integration.wheel_conflict(vals)
    assert integration.WHEEL_CONFLICT_TEXT == "有兩個輪盤用了同一個按鍵，只會開啟排在前面的那個"
    assert [r[1] for r in integration.WHEEL_ROWS] == ["轉換輪盤", "工具輪盤", "動作輪盤"]


def test_autostart_requires_launcher_and_never_overwrites():
    path = settings.autostart_path()
    assert not integration.autostart_enabled()
    with pytest.raises(errors.QSError):
        integration.enable_autostart(Path("/nonexistent/quickseemd-agent"))
    launcher = integration.agent_launcher()
    launcher.parent.mkdir(parents=True, exist_ok=True)
    launcher.write_text("#!/bin/sh\n")
    launcher.chmod(0o755)
    try:
        assert integration.enable_autostart() == path
        text = path.read_text("utf-8")
        assert "Exec=" in text and "quickseemd-agent" in text and integration.MARKER in text
        assert integration.autostart_enabled()
        assert integration.enable_autostart() == path  # 已經有了就不動
        assert integration.disable_autostart()
        assert not path.exists()
        assert not integration.disable_autostart()
        # 不是我們寫的檔案不刪
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("[Desktop Entry]\nExec=/usr/bin/other\n")
        assert not integration.disable_autostart() and path.exists()
        path.unlink()
    finally:
        launcher.unlink()


def test_desktop_quote():
    assert integration.desktop_quote("/a/b") == "/a/b"
    assert integration.desktop_quote('/a b/"x"$') == '"/a b/\\"x\\"\\$"'


# ---------------------------------------------------------------- 視窗大小


def test_window_size_roundtrip():
    assert winstate.load_size() == winstate.DEFAULT_SIZE
    winstate.save_size(1300, 900)
    assert winstate.load_size() == (1300, 900)
    winstate.save_size(10, 10)
    assert winstate.load_size() == winstate.MIN_SIZE
    winstate.state_path().write_text("{bad json")
    assert winstate.load_size() == winstate.DEFAULT_SIZE


# ---------------------------------------------------------------- QR 與桌面動作


def test_desktop_actions_are_recorded_under_test_guard(tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("1")
    desktop.TEST_LOG.clear()
    desktop.reveal([f, tmp_path / "missing"])
    desktop.open_paths([f])
    assert desktop.TEST_LOG == [("reveal", [str(f)]), ("open", [str(f)])]
    desktop.reveal([tmp_path / "missing"])
    assert len(desktop.TEST_LOG) == 2


# ---------------------------------------------------------------- 工作程序


@pytest.fixture
def fake_tools(monkeypatch):
    monkeypatch.setenv("QUICKSEEMD_EXTRA_TOOLS", "tests.app_fake_tools")
    registry.reload()
    yield
    monkeypatch.delenv("QUICKSEEMD_EXTRA_TOOLS")
    registry.reload()


def _pump_until(pred, timeout: float = 20.0) -> None:
    ctx = GLib.MainContext.default()
    end = time.monotonic() + timeout
    while not pred() and time.monotonic() < end:
        ctx.iteration(False)
        time.sleep(0.01)
    assert pred(), "等待逾時"


def test_jobrunner_runs_tool_in_worker(fake_tools, tmp_path):
    src = tmp_path / "in.txt"
    src.write_text("內容")
    progress: list[tuple[float, str | None]] = []
    done: list[dict] = []
    spec = JobSpec("tool", "test.fail", [str(src)], {}, None)
    runner = jobrunner.JobRunner(spec, lambda f, m: progress.append((f, m)), done.append)
    runner.start()
    assert runner.active
    _pump_until(lambda: bool(done))
    assert done[0]["error"].startswith("故意失敗") and not runner.active
    assert progress and progress[-1][1] == "處理中：故意失敗"


def test_jobrunner_success_and_progress_monotonic(fake_tools, tmp_path):
    from conftest import make_pdf

    pdf = make_pdf(tmp_path / "文件.pdf", pages=1)
    progress: list[float] = []
    done: list[dict] = []
    spec = JobSpec("tool", "test.copy", [str(pdf)], {}, None)
    runner = jobrunner.JobRunner(spec, lambda f, m: progress.append(f), done.append)
    runner.start()
    _pump_until(lambda: bool(done))
    assert done[0].get("error") is None
    out = Path(done[0]["outputs"][0])
    assert out.name == "文件-已複製.pdf" and out.exists()
    assert progress == sorted(progress)


def test_jobrunner_cancel(fake_tools, tmp_path):
    done: list[dict] = []
    spec = JobSpec("tool", "test.slow", [str(tmp_path)], {}, None)
    runner = jobrunner.JobRunner(spec, lambda f, m: None, done.append)
    runner.start()
    time.sleep(0.8)
    runner.cancel()
    _pump_until(lambda: bool(done))
    assert done[0].get("cancelled") is True
    runner.cancel()  # 結束後再取消不會出錯


# ---------------------------------------------------------------- D-Bus


BUS_CONFIG = """<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <type>session</type>
  <listen>unix:dir={dir}</listen>
  <auth>EXTERNAL</auth>
  <policy context="default">
    <allow send_destination="*" eavesdrop="true"/>
    <allow eavesdrop="true"/>
    <allow own="*"/>
  </policy>
</busconfig>
"""


@pytest.fixture
def private_bus(tmp_path):
    conf = tmp_path / "bus.conf"
    sock = tmp_path / "sock"
    sock.mkdir()
    conf.write_text(BUS_CONFIG.format(dir=sock))
    proc = subprocess.Popen(
        ["/usr/bin/dbus-daemon", f"--config-file={conf}", "--print-address=1", "--nofork"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
    )
    address = proc.stdout.readline().decode().strip()
    flags = (
        Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
        | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION
    )
    server = Gio.DBusConnection.new_for_address_sync(address, flags, None, None)
    client = Gio.DBusConnection.new_for_address_sync(address, flags, None, None)
    yield server, client
    client.close_sync(None)
    server.close_sync(None)
    proc.terminate()
    proc.wait(timeout=5)


def _export_fake_agent(conn) -> list[str]:  # noqa: ANN001
    calls: list[str] = []
    node = Gio.DBusNodeInfo.new_for_xml(ipc.AGENT_XML)

    def on_call(_c, _s, _p, _i, method, _params, invocation):  # noqa: ANN001
        calls.append(method)
        if method == "Ping":
            invocation.return_value(GLib.Variant("(s)", ("1.0",)))
        else:
            invocation.return_value(None)

    conn.register_object(ipc.AGENT_OBJECT_PATH, node.interfaces[0], on_call, None, None)
    owned: list[bool] = []
    Gio.bus_own_name_on_connection(
        conn, ipc.AGENT_BUS_NAME, Gio.BusNameOwnerFlags.NONE, lambda *a: owned.append(True), None
    )
    _pump_until(lambda: bool(owned), 5)
    return calls


def test_agent_ping_and_quit(private_bus):
    server, client = private_bus
    results: list[bool] = []
    qsdbus.ping_agent(results.append, connection=client)
    _pump_until(lambda: bool(results), 5)
    assert results == [False]  # 沒有背景程式
    calls = _export_fake_agent(server)
    qsdbus.ping_agent(results.append, connection=client, autostart=False)
    _pump_until(lambda: len(results) == 2, 5)
    assert results[1] is True and calls == ["Ping"]
    quits: list[bool] = []
    qsdbus.quit_agent(quits.append, connection=client)
    _pump_until(lambda: bool(quits), 5)
    assert quits == [True] and calls == ["Ping", "Quit"]


def test_preview_show_file_and_reveal_call(private_bus):
    server, client = private_bus
    got: list[tuple] = []
    node = Gio.DBusNodeInfo.new_for_xml(ipc.PREVIEW_XML)

    def on_call(_c, _s, _p, _i, method, params, invocation):  # noqa: ANN001
        got.append((method, params.unpack()))
        invocation.return_value(None)

    server.register_object(ipc.PREVIEW_OBJECT_PATH, node.interfaces[0], on_call, None, None)
    owned: list[bool] = []
    Gio.bus_own_name_on_connection(
        server, ipc.PREVIEW_BUS_NAME, 0, lambda *a: owned.append(True), None
    )
    _pump_until(lambda: bool(owned), 5)
    errs: list[str | None] = []
    qsdbus.preview_show_file("file:///tmp/a.txt", 1234, errs.append, connection=client)
    _pump_until(lambda: bool(errs), 5)
    assert errs == [None]
    assert got == [("ShowFile", ("file:///tmp/a.txt", 1234, False))]


def test_dbus_without_bus_reports_failure():
    errs: list[str | None] = []
    qsdbus.preview_show_file("file:///x", 0, errs.append)
    _pump_until(lambda: bool(errs), 5)
    assert errs[0]
    pings: list[bool] = []
    qsdbus.ping_agent(pings.append)
    _pump_until(lambda: bool(pings), 5)
    assert pings == [False]
    quits: list[bool] = []
    qsdbus.quit_agent(quits.append)
    _pump_until(lambda: bool(quits), 5)
    assert quits == [False]
    assert os.environ["QS_TEST_GUARD"] == "1"
