"""QUICK_SEEMD 的 Nemo 右鍵選單（nemo-python 6，在 Nemo 的程序裡執行）。

安裝位置：~/.local/share/nemo-python/extensions/quickseemd_nemo.py；
QUICK_SEEMD 的程式在 ~/.local/share/quickseemd/lib（安裝程式放的），這裡加進 sys.path。

規則（照原版 Finder 右鍵選單）：
- 只處理本機檔案（file://）；選取的每個檔案都要符合動作的類型；「合併」至少 2 個檔案。
- 一個根項目「QUICK_SEEMD 轉換」，子選單依分組列出：分組標題不能點，分組之間有分隔線，
  最後是分隔線＋「更多工具與選項…」。
- Nemo 把底線當成快速鍵記號，所有文字裡的底線都要寫成兩個。
- 點了動作：非同步呼叫背景程式 dev.quickseemd.Agent.RunAction（需要時 D-Bus 自動啟動）；
  只有「背景程式不在」的錯誤（服務不存在、名稱沒有擁有者、沒有回應）才自己啟動
  ~/.local/bin/quickseemd-agent，等它上線後再試一次；其他錯誤只記錄。
- 「更多工具與選項…」：啟動 ~/.local/bin/quickseemd open 檔案…（不等它結束）。

這個檔案只載入 GObject、Gio、GLib、Nemo 與不含介面的 quickseemd.core 模組
（Nemo 是 GTK3 程式，絕不能載入 GTK4）。get_file_items 會被頻繁呼叫，只讀檔案的類型資訊。
"""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import gi

gi.require_version("Nemo", "3.0")
from gi.repository import Gio, GLib, GObject, Nemo  # noqa: E402

log = logging.getLogger("quickseemd.nemo")

NAME_PREFIX = "QuickSeemd::"
ROOT_ICON = "quickseemd-symbolic"
MORE_SYMBOL = "square.grid.2x2"
CALL_TIMEOUT_MS = 10_000
AGENT_START_TIMEOUT_MS = 15_000
AGENT_BIN = "quickseemd-agent"
APP_BIN = "quickseemd"


def lib_dir() -> Path:
    return Path(GLib.get_user_data_dir()) / "quickseemd" / "lib"


def _bin(name: str) -> str:
    return os.path.join(os.path.expanduser("~"), ".local", "bin", name)


def _import_core() -> tuple[Any, Any, Any, Any]:
    lib = str(lib_dir())
    if os.path.isdir(lib) and lib not in sys.path:
        sys.path.insert(0, lib)
    from quickseemd.core import filetypes, icons, ipc, quickactions  # noqa: PLC0415

    return filetypes, icons, ipc, quickactions


try:
    filetypes, icons, ipc, quickactions = _import_core()
    MAX_FILES = ipc.MAX_PATHS
except ImportError as _exc:  # 程式還沒安裝好：不提供選單，但不能讓 Nemo 出錯
    log.warning("找不到 QUICK_SEEMD 程式（%s），右鍵選單停用", _exc)
    filetypes = icons = ipc = quickactions = None
    MAX_FILES = 0


def _label(text: str) -> str:
    return text.replace("_", "__")


# ------------------------------------------------------------------ 對外依賴（測試可替換）


def _get_bus(callback: Callable[[Any, GLib.Error | None], None]) -> None:
    def done(_source: Any, result: Gio.AsyncResult) -> None:
        try:
            callback(Gio.bus_get_finish(result), None)
        except GLib.Error as exc:
            callback(None, exc)

    Gio.bus_get(Gio.BusType.SESSION, None, done)


def _spawn_raw(argv: Sequence[str]) -> None:
    GLib.spawn_async(
        list(argv), working_directory=os.path.expanduser("~"), flags=GLib.SpawnFlags.DEFAULT
    )


def _spawn(argv: Sequence[str]) -> bool:
    """啟動程式、不等它結束（GLib 會讓它脫離 Nemo）。"""
    try:
        _spawn_raw(argv)
    except GLib.Error as exc:
        log.warning("無法啟動 %s：%s", argv[0], exc.message)
        return False
    return True


class _NameWaiter:
    """等某個 D-Bus 名稱出現（背景程式啟動完成），逾時就放棄。"""

    def __init__(self, name: str, appeared: Callable[[], None], timeout_ms: int) -> None:
        self._appeared = appeared
        self._done = False
        self._watch = Gio.bus_watch_name(
            Gio.BusType.SESSION, name, Gio.BusNameWatcherFlags.NONE, self._on_appeared, None
        )
        self._timer = GLib.timeout_add(timeout_ms, self._on_timeout)

    def _finish(self) -> bool:
        if self._watch:
            Gio.bus_unwatch_name(self._watch)
            self._watch = 0
        return GLib.SOURCE_REMOVE

    def _on_appeared(self, _conn: Gio.DBusConnection, _name: str, _owner: str) -> None:
        if self._done:
            return
        self._done = True
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0
        GLib.idle_add(self._finish)
        self._appeared()

    def _on_timeout(self) -> bool:
        self._timer = 0
        if not self._done:
            self._done = True
            log.warning("等不到 QUICK_SEEMD 背景程式啟動")
            self._finish()
        return GLib.SOURCE_REMOVE


def _wait_for_name(name: str, appeared: Callable[[], None], timeout_ms: int) -> None:
    _NameWaiter(name, appeared, timeout_ms)


# ------------------------------------------------------------------ 執行動作


# 只有這些錯誤代表「背景程式不在」，才值得自己啟動它再試一次；
# 其他錯誤（拒絕存取、參數不對、背景程式回報失敗…）再開一份也沒用
_AGENT_MISSING = (
    Gio.DBusError.SERVICE_UNKNOWN,
    Gio.DBusError.NAME_HAS_NO_OWNER,
    Gio.DBusError.NO_REPLY,
)


def agent_missing(exc: GLib.Error) -> bool:
    return any(exc.matches(Gio.dbus_error_quark(), code) for code in _AGENT_MISSING)


class _AgentCall:
    """呼叫背景程式的 RunAction；背景程式不在時自己啟動它，並重試一次。"""

    def __init__(self, action_id: str, paths: list[str]) -> None:
        self._params = GLib.Variant("(sas)", (action_id, paths))
        self._action_id = action_id

    def start(self) -> None:
        _get_bus(lambda conn, err: self._call(conn, err, retry=False))

    def _call(
        self, conn: Gio.DBusConnection | None, err: GLib.Error | None, *, retry: bool
    ) -> None:
        if conn is None:
            reason = err.message if err is not None else "沒有 D-Bus 連線"
            log.warning("無法執行 %s：%s", self._action_id, reason)
            return
        conn.call(
            ipc.AGENT_BUS_NAME,
            ipc.AGENT_OBJECT_PATH,
            ipc.AGENT_INTERFACE,
            "RunAction",
            self._params,
            None,
            Gio.DBusCallFlags.NONE,
            CALL_TIMEOUT_MS,
            None,
            lambda source, result: self._on_reply(source, result, retry),
        )

    def _on_reply(self, conn: Gio.DBusConnection, result: Gio.AsyncResult, retry: bool) -> None:
        try:
            conn.call_finish(result)
        except GLib.Error as exc:
            self._failed(exc, retry)

    def _failed(self, exc: GLib.Error, retry: bool) -> None:
        if retry or not agent_missing(exc):
            log.warning("無法執行 %s：%s", self._action_id, exc.message)
            return
        if _spawn([_bin(AGENT_BIN)]):
            _wait_for_name(ipc.AGENT_BUS_NAME, self._retry, AGENT_START_TIMEOUT_MS)

    def _retry(self) -> None:
        _get_bus(lambda conn, err: self._call(conn, err, retry=True))


def _on_action(_item: Any, action_id: str, paths: list[str]) -> None:
    _AgentCall(action_id, paths).start()


def _on_more(_item: Any, paths: list[str]) -> None:
    _spawn([_bin(APP_BIN), "open", *paths])


# ------------------------------------------------------------------ 選單


def _file_refs(files: Sequence[Any]) -> list[Any] | None:
    """Nemo 的檔案 → FileRef；有任何一個不是本機檔案就整個不顯示選單。"""
    if not files or len(files) > MAX_FILES:
        return None
    refs = []
    for info in files:
        if info.get_uri_scheme() != "file":
            return None
        location = info.get_location()
        path = location.get_path() if location is not None else None
        if not path:
            return None
        mime = info.get_mime_type() or filetypes.UNKNOWN_MIME
        refs.append(filetypes.FileRef(path, mime, bool(info.is_directory())))
    return refs


def _separator(name: str) -> Nemo.MenuItem:
    return Nemo.MenuItem.new_separator(NAME_PREFIX + name)


def _action_item(action: Any, paths: list[str]) -> Nemo.MenuItem:
    item = Nemo.MenuItem(
        name=NAME_PREFIX + action.id, label=_label(action.title), icon=icons.icon_name(action.icon)
    )
    item.connect("activate", _on_action, action.id, paths)
    return item


def _submenu(actions: Sequence[Any], paths: list[str]) -> Nemo.Menu:
    sub = Nemo.Menu()
    for index, (group, members) in enumerate(quickactions.grouped(actions)):
        if index:
            sub.append_item(_separator(f"separator-{index}"))
        heading = Nemo.MenuItem(
            name=f"{NAME_PREFIX}group-{index}", label=_label(group), sensitive=False
        )
        sub.append_item(heading)
        for action in members:
            sub.append_item(_action_item(action, paths))
    sub.append_item(_separator("separator-more"))
    more = Nemo.MenuItem(
        name=NAME_PREFIX + "more",
        label=_label(quickactions.MORE_TOOLS_TITLE),
        icon=icons.icon_name(MORE_SYMBOL),
    )
    more.connect("activate", _on_more, paths)
    sub.append_item(more)
    return sub


def build_items(files: Sequence[Any]) -> list[Nemo.MenuItem]:
    if quickactions is None:
        return []
    refs = _file_refs(files)
    if not refs:
        return []
    actions = quickactions.menu_actions(refs)
    if not actions:
        return []
    paths = [r.path for r in refs]
    root = Nemo.MenuItem(
        name=NAME_PREFIX + "root", label=_label(quickactions.MENU_ROOT_TITLE), icon=ROOT_ICON
    )
    root.set_submenu(_submenu(actions, paths))
    return [root]


class QuickSeemdMenuProvider(GObject.GObject, Nemo.MenuProvider):
    def get_file_items(self, _window: Any, files: Sequence[Any]) -> list[Nemo.MenuItem]:
        try:
            return build_items(files)
        except Exception:  # noqa: BLE001 — 擴充的錯誤絕不能讓 Nemo 的右鍵選單壞掉
            log.exception("建立 QUICK_SEEMD 右鍵選單時發生錯誤")
            return []

    def get_background_items(self, _window: Any, _folder: Any) -> list[Nemo.MenuItem]:
        return []
