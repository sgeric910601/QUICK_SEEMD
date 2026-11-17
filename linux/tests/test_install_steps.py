"""安裝程式的選用步驟：--deps（apt 套件）與 --models（去背元件、臉部模型）。

apt-get、pkexec、dpkg-query 一律換成測試用的替身腳本（載入腳本函式後改變數），
絕不會真的跳出密碼視窗或安裝任何套件。
"""

from __future__ import annotations

import hashlib
import json
import shutil
from pathlib import Path

import pytest
from install_support import INSTALL, LINUX_DIR, Home, make_home, run, run_sourced


@pytest.fixture
def home(tmp_path: Path) -> Home:
    return make_home(tmp_path)


def _fake(path: Path, body: str) -> Path:
    path.write_text("#!/bin/bash\n" + body, "utf-8")
    path.chmod(0o755)
    return path


@pytest.fixture
def fakes(tmp_path: Path) -> dict[str, Path]:
    d = tmp_path / "fakes"
    d.mkdir()
    log = d / "calls.log"
    return {
        "log": log,
        # 只有 pkg-a 已安裝
        "dpkg": _fake(
            d / "dpkg-query",
            'for a in "$@"; do last="$a"; done\n'
            '[ "$last" = "pkg-a" ] && { printf "install ok installed"; exit 0; }\n'
            "exit 1\n",
        ),
        "apt": _fake(
            d / "apt-get",
            f'echo "apt-get $*" >> "{log}"\n'
            'for a in "$@"; do case "$a" in pkg-*) echo "Inst $a (1.0 Ubuntu:24.04/noble [amd64])";; esac; done\n'
            '[ -n "${FAKE_REMOVE:-}" ] && echo "Remv important-thing [1.0]"\n'
            "exit 0\n",
        ),
        "pkexec": _fake(d / "pkexec", f'echo "pkexec $*" >> "{log}"\nexit 0\n'),
    }


def _deps(h: Home, fakes: dict[str, Path], answer: str, extra: str = "") -> tuple:
    code = (
        f'QS_DPKG_QUERY="{fakes["dpkg"]}"; QS_APT_GET="{fakes["apt"]}"; '
        f'QS_PKEXEC="{fakes["pkexec"]}"\n'
        "APT_PACKAGES=(pkg-a pkg-b pkg-c)\n"
        f"{extra}\n"
        "qs_init_paths; step_deps\n"
    )
    res = run_sourced(h, INSTALL, code, stdin=answer)
    calls = fakes["log"].read_text("utf-8").splitlines() if fakes["log"].exists() else []
    return res, calls


def test_deps_lists_simulates_and_installs_after_yes(home: Home, fakes):
    res, calls = _deps(home, fakes, "y\n")
    assert res.returncode == 0, res.stdout + res.stderr
    assert "pkg-b" in res.stdout and "pkg-c" in res.stdout
    assert calls[0].startswith("apt-get -s install --no-install-recommends")
    assert "pkg-a" not in calls[0]  # 已安裝的不列
    assert calls[-1] == f"pkexec {fakes['apt']} install -y --no-install-recommends pkg-b pkg-c"
    assert not any("autoremove" in c for c in calls)


def test_deps_answer_no_installs_nothing(home: Home, fakes):
    res, calls = _deps(home, fakes, "n\n")
    assert res.returncode == 0
    assert not any(c.startswith("pkexec") for c in calls)
    assert "略過" in res.stdout


def test_deps_no_answer_means_no(home: Home, fakes):
    _res, calls = _deps(home, fakes, "")
    assert not any(c.startswith("pkexec") for c in calls)


def test_deps_stops_when_simulation_removes_packages(home: Home, fakes):
    res, calls = _deps(home, fakes, "y\n", extra="export FAKE_REMOVE=1")
    assert res.returncode != 0
    assert "移除" in res.stdout + res.stderr
    assert not any(c.startswith("pkexec") for c in calls)


def test_deps_all_installed(home: Home, fakes):
    code = (
        f'QS_DPKG_QUERY="{fakes["dpkg"]}"; QS_APT_GET="{fakes["apt"]}"; '
        f'QS_PKEXEC="{fakes["pkexec"]}"\n'
        "APT_PACKAGES=(pkg-a)\nqs_init_paths; step_deps\n"
    )
    res = run_sourced(home, INSTALL, code)
    assert res.returncode == 0
    assert "都已安裝" in res.stdout
    assert not fakes["log"].exists()


def test_tool_paths_are_fixed_when_run_as_program():
    """直接執行時用固定的絕對路徑，不從環境變數讀（避免被換成別的程式）。"""
    text = (LINUX_DIR / "install-lib.sh").read_text("utf-8") + INSTALL.read_text("utf-8")
    assert "QS_APT_GET=/usr/bin/apt-get" in text
    assert "QS_PKEXEC=/usr/bin/pkexec" in text
    assert "${QS_APT_GET:-" not in text and "${QS_PKEXEC:-" not in text


def test_default_package_list_is_runtime_only(home: Home):
    res = run_sourced(home, INSTALL, 'printf "%s\\n" "${APT_PACKAGES[@]}"')
    names = res.stdout.split()
    assert "nemo-python" in names and "gir1.2-webkit2-4.1" in names
    assert "python3-pytest" not in names and "black" not in names


# ------------------------------------------------------------------ --models


def _yunet_info() -> dict:
    return json.loads((LINUX_DIR / "data" / "models.json").read_text("utf-8"))["yunet"]


def test_models_skips_everything_that_exists(home: Home):
    import conftest  # noqa: PLC0415

    real = Path(conftest.REAL_DATA) / "quickseemd" / "models" / _yunet_info()["file"]
    if not real.is_file():
        pytest.skip("這台電腦沒有 YuNet 模型可以借用")
    models = home.qs / "models"
    models.mkdir(parents=True)
    shutil.copyfile(real, models / real.name)  # 只讀真的模型，複製到測試資料夾
    rembg = models / "models" / "isnet-general-use" / "isnet-general-use.onnx"
    rembg.parent.mkdir(parents=True)
    rembg.write_bytes(b"fake model")
    _fake_venv(home)
    res = run_sourced(home, INSTALL, "qs_init_paths; step_models")
    assert res.returncode == 0, res.stdout + res.stderr
    assert res.stdout.count("略過") >= 3
    assert hashlib.sha256((models / real.name).read_bytes()).hexdigest() == _yunet_info()["sha256"]


def _fake_venv(h: Home) -> None:
    """假的去背虛擬環境（只要 python 存在，安裝程式就會略過建立）。"""
    bindir = h.qs / "venv-rembg" / "bin"
    bindir.mkdir(parents=True, exist_ok=True)
    _fake(bindir / "python", "exit 0\n")


def test_models_does_not_overwrite_mismatching_file(home: Home):
    models = home.qs / "models"
    models.mkdir(parents=True)
    wrong = models / _yunet_info()["file"]
    wrong.write_bytes(b"not the model")
    rembg = models / "models" / "isnet-general-use" / "isnet-general-use.onnx"
    rembg.parent.mkdir(parents=True)
    rembg.write_bytes(b"fake model")
    _fake_venv(home)
    res = run_sourced(home, INSTALL, "qs_init_paths; step_models")
    assert wrong.read_bytes() == b"not the model"
    assert "雜湊不符" in res.stdout + res.stderr


@pytest.mark.parametrize("script", [INSTALL, LINUX_DIR / "uninstall.sh"])
def test_sourcing_never_runs_main(home: Home, script: Path):
    res = run_sourced(home, script, "echo sourced")
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == "sourced"
    assert not home.data.exists()


def test_sha256_helper(home: Home, tmp_path: Path):
    f = tmp_path / "x.bin"
    f.write_bytes(b"abc")
    res = run_sourced(home, INSTALL, f'qs_sha256 "{f}"')
    assert res.stdout.strip() == hashlib.sha256(b"abc").hexdigest()
