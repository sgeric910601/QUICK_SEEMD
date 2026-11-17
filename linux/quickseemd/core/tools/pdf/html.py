"""HTML 轉 PDF（對應原版 PTHTML.swift）。

原版用 WebKit 排成一張長頁面再切頁；這裡用 WeasyPrint 直接分頁（同樣以螢幕樣式排版，
自動在文字行之間分頁，文字可選取）。
- 只能讀取網頁所在資料夾（含子資料夾）裡的檔案，與原版的讀取權限相同
- 網路資源照樣下載（每個資源最多等 10 秒）
"""

from __future__ import annotations

import io
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

from ... import errors, naming
from ...model import PDF, TINTS, ToolContext, ToolDefinition, per_file
from ...options import Choice, Kind, Option, Values, note
from .common import A4, LETTER, as_bool, as_float, work_dir

log = logging.getLogger(__name__)

FETCH_TIMEOUT = 10
ALLOWED_SCHEMES = frozenset({"http", "https", "data"})
NO_BACKGROUND_CSS = (
    "*, *::before, *::after { background: transparent !important; "
    "background-image: none !important; box-shadow: none !important; }"
)


def page_size(paper: str, landscape: bool) -> tuple[float, float]:
    w, h = LETTER if paper == "letter" else A4
    return (h, w) if landscape else (w, h)


def _inside(path: Path, root: Path) -> bool:
    return path == root or root in path.parents


def make_fetcher(folder: Path) -> Callable[..., dict[str, Any]]:
    """只允許讀取網頁資料夾內的檔案（含符號連結解析後的實際位置）與網路資源。"""
    from weasyprint import default_url_fetcher  # noqa: PLC0415 — 用到才載入（較慢）

    root = Path(folder).resolve()

    def fetch(url: str, timeout: float = FETCH_TIMEOUT, ssl_context: Any = None) -> dict[str, Any]:
        parts = urlsplit(url)
        scheme = parts.scheme.lower()
        if scheme == "file":
            target = Path(unquote(parts.path)).resolve()
            if parts.netloc not in ("", "localhost") or not _inside(target, root):
                raise ValueError(f"不允許讀取網頁資料夾以外的檔案：{url}")
        elif scheme not in ALLOWED_SCHEMES:
            raise ValueError(f"不支援的網址：{url}")
        return default_url_fetcher(
            url, timeout=min(timeout, FETCH_TIMEOUT), ssl_context=ssl_context
        )

    return fetch


def _page_css(values: Values) -> str:
    w, h = page_size(str(values.get("paper") or "a4"), as_bool(values.get("landscape"), False))
    m = as_float(values.get("margin"), 36)
    css = f"@page {{ size: {w}pt {h}pt !important; margin: {m}pt !important; }}"
    if not as_bool(values.get("backgrounds"), True):
        css += "\n" + NO_BACKGROUND_CSS
    return css


def _render(file: Path, values: Values) -> Any:
    from weasyprint import CSS, HTML  # noqa: PLC0415

    try:
        source = file.read_bytes()
    except OSError as exc:
        raise errors.unreadable(f"網頁載入失敗：{exc.strerror or exc}") from exc
    folder = file.resolve().parent
    try:
        page = HTML(
            file_obj=io.BytesIO(source),
            base_url=folder.as_uri() + "/",
            url_fetcher=make_fetcher(folder),
            media_type="screen",
        )
        return page.render(stylesheets=[CSS(string=_page_css(values))])
    except (ValueError, TypeError, AssertionError, LookupError, OSError) as exc:
        raise errors.QSError(f"WeasyPrint 無法產生 PDF：{exc}") from exc


def _html_file(file: Path, values: Values, ctx: ToolContext) -> list[Path]:
    ctx.progress(0.05, f"載入「{file.name}」…")
    document = _render(file, values)
    ctx.check_cancelled()
    ctx.progress(0.7, "分頁中…")
    count = len(document.pages)
    if count == 0:
        raise errors.QSError("WeasyPrint 產生的 PDF 無法讀取。")
    document.metadata.title = naming.base_name(file)
    tmp = work_dir(ctx, file) / "html.pdf"
    try:
        document.write_pdf(tmp)
    except (OSError, ValueError) as exc:
        raise errors.write_failed(str(exc)) from exc
    ctx.progress(1, f"已轉成 {count} 頁 PDF")
    return [ctx.deliver(tmp, ctx.output_name(file, None, "pdf"), file)]


def run_from_html(files: list[Path], values: Values, ctx: ToolContext) -> list[Path]:
    return per_file(files, ctx, lambda f, sub: _html_file(f, values, sub), skip_errors=False)


FROM_HTML = ToolDefinition(
    id="pdf.from-html",
    category=PDF,
    title="HTML 轉 PDF",
    subtitle="把本機的 HTML 網頁轉成分頁的 PDF，文字可選取。",
    icon="globe",
    run=run_from_html,
    input_types=("text/html",),
    input_label="HTML 檔案",
    action_title="轉成 PDF",
    keywords=("html to pdf", "web page", "webpage", "網頁", "HTML", "網頁轉PDF"),
    tint=TINTS["teal"],
    badge="PDF",
    options=(
        Option(
            "paper",
            Kind.CHIPS,
            "紙張大小",
            "a4",
            choices=(Choice("a4", "A4"), Choice("letter", "Letter")),
            group="紙張",
            group_icon="doc",
        ),
        Option(
            "landscape",
            Kind.SEGMENTED,
            "方向",
            False,
            choices=(Choice(False, "直向"), Choice(True, "橫向")),
            group="紙張",
            group_icon="doc",
        ),
        Option(
            "margin",
            Kind.SEGMENTED,
            "邊界",
            36,
            choices=(Choice(0, "無"), Choice(18, "窄"), Choice(36, "標準"), Choice(54, "寬")),
            group="邊界",
            group_icon="square.dashed",
        ),
        Option(
            "backgrounds",
            Kind.TOGGLE,
            "列印背景色與背景圖片",
            True,
            caption="關閉時只保留文字與圖片，較省墨水",
            group="外觀",
            group_icon="paintbrush",
        ),
        note(
            "會讀取同一個資料夾中的圖片與樣式表；網頁引用的網路資源需要連網才會顯示。"
            "頁面在文字行之間自動分頁。"
        ),
    ),
)
