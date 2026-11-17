"""安裝程式：裝好的檔案位置、內容、權限與安裝紀錄。"""

from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest
from gi.repository import GLib
from install_support import (
    ICON_SIZES,
    INSTALL,
    LINUX_DIR,
    MODULES,
    NEMO_HINT,
    REPO_DIR,
    Home,
    all_files,
    make_home,
    run,
)

PKG = LINUX_DIR / "quickseemd"


@pytest.fixture(scope="module")
def installed(tmp_path_factory: pytest.TempPathFactory) -> tuple[Home, subprocess.CompletedProcess]:
    h = make_home(tmp_path_factory.mktemp("install"))
    res = run(h, INSTALL)
    return h, res


def _keyfile(path: Path) -> GLib.KeyFile:
    kf = GLib.KeyFile()
    kf.load_from_file(str(path), GLib.KeyFileFlags.NONE)
    return kf


def test_install_succeeds_with_nemo_hint(installed):
    _h, res = installed
    assert res.returncode == 0, res.stdout + res.stderr
    assert NEMO_HINT in res.stdout


def test_package_is_copied_without_caches(installed):
    h, _res = installed
    target = h.lib / "quickseemd"
    sources = [
        p
        for p in PKG.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and not p.name.startswith(".")
    ]
    assert sources
    for src in sources:
        copy = target / src.relative_to(PKG)
        assert copy.read_bytes() == src.read_bytes(), copy
        assert stat.S_IMODE(copy.stat().st_mode) == 0o644
    assert not list(target.rglob("__pycache__"))
    assert (target / "VERSION").read_text("utf-8") == (REPO_DIR / "VERSION").read_text("utf-8")


def test_data_files(installed):
    h, _res = installed
    share = h.qs / "data"
    assert (share / "models.json").read_bytes() == (LINUX_DIR / "data/models.json").read_bytes()
    assert (share / "rembg-requirements.txt").is_file()
    assert (share / "icons" / "quickseemd-symbolic.svg").is_file()
    assert not (share / "templates").exists()


@pytest.mark.parametrize(("name", "module"), sorted(MODULES.items()))
def test_launchers(installed, name: str, module: str):
    h, _res = installed
    launcher = h.bin / name
    text = launcher.read_text("utf-8")
    assert stat.S_IMODE(launcher.stat().st_mode) == 0o755
    assert text.startswith("#!/bin/bash\n")
    assert f"export PYTHONPATH={h.lib}\n" in text
    assert f'exec /usr/bin/python3 -s -m {module} "$@"\n' in text


def test_launcher_runs_installed_package(installed):
    h, _res = installed
    res = subprocess.run(
        [str(h.bin / "quickseemd"), "tools", "--list"],
        capture_output=True,
        text=True,
        env=h.env,
        timeout=60,
        check=False,
    )
    assert res.returncode == 0, res.stderr


def test_desktop_entry(installed):
    h, _res = installed
    path = h.data / "applications" / "dev.quickseemd.App.desktop"
    kf = _keyfile(path)
    group = "Desktop Entry"
    assert kf.get_string(group, "Name") == "QUICK_SEEMD"
    assert kf.get_string(group, "Comment") == "萬用預覽・轉檔・工具箱"
    assert kf.get_string(group, "Icon") == "dev.quickseemd.App"
    assert kf.get_string(group, "Exec") == str(h.bin / "quickseemd")
    keys, _count = kf.get_keys(group)
    assert "MimeType" not in keys
    validator = shutil.which("desktop-file-validate", path="/usr/bin")
    if validator:
        res = subprocess.run([validator, str(path)], capture_output=True, text=True, check=False)
        assert res.returncode == 0, res.stdout + res.stderr


@pytest.mark.parametrize(
    ("file", "bus_name", "launcher"),
    [
        ("org.nemo.Preview.service", "org.nemo.Preview", "quickseemd-preview"),
        ("dev.quickseemd.Agent.service", "dev.quickseemd.Agent", "quickseemd-agent"),
    ],
)
def test_dbus_services(installed, file: str, bus_name: str, launcher: str):
    h, _res = installed
    path = h.data / "dbus-1" / "services" / file
    kf = _keyfile(path)
    assert kf.get_string("D-BUS Service", "Name") == bus_name
    exec_line = kf.get_string("D-BUS Service", "Exec")
    assert exec_line == str(h.bin / launcher)
    assert "--gapplication-service" not in path.read_text("utf-8")


def test_nemo_extension(installed):
    h, _res = installed
    ext = h.data / "nemo-python" / "extensions" / "quickseemd_nemo.py"
    assert ext.read_bytes() == (PKG / "nemo" / "quickseemd_nemo.py").read_bytes()


def test_icons(installed):
    from PIL import Image  # noqa: PLC0415

    h, _res = installed
    for size in ICON_SIZES:
        png = h.hicolor / f"{size}x{size}" / "apps" / "dev.quickseemd.App.png"
        with Image.open(png) as img:
            assert img.size == (size, size)
            assert img.format == "PNG"
    svg = h.hicolor / "symbolic" / "apps" / "quickseemd-symbolic.svg"
    assert svg.read_bytes() == (LINUX_DIR / "data/icons/quickseemd-symbolic.svg").read_bytes()
    assert (h.hicolor / "icon-theme.cache").is_file()


def test_autostart(installed):
    h, _res = installed
    kf = _keyfile(h.config / "autostart" / "dev.quickseemd.Agent.desktop")
    assert kf.get_string("Desktop Entry", "Exec") == str(h.bin / "quickseemd-agent")
    assert kf.get_boolean("Desktop Entry", "X-GNOME-Autostart-enabled")


def test_manifest_lists_every_installed_file(installed):
    h, _res = installed
    entries = h.manifest_entries()
    assert len(entries) == len(set(entries))
    listed = {Path(e) for e in entries}
    assert all(p.is_absolute() and p.is_file() for p in listed)
    created = all_files(h.home) - {h.manifest, h.hicolor / "icon-theme.cache"}
    assert created == listed


def test_nothing_written_outside_home(installed):
    h, _res = installed
    leftovers = [p for p in (h.root / "tmp").rglob("*")]
    assert leftovers == []
    outside = {p for p in h.root.rglob("*") if p.is_file()} - all_files(h.home)
    assert outside == set()


def test_symbolic_svg_only_uses_fill():
    """GTK 只會重新著色填色，單色圖示不能用線條。"""
    text = (LINUX_DIR / "data/icons/quickseemd-symbolic.svg").read_text("utf-8")
    assert "stroke" not in text
    assert text.count("<path") == 5


def test_install_script_is_strict_bash():
    text = INSTALL.read_text("utf-8")
    assert text.startswith("#!/bin/bash\n")
    assert "set -euo pipefail" in text
    for script in (INSTALL, LINUX_DIR / "uninstall.sh", LINUX_DIR / "install-lib.sh"):
        res = subprocess.run(["/bin/bash", "-n", str(script)], capture_output=True, check=False)
        assert res.returncode == 0, res.stderr
    assert os.access(INSTALL, os.X_OK)
