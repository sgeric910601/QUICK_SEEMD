"""原版 SF Symbols 名稱 → Linux 圖示主題（Adwaita）符號圖示名稱。

工具與動作的定義都寫原版的 SF Symbol 名稱；介面層用 icon_name() 轉換。
對照表只用 Ubuntu 24.04 的 Adwaita 主題確定存在的名稱；找不到時用 FALLBACK。
"""

from __future__ import annotations

FALLBACK = "applications-system-symbolic"

SF_TO_FREEDESKTOP: dict[str, str] = {
    # 分類
    "doc.richtext": "x-office-document-symbolic",
    "doc.text": "x-office-document-symbolic",
    "photo": "image-x-generic-symbolic",
    "film": "video-x-generic-symbolic",
    "square.grid.2x2": "view-app-grid-symbolic",
    "square.grid.2x2.fill": "view-app-grid-symbolic",
    "tablecells": "x-office-spreadsheet-symbolic",
    "play.rectangle": "x-office-presentation-symbolic",
    "waveform": "audio-x-generic-symbolic",
    "waveform.path": "audio-x-generic-symbolic",
    "waveform.badge.minus": "audio-x-generic-symbolic",
    "music.note": "audio-x-generic-symbolic",
    "character.textbox": "text-x-generic-symbolic",
    "archivebox": "package-x-generic-symbolic",
    "doc.zipper": "package-x-generic-symbolic",
    # 動作
    "arrow.down.right.and.arrow.up.left": "view-restore-symbolic",
    "arrow.up.left.and.arrow.down.right": "view-fullscreen-symbolic",
    "arrow.down.left.and.arrow.up.right": "view-restore-symbolic",
    "arrow.up.backward.and.arrow.down.forward.square": "zoom-in-symbolic",
    "arrow.triangle.2.circlepath": "media-playlist-repeat-symbolic",
    "rectangle.stack.badge.plus": "list-add-symbolic",
    "photo.on.rectangle.angled": "image-x-generic-symbolic",
    "photo.on.rectangle": "image-x-generic-symbolic",
    "photo.stack": "image-x-generic-symbolic",
    "scissors": "edit-cut-symbolic",
    "text.viewfinder": "edit-find-symbolic",
    "rotate.right": "object-rotate-right-symbolic",
    "number.square": "insert-text-symbolic",
    "person.and.background.dotted": "applications-graphics-symbolic",
    "location.slash": "location-services-disabled-symbolic",
    "speaker.slash": "audio-volume-muted-symbolic",
    "qrcode.viewfinder": "camera-photo-symbolic",
    "camera.viewfinder": "camera-photo-symbolic",
    "arrow.up.doc": "document-save-symbolic",
    "square.grid.3x3.square": "view-grid-symbolic",
    "doc.badge.ellipsis": "edit-cut-symbolic",
    "seal": "format-text-bold-symbolic",
    "signature": "format-text-bold-symbolic",
    "lock.doc": "security-high-symbolic",
    "lock.open": "changes-allow-symbolic",
    "doc.plaintext": "text-x-generic-symbolic",
    "globe": "web-browser-symbolic",
    "info.circle": "dialog-information-symbolic",
    "crop": "edit-select-all-symbolic",
    "face.dashed": "face-smile-symbolic",
    "app.dashed": "applications-graphics-symbolic",
    "captions.bubble": "text-x-generic-symbolic",
    "hare": "media-seek-forward-symbolic",
    "aspectratio": "view-dual-symbolic",
    "wand.and.stars": "system-run-symbolic",
    "questionmark": "dialog-question-symbolic",
    "ellipsis.circle": "view-more-horizontal-symbolic",
    "wrench.and.screwdriver": "preferences-system-symbolic",
    "bolt": "system-run-symbolic",
    # 動作輪盤
    "square.and.arrow.up": "send-to-symbolic",
    "tray.and.arrow.down": "folder-download-symbolic",
    "doc.on.doc": "edit-copy-symbolic",
    "folder": "folder-symbolic",
    "link": "insert-link-symbolic",
    "terminal": "utilities-terminal-symbolic",
    # 介面
    "doc.viewfinder": "document-open-symbolic",
    "cursorarrow.click.2": "input-mouse-symbolic",
    "checkmark.circle.fill": "emblem-ok-symbolic",
}


def icon_name(sf_symbol: str | None) -> str:
    if not sf_symbol:
        return FALLBACK
    return SF_TO_FREEDESKTOP.get(sf_symbol, FALLBACK)
