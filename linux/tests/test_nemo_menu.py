"""Nemo 右鍵選單擴充（用假的 Nemo 模組，不載入 GTK3，也不連真的 D-Bus）。"""

from __future__ import annotations

import importlib.util
import sys
import types
from pathlib import Path

import pytest
from gi.repository import Gio, GLib, GObject

import conftest
from quickseemd.core import ipc, quickactions

EXT_PATH = conftest.LINUX_DIR / "quickseemd" / "nemo" / "quickseemd_nemo.py"


# ------------------------------------------------------------------ 假的 Nemo 型別


class FakeMenuItem:
    def __init__(self, name="", label="", tip="", icon=None, sensitive=True, separator=False):
        self.name = name
        self.label = label
        self.tip = tip
        self.icon = icon
        self.sensitive = sensitive
        self.separator = separator
        self.submenu = None
        self.handlers: list = []

    @classmethod
    def new_separator(cls, name):
        return cls(name=name, separator=True)

    def set_submenu(self, menu):
        self.submenu = menu

    def connect(self, signal, callback, *args):
        assert signal == "activate"
        self.handlers.append((callback, args))

    def activate(self):
        for callback, args in self.handlers:
            callback(self, *args)


class FakeMenu:
    def __init__(self):
        self.items: list[FakeMenuItem] = []

    def append_item(self, item):
        self.items.append(item)


class FakeMenuProvider:  # 一般的 Python mixin（真的 Nemo.MenuProvider 是 GInterface）
    pass


class FakeFileInfo:
    def __init__(self, path: str | None, mime: str, is_dir: bool = False, scheme: str = "file"):
        self._path = path
        self._mime = mime
        self._dir = is_dir
        self._scheme = scheme

    def get_uri_scheme(self):
        return self._scheme

    def get_location(self):
        if self._path is None:
            return Gio.File.new_for_uri("sftp://host/x")
        return Gio.File.new_for_path(self._path)

    def get_mime_type(self):
        return self._mime

    def is_directory(self):
        return self._dir


@pytest.fixture
def ext(monkeypatch: pytest.MonkeyPatch):
    fake = types.ModuleType("gi.repository.Nemo")
    fake.MenuItem = FakeMenuItem
    fake.Menu = FakeMenu
    fake.MenuProvider = FakeMenuProvider
    monkeypatch.setitem(sys.modules, "gi.repository.Nemo", fake)
    spec = importlib.util.spec_from_file_location("quickseemd_nemo_under_test", EXT_PATH)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _items(ext, files):
    provider = ext.QuickSeemdMenuProvider()
    return provider.get_file_items(None, files)


def _sub(items) -> list[FakeMenuItem]:
    (root,) = items
    return root.submenu.items


# ------------------------------------------------------------------ 選單內容


def test_module_has_no_gtk_dependency():
    src = EXT_PATH.read_text("utf-8")
    assert "Gtk" not in src.replace("GTK", "")
    assert 'require_version("Gtk"' not in src


def test_root_item_for_pdf(ext):
    items = _items(ext, [FakeFileInfo("/tmp/a.pdf", "application/pdf")])
    (root,) = items
    assert root.label == "QUICK__SEEMD 轉換"
    assert root.icon == "quickseemd-symbolic"
    assert root.submenu is not None


def test_pdf_submenu_structure(ext):
    sub = _sub(_items(ext, [FakeFileInfo("/tmp/a.pdf", "application/pdf")]))
    labels = [(i.label, i.sensitive, i.separator) for i in sub]
    # 第一個分組標題不能點、前面沒有分隔線
    assert labels[0] == ("PDF", False, False)
    assert ("壓縮 PDF（建議）", True, False) in labels
    assert ("合併成一個 PDF", True, False) not in labels  # 只有一個檔案
    # 「其他」分組前有分隔線
    other_index = [i.label for i in sub].index("其他")
    assert sub[other_index - 1].separator
    # 最後是分隔線＋「更多工具與選項…」
    assert sub[-2].separator
    assert sub[-1].label == "更多工具與選項…"
    assert sub[-1].icon == "view-app-grid-symbolic"


def test_items_have_icons_and_unique_names(ext):
    sub = _sub(_items(ext, [FakeFileInfo("/tmp/a.png", "image/png")]))
    names = [i.name for i in sub]
    assert len(names) == len(set(names))
    actions = [i for i in sub if i.sensitive and not i.separator]
    assert all(i.icon for i in actions)
    rotate = next(i for i in actions if i.label == "向右旋轉 90°")
    assert rotate.icon == "object-rotate-right-symbolic"


def test_underscores_are_escaped_everywhere(ext, monkeypatch):
    fake_action = quickactions.QuickAction(
        "pdf.snake_case", "轉成 my_file", "doc.text", "P_D_F", ("item",), "pdf.compress"
    )
    monkeypatch.setattr(ext.quickactions, "menu_actions", lambda refs: [fake_action])
    sub = _sub(_items(ext, [FakeFileInfo("/tmp/a.txt", "text/plain")]))
    assert sub[0].label == "P__D__F"
    assert sub[1].label == "轉成 my__file"


def test_merge_needs_two_files(ext):
    files = [FakeFileInfo(f"/tmp/{n}.pdf", "application/pdf") for n in ("a", "b")]
    labels = [i.label for i in _sub(_items(ext, files))]
    assert "合併成一個 PDF" in labels


def test_folder_only_gets_zip(ext):
    sub = _sub(_items(ext, [FakeFileInfo("/tmp/資料夾", "inode/directory", is_dir=True)]))
    actions = [i.label for i in sub if i.sensitive and not i.separator]
    assert actions == ["壓縮成 ZIP", "更多工具與選項…"]


def test_mixed_selection_uses_common_actions(ext):
    files = [FakeFileInfo("/tmp/a.pdf", "application/pdf"), FakeFileInfo("/tmp/b.png", "image/png")]
    actions = [i.label for i in _sub(_items(ext, files)) if i.sensitive and not i.separator]
    assert actions == ["壓縮成 ZIP", "更多工具與選項…"]


def test_non_local_files_get_no_menu(ext):
    assert _items(ext, [FakeFileInfo(None, "application/pdf", scheme="sftp")]) == []
    files = [FakeFileInfo("/tmp/a.pdf", "application/pdf"), FakeFileInfo(None, "x", scheme="smb")]
    assert _items(ext, files) == []


def test_empty_selection_gets_no_menu(ext):
    assert _items(ext, []) == []


def test_no_actions_gets_no_menu(ext, monkeypatch):
    monkeypatch.setattr(ext.quickactions, "menu_actions", lambda refs: [])
    assert _items(ext, [FakeFileInfo("/tmp/a.pdf", "application/pdf")]) == []


def test_too_many_files_gets_no_menu(ext, monkeypatch):
    monkeypatch.setattr(ext, "MAX_FILES", 2)
    files = [FakeFileInfo(f"/tmp/{i}.pdf", "application/pdf") for i in range(3)]
    assert _items(ext, files) == []


def test_errors_never_reach_nemo(ext, monkeypatch):
    def boom(refs):
        raise RuntimeError("壞了")

    monkeypatch.setattr(ext.quickactions, "menu_actions", boom)
    assert _items(ext, [FakeFileInfo("/tmp/a.pdf", "application/pdf")]) == []


def test_background_items_are_empty(ext):
    assert ext.QuickSeemdMenuProvider().get_background_items(None, None) == []


def test_missing_mime_counts_as_unknown(ext):
    items = _items(ext, [FakeFileInfo("/tmp/a.bin", None)])  # type: ignore[arg-type]
    labels = [i.label for i in _sub(items)]
    assert "壓縮成 ZIP" in labels


# ------------------------------------------------------------------ 點選後的動作


class FakeConnection:
    def __init__(self, fail_times: int = 0):
        self.fail_times = fail_times
        self.calls: list[tuple] = []

    def call(self, name, path, iface, method, params, reply, flags, timeout, cancel, callback):
        self.calls.append((name, path, iface, method, params.unpack(), flags))
        callback(self, ("result", len(self.calls)))

    def call_finish(self, result):
        if len(self.calls) <= self.fail_times:
            raise GLib.Error.new_literal(
                Gio.dbus_error_quark(), "沒有回應", Gio.DBusError.SERVICE_UNKNOWN
            )
        return GLib.Variant("()", ())


class FakeEnv:
    """擴充對外的三個依賴：取得 bus、啟動程式、等 bus 名稱出現。"""

    def __init__(self, conn: FakeConnection | None):
        self.conn = conn
        self.spawned: list[list[str]] = []
        self.watched: list[str] = []

    def get_bus(self, callback):
        if self.conn is None:
            callback(None, GLib.Error.new_literal(Gio.io_error_quark(), "沒有 bus", 0))
        else:
            callback(self.conn, None)

    def spawn(self, argv):
        self.spawned.append(list(argv))
        return True

    def wait_for_name(self, name, appeared, timeout_ms):
        self.watched.append(name)
        appeared()


def _activate(ext, monkeypatch, env: FakeEnv, label: str, files):
    monkeypatch.setattr(ext, "_get_bus", env.get_bus)
    monkeypatch.setattr(ext, "_spawn", env.spawn)
    monkeypatch.setattr(ext, "_wait_for_name", env.wait_for_name)
    sub = _sub(_items(ext, files))
    next(i for i in sub if i.label == label).activate()


def test_action_calls_agent_run_action(ext, monkeypatch):
    env = FakeEnv(FakeConnection())
    _activate(
        ext, monkeypatch, env, "壓縮 PDF（建議）", [FakeFileInfo("/tmp/a b.pdf", "application/pdf")]
    )
    (call,) = env.conn.calls
    name, path, iface, method, params, flags = call
    assert (name, path, iface, method) == (
        ipc.AGENT_BUS_NAME,
        ipc.AGENT_OBJECT_PATH,
        ipc.AGENT_INTERFACE,
        "RunAction",
    )
    assert params == ("pdf.compress-recommended", ["/tmp/a b.pdf"])
    assert not flags & Gio.DBusCallFlags.NO_AUTO_START  # 允許自動啟動
    assert env.spawned == []


def test_failed_call_starts_agent_and_retries_once(ext, monkeypatch, tmp_path):
    env = FakeEnv(FakeConnection(fail_times=1))
    _activate(ext, monkeypatch, env, "加上頁碼", [FakeFileInfo("/tmp/a.pdf", "application/pdf")])
    assert env.spawned == [[str(Path.home() / ".local" / "bin" / "quickseemd-agent")]]
    assert env.watched == [ipc.AGENT_BUS_NAME]
    assert len(env.conn.calls) == 2


def test_second_failure_gives_up(ext, monkeypatch):
    env = FakeEnv(FakeConnection(fail_times=5))
    _activate(ext, monkeypatch, env, "加上頁碼", [FakeFileInfo("/tmp/a.pdf", "application/pdf")])
    assert len(env.conn.calls) == 2  # 只重試一次
    assert len(env.spawned) == 1


def test_no_bus_falls_back_to_spawn(ext, monkeypatch):
    env = FakeEnv(None)
    _activate(ext, monkeypatch, env, "加上頁碼", [FakeFileInfo("/tmp/a.pdf", "application/pdf")])
    assert len(env.spawned) == 1


def test_more_tools_opens_app(ext, monkeypatch):
    env = FakeEnv(FakeConnection())
    files = [
        FakeFileInfo("/tmp/a.pdf", "application/pdf"),
        FakeFileInfo("/tmp/b.pdf", "application/pdf"),
    ]
    _activate(ext, monkeypatch, env, "更多工具與選項…", files)
    app = str(Path.home() / ".local" / "bin" / "quickseemd")
    assert env.spawned == [[app, "open", "/tmp/a.pdf", "/tmp/b.pdf"]]
    assert env.conn.calls == []


def test_spawn_failure_is_logged_not_raised(ext, monkeypatch, caplog):
    def bad_spawn(argv):
        raise GLib.Error.new_literal(GLib.spawn_error_quark(), "找不到程式", 0)

    monkeypatch.setattr(ext, "_spawn_raw", bad_spawn)
    assert ext._spawn(["/nonexistent/quickseemd"]) is False


def test_lib_dir_is_under_user_data(ext):
    assert ext.lib_dir() == Path(GLib.get_user_data_dir()) / "quickseemd" / "lib"


def test_provider_is_gobject(ext):
    assert issubclass(ext.QuickSeemdMenuProvider, GObject.GObject)
