"""解除安裝：只刪安裝紀錄裡的檔案；模型與去背元件要 --purge 才刪；結束程式前先問。"""

from __future__ import annotations

from pathlib import Path

import pytest
from install_support import INSTALL, UNINSTALL, Home, all_files, make_home, run, run_sourced

FOREIGN = "使用者的檔案\n"


@pytest.fixture
def installed(tmp_path: Path) -> Home:
    h = make_home(tmp_path)
    res = run(h, INSTALL)
    assert res.returncode == 0, res.stdout + res.stderr
    return h


def _foreign_files(h: Home) -> list[Path]:
    files = [
        h.lib / "quickseemd" / "使用者筆記.txt",
        h.bin / "other-tool",
        h.data / "applications" / "other.desktop",
        h.data / "nemo-python" / "extensions" / "other_ext.py",
        h.home / "文件" / "報告.txt",
    ]
    for f in files:
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(FOREIGN, "utf-8")
    return files


def test_uninstall_removes_only_manifest_files(installed: Home):
    h = installed
    entries = [Path(e) for e in h.manifest_entries()]
    foreign = _foreign_files(h)
    res = run(h, UNINSTALL, stdin="n\n")
    assert res.returncode == 0, res.stdout + res.stderr
    assert not any(p.exists() for p in entries)
    assert not h.manifest.exists()
    for f in foreign:
        assert f.read_text("utf-8") == FOREIGN
    assert "nemo -q" in res.stdout


def test_uninstall_leaves_no_empty_folders_of_ours(installed: Home):
    h = installed
    run(h, UNINSTALL, stdin="n\n")
    assert not h.lib.exists()
    assert not (h.qs / "data").exists()
    assert not (h.hicolor / "16x16").exists()


def test_models_and_venv_kept_without_purge(installed: Home):
    h = installed
    model = h.qs / "models" / "face.onnx"
    venv_py = h.qs / "venv-rembg" / "bin" / "python"
    for f in (model, venv_py):
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text("x", "utf-8")
    run(h, UNINSTALL, stdin="n\n")
    assert model.exists() and venv_py.exists()


def test_purge_removes_models_venv_settings_and_cache(installed: Home, tmp_path: Path):
    h = installed
    real_models = tmp_path / "真的模型"
    real_models.mkdir()
    (real_models / "keep.onnx").write_text("x", "utf-8")
    (h.qs / "models").symlink_to(real_models)  # 符號連結：只能刪連結本身
    venv_py = h.qs / "venv-rembg" / "bin" / "python"
    venv_py.parent.mkdir(parents=True)
    venv_py.write_text("x", "utf-8")
    settings = h.config / "quickseemd" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text("{}", "utf-8")
    cached = h.cache / "quickseemd" / "preview" / "a.pdf"
    cached.parent.mkdir(parents=True)
    cached.write_text("x", "utf-8")
    res = run(h, UNINSTALL, "--purge", stdin="n\n")
    assert res.returncode == 0, res.stdout + res.stderr
    assert not (h.qs / "models").exists() and not (h.qs / "models").is_symlink()
    assert (real_models / "keep.onnx").exists()
    assert not (h.qs / "venv-rembg").exists()
    assert not settings.exists() and not cached.exists()
    assert not h.qs.exists()


def test_manifest_entries_outside_roots_are_ignored(installed: Home, tmp_path: Path):
    h = installed
    precious = h.home / "重要.txt"
    precious.write_text(FOREIGN, "utf-8")
    outside = tmp_path / "外面.txt"
    outside.write_text(FOREIGN, "utf-8")
    sneaky = f"{h.lib}/../../../../重要.txt"
    entries = h.manifest_entries() + [str(precious), str(outside), sneaky, "相對路徑.txt"]
    h.manifest.write_text("\n".join(entries) + "\n", "utf-8")
    res = run(h, UNINSTALL, stdin="n\n")
    assert res.returncode == 0
    assert precious.read_text("utf-8") == FOREIGN
    assert outside.read_text("utf-8") == FOREIGN
    assert "略過" in res.stdout + res.stderr


def test_symlinked_folder_inside_lib_is_not_followed(installed: Home, tmp_path: Path):
    h = installed
    elsewhere = tmp_path / "別的地方"
    elsewhere.mkdir()
    victim = elsewhere / "ipc.py"
    victim.write_text(FOREIGN, "utf-8")
    core = h.lib / "quickseemd" / "core"
    moved = h.root / "core-moved"
    core.rename(moved)
    core.symlink_to(elsewhere)
    res = run(h, UNINSTALL, stdin="n\n")
    assert res.returncode == 0
    assert victim.read_text("utf-8") == FOREIGN


def test_pyc_of_our_modules_are_removed(installed: Home):
    h = installed
    cache = h.lib / "quickseemd" / "core" / "__pycache__"
    cache.mkdir()
    ours = cache / "ipc.cpython-312.pyc"
    ours.write_bytes(b"pyc")
    ext_cache = h.data / "nemo-python" / "extensions" / "__pycache__"
    ext_cache.mkdir()
    ext_pyc = ext_cache / "quickseemd_nemo.cpython-312.pyc"
    ext_pyc.write_bytes(b"pyc")
    other_pyc = ext_cache / "other_ext.cpython-312.pyc"
    other_pyc.write_bytes(b"pyc")
    run(h, UNINSTALL, stdin="n\n")
    assert not ours.exists() and not ext_pyc.exists()
    assert other_pyc.exists()


def test_uninstall_without_manifest(tmp_path: Path):
    h = make_home(tmp_path)
    res = run(h, UNINSTALL, stdin="n\n")
    assert res.returncode == 0
    assert "找不到安裝紀錄" in res.stdout


def test_uninstall_then_reinstall(installed: Home):
    h = installed
    assert run(h, UNINSTALL, stdin="n\n").returncode == 0
    res = run(h, INSTALL)
    assert res.returncode == 0, res.stdout + res.stderr
    assert (h.bin / "quickseemd").is_file()


def test_nothing_left_except_foreign(installed: Home):
    h = installed
    foreign = set(_foreign_files(h))
    run(h, UNINSTALL, stdin="n\n")
    left = all_files(h.home) - foreign
    # 只剩圖示快取（共用的快取檔，解除安裝後會重建）
    assert {p.name for p in left} <= {"icon-theme.cache"}


# ------------------------------------------------------------------ 結束執行中的程式


def _fake_proc(root: Path, pid: int, argv: list[str], environ: dict[str, str]) -> None:
    d = root / str(pid)
    d.mkdir(parents=True)
    (d / "cmdline").write_bytes(b"\0".join(a.encode() for a in argv) + b"\0")
    (d / "environ").write_bytes(b"\0".join(f"{k}={v}".encode() for k, v in environ.items()) + b"\0")


@pytest.fixture
def fake_proc(installed: Home, tmp_path: Path) -> Path:
    h = installed
    root = tmp_path / "proc"
    ours = {"PYTHONPATH": str(h.lib), "HOME": str(h.home)}
    py = "/usr/bin/python3"
    _fake_proc(root, 101, [py, "-s", "-m", "quickseemd.agent"], ours)
    _fake_proc(root, 102, [py, "-s", "-m", "quickseemd.agent"], {"PYTHONPATH": "/elsewhere"})
    _fake_proc(root, 103, ["/usr/bin/nemo"], ours)
    _fake_proc(root, 104, [py, "-s", "-m", "quickseemd", "open", "/tmp/a.pdf"], ours)
    _fake_proc(root, 105, [py, "-m", "quickseemd.worker"], ours)
    _fake_proc(root, 106, [py, "-s", "-m", "quickseemd.agentx"], ours)
    (root / "self").mkdir()
    return root


def test_find_processes(installed: Home, fake_proc: Path):
    res = run_sourced(installed, UNINSTALL, f'QS_PROC="{fake_proc}"; qs_init_paths; qs_find_pids')
    assert res.returncode == 0, res.stderr
    assert res.stdout.split() == ["101", "104", "105"]


def test_offer_stop_yes(installed: Home, fake_proc: Path):
    code = (
        f'QS_PROC="{fake_proc}"; qs_init_paths\n'
        'qs_stop_pids() { echo "STOP $*"; }\n'
        "qs_offer_stop\n"
    )
    res = run_sourced(installed, UNINSTALL, code, stdin="y\n")
    assert res.returncode == 0, res.stderr
    assert "STOP 101 104 105" in res.stdout
    assert "quickseemd.agent" in res.stdout  # 先列出來給使用者看


def test_offer_stop_no(installed: Home, fake_proc: Path):
    code = (
        f'QS_PROC="{fake_proc}"; qs_init_paths\n'
        'qs_stop_pids() { echo "STOP $*"; }\n'
        "qs_offer_stop\n"
    )
    res = run_sourced(installed, UNINSTALL, code, stdin="n\n")
    assert res.returncode == 0
    assert "STOP" not in res.stdout


def test_offer_stop_without_answer_means_no(installed: Home, fake_proc: Path):
    code = (
        f'QS_PROC="{fake_proc}"; qs_init_paths\n'
        'qs_stop_pids() { echo "STOP $*"; }\n'
        "qs_offer_stop\n"
    )
    res = run_sourced(installed, UNINSTALL, code, stdin="")
    assert "STOP" not in res.stdout
