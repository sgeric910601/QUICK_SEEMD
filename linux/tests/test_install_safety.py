"""安裝程式：重複安裝、不覆蓋別人的檔案、安裝紀錄外的路徑絕不碰。"""

from __future__ import annotations

from pathlib import Path

import pytest
from install_support import INSTALL, Home, make_home, run

FOREIGN = "這是使用者自己的檔案\n"


@pytest.fixture
def home(tmp_path: Path) -> Home:
    return make_home(tmp_path)


def _snapshot(h: Home) -> dict[str, bytes]:
    return {e: Path(e).read_bytes() for e in h.manifest_entries()}


def test_reinstall_is_idempotent(home: Home):
    assert run(home, INSTALL).returncode == 0
    first = _snapshot(home)
    res = run(home, INSTALL)
    assert res.returncode == 0, res.stdout + res.stderr
    assert _snapshot(home) == first


def test_reinstall_restores_our_modified_file(home: Home):
    assert run(home, INSTALL).returncode == 0
    launcher = home.bin / "quickseemd"
    original = launcher.read_text("utf-8")
    launcher.write_text("#!/bin/sh\nexit 1\n", "utf-8")
    assert run(home, INSTALL).returncode == 0
    assert launcher.read_text("utf-8") == original


@pytest.mark.parametrize(
    "relative",
    [
        ".local/bin/quickseemd",
        ".local/share/dbus-1/services/org.nemo.Preview.service",
        ".local/share/nemo-python/extensions/quickseemd_nemo.py",
        ".local/share/quickseemd/lib/quickseemd/core/ipc.py",
        ".config/autostart/dev.quickseemd.Agent.desktop",
    ],
)
def test_refuses_to_overwrite_foreign_file(home: Home, relative: str):
    foreign = home.home / relative
    foreign.parent.mkdir(parents=True, exist_ok=True)
    foreign.write_text(FOREIGN, "utf-8")
    res = run(home, INSTALL)
    assert res.returncode != 0
    out = res.stdout + res.stderr
    assert "不是 QUICK_SEEMD 安裝的" in out
    assert str(foreign) in out
    assert foreign.read_text("utf-8") == FOREIGN
    # 發現衝突就什麼都不裝
    assert not home.manifest.exists()
    assert not (home.bin / "quickseemd-preview").exists()


def test_refuses_foreign_symlink(home: Home, tmp_path: Path):
    target = tmp_path / "別人的程式"
    target.write_text(FOREIGN, "utf-8")
    home.bin.mkdir(parents=True)
    (home.bin / "quickseemd-agent").symlink_to(target)
    res = run(home, INSTALL)
    assert res.returncode != 0
    assert (home.bin / "quickseemd-agent").is_symlink()
    assert target.read_text("utf-8") == FOREIGN


def test_extra_files_in_our_folders_are_kept(home: Home):
    assert run(home, INSTALL).returncode == 0
    note = home.lib / "quickseemd" / "筆記.txt"
    note.write_text(FOREIGN, "utf-8")
    assert run(home, INSTALL).returncode == 0
    assert note.read_text("utf-8") == FOREIGN
    assert str(note) not in home.manifest_entries()


def test_files_from_older_version_are_removed(home: Home):
    assert run(home, INSTALL).returncode == 0
    old = home.lib / "quickseemd" / "old_module.py"
    old.write_text("# 舊版的模組\n", "utf-8")
    entries = home.manifest_entries() + [str(old)]
    home.manifest.write_text("\n".join(entries) + "\n", "utf-8")
    assert run(home, INSTALL).returncode == 0
    assert not old.exists()
    assert str(old) not in home.manifest_entries()


def test_manifest_entries_outside_install_roots_are_never_touched(home: Home, tmp_path: Path):
    assert run(home, INSTALL).returncode == 0
    precious = home.home / "重要.txt"
    precious.write_text(FOREIGN, "utf-8")
    outside = tmp_path / "outside.txt"
    outside.write_text(FOREIGN, "utf-8")
    sneaky = home.lib / ".." / ".." / ".." / ".." / "重要.txt"
    entries = home.manifest_entries() + [str(precious), str(outside), str(sneaky), "relative.txt"]
    home.manifest.write_text("\n".join(entries) + "\n", "utf-8")
    res = run(home, INSTALL)
    assert res.returncode == 0, res.stdout + res.stderr
    assert precious.read_text("utf-8") == FOREIGN
    assert outside.read_text("utf-8") == FOREIGN
    assert str(precious) not in home.manifest_entries()


def test_no_autostart_option(home: Home):
    autostart = home.config / "autostart" / "dev.quickseemd.Agent.desktop"
    assert run(home, INSTALL, "--no-autostart").returncode == 0
    assert not autostart.exists()
    assert str(autostart) not in home.manifest_entries()
    assert run(home, INSTALL).returncode == 0
    assert autostart.is_file()
    # 之後選擇不要自動啟動：移除我們先前裝的那一份
    assert run(home, INSTALL, "--no-autostart").returncode == 0
    assert not autostart.exists()


@pytest.mark.parametrize("bad", ['quote"home', "dollar$home", "back`tick", "per%cent"])
def test_rejects_unsafe_home_path(tmp_path: Path, bad: str):
    h = make_home(tmp_path / bad)
    res = run(h, INSTALL)
    assert res.returncode != 0
    assert "不支援的字元" in res.stdout + res.stderr
    assert not h.manifest.exists()


def test_home_with_spaces_and_chinese_is_quoted(tmp_path: Path):
    h = make_home(tmp_path / "我的 家")
    res = run(h, INSTALL)
    assert res.returncode == 0, res.stdout + res.stderr
    service = h.data / "dbus-1" / "services" / "org.nemo.Preview.service"
    assert f'Exec="{h.bin / "quickseemd-preview"}"' in service.read_text("utf-8")
    from gi.repository import GLib  # noqa: PLC0415

    kf = GLib.KeyFile()
    kf.load_from_file(str(h.data / "applications" / "dev.quickseemd.App.desktop"), 0)
    ok, argv = GLib.shell_parse_argv(kf.get_string("Desktop Entry", "Exec"))
    assert ok and argv == [str(h.bin / "quickseemd")]


def test_relative_xdg_dirs_are_ignored(home: Home):
    env = dict(home.env, XDG_DATA_HOME="relative/share", XDG_CONFIG_HOME="")
    res = run(home, INSTALL, env=env)
    assert res.returncode == 0, res.stdout + res.stderr
    assert (home.home / ".local/share/quickseemd/install-manifest.txt").is_file()
    assert (home.home / ".config/autostart/dev.quickseemd.Agent.desktop").is_file()


def test_unknown_option(home: Home):
    res = run(home, INSTALL, "--bogus")
    assert res.returncode == 2
    assert "未知的參數" in res.stdout + res.stderr


def test_help(home: Home):
    res = run(home, INSTALL, "--help")
    assert res.returncode == 0
    for flag in ("--deps", "--models", "--no-autostart"):
        assert flag in res.stdout
    assert not home.manifest.exists()
