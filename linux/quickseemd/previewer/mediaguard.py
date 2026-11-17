"""播放器不能連網（只用 GStreamer，不碰 GTK）。

預覽只播放本機檔案，但檔案內容可以騙過副檔名：例如叫 track01.mp3、其實是 HLS 播放清單，
GStreamer 會自動接上 HLS 解封裝元件，然後去網路抓片段。所以：
- harden_registry()：把會自己連網的解封裝元件（HLS、DASH、Smooth Streaming、SDP）
  排名降成 NONE，自動接線時不會被選上。
- PipelineGuard：播放器每加入一個元件就檢查；不是 filesrc 的來源元件（網路來源等）、
  或上面那些解封裝元件，立刻鎖住狀態不讓它啟動，並通知呼叫端停止播放、顯示錯誤。
"""

from __future__ import annotations

import logging
from collections.abc import Callable

import gi

gi.require_version("Gst", "1.0")
from gi.repository import Gst  # noqa: E402

log = logging.getLogger(__name__)

NETWORK_DEMUXERS = (
    "hlsdemux",
    "hlsdemux2",
    "dashdemux",
    "dashdemux2",
    "mssdemux",
    "mssdemux2",
    "sdpdemux",
)
ALLOWED_SOURCES = frozenset({"filesrc"})
REFUSED_TEXT = "這個檔案要從網路讀取內容，預覽不會連網，所以不播放。"


def harden_registry() -> list[str]:
    """把會連網的解封裝元件降成不自動使用；回傳這次實際調整的名稱。"""
    registry = Gst.Registry.get()
    changed = []
    for name in NETWORK_DEMUXERS:
        feature = registry.lookup_feature(name)
        if feature is not None and feature.get_rank() != Gst.Rank.NONE:
            feature.set_rank(Gst.Rank.NONE)
            changed.append(name)
    return changed


def is_forbidden(element: Gst.Element) -> bool:
    factory = element.get_factory()
    if factory is None:
        return False
    name = factory.get_name()
    if name in NETWORK_DEMUXERS:
        return True
    klass = factory.get_metadata("klass") or ""
    return klass.split("/")[0] == "Source" and name not in ALLOWED_SOURCES


class PipelineGuard:
    """接在 playbin 上：發現不允許的元件就鎖住它並呼叫 on_refused(元件名稱)。

    on_refused 可能在 GStreamer 的串流執行緒被呼叫，呼叫端要自己轉回主執行緒。
    """

    def __init__(self, on_refused: Callable[[str], None]) -> None:
        self._on_refused = on_refused

    def attach(self, playbin: Gst.Element) -> None:
        playbin.connect("source-setup", self._check)
        playbin.connect("element-setup", self._check)

    def _check(self, _playbin: Gst.Element, element: Gst.Element) -> None:
        if not is_forbidden(element):
            return
        element.set_locked_state(True)  # 停在 NULL，永遠不會開始連線
        name = element.get_factory().get_name()
        log.warning("拒絕播放器使用會連網的元件：%s", name)
        self._on_refused(name)
