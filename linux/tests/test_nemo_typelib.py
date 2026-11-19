"""用真的 Nemo 型別庫載入擴充（在獨立程序裡跑：Nemo 型別庫會帶入 GTK3，不能和 GTK4 的測試共用程序）。"""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

import conftest

EXT_PATH = conftest.LINUX_DIR / "quickseemd" / "nemo" / "quickseemd_nemo.py"

PROBE = r"""
import importlib.util, json, sys
import gi
from gi.repository import Gio
spec = importlib.util.spec_from_file_location("quickseemd_nemo", sys.argv[1])
ext = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ext)

class Info:
    def __init__(self, path, mime, is_dir=False):
        self.path, self.mime, self.is_dir = path, mime, is_dir
    def get_uri_scheme(self): return "file"
    def get_location(self): return Gio.File.new_for_path(self.path)
    def get_mime_type(self): return self.mime
    def is_directory(self): return self.is_dir

provider = ext.QuickSeemdMenuProvider()
items = provider.get_file_items(None, [Info("/tmp/a.pdf", "application/pdf")])
root = items[0]
sub = root.props.menu.get_items()
out = {
    "root": [root.props.label, root.props.icon, root.props.sensitive],
    "sub": [[i.props.name, i.props.label, i.props.icon, i.props.sensitive, i.props.separator]
            for i in sub],
    "gtk": gi.get_required_version("Gtk"),
    "is_provider": isinstance(provider, ext.Nemo.MenuProvider),
}
print(json.dumps(out, ensure_ascii=False))
"""


def _run_probe(env_extra: dict[str, str] | None = None) -> dict:
    env = dict(os.environ)
    env.pop("DISPLAY", None)
    env["PYTHONPATH"] = str(conftest.LINUX_DIR)
    env.update(env_extra or {})
    res = subprocess.run(
        [sys.executable, "-c", PROBE, str(EXT_PATH)],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    )
    assert res.returncode == 0, res.stderr
    return json.loads(res.stdout.strip().splitlines()[-1])


def test_real_nemo_menu_items():
    out = _run_probe()
    assert out["is_provider"]
    assert out["root"] == ["QUICK__SEEMD 轉換", "quickseemd-symbolic", True]
    sub = out["sub"]
    heading = sub[0]
    assert heading[1] == "PDF" and heading[3] is False  # 分組標題不能點
    compress = next(i for i in sub if i[1] == "壓縮 PDF（建議）")
    assert compress[3] is True and compress[2] == "view-restore-symbolic"
    assert sub[-2][4] is True  # 分隔線
    assert sub[-1][1] == "更多工具與選項…"
    # 擴充本身不要求任何 GTK 版本（Nemo 自己會帶 GTK3）
    assert out["gtk"] in (None, "3.0")


def test_extension_uses_installed_lib_when_present(tmp_path: Path):
    """裝好的程式（~/.local/share/quickseemd/lib）優先於其他位置。"""
    data = tmp_path / "data"
    lib = data / "quickseemd" / "lib"
    pkg = lib / "quickseemd"
    pkg.mkdir(parents=True)
    # 放一份會讓匯入失敗的假套件：證明擴充真的去這裡找
    (pkg / "__init__.py").write_text("raise ImportError('來自安裝位置')\n", "utf-8")
    env = dict(os.environ)
    env.pop("DISPLAY", None)
    env["XDG_DATA_HOME"] = str(data)
    env.pop("PYTHONPATH", None)
    code = (
        "import importlib.util, sys\n"
        "spec = importlib.util.spec_from_file_location('e', sys.argv[1])\n"
        "m = importlib.util.module_from_spec(spec); spec.loader.exec_module(m)\n"
        "print(m.quickactions is None, sys.path[0])\n"
    )
    res = subprocess.run(
        [sys.executable, "-c", code, str(EXT_PATH)],
        capture_output=True,
        text=True,
        env=env,
        timeout=60,
        check=False,
    )
    assert res.returncode == 0, res.stderr
    assert res.stdout.split() == ["True", str(lib)]
    assert "來自安裝位置" in res.stderr  # 有記錄警告，Nemo 不會壞


@pytest.mark.parametrize("module", ["filetypes", "quickactions", "icons", "ipc"])
def test_core_modules_used_by_extension_do_not_import_gtk(module: str):
    code = (
        f"import sys; import quickseemd.core.{module}\n"
        "bad = [m for m in sys.modules if m in ('gi.repository.Gtk', 'gi.repository.Gdk')]\n"
        "print(bad)\n"
    )
    env = dict(os.environ, PYTHONPATH=str(conftest.LINUX_DIR))
    res = subprocess.run(
        [sys.executable, "-c", code], capture_output=True, text=True, env=env, check=False
    )
    assert res.returncode == 0, res.stderr
    assert res.stdout.strip() == "[]"
