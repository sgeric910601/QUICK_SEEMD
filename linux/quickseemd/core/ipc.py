"""程序之間的 D-Bus 介面（凍結的合約）。

- dev.quickseemd.Agent：背景程式（系統匣、輪盤、進度面板、右鍵工作佇列）
- org.nemo.Preview：Nemo 空白鍵預覽（介面由 Nemo 決定）
- dev.quickseemd.App：主程式（Gtk.Application，單一實例；用命令列 quickseemd open 叫出）
"""

from __future__ import annotations

import os
from collections.abc import Sequence
from pathlib import Path

AGENT_BUS_NAME = "dev.quickseemd.Agent"
AGENT_OBJECT_PATH = "/dev/quickseemd/Agent"
AGENT_INTERFACE = "dev.quickseemd.Agent"

AGENT_XML = """
<node>
  <interface name="dev.quickseemd.Agent">
    <!-- 執行一鍵動作（右鍵、輪盤）；paths 是絕對路徑 -->
    <method name="RunAction">
      <arg type="s" name="action" direction="in"/>
      <arg type="as" name="paths" direction="in"/>
    </method>
    <!-- 萬用轉換成某個格式（輪盤的格式格子） -->
    <method name="Convert">
      <arg type="s" name="format" direction="in"/>
      <arg type="as" name="paths" direction="in"/>
    </method>
    <!-- 在右上角進度面板顯示一則已完成的訊息 -->
    <method name="Notify">
      <arg type="s" name="title" direction="in"/>
      <arg type="s" name="icon" direction="in"/>
      <arg type="s" name="message" direction="in"/>
    </method>
    <!-- 把檔案加進浮動暫存 -->
    <method name="AddToShelf">
      <arg type="as" name="paths" direction="in"/>
    </method>
    <method name="Ping">
      <arg type="s" name="version" direction="out"/>
    </method>
    <method name="Quit"/>
  </interface>
</node>
"""

PREVIEW_BUS_NAME = "org.nemo.Preview"
PREVIEW_OBJECT_PATH = "/org/nemo/Preview"
PREVIEW_INTERFACE = "org.nemo.Preview"

PREVIEW_XML = """
<node>
  <interface name="org.nemo.Preview">
    <method name="ShowFile">
      <arg type="s" name="uri" direction="in"/>
      <arg type="i" name="xid" direction="in"/>
      <arg type="b" name="closeIfAlreadyShown" direction="in"/>
    </method>
    <method name="Close"/>
  </interface>
</node>
"""

APP_ID = "dev.quickseemd.App"

MAX_PATHS = 10000


def validate_paths(paths: Sequence[str]) -> list[Path]:
    """D-Bus 收到的路徑：必須是絕對路徑、存在、不含 NUL；不存在的直接略過（原版行為）。"""
    if len(paths) > MAX_PATHS:
        raise ValueError("檔案太多")
    out: list[Path] = []
    for raw in paths:
        if not isinstance(raw, str) or "\0" in raw or not os.path.isabs(raw):
            continue
        p = Path(os.path.normpath(raw))
        if p.exists():
            out.append(p)
    return out


def call_agent(method: str, args_variant, timeout_ms: int = 5000) -> None:  # noqa: ANN001
    """同步呼叫背景程式（需要時由 D-Bus 自動啟動）。給命令列與測試用。"""
    from gi.repository import Gio  # noqa: PLC0415

    bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
    bus.call_sync(
        AGENT_BUS_NAME,
        AGENT_OBJECT_PATH,
        AGENT_INTERFACE,
        method,
        args_variant,
        None,
        Gio.DBusCallFlags.NONE,
        timeout_ms,
        None,
    )
