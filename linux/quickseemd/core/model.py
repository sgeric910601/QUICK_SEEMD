"""工具框架（對應原版 ToolModel.swift）。

- Category：分類與顏色
- ToolDefinition：一個工具的描述（名稱、接受的檔案、選項、執行函式、右鍵預設值）
- ToolContext：執行時的進度、取消、輸出位置與檔名、交付輸出檔
- per_file：逐檔處理並平均分配進度
"""

from __future__ import annotations

import os
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import errors, naming, process
from .filetypes import FileRef, conforms_any, file_ref
from .options import Info, Option, Values
from .options import defaults as option_defaults

RGB = tuple[float, float, float]

# 原版的配色（sRGB 0–1）
TINTS: dict[str, RGB] = {
    "pdf": (0.89, 0.20, 0.18),
    "word": (0.11, 0.36, 0.74),
    "excel": (0.11, 0.50, 0.29),
    "powerpoint": (0.83, 0.32, 0.16),
    "image": (0.95, 0.62, 0.10),
    "video": (0.49, 0.33, 0.95),
    "audio": (0.86, 0.24, 0.55),
    "teal": (0.10, 0.60, 0.62),
    "slate": (0.36, 0.42, 0.52),
}


@dataclass(frozen=True)
class Category:
    id: str
    title: str
    short_title: str
    icon: str
    tint: RGB
    subtitle: str


PDF = Category(
    "pdf",
    "PDF 工具",
    "PDF",
    "doc.richtext",
    TINTS["pdf"],
    "合併、拆分、壓縮、轉換、加浮水印、保護……處理 PDF 需要的一切。",
)
OFFICE = Category(
    "office",
    "Office 文件",
    "Office",
    "doc.text",
    TINTS["word"],
    "Word、Excel、PowerPoint 與 PDF 互轉，使用 LibreOffice 轉換引擎。",
)
IMAGE = Category(
    "image",
    "圖片工具",
    "圖片",
    "photo",
    TINTS["image"],
    "轉檔、壓縮、調整大小、裁剪、去背、浮水印，批次處理大量圖片。",
)
MEDIA = Category(
    "media",
    "影音工具",
    "影音",
    "film",
    TINTS["video"],
    "轉換、壓縮、剪輯影片，做 GIF、擷取音訊，音訊格式互轉。",
)
OTHER = Category(
    "other",
    "其他工具",
    "其他",
    "square.grid.2x2",
    TINTS["teal"],
    "萬用格式轉換、壓縮檔與文字編碼等實用工具。",
)
CATEGORIES: tuple[Category, ...] = (PDF, OFFICE, IMAGE, MEDIA, OTHER)
ALL_SUBTITLE = (
    "PDF、Word、Excel、PowerPoint、圖片、影片——合併、壓縮、轉檔一次搞定，全部在這台電腦上離線完成。"
)

Reporter = Callable[[float, "str | None"], None]
RunFunc = Callable[[list[Path], Values, "ToolContext"], list[Path]]


@dataclass(frozen=True)
class ToolDefinition:
    """一個工具。run 在工作程序裡執行，不可以碰任何介面函式庫。"""

    id: str
    category: Category
    title: str
    subtitle: str
    icon: str  # 原版 SF Symbol 名稱
    run: RunFunc
    input_types: tuple[str, ...] = ()  # filetypes 的類型描述；空＝全部
    input_label: str = "檔案"
    allows_multiple: bool = True
    minimum_files: int = 1
    reorderable: bool = False
    action_title: str | Callable[[Values, Info], str] = "開始"
    keywords: tuple[str, ...] = ()
    options: tuple[Option, ...] = ()
    tint: RGB | None = None
    badge: str | None = None
    # 右鍵與輪盤的預設值：動作代碼 → 覆蓋的選項值（未列出的用 options 預設值）
    presets: dict[str, Values] = field(default_factory=dict)
    # 讀檔案資訊給選項畫面用（頁數、像素…）；在背景執行緒呼叫，要快、唯讀
    inspect: Callable[[list[Path]], Info] | None = None
    # 檔案變動時要預先填入的選項值（例如以第一張圖的大小填入寬高）
    prefill: Callable[[list[Path], Info], Values] | None = None
    # 回傳中文原因＝不能執行（顯示在執行按鈕上方）
    validate: Callable[[list[Path], Values, Info], str | None] | None = None
    # 檔案區上方的橘色提示（例如「已略過 2 個不支援的檔案」以外的工具專屬提示）
    files_note: Callable[[list[Path], Values, Info], str | None] | None = None
    custom_page: str | None = None  # "converter"：整頁換成萬用轉換
    custom_view: str | None = None  # "pdf-organize" "image-crop" "media-trim" "audio-trim"
    requires: tuple[str, ...] = ()  # 需要的外部工具（backends 的鍵）
    # 使用者改了某個選項後的連動（例如換形狀就重設留白、鎖定比例時同步寬高）：
    # adjust(目前的值, 剛改的鍵, info) → 要一併改的值；介面層合併後重畫
    adjust: Callable[[Values, str, Info], Values] | None = None

    # ------------------------------------------------------------ 查詢

    @property
    def tint_rgb(self) -> RGB:
        return self.tint or self.category.tint

    def defaults(self) -> Values:
        return option_defaults(self.options)

    def values_for_preset(self, action_id: str) -> Values:
        values = self.defaults()
        values.update(self.presets.get(action_id, {}))
        return values

    def option(self, key: str) -> Option | None:
        for opt in self.options:
            if opt.key == key:
                return opt
        return None

    def title_for_action(self, values: Values, info: Info) -> str:
        if callable(self.action_title):
            return self.action_title(values, info)
        return self.action_title

    def accepts_ref(self, ref: FileRef) -> bool:
        return conforms_any(ref, self.input_types)

    def accepts(self, path: str | os.PathLike[str]) -> bool:
        return self.accepts_ref(file_ref(path))

    def accepts_all(self, refs: Sequence[FileRef]) -> bool:
        if not refs:
            return False
        if len(refs) > 1 and not self.allows_multiple:
            return False
        return all(self.accepts_ref(r) for r in refs)

    def matches(self, query: str) -> bool:
        q = query.strip().lower()
        if not q:
            return True
        hay = [self.title, self.subtitle, self.input_label, *self.keywords]
        return any(q in h.lower() for h in hay)

    def check_files(self, files: Sequence[Path]) -> str | None:
        """框架層的檢查（順序照原版）。"""
        if len(files) < self.minimum_files:
            return f"至少需要 {self.minimum_files} 個檔案"
        if not self.allows_multiple and len(files) > 1:
            return "這個工具一次只能處理 1 個檔案"
        return None


# ---------------------------------------------------------------- 執行環境


class _Shared:
    """同一個工作所有分段共用的狀態。"""

    def __init__(self, reporter: Reporter | None, cancel: threading.Event) -> None:
        self.reporter = reporter
        self.cancel = cancel
        self.last_message: str | None = None
        self.fraction = 0.0
        self.work_dirs: list[Path] = []
        self.skipped: list[str] = []
        self.clipboard: str | None = None
        self.lock = threading.Lock()


class ToolContext:
    """執行中的工具用來回報進度、檢查取消、決定輸出檔名並交付結果。"""

    def __init__(
        self,
        output_folder: Path | None = None,
        reporter: Reporter | None = None,
        cancel: threading.Event | None = None,
        *,
        _shared: _Shared | None = None,
        _lower: float = 0.0,
        _upper: float = 1.0,
    ) -> None:
        self.output_folder = Path(output_folder) if output_folder else None
        self._shared = _shared or _Shared(reporter, cancel or threading.Event())
        self._lower = _lower
        self._upper = _upper

    # ------------------------------------------------------------ 進度與取消

    @property
    def cancel_event(self) -> threading.Event:
        return self._shared.cancel

    @property
    def last_message(self) -> str | None:
        return self._shared.last_message

    @property
    def skipped(self) -> list[str]:
        return list(self._shared.skipped)

    def progress(self, fraction: float, message: str | None = None) -> None:
        f = min(max(fraction, 0.0), 1.0)
        overall = self._lower + (self._upper - self._lower) * f
        sh = self._shared
        with sh.lock:
            sh.fraction = max(sh.fraction, overall)
            if message is not None:
                sh.last_message = message
            frac, msg = sh.fraction, sh.last_message
        if sh.reporter is not None:
            sh.reporter(frac, msg)

    def segment(self, index: int, count: int) -> ToolContext:
        width = (self._upper - self._lower) / max(count, 1)
        lower = self._lower + width * index
        return ToolContext(
            self.output_folder, _shared=self._shared, _lower=lower, _upper=lower + width
        )

    def check_cancelled(self) -> None:
        if self._shared.cancel.is_set():
            raise errors.Cancelled()

    def note_skip(self, message: str) -> None:
        with self._shared.lock:
            self._shared.skipped.append(message)

    @property
    def clipboard(self) -> str | None:
        return self._shared.clipboard

    def set_clipboard(self, text: str) -> None:
        """要放進剪貼簿的文字（工作程序不能持有剪貼簿，由介面程序在完成後拷貝）。"""
        with self._shared.lock:
            self._shared.clipboard = text

    # ------------------------------------------------------------ 輸出

    def output_directory(self, input_path: Path) -> Path:
        return self.output_folder or Path(input_path).resolve().parent

    def output_name(self, input_path: Path, tag: str | None, ext: str) -> str:
        return naming.output_name(input_path, tag, ext)

    def make_work_dir(self, near: Path | None = None) -> Path:
        """暫存資料夾（和輸出位置同一顆磁碟）；工作結束時由執行器清掉。"""
        target = Path(near) if near else (self.output_folder or Path.home())
        if target.is_file():
            target = target.parent
        d = naming.make_work_dir(target)
        with self._shared.lock:
            self._shared.work_dirs.append(d)
        return d

    def deliver(self, temp: Path, name: str, for_input: Path) -> Path:
        """把暫存的輸出（檔案或資料夾）以 name 放到輸出位置，同名加編號、絕不覆蓋。"""
        return naming.commit(Path(temp), self.output_directory(for_input), name)

    def cleanup(self) -> None:
        with self._shared.lock:
            dirs, self._shared.work_dirs = self._shared.work_dirs, []
        for d in dirs:
            naming.remove_work_dir(d)

    # ------------------------------------------------------------ 外部程式

    def run_process(self, args: Sequence[str], tool_name: str, **kwargs: Any) -> process.Result:
        """執行外部程式（失敗時丟出含最後 3 行訊息的中文錯誤；取消時結束整個程序群組）。"""
        return process.run_checked(list(args), tool_name, cancel=self.cancel_event, **kwargs)


def per_file(
    files: Sequence[Path],
    ctx: ToolContext,
    body: Callable[[Path, ToolContext], list[Path]],
    *,
    skip_errors: bool | None = None,
) -> list[Path]:
    """逐檔執行 body，進度平均分配。

    skip_errors（預設：多於 1 個檔案時開啟）時，body 丟出 SkipFile 會略過該檔並記下原因；
    只有 1 個檔案時照常丟出錯誤（對應原版 ITSkip）。
    """
    outputs: list[Path] = []
    n = len(files)
    skipping = (n > 1) if skip_errors is None else skip_errors
    for i, f in enumerate(files):
        ctx.check_cancelled()
        sub = ctx.segment(i, n)
        sub.progress(0, f"處理中：{Path(f).name}")
        try:
            outputs.extend(body(Path(f), sub))
        except errors.SkipFile as exc:
            if not skipping:
                raise
            ctx.note_skip(str(exc))
        sub.progress(1)
    return outputs


@dataclass
class RunResult:
    action: str
    outputs: list[Path] = field(default_factory=list)
    summary: str | None = None
    seconds: float = 0.0
    error: str | None = None
    skipped: list[str] = field(default_factory=list)
    clipboard: str | None = None  # 介面程序完成後要拷貝到剪貼簿的文字

    def to_json(self) -> dict[str, Any]:
        """原版 --qs-run 的結果格式（失敗時用 error 取代 outputs）；另加 clipboard。"""
        out: dict[str, Any] = {"action": self.action, "seconds": round(self.seconds, 3)}
        if self.error is not None:
            out["error"] = self.error
        else:
            out["outputs"] = [str(p) for p in self.outputs]
            if self.clipboard is not None:
                out["clipboard"] = self.clipboard
        if self.summary:
            out["summary"] = self.summary
        return out
