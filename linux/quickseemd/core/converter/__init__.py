"""萬用格式轉換（對應原版 App/Converter）。

凍結的介面（輪盤、主程式、工作程序都靠它）：
- targets_for(path) -> list[Target]：這個檔案能轉成哪些格式（順序照原版 plan()，同 id 先到先贏）
- common_targets(paths) -> list[Target]：多個檔案共同可轉的格式（順序依第一個檔案）
- unavailable(path) -> list[Unavailable]：目前做不到、需要安裝某工具的提示
- convert(path, format_id, ctx, options) -> list[Path]：轉一個檔案（輸出經 ctx.deliver 交付）
- merge_to_pdf(paths, ctx, options) -> Path：圖片／PDF 依序合成一份
- OPTION_DEFAULTS：轉換選項預設值（鍵名凍結）

實作放在 engine.py 等模組（由萬用轉換的工作負責）；這裡只轉呼叫，未實作時丟 NotImplementedError。
"""

from __future__ import annotations

import importlib
import importlib.util
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .formats import BY_ID, FORMATS, Format, format_by_id  # noqa: F401

# 轉換選項（原版 ConversionOptions；鍵名凍結）
OPTION_DEFAULTS: dict[str, Any] = {
    "image_quality": 0.85,  # 0.3–1.0
    "image_max_pixel": None,  # None（原始）、4096、2048、1024、512
    "pdf_dpi": 150,  # 72、150、300
    "video_size": 0,  # 0＝原始；480、720、1080、2160（短邊上限）
    "frame_time": None,  # None＝自動 min(1, 長度/2)；否則秒數
    "gif_fps": 12,
    "gif_width": 480,
    "gif_seconds": 15,
    "audio_bitrate": 192000,
    "source_encoding": "auto",  # auto、utf-8、utf-16、big5、gb18030、shift_jis、euc-kr、cp1252、mac_roman
}


@dataclass(frozen=True)
class Target:
    format: Format
    route: str  # image、imageToPDF、ocr、animatedToVideo、pdfToImages、pdfToText、document、data、text、audio、video、videoToGIF、videoFrame、archive、external
    note: str | None = None  # 格式按鈕上的小字（例如「每頁一張」）
    backend: str | None = None  # 使用的外部工具（backends 的鍵），內建則為 None

    @property
    def id(self) -> str:
        return self.format.id


@dataclass(frozen=True)
class Unavailable:
    formats: str  # 例如「MP3、OGG、Opus」
    reason: str
    backend: str | None


def _engine():
    if importlib.util.find_spec(__name__ + ".engine") is None:
        raise NotImplementedError("萬用轉換引擎尚未實作")
    return importlib.import_module(__name__ + ".engine")


def targets_for(path: Path) -> list[Target]:
    return _engine().targets_for(Path(path))


def common_targets(paths: Sequence[Path]) -> list[Target]:
    if not paths:
        return []
    first = targets_for(paths[0])
    rest = [{t.id for t in targets_for(p)} for p in paths[1:]]
    return [t for t in first if all(t.id in ids for ids in rest)]


def unavailable(path: Path) -> list[Unavailable]:
    return _engine().unavailable(Path(path))


def convert(
    path: Path, format_id: str, ctx, options: dict[str, Any] | None = None
) -> list[Path]:  # noqa: ANN001
    opts = dict(OPTION_DEFAULTS)
    opts.update(options or {})
    return _engine().convert(Path(path), format_id, ctx, opts)


def merge_to_pdf(
    paths: Sequence[Path], ctx, options: dict[str, Any] | None = None
) -> Path:  # noqa: ANN001
    opts = dict(OPTION_DEFAULTS)
    opts.update(options or {})
    return _engine().merge_to_pdf([Path(p) for p in paths], ctx, opts)
