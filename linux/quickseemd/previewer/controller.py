"""預覽流程控制（不碰 GTK）：D-Bus 請求 → 背景載入 → 視窗。

- show_file() 在主執行緒、回覆 D-Bus 之後呼叫；立刻更新狀態（記下網址），
  解析與載入交給背景執行緒，完成後回到主執行緒再顯示。
- 載入超過 LOADING_DELAY_MS 才顯示「正在載入…」，快的檔案不會閃一下。
- 過期的結果（已換檔案或已關閉）一律丟掉。
- Office 文件先顯示「正在轉換文件…」，轉好後再換成 PDF。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any, Protocol

from . import content as content_mod
from . import fallback
from . import uri as uri_mod
from .content import Content
from .state import Decision, PreviewState, on_close, on_show_file
from .uri import Target, TargetKind

log = logging.getLogger(__name__)

LOADING_DELAY_MS = 250
LOADING_TEXT = "正在載入…"

Background = Callable[[Callable[[], Any], Callable[[Any], None]], None]
Later = Callable[[int, Callable[[], None]], None]


class Ui(Protocol):
    def prepare(self, xid: int) -> None: ...
    def show_loading(self, title: str, text: str) -> None: ...
    def show_content(self, c: Content) -> None: ...
    def present(self) -> None: ...
    def close_window(self) -> None: ...


def _crash_content(uri: str) -> Content:
    target = Target(TargetKind.INVALID, uri, uri_mod.display_name(uri))
    doc = fallback.error_page(target.name, content_mod.UNEXPECTED)
    return Content(content_mod.PAGE, target, target.name, doc.kind_label, document=doc)


class Controller:
    def __init__(
        self,
        ui: Ui,
        background: Background,
        later: Later,
        *,
        resolve: Callable[[str], Target] = uri_mod.resolve,
        load: Callable[[Target], Content] = content_mod.load,
        finish_office: Callable[[Content], Content] = content_mod.finish_office,
    ) -> None:
        self._ui = ui
        self._background = background
        self._later = later
        self._resolve = resolve
        self._load = load
        self._finish_office = finish_office
        self._state = PreviewState()
        self._displayed = -1

    @property
    def state(self) -> PreviewState:
        return self._state

    def show_file(self, uri: str, xid: int, close_if_shown: bool) -> Decision:
        self._state, decision = on_show_file(self._state, uri, close_if_shown)
        if decision is Decision.CLOSE:
            self._ui.close_window()
        elif decision is Decision.PRESENT:
            self._ui.present()
        else:
            self._start(uri, xid, self._state.generation)
        return decision

    def close(self) -> None:
        if not self._state.is_open:
            return
        self._state = on_close(self._state)
        self._ui.close_window()

    # ------------------------------------------------------------ 內部流程

    def _start(self, uri: str, xid: int, generation: int) -> None:
        self._ui.prepare(xid)
        title = uri_mod.display_name(uri)
        self._later(LOADING_DELAY_MS, lambda: self._maybe_loading(generation, title))
        self._run(generation, lambda: self._safe(uri, lambda: self._load(self._resolve(uri))))

    def _run(self, generation: int, job: Callable[[], Content]) -> None:
        self._background(job, lambda result: self._on_loaded(generation, result))

    def _safe(self, uri: str, job: Callable[[], Content]) -> Content:
        try:
            return job()
        except Exception:  # noqa: BLE001 — 背景工作出錯也要讓視窗顯示錯誤頁，不能一直轉圈
            log.exception("準備預覽 %s 時發生錯誤", uri)
            return _crash_content(uri)

    def _maybe_loading(self, generation: int, title: str) -> None:
        if self._state.is_current(generation) and self._displayed != generation:
            self._ui.show_loading(title, LOADING_TEXT)

    def _on_loaded(self, generation: int, result: Content) -> None:
        if not self._state.is_current(generation):
            return
        self._displayed = generation
        self._ui.show_content(result)
        if result.view == content_mod.OFFICE:
            uri = result.target.uri
            self._run(generation, lambda: self._safe(uri, lambda: self._finish_office(result)))
