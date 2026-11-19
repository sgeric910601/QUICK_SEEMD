"""字型樣張（Linux 版補充；原版交給 macOS 內建預覽）。

字型檔本身當附件（cid:font-file），以 @font-face 載入；家族名稱用系統的 fc-scan 讀，
讀不到就用檔名。中英文範例句子、字母數字表，與多種字級。
"""

from __future__ import annotations

import logging
import os
from pathlib import Path

from .. import process
from . import Attachment, Document
from .page import Chip, FileInfo, Notice, Page, build, escape

log = logging.getLogger(__name__)

FC_SCAN = "/usr/bin/fc-scan"
MAX_FONT_BYTES = 64 * 1024 * 1024
FC_TIMEOUT = 5.0
SIZES = (12, 18, 24, 36, 48, 72)
SAMPLES = (
    "我能吞下玻璃而不傷身體。永和九年，歲在癸丑，暮春之初。",
    "The quick brown fox jumps over the lazy dog.",
)
GLYPHS = (
    "ABCDEFGHIJKLMNOPQRSTUVWXYZ",
    "abcdefghijklmnopqrstuvwxyz",
    "0123456789 !?&@#%*()[]{}<>/\\",
    "一二三四五六七八九十 繁體中文 ＡＢＣ １２３ 。，、；：「」",
)
FORMATS = {  # 開頭特徵 → （顯示名稱, MIME）
    b"\x00\x01\x00\x00": ("TTF", "font/ttf"),
    b"true": ("TTF", "font/ttf"),
    b"OTTO": ("OTF", "font/otf"),
    b"ttcf": ("TTC", "font/collection"),
    b"wOFF": ("WOFF", "font/woff"),
    b"wOF2": ("WOFF2", "font/woff2"),
}
EXT_FORMATS = {
    "ttf": ("TTF", "font/ttf"),
    "otf": ("OTF", "font/otf"),
    "ttc": ("TTC", "font/collection"),
    "otc": ("TTC", "font/collection"),
    "woff": ("WOFF", "font/woff"),
    "woff2": ("WOFF2", "font/woff2"),
}
CSS = """
@font-face { font-family: "qs-specimen"; src: url(cid:font-file); }
.font { padding: 22px 28px 48px; }
.font .glyphs { font-family: "qs-specimen", var(--sans); font-size: 22px; line-height: 1.5;
  padding: 14px 18px; margin-bottom: 22px; border: 1px solid var(--line); border-radius: 10px;
  background: var(--bg-2); word-break: break-all; }
.font .row { display: grid; grid-template-columns: 52px 1fr; gap: 12px; align-items: baseline;
  padding: 8px 0; border-bottom: 1px solid var(--line); }
.font .size { font: 11px var(--mono); color: var(--fg-3); text-align: right; }
.font .sample { font-family: "qs-specimen", var(--sans); line-height: 1.3; overflow-wrap: anywhere; }
"""


def family_name(path: Path) -> str | None:
    """用 fc-scan 讀字型家族名稱（集合檔取第一個）；工具不在或失敗時回傳 None。"""
    if not os.path.isfile(FC_SCAN):
        return None
    try:
        result = process.run(
            [FC_SCAN, "--format", "%{family}\n", str(path.resolve())], timeout=FC_TIMEOUT
        )
    except Exception:  # noqa: BLE001 — 讀不到名稱就改用檔名，不影響樣張
        log.debug("fc-scan 失敗：%s", path, exc_info=True)
        return None
    if result.returncode != 0:
        return None
    first = result.stdout.strip().split("\n", 1)[0]
    name = first.split(",", 1)[0].strip()
    return name or None


def detect_format(path: Path, ext: str) -> tuple[str, str]:
    try:
        with open(path, "rb") as fh:
            magic = fh.read(4)
    except OSError:
        magic = b""
    return FORMATS.get(magic) or EXT_FORMATS.get(ext) or (ext.upper() or "字型", "font/ttf")


def _body() -> str:
    glyphs = "<br>".join(escape(g) for g in GLYPHS)
    rows = "".join(
        f'<div class="row"><span class="size">{size}px</span>'
        f'<div class="sample" style="font-size:{size}px">{escape(text)}</div></div>'
        for size in SIZES
        for text in SAMPLES
    )
    return f'<div class="font"><div class="glyphs">{glyphs}</div>{rows}</div>'


def render(path: Path) -> Document:
    info = FileInfo.of(path)
    label, mime = detect_format(path, info.ext)
    family = family_name(path) or os.path.splitext(info.name)[0]
    page = Page(kind="字型", size=(860, 680), extra_css=CSS)
    page.chips = [Chip(family), Chip(label)]
    if info.regular and 0 < info.size <= MAX_FONT_BYTES:
        try:
            data = path.read_bytes()
            page.attachments["font-file"] = Attachment("font-file", data, mime)
        except OSError:
            log.debug("讀不到字型檔：%s", path, exc_info=True)
            page.notices.append(Notice("讀不到字型檔，以系統字型顯示", warn=True))
    else:
        page.notices.append(Notice("字型檔太大或無法讀取，以系統字型顯示", warn=True))
    page.body = _body()
    return build(page, info)
