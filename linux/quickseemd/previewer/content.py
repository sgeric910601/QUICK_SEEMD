"""準備要顯示的內容（在背景執行緒執行，不碰 GTK）。

load(target) 依預覽核心的分類決定用哪種元件顯示：
- PAGE   網頁（程式碼、Markdown、資料夾、字型樣張、資訊卡、錯誤）
- IMAGE  圖片與動圖（已載入的 GdkPixbuf）
- VIDEO / AUDIO  GStreamer 播放（只帶路徑）
- PDF    Poppler 文件（已載入）
- OFFICE 要先用 LibreOffice 轉成 PDF：視窗先顯示「正在轉換文件…」，再呼叫 finish_office()

任何錯誤都變成可以顯示的錯誤頁，不會丟出例外。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any

from ..core import errors, filetypes
from ..core import preview as core_preview
from ..core.preview import Document
from . import fallback, loaders, office
from .uri import Target, TargetKind

log = logging.getLogger(__name__)

PAGE = "page"
IMAGE = "image"
VIDEO = "video"
AUDIO = "audio"
PDF = "pdf"
OFFICE = "office"

UNEXPECTED = "發生未預期的錯誤。"


@dataclass(frozen=True)
class Content:
    view: str
    target: Target
    title: str
    subtitle: str = ""
    document: Document | None = None
    path: Path | None = None
    payload: Any = None
    natural_size: tuple[float, float] | None = None
    can_open: bool = True


def classify(path: Path) -> str:
    try:
        return core_preview.classify(path)
    except NotImplementedError:
        return fallback.classify(path)


def _page(target: Target, doc: Document, can_open: bool = True) -> Content:
    return Content(PAGE, target, target.name, doc.kind_label, document=doc, can_open=can_open)


def _render(path: Path, kind: str) -> Document:
    try:
        if kind == "font":
            return core_preview.render_font(path)
        return core_preview.render(path)
    except NotImplementedError:
        return fallback.file_page(path)


def _load_page(target: Target, kind: str) -> Content:
    if kind == "special":
        return _page(target, fallback.special_page(target.name, "", target.path), False)
    return _page(target, _render(target.path, kind))


def _describe(path: Path) -> str:
    return filetypes.describe(filetypes.mime_of(path))


def _load_image(target: Target, _kind: str) -> Content:
    img = loaders.load_image(target.path)
    src_w, src_h = img.source_size
    subtitle = f"{_describe(target.path)} · {src_w} × {src_h}"
    return Content(
        IMAGE, target, target.name, subtitle, payload=img, natural_size=(img.width, img.height)
    )


def _pdf_content(target: Target, pdf_path: Path, subtitle: str) -> Content:
    doc = loaders.load_pdf(pdf_path)
    size = doc.get_page(0).get_size()
    full = f"{subtitle} · {doc.get_n_pages()} 頁"
    return Content(PDF, target, target.name, full, path=pdf_path, payload=doc, natural_size=size)


def _load_pdf(target: Target, _kind: str) -> Content:
    return _pdf_content(target, target.path, _describe(target.path))


def _load_media(target: Target, kind: str) -> Content:
    view = VIDEO if kind == "video" else AUDIO
    return Content(view, target, target.name, _describe(target.path), path=target.path)


def _load_office(target: Target, _kind: str) -> Content:
    return Content(OFFICE, target, target.name, _describe(target.path), path=target.path)


_LOADERS: dict[str, Callable[[Target, str], Content]] = {
    "image": _load_image,
    "pdf": _load_pdf,
    "video": _load_media,
    "audio": _load_media,
    "office": _load_office,
}


def _unavailable(target: Target) -> Content:
    if target.kind is TargetKind.SPECIAL:
        doc = fallback.special_page(target.name, target.detail, target.path)
        return _page(target, doc, can_open=False)
    if target.kind is TargetKind.MISSING:
        return _page(target, fallback.missing_page(target.name, target.path), can_open=False)
    if target.kind is TargetKind.REMOTE:  # 連「用預設程式開啟」都不給，完全不碰那個位置
        return _page(target, fallback.remote_page(target.name, target.uri), can_open=False)
    return _page(target, fallback.invalid_page(target.name), can_open=False)


def _guarded(target: Target, body: Callable[[], Content]) -> Content:
    try:
        return body()
    except errors.QSError as exc:
        return _page(target, fallback.error_page(target.name, str(exc)))
    except Exception:  # noqa: BLE001 — 預覽不能因為單一檔案而當掉；記錄後顯示錯誤頁
        log.exception("預覽 %s 時發生錯誤", target.uri)
        return _page(target, fallback.error_page(target.name, UNEXPECTED))


def _load_local(target: Target) -> Content:
    kind = "folder" if target.kind is TargetKind.FOLDER else classify(target.path)
    loader = _LOADERS.get(kind, _load_page)
    return loader(target, kind)


def load(target: Target) -> Content:
    if target.kind not in (TargetKind.FILE, TargetKind.FOLDER):
        return _unavailable(target)
    return _guarded(target, lambda: _load_local(target))


def finish_office(content: Content) -> Content:
    """Office 文件的第二階段：轉成 PDF 後改用 PDF 顯示。"""
    target = content.target

    def body() -> Content:
        pdf = office.convert_to_pdf(content.path)
        return replace(_pdf_content(target, pdf, content.subtitle), title=content.title)

    return _guarded(target, body)
