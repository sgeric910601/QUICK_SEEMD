"""拖曳監看用的兩條 python-xlib 連線（各自只給一個執行緒用）。"""

from __future__ import annotations

import logging
import time
from pathlib import Path

from Xlib import XK, X
from Xlib.ext import ge, xfixes
from Xlib.ext import xinput as xi
from Xlib.protocol import rq

from .taps import KEYSYM_NAMES
from .wheel_model import parse_uri_list
from .x11_drag import (
    FETCH_TIMEOUT,
    XI_RAW_KEY_PRESS,
    XI_RAW_KEY_RELEASE,
    PointerSample,
    RawKey,
    parse_raw_key,
)

log = logging.getLogger(__name__)

PROPERTY_NAME = "QS_DRAG_FILES"


class _XIQueryVersion22(rq.ReplyRequest):
    """XIQueryVersion 2.2（python-xlib 內建的只會問 2.0，原始事件才不會在抓取時漏掉）。"""

    _request = rq.Struct(
        rq.Card8("opcode"),
        rq.Opcode(47),
        rq.RequestLength(),
        rq.Card16("major_version"),
        rq.Card16("minor_version"),
    )
    _reply = rq.Struct(
        rq.ReplyCode(),
        rq.Pad(1),
        rq.Card16("sequence_number"),
        rq.ReplyLength(),
        rq.Card16("major_version"),
        rq.Card16("minor_version"),
        rq.Pad(20),
    )


def _keycode_map(d) -> dict[int, str]:  # noqa: ANN001
    """修飾鍵與 Esc 的 keycode → 我們的鍵名稱。"""
    out: dict[int, str] = {}
    for name, key in KEYSYM_NAMES.items():
        keysym = XK.string_to_keysym(name)
        if not keysym:
            continue
        for keycode, _index in d.keysym_to_keycodes(keysym):
            out.setdefault(keycode, key)
    return out


class EventConnection:
    """監看 XdndSelection 換主人與原始按鍵事件。"""

    def __init__(self, d) -> None:  # noqa: ANN001
        self._d = d
        self._xi_opcode: int | None = None
        self._keys: dict[int, str] = {}
        self.resource_mask = d.display.info.resource_id_mask

    def setup(self) -> bool:
        d = self._d
        try:
            d.xfixes_query_version()
            root = d.screen().root
            atom = d.intern_atom("XdndSelection")
            d.xfixes_select_selection_input(root, atom, xfixes.XFixesSetSelectionOwnerNotifyMask)
        except Exception as exc:  # noqa: BLE001 — 沒有 XFixes 就無法偵測拖曳
            log.warning("X 伺服器不支援 XFixes，拖曳輪盤停用：%s", exc)
            return False
        self._keys = _keycode_map(d)
        self._setup_raw_keys(root)
        d.flush()
        return True

    def _setup_raw_keys(self, root) -> None:  # noqa: ANN001
        d = self._d
        try:
            info = d.query_extension(xi.extname)
            if info is None:
                return
            reply = _XIQueryVersion22(
                display=d.display, opcode=info.major_opcode, major_version=2, minor_version=2
            )
            mask = xi.RawKeyPressMask | xi.RawKeyReleaseMask
            root.xinput_select_events([(xi.AllMasterDevices, mask)])
            self._xi_opcode = info.major_opcode
            log.debug("XInput %d.%d 原始按鍵事件已啟用", reply.major_version, reply.minor_version)
        except Exception as exc:  # noqa: BLE001 — 沒有 XInput 2 時只靠輪詢也能用
            log.info("無法啟用 XInput 原始按鍵事件：%s", exc)

    def next(self) -> tuple[str, object]:
        """等下一個事件：("owner", 視窗代碼)、("key", RawKey) 或 ("other", None)。"""
        e = self._d.next_event()
        if type(e).__name__ == "SetSelectionOwnerNotify":
            owner = e.owner
            return "owner", int(owner.id) if owner else 0
        if (
            e.type == ge.GenericEventCode
            and self._xi_opcode is not None
            and getattr(e, "extension", None) == self._xi_opcode
            and getattr(e, "evtype", None) in (XI_RAW_KEY_PRESS, XI_RAW_KEY_RELEASE)
        ):
            keycode = parse_raw_key(bytes(e.data))
            key = self._keys.get(keycode or -1)
            if key:
                return "key", RawKey(key, e.evtype == XI_RAW_KEY_PRESS, time.monotonic())
        return "other", None


class TrackerConnection:
    """拖曳中讀游標、Esc 狀態，並向來源要檔案清單。"""

    def __init__(self, d) -> None:  # noqa: ANN001
        self._d = d
        self._root = d.screen().root
        self._win = self._root.create_window(0, 0, 1, 1, 0, X.CopyFromParent)
        self._xdnd = d.intern_atom("XdndSelection")
        self._uri_list = d.intern_atom("text/uri-list")
        self._prop = d.intern_atom(PROPERTY_NAME)
        self._incr = d.intern_atom("INCR")
        escape = XK.string_to_keysym("Escape")
        self._escape_codes = [kc for kc, _i in d.keysym_to_keycodes(escape)]

    def query(self) -> PointerSample:
        t = time.monotonic()
        q = self._root.query_pointer()
        return PointerSample(int(q.mask), int(q.root_x), int(q.root_y), t)

    def escape_down(self) -> bool:
        keymap = self._d.query_keymap()
        return any(keymap[kc // 8] & (1 << (kc % 8)) for kc in self._escape_codes)

    def fetch_files(self) -> list[Path] | None:
        """向拖曳來源要 text/uri-list；來源不回應或拒絕時回傳 None。"""
        d = self._d
        self._win.convert_selection(self._xdnd, self._uri_list, self._prop, X.CurrentTime)
        d.flush()
        deadline = time.monotonic() + FETCH_TIMEOUT
        while time.monotonic() < deadline:
            while d.pending_events():
                e = d.next_event()
                if e.type == X.SelectionNotify:
                    return self._read_property(e.property)
            time.sleep(0.01)
        log.info("拖曳來源沒有及時提供檔案清單")
        return None

    def _read_property(self, prop: int) -> list[Path] | None:
        if prop == X.NONE:
            log.info("拖曳來源拒絕提供檔案清單")
            return None
        reply = self._win.get_full_property(self._prop, X.AnyPropertyType)
        self._win.delete_property(self._prop)
        self._d.flush()
        if reply is None or reply.property_type == self._incr:
            return None
        value = reply.value
        data = value if isinstance(value, bytes) else bytes(value)
        return parse_uri_list(data)
