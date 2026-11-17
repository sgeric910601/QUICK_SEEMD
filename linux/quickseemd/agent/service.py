"""D-Bus 介面 dev.quickseemd.Agent（介面定義在 core.ipc，凍結）。

收到呼叫先檢查參數（路徑必須是存在的絕對路徑、格式必須認得、文字長度有上限），
立刻回覆，再排到主迴圈執行真正的工作，不讓呼叫端（Nemo 擴充、主程式）等。
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any, Protocol

from .. import __version__
from ..core import ipc
from ..core.converter import formats

log = logging.getLogger(__name__)

INVALID_ARGS = "org.freedesktop.DBus.Error.InvalidArgs"
UNKNOWN_METHOD = "org.freedesktop.DBus.Error.UnknownMethod"
FAILED = "org.freedesktop.DBus.Error.Failed"
MAX_ID = 200
MAX_TITLE = 200
MAX_MESSAGE = 2000
_ICON_RE = re.compile(r"^[A-Za-z0-9._-]{1,100}$")

Dispatch = Callable[[Callable[[], None]], None]


class AgentHandler(Protocol):
    def run_action(self, action: str, paths: list[Path]) -> None: ...

    def convert(self, fmt: str, paths: list[Path]) -> None: ...

    def notify(self, title: str, icon: str, message: str) -> None: ...

    def add_to_shelf(self, paths: list[Path]) -> None: ...

    def quit(self) -> None: ...


class ServiceError(Exception):
    def __init__(self, name: str, message: str) -> None:
        super().__init__(message)
        self.name = name
        self.message = message


def interface_info():  # noqa: ANN201 — Gio.DBusInterfaceInfo
    from gi.repository import Gio  # noqa: PLC0415

    return Gio.DBusNodeInfo.new_for_xml(ipc.AGENT_XML).interfaces[0]


def _paths(raw: Any) -> list[Path]:
    if not isinstance(raw, (list, tuple)) or not all(isinstance(p, str) for p in raw):
        raise ServiceError(INVALID_ARGS, "paths 必須是字串清單")
    try:
        return ipc.validate_paths(raw)
    except ValueError as exc:
        raise ServiceError(INVALID_ARGS, str(exc)) from exc


def _text(raw: Any, limit: int) -> str:
    if not isinstance(raw, str):
        raise ServiceError(INVALID_ARGS, "參數必須是文字")
    return raw[:limit]


def _identifier(raw: Any) -> str:
    if not isinstance(raw, str) or not raw or len(raw) > MAX_ID or "\0" in raw:
        raise ServiceError(INVALID_ARGS, "代碼不正確")
    return raw


def _args(params: Sequence[Any], count: int) -> Sequence[Any]:
    if len(params) != count:
        raise ServiceError(INVALID_ARGS, "參數數量不對")
    return params


class AgentService:
    """方法分派（不碰匯流排的部分可以直接測試）。"""

    def __init__(self, handler: AgentHandler, dispatch: Dispatch) -> None:
        self._handler = handler
        self._dispatch = dispatch
        self._registration: int | None = None

    def handle(self, method: str, params: Sequence[Any]) -> tuple[str, Any] | bool | None:
        """處理一個呼叫；回傳 (簽名, 值) 表示有回傳值，其他表示沒有。"""
        h = self._handler
        if method == "RunAction":
            action_raw, paths_raw = _args(params, 2)
            action, paths = _identifier(action_raw), _paths(paths_raw)
            if paths:
                self._dispatch(lambda: h.run_action(action, paths))
            return True
        if method == "Convert":
            fmt_raw, paths_raw = _args(params, 2)
            fmt, paths = _identifier(fmt_raw), _paths(paths_raw)
            if fmt not in formats.BY_ID:
                raise ServiceError(INVALID_ARGS, f"未知的格式：{fmt}")
            if paths:
                self._dispatch(lambda: h.convert(fmt, paths))
            return True
        if method == "Notify":
            return self._notify(params)
        if method == "AddToShelf":
            (paths_raw,) = _args(params, 1)
            paths = _paths(paths_raw)
            if paths:
                self._dispatch(lambda: h.add_to_shelf(paths))
            return True
        if method == "Ping":
            return ("s", __version__)
        if method == "Quit":
            self._dispatch(h.quit)
            return True
        raise ServiceError(UNKNOWN_METHOD, f"沒有這個方法：{method}")

    def _notify(self, params: Sequence[Any]) -> bool:
        title_raw, icon_raw, message_raw = _args(params, 3)
        title = _text(title_raw, MAX_TITLE)
        message = _text(message_raw, MAX_MESSAGE)
        # 只允許英數、點、底線、連字號（沒有斜線）；圖示名稱只拿來查圖示主題，不會當成路徑
        icon = icon_raw if isinstance(icon_raw, str) and _ICON_RE.match(icon_raw) else ""
        self._dispatch(lambda: self._handler.notify(title, icon, message))
        return True

    # ------------------------------------------------------------ 匯流排

    def register(
        self, connection, object_path: str = ipc.AGENT_OBJECT_PATH
    ) -> None:  # noqa: ANN001
        self._registration = connection.register_object(
            object_path, interface_info(), self._on_call, None, None
        )

    def unregister(self, connection) -> None:  # noqa: ANN001
        if self._registration:
            connection.unregister_object(self._registration)
            self._registration = None

    def _on_call(
        self, _conn, _sender, _path, _iface, method, params, invocation  # noqa: ANN001
    ) -> None:
        from gi.repository import GLib  # noqa: PLC0415

        try:
            result = self.handle(method, tuple(params.unpack()) if params is not None else ())
        except ServiceError as exc:
            invocation.return_dbus_error(exc.name, exc.message)
            return
        except Exception as exc:  # noqa: BLE001 — 任何意外都要回覆呼叫端，不能讓它卡住
            log.exception("處理 %s 時發生錯誤", method)
            invocation.return_dbus_error(FAILED, str(exc))
            return
        if isinstance(result, tuple):
            invocation.return_value(GLib.Variant(f"({result[0]})", (result[1],)))
        else:
            invocation.return_value(None)
