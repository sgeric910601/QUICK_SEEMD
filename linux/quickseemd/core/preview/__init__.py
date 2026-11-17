"""空白鍵預覽的內容（對應原版 Core/）。

凍結的介面：
- classify(path) -> str：決定用哪種方式顯示
    "html"   用 render() 產生的網頁（程式碼、Markdown、CSV、SQLite、壓縮檔、二進位…）
    "image"  圖片（含動圖），原生元件顯示
    "video" / "audio"  GStreamer 播放
    "pdf"    poppler 逐頁畫
    "font"   字型樣張（render_font() 產生網頁）
    "office" LibreOffice 轉 PDF 後當成 pdf 顯示
    "folder" 資料夾摘要卡（render() 產生網頁）
    "special" 不是一般檔案（管線、裝置…），只顯示資訊，不能打開讀
- render(path) -> Document：網頁內容（只有 HTML 與附件，不讀網路、沒有 JavaScript）

網頁裡引用附件一律寫成 "cid:名稱"；預覽視窗透過自訂網址提供附件內容。
"""

from __future__ import annotations

import importlib
import importlib.util
from dataclasses import dataclass, field
from pathlib import Path

KINDS = ("html", "image", "video", "audio", "pdf", "font", "office", "folder", "special")


@dataclass(frozen=True)
class Attachment:
    name: str
    data: bytes
    mime: str


@dataclass
class Document:
    html: str
    kind_label: str  # 頁首左邊的類型名稱（例如「JSON」「CSV 表格」）
    preferred_size: tuple[int, int] = (920, 700)
    attachments: dict[str, Attachment] = field(default_factory=dict)


def _engine():
    if importlib.util.find_spec(__name__ + ".engine") is None:
        raise NotImplementedError("預覽核心尚未實作")
    return importlib.import_module(__name__ + ".engine")


def classify(path: Path) -> str:
    return _engine().classify(Path(path))


def render(path: Path) -> Document:
    return _engine().render(Path(path))


def render_font(path: Path) -> Document:
    return _engine().render_font(Path(path))
