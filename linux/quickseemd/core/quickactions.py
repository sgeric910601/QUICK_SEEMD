"""右鍵與輪盤的一鍵動作清單（對應原版 Shared/QuickActions+*.swift）。

順序＝原版 all = pdf + office + image + media + other。
preset 是交給工具的選項值；鍵名與值必須是該工具 options 裡存在的（合約測試會檢查）。
Office 的 10 個動作在 PR1 先停用（enabled=False），第二個 PR 實作 Office 工具時打開。
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from .filetypes import FileRef, conforms_any

PDF_T = ("application/pdf",)
IMAGE_T = ("image/*",)
MOVIE_T = ("video/*",)
AUDIO_T = ("audio/*",)
ITEM_T = ("item",)
ARCHIVE_T = ("archive",)
TEXT_T = ("text/plain",)
WORD_T = ("word",)
EXCEL_T = ("excel",)
PPT_T = ("powerpoint",)
OFFICE_ANY_T = ("word", "excel", "powerpoint")

GROUPS: tuple[str, ...] = ("PDF", "Office", "圖片", "影音", "其他")


@dataclass(frozen=True)
class QuickAction:
    id: str
    title: str
    icon: str  # 原版 SF Symbol 名稱
    group: str
    input_types: tuple[str, ...]
    tool_id: str
    preset: dict[str, Any] = field(default_factory=dict)
    combines_files: bool = False
    enabled: bool = True

    def accepts(self, refs: Sequence[FileRef]) -> bool:
        return bool(refs) and all(conforms_any(r, self.input_types) for r in refs)


def _a(
    id_: str,
    title: str,
    icon: str,
    group: str,
    types: tuple[str, ...],
    tool: str,
    preset: dict[str, Any] | None = None,
    *,
    combines: bool = False,
    enabled: bool = True,
) -> QuickAction:
    return QuickAction(id_, title, icon, group, types, tool, preset or {}, combines, enabled)


ACTIONS: tuple[QuickAction, ...] = (
    # PDF
    _a(
        "pdf.compress-recommended",
        "壓縮 PDF（建議）",
        "arrow.down.right.and.arrow.up.left",
        "PDF",
        PDF_T,
        "pdf.compress",
        {"level": "recommended"},
    ),
    _a(
        "pdf.merge-pdfs",
        "合併成一個 PDF",
        "rectangle.stack.badge.plus",
        "PDF",
        PDF_T,
        "pdf.merge",
        {"bookmarks": True, "duplex": False, "name": ""},
        combines=True,
    ),
    _a(
        "pdf.to-jpg",
        "轉成 JPG 圖片",
        "photo.on.rectangle.angled",
        "PDF",
        PDF_T,
        "pdf.to-images",
        {"mode": "pages", "format": "jpg"},
    ),
    _a(
        "pdf.split-pages",
        "每一頁拆成一個 PDF",
        "scissors",
        "PDF",
        PDF_T,
        "pdf.split",
        {"mode": "every_page", "folder": True},
    ),
    _a("pdf.ocr-searchable", "轉成可搜尋的 PDF（OCR）", "text.viewfinder", "PDF", PDF_T, "pdf.ocr"),
    _a(
        "pdf.rotate-right",
        "向右旋轉 90°",
        "rotate.right",
        "PDF",
        PDF_T,
        "pdf.rotate",
        {"angle": 90, "pages": ""},
    ),
    _a("pdf.add-page-numbers", "加上頁碼", "number.square", "PDF", PDF_T, "pdf.page-numbers"),
    _a(
        "pdf.images-to-pdf",
        "合併成一個 PDF",
        "photo.stack",
        "PDF",
        IMAGE_T,
        "pdf.from-images",
        {"output": "merge"},
        combines=True,
    ),
    # Office（第二個 PR）
    _a(
        "office.to-pdf",
        "轉成 PDF",
        "doc.richtext",
        "Office",
        OFFICE_ANY_T,
        "office.word-to-pdf",
        enabled=False,
    ),
    _a(
        "office.pdf-to-docx",
        "轉成 Word（.docx）",
        "doc.text",
        "Office",
        PDF_T,
        "office.pdf-to-word",
        enabled=False,
    ),
    _a(
        "office.pdf-to-xlsx",
        "轉成 Excel（.xlsx）",
        "tablecells",
        "Office",
        PDF_T,
        "office.pdf-to-excel",
        enabled=False,
    ),
    _a(
        "office.pdf-to-pptx",
        "轉成 PowerPoint（.pptx）",
        "play.rectangle",
        "Office",
        PDF_T,
        "office.pdf-to-ppt",
        enabled=False,
    ),
    _a(
        "office.doc-to-docx",
        "升級成 .docx",
        "arrow.up.doc",
        "Office",
        ("application/msword",),
        "office.word-convert",
        enabled=False,
    ),
    _a(
        "office.xls-to-xlsx",
        "升級成 .xlsx",
        "arrow.up.doc",
        "Office",
        ("application/vnd.ms-excel",),
        "office.excel-convert",
        enabled=False,
    ),
    _a(
        "office.ppt-to-png",
        "轉成 PNG 圖片（每張投影片）",
        "photo.on.rectangle",
        "Office",
        PPT_T,
        "office.ppt-convert",
        enabled=False,
    ),
    _a(
        "office.ppt-to-jpg",
        "轉成 JPG 圖片（每張投影片）",
        "photo",
        "Office",
        PPT_T,
        "office.ppt-convert",
        enabled=False,
    ),
    _a(
        "office.ppt-to-pptx",
        "升級成 .pptx",
        "arrow.up.doc",
        "Office",
        ("application/vnd.ms-powerpoint",),
        "office.ppt-convert",
        enabled=False,
    ),
    _a(
        "office.excel-to-csv",
        "轉成 CSV",
        "tablecells",
        "Office",
        EXCEL_T,
        "office.excel-convert",
        enabled=False,
    ),
    # 圖片
    _a(
        "image.to-jpg",
        "轉成 JPG",
        "photo",
        "圖片",
        IMAGE_T,
        "image.convert",
        {"format": "jpeg", "quality": 0.9},
    ),
    _a(
        "image.to-png",
        "轉成 PNG",
        "photo.on.rectangle",
        "圖片",
        IMAGE_T,
        "image.convert",
        {"format": "png"},
    ),
    _a(
        "image.to-heic",
        "轉成 HEIC",
        "photo.stack",
        "圖片",
        IMAGE_T,
        "image.convert",
        {"format": "heic", "quality": 0.85},
    ),
    _a(
        "image.compress-recommended",
        "壓縮圖片（建議）",
        "arrow.down.right.and.arrow.up.left",
        "圖片",
        IMAGE_T,
        "image.compress",
        {"level": "recommended"},
    ),
    _a(
        "image.resize-half",
        "縮小成 50%",
        "arrow.down.left.and.arrow.up.right",
        "圖片",
        IMAGE_T,
        "image.resize",
        {"mode": "percent", "percent": 50},
    ),
    _a(
        "image.remove-bg-transparent",
        "移除背景",
        "person.and.background.dotted",
        "圖片",
        IMAGE_T,
        "image.remove-bg",
        {"background": "transparent", "format": "png"},
    ),
    _a(
        "image.rotate-right",
        "向右旋轉 90°",
        "rotate.right",
        "圖片",
        IMAGE_T,
        "image.rotate",
        {"angle": 90, "flip_h": False, "flip_v": False},
    ),
    _a(
        "image.strip-location",
        "移除相片位置資訊",
        "location.slash",
        "圖片",
        IMAGE_T,
        "image.strip-metadata",
        {"mode": "location"},
    ),
    # 影音
    _a(
        "video.convert-mp4",
        "轉成 MP4",
        "arrow.triangle.2.circlepath",
        "影音",
        MOVIE_T,
        "video.convert",
        {"format": "mp4"},
    ),
    _a(
        "video.compress-medium",
        "壓縮影片（建議）",
        "arrow.down.right.and.arrow.up.left",
        "影音",
        MOVIE_T,
        "video.compress",
        {"level": "balanced", "codec": "h264"},
    ),
    _a("video.gif-default", "轉成 GIF 動畫", "photo.stack", "影音", MOVIE_T, "video.to-gif"),
    _a(
        "video.audio-m4a",
        "擷取音訊（M4A）",
        "waveform",
        "影音",
        MOVIE_T,
        "video.extract-audio",
        {"format": "m4a", "bitrate": 192},
    ),
    _a("video.remove-audio", "移除聲音", "speaker.slash", "影音", MOVIE_T, "video.mute"),
    _a(
        "video.rotate-right",
        "影片向右旋轉 90°",
        "rotate.right",
        "影音",
        MOVIE_T,
        "video.rotate",
        {"rotation": "right", "method": "fast"},
    ),
    _a(
        "audio.convert-m4a",
        "轉成 M4A",
        "music.note",
        "影音",
        AUDIO_T,
        "audio.convert",
        {"format": "m4a", "bitrate": 256},
    ),
    _a(
        "audio.convert-wav",
        "轉成 WAV",
        "waveform.path",
        "影音",
        AUDIO_T,
        "audio.convert",
        {"format": "wav"},
    ),
    # 其他
    _a(
        "other.zip",
        "壓縮成 ZIP",
        "doc.zipper",
        "其他",
        ITEM_T,
        "other.compress",
        {"format": "zip", "together": True},
        combines=True,
    ),
    _a("other.unzip", "解壓縮", "archivebox", "其他", ARCHIVE_T, "other.extract"),
    _a(
        "other.to-utf8",
        "轉成 UTF-8 文字",
        "character.textbox",
        "其他",
        TEXT_T,
        "other.text-encoding",
        {"target": "utf8"},
    ),
    _a("other.read-qr", "讀取 QR Code", "qrcode.viewfinder", "其他", IMAGE_T, "other.read-qr"),
)

BY_ID: dict[str, QuickAction] = {a.id: a for a in ACTIONS}

MORE_TOOLS_TITLE = "更多工具與選項…"
MENU_ROOT_TITLE = "QUICK_SEEMD 轉換"


def action(action_id: str) -> QuickAction | None:
    return BY_ID.get(action_id)


def enabled_actions() -> tuple[QuickAction, ...]:
    return tuple(a for a in ACTIONS if a.enabled)


def menu_actions(refs: Sequence[FileRef]) -> list[QuickAction]:
    """右鍵選單的項目（原版 FinderSync 規則）：

    - id 含 "merge" 的動作至少要選 2 個檔案
    - 每個選取的檔案都要符合動作的類型（資料夾只符合 "item"）
    """
    if not refs:
        return []
    out = []
    for a in enabled_actions():
        if "merge" in a.id and len(refs) < 2:
            continue
        if a.accepts(refs):
            out.append(a)
    return out


def wheel_actions(refs: Sequence[FileRef], minimum_files: dict[str, int]) -> list[QuickAction]:
    """輪盤用（原版 WheelCatalog 規則）：檔案數 ≥ 工具的 minimum_files，只檢查前 20 個檔案。"""
    if not refs:
        return []
    sample = list(refs[:20])
    out = []
    for a in enabled_actions():
        if len(refs) < minimum_files.get(a.tool_id, 1):
            continue
        if a.accepts(sample):
            out.append(a)
    return out


def grouped(actions: Sequence[QuickAction]) -> list[tuple[str, list[QuickAction]]]:
    """依原版順序分組（給右鍵選單畫分組標題）。"""
    groups: list[tuple[str, list[QuickAction]]] = []
    for a in actions:
        if not groups or groups[-1][0] != a.group:
            groups.append((a.group, []))
        groups[-1][1].append(a)
    return groups
