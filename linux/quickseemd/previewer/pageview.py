"""網頁內容的顯示（WebKit2 4.1，GTK3）。

安全設計（預覽的網頁可能含有 Markdown／筆記本裡的原始 HTML）：
- 頁面與附件只從自訂網址 qs-preview:// 提供，不用 file://。
- 內容過濾器封鎖 qs-preview://doc/ 以外的一切載入；過濾器還沒就緒前不載入任何頁面，
  過濾器建立失敗就不顯示網頁。
- 關閉 JavaScript、DNS 預先查詢、超連結稽核、本機儲存；拒絕一切跳轉（連結點了沒反應），
  只允許同一頁裡的錨點。（新版 WebKitGTK 已把 DNS 預先查詢與超連結稽核移除、固定關閉，
  這類標為「已棄用」的設定就跳過，避免每次啟動印出警告。）
- 網頁底色用系統主題的內容底色（深色主題也跟著變）。
- 用不留資料的 WebContext（不寫 Cookie、快取到磁碟）。
"""

from __future__ import annotations

import itertools
import logging
from collections.abc import Callable
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("WebKit2", "4.1")
from gi.repository import Gdk, Gio, GLib, GObject, Gtk, WebKit2  # noqa: E402

from ..core import backends  # noqa: E402
from ..core.preview import Document  # noqa: E402
from . import scheme  # noqa: E402

log = logging.getLogger(__name__)

FILTER_FAILED_TEXT = "無法啟用預覽的安全防護，為了安全不顯示這個網頁。"
_SETTINGS_OFF = (
    "enable-dns-prefetching",
    "enable-hyperlink-auditing",
    "enable-javascript",
    "enable-javascript-markup",
    "javascript-can-open-windows-automatically",
    "javascript-can-access-clipboard",
    "enable-html5-local-storage",
    "enable-html5-database",
    "enable-page-cache",
    "enable-webaudio",
    "enable-webgl",
    "enable-media",
    "enable-media-stream",
    "enable-developer-extras",
    "allow-file-access-from-file-urls",
    "allow-universal-access-from-file-urls",
    "enable-back-forward-navigation-gestures",
)

BASE_COLOR = "theme_base_color"
FALLBACK_BASE = (1.0, 1.0, 1.0, 1.0)

FilterCallback = Callable[[WebKit2.UserContentFilter | None], None]


def _filter_store_dir() -> Path:
    return backends.cache_dir() / "preview" / "filters"


def _secure_settings() -> WebKit2.Settings:
    settings = WebKit2.Settings()
    for name in _SETTINGS_OFF:
        spec = settings.find_property(name)
        if spec is None or spec.flags & GObject.ParamFlags.DEPRECATED:
            continue  # 這個版本已經沒有這項功能（固定關閉）
        if settings.get_property(name):
            settings.set_property(name, False)
    return settings


class PageContext:
    """所有預覽網頁共用的 WebKit 環境（自訂網址、過濾器、設定），一個程序只有一份。"""

    _instance: PageContext | None = None

    @classmethod
    def get(cls) -> PageContext:
        if cls._instance is None:
            cls._instance = cls(_filter_store_dir())
        return cls._instance

    def __init__(self, store_dir: Path) -> None:
        self.context = WebKit2.WebContext.new_ephemeral()
        self.context.set_cache_model(WebKit2.CacheModel.DOCUMENT_VIEWER)
        self.context.register_uri_scheme(scheme.SCHEME, self._on_request)
        self.settings = _secure_settings()
        self.pages: dict[int, scheme.Page] = {}
        self._tokens = itertools.count(1)
        self._filter: WebKit2.UserContentFilter | None = None
        self._done = False
        self._waiting: list[FilterCallback] = []
        store_dir.mkdir(parents=True, exist_ok=True)
        self._store = WebKit2.UserContentFilterStore.new(str(store_dir))
        rules = GLib.Bytes.new(scheme.filter_rules().encode("utf-8"))
        self._store.save(scheme.FILTER_ID, rules, None, self._on_saved)

    def next_token(self) -> int:
        return next(self._tokens)

    def when_ready(self, callback: FilterCallback) -> None:
        """過濾器就緒（或確定失敗）後呼叫 callback(過濾器或 None)。"""
        if self._done:
            callback(self._filter)
        else:
            self._waiting.append(callback)

    def _on_saved(self, store: WebKit2.UserContentFilterStore, result: Gio.AsyncResult) -> None:
        try:
            self._filter = store.save_finish(result)
        except GLib.Error as exc:
            log.error("內容過濾器建立失敗：%s", exc.message)
            self._filter = None
        self._done = True
        waiting, self._waiting = self._waiting, []
        for callback in waiting:
            callback(self._filter)

    def _on_request(self, request: WebKit2.URISchemeRequest) -> None:
        res = scheme.lookup(request.get_uri(), self.pages)
        if res is None:
            err = GLib.Error.new_literal(
                Gio.io_error_quark(), "找不到預覽內容", Gio.IOErrorEnum.NOT_FOUND
            )
            request.finish_error(err)
            return
        stream = Gio.MemoryInputStream.new_from_bytes(GLib.Bytes.new(res.data))
        response = WebKit2.URISchemeResponse.new(stream, len(res.data))
        response.set_content_type(res.mime)
        request.finish_with_response(response)


class PageView(Gtk.Box):
    """顯示一份 Document 的元件；元件銷毀時一併撤下它的頁面內容。"""

    def __init__(self, doc: Document) -> None:
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self._ctx = PageContext.get()
        self._token = self._ctx.next_token()
        self._uri = scheme.page_uri(self._token)
        self._ctx.pages[self._token] = scheme.build_page(doc, self._token)
        self._alive = True
        self._manager = WebKit2.UserContentManager()
        self.view = WebKit2.WebView(
            web_context=self._ctx.context,
            user_content_manager=self._manager,
            settings=self._ctx.settings,
        )
        self.connect("style-updated", self._apply_background)
        self._apply_background(self)
        self.view.connect("decide-policy", self._on_policy)
        self.view.connect("context-menu", self._on_context_menu)
        self.pack_start(self.view, True, True, 0)
        self.connect("destroy", self._on_destroy)
        self._ctx.when_ready(self._load)

    @property
    def page_uri(self) -> str:
        return self._uri

    def _apply_background(self, _widget: Gtk.Widget) -> None:
        found, color = self.get_style_context().lookup_color(BASE_COLOR)
        self.view.set_background_color(color if found else Gdk.RGBA(*FALLBACK_BASE))

    def _load(self, content_filter: WebKit2.UserContentFilter | None) -> None:
        if not self._alive:
            return
        if content_filter is None:
            self.remove(self.view)
            label = Gtk.Label(label=FILTER_FAILED_TEXT, wrap=True, margin=24)
            self.pack_start(label, True, True, 0)
            label.show()
            return
        self._manager.add_filter(content_filter)
        self.view.load_uri(self._uri)

    def _on_policy(
        self,
        _view: WebKit2.WebView,
        decision: WebKit2.PolicyDecision,
        kind: WebKit2.PolicyDecisionType,
    ) -> bool:
        if kind == WebKit2.PolicyDecisionType.RESPONSE:
            return False
        if kind == WebKit2.PolicyDecisionType.NAVIGATION_ACTION:
            uri = decision.get_navigation_action().get_request().get_uri()
            if scheme.is_allowed_navigation(uri, self._uri):
                decision.use()
                return True
        decision.ignore()
        return True

    def _on_context_menu(
        self, _view, menu: WebKit2.ContextMenu, _event, _hit
    ) -> bool:  # noqa: ANN001
        """右鍵選單只留「拷貝」。"""
        for item in list(menu.get_items()):
            if item.get_stock_action() != WebKit2.ContextMenuAction.COPY:
                menu.remove(item)
        return menu.get_n_items() == 0

    def _on_destroy(self, _widget: Gtk.Widget) -> None:
        self._alive = False
        self._ctx.pages.pop(self._token, None)
