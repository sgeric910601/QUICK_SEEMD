"""啟動主程式、自動啟動檔（只動 settings.autostart_path() 這一個檔案）。"""

from __future__ import annotations

import os
import sys
import time
from pathlib import Path

import pytest

from quickseemd.agent import launcher
from quickseemd.core import settings


def _exe(path: Path, body: str = "#!/bin/sh\nexit 0\n") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body)
    path.chmod(0o755)
    return path


def test_fallback_runs_module_with_pythonpath(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", [str(tmp_path / "nowhere" / "x.py")])
    argv, env = launcher.app_command("open", "/tmp/a.pdf")
    assert argv[1:] == ["-m", "quickseemd", "open", "/tmp/a.pdf"]
    assert os.path.isabs(argv[0])
    assert env["PYTHONPATH"].endswith("linux") or Path(env["PYTHONPATH"], "quickseemd").is_dir()


def test_launcher_next_to_argv0_wins(monkeypatch, tmp_path):
    bindir = tmp_path / "bin"
    _exe(bindir / "quickseemd")
    local = _exe(Path.home() / ".local" / "bin" / "quickseemd")
    try:
        monkeypatch.setattr(sys, "argv", [str(bindir / "quickseemd-agent")])
        argv, env = launcher.app_command()
        assert argv == [str(bindir / "quickseemd")]
        assert env == {}
    finally:
        local.unlink()


def test_launcher_in_local_bin(monkeypatch, tmp_path):
    local = _exe(Path.home() / ".local" / "bin" / "quickseemd")
    try:
        monkeypatch.setattr(sys, "argv", [str(tmp_path / "x.py")])
        argv, _env = launcher.app_command("open")
        assert argv == [str(local), "open"]
    finally:
        local.unlink()


def test_non_executable_candidate_is_skipped(monkeypatch, tmp_path):
    bindir = tmp_path / "bin"
    bindir.mkdir()
    (bindir / "quickseemd").write_text("not executable")
    monkeypatch.setattr(sys, "argv", [str(bindir / "x")])
    argv, _env = launcher.app_command()
    assert argv[1:3] == ["-m", "quickseemd"]


def test_open_files_command(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", [str(tmp_path / "x.py")])
    argv, _ = launcher.open_files_command([Path("/a/b.pdf")], tool_id="pdf.compress")
    assert argv[-4:] == ["open", "--tool", "pdf.compress", "/a/b.pdf"]
    argv, _ = launcher.open_files_command([Path("/a/b.pdf"), Path("/c")])
    assert argv[-3:] == ["open", "/a/b.pdf", "/c"]
    argv, _ = launcher.open_files_command([], tool_id="pdf.merge")
    assert argv[-3:] == ["open", "--tool", "pdf.merge"]


def test_spawn_runs_and_reaps(tmp_path):
    marker = tmp_path / "ran"
    script = _exe(tmp_path / "s.sh", f'#!/bin/sh\necho "$@" > "{marker}"\n')
    assert launcher.spawn([str(script), "一", "二"]) is True
    deadline = time.monotonic() + 5
    while not marker.exists() and time.monotonic() < deadline:
        time.sleep(0.02)
    assert marker.read_text().strip() == "一 二"


def test_spawn_rejects_relative_and_reports_failure(tmp_path):
    assert launcher.spawn(["quickseemd"]) is False
    assert launcher.spawn([str(tmp_path / "missing")]) is False


@pytest.mark.parametrize(
    ("argv", "expected"),
    [
        (["/usr/bin/a"], "/usr/bin/a"),
        (["/opt/my app/run", "-x"], '"/opt/my app/run" -x'),
        (["/a", 'q"uote'], '/a "q\\\\"uote"'),
        (["/a", "50%"], "/a 50%%"),
        (["/a", "$HOME"], '/a "\\\\$HOME"'),
    ],
)
def test_desktop_exec_quoting(argv, expected):
    assert launcher.desktop_exec(argv) == expected


def test_autostart_toggle_creates_and_removes_only_our_file(monkeypatch, tmp_path):
    agent = _exe(tmp_path / "bin" / "quickseemd-agent")
    monkeypatch.setattr(sys, "argv", [str(agent)])
    path = settings.autostart_path()
    assert not launcher.autostart_enabled()
    launcher.set_autostart(True)
    assert path.is_file()
    text = path.read_text("utf-8")
    assert f"Exec={agent}\n" in text
    assert "Type=Application" in text
    assert "Icon=dev.quickseemd.App" in text
    assert launcher.autostart_enabled()
    neighbour = path.parent / "other.desktop"
    neighbour.write_text("[Desktop Entry]\n")
    launcher.set_autostart(False)
    assert not path.exists()
    assert neighbour.exists()
    assert not launcher.autostart_enabled()
    launcher.set_autostart(False)  # 已經關了也不出錯


def test_autostart_disabled_by_hidden_flag():
    path = settings.autostart_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[Desktop Entry]\nType=Application\nHidden=true\n")
    try:
        assert not launcher.autostart_enabled()
    finally:
        path.unlink()


def test_agent_exec_fallback(monkeypatch, tmp_path):
    monkeypatch.setattr(sys, "argv", [str(tmp_path / "x.py")])
    argv = launcher.agent_command()
    assert argv[0] == "/usr/bin/env"
    assert argv[1].startswith("PYTHONPATH=")
    assert argv[-2:] == ["-m", "quickseemd.agent"]
