"""萬用轉換頁的資料模型（對應原版 ConverterModel），不需要畫面。

轉換引擎（targets_for／unavailable）由另一個工作提供；還沒裝時會丟 NotImplementedError，
這裡記成 engine_missing，介面顯示「轉換引擎尚未安裝」。分析在背景執行緒做（analyze_path），
結果交回主執行緒用 apply_analysis 套用；所有修改都換成新的 tuple／dataclass。
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from ..core import backends, converter, filetypes
from ..core.converter import OPTION_DEFAULTS, Target, Unavailable
from ..core.converter.formats import FORMAT_CATEGORIES, LOSSY_IMAGE_IDS, FormatCategory
from ..core.result import format_size

LOG = logging.getLogger(__name__)

SECTIONS = ("quality", "size", "pdf_dpi", "video_size", "gif", "frame", "bitrate", "encoding")
SECTION_TITLES: dict[str, tuple[str, str]] = {
    "quality": ("品質", "適用於 JPEG、HEIC、AVIF、JPEG 2000；數值越低檔案越小。"),
    "size": ("尺寸", "只會縮小、不會放大，並保持長寬比例。"),
    "pdf_dpi": ("PDF 解析度", "每一頁輸出成一張圖片；多頁時會放進同名資料夾。"),
    "video_size": ("解析度", "以短邊計算（720p = 短邊 720 像素），比原始小時才會縮小。"),
    "gif": ("GIF 動畫", "GIF 檔案通常很大，建議 10–15 fps、寬度 480 px 以內。"),
    "frame": ("擷取時間點", "自動：取第 1 秒（短片取中間）的畫面。"),
    "bitrate": ("位元率", "AAC 192 kbps 已接近 CD 音質；無損格式不受此設定影響。"),
    "encoding": ("來源編碼", "若轉出的文字是亂碼，請手動指定原始檔的編碼。"),
}
SIZE_CHOICES: tuple[tuple[int | None, str], ...] = (
    (None, "原始大小"),
    (4096, "最長邊 4096 px"),
    (2048, "最長邊 2048 px"),
    (1024, "最長邊 1024 px"),
    (512, "最長邊 512 px"),
)
DPI_CHOICES = ((72, "72 DPI（螢幕）"), (150, "150 DPI（標準）"), (300, "300 DPI（列印）"))
VIDEO_SIZES = ((0, "原始大小"), (480, "480p"), (720, "720p"), (1080, "1080p"), (2160, "4K"))
GIF_FPS = (5, 8, 10, 12, 15, 20, 24)
GIF_WIDTHS = (240, 320, 480, 640, 800, 1080)
GIF_SECONDS = (3, 5, 10, 15, 30, 60)
BITRATES = ((128000, "128 kbps"), (192000, "192 kbps"), (256000, "256 kbps"), (320000, "320 kbps"))
ENCODINGS = (
    ("auto", "自動偵測"),
    ("utf-8", "UTF-8"),
    ("utf-16", "UTF-16"),
    ("big5", "Big5（繁體中文）"),
    ("gb18030", "GB18030（簡體中文）"),
    ("shift_jis", "Shift-JIS（日文）"),
    ("euc-kr", "EUC-KR（韓文）"),
    ("cp1252", "Windows-1252（西歐）"),
    ("mac_roman", "Mac Roman"),
)
AUDIO_BITRATE_IDS = ("m4a", "aac", "mp3", "opus")
PROGRESS_STEP = 0.01


# ---------------------------------------------------------------- 分析


@dataclass(frozen=True)
class Analysis:
    targets: tuple[Target, ...]
    unavailable: tuple[Unavailable, ...]
    kind: str
    detail: str | None
    is_image: bool = False
    is_pdf: bool = False
    is_video: bool = False
    is_audio: bool = False
    is_textual: bool = False
    engine_missing: bool = False


def _flags(mime: str) -> dict[str, bool]:
    return {
        "is_image": mime.startswith("image/"),
        "is_pdf": mime == "application/pdf",
        "is_video": mime.startswith("video/"),
        "is_audio": mime.startswith("audio/"),
        "is_textual": filetypes.is_text_mime(mime),
    }


def analyze_path(path: Path) -> Analysis:
    """在背景執行緒呼叫：問轉換引擎能轉成什麼、哪些做不到。"""
    ref = filetypes.file_ref(path)
    kind = "資料夾" if ref.is_dir else filetypes.describe(ref.mime)
    flags = {} if ref.is_dir else _flags(ref.mime)
    try:
        targets = tuple(converter.targets_for(path))
        unavailable = tuple(converter.unavailable(path))
    except NotImplementedError:
        return Analysis((), (), kind, None, engine_missing=True, **flags)
    except Exception:  # noqa: BLE001 — 單一檔案分析失敗只讓它沒有可用格式
        LOG.exception("分析檔案失敗：%s", path)
        return Analysis((), (), kind, None, **flags)
    return Analysis(targets, unavailable, kind, None, **flags)


# ---------------------------------------------------------------- 工作


@dataclass(frozen=True)
class JobStatus:
    kind: str = "idle"  # idle queued running done failed cancelled
    fraction: float = 0.0
    outputs: tuple[Path, ...] = ()
    error: str | None = None

    @property
    def finished(self) -> bool:
        return self.kind in ("done", "failed", "cancelled")


@dataclass(frozen=True)
class ConvJob:
    uid: int
    path: Path
    name: str
    size_text: str
    kind: str = ""
    detail: str | None = None
    analyzing: bool = True
    targets: tuple[Target, ...] = ()
    unavailable: tuple[Unavailable, ...] = ()
    target_id: str | None = None
    status: JobStatus = field(default_factory=JobStatus)
    is_image: bool = False
    is_pdf: bool = False
    is_video: bool = False
    is_audio: bool = False
    is_textual: bool = False

    @property
    def target(self) -> Target | None:
        return next((t for t in self.targets if t.id == self.target_id), None)

    @property
    def mergeable(self) -> bool:
        return self.is_image or self.is_pdf


@dataclass(frozen=True)
class Banner:
    message: str
    paths: tuple[Path, ...] = ()
    is_error: bool = False


def _make_job(uid: int, path: Path) -> ConvJob:
    if path.is_dir():
        try:
            count = len(os.listdir(path))
        except OSError:
            count = 0
        return ConvJob(uid, path, path.name, "", "資料夾", f"{count} 個項目")
    try:
        size = format_size(path.stat().st_size)
    except OSError:
        size = ""
    return ConvJob(uid, path, path.name, size)


def _sections_for(job: ConvJob) -> set[str]:
    target = job.target
    if target is None:
        return set()
    fid, cat = target.id, target.format.category.id
    image_out = cat == "image" and fid != "anim-gif"
    lossy = fid in LOSSY_IMAGE_IDS
    if job.is_video:
        if fid == "anim-gif":
            return {"gif"}
        if fid in ("png", "jpeg"):
            return {"frame", "size"} | ({"quality"} if fid == "jpeg" else set())
        if cat == "video":
            return {"video_size"}
        return {"bitrate"} if cat == "audio" and fid in AUDIO_BITRATE_IDS else set()
    if job.is_pdf and image_out:
        return {"pdf_dpi", "size"} | ({"quality"} if lossy else set())
    out: set[str] = set()
    if job.is_image:
        if (image_out and fid not in ("ico", "icns")) or fid in ("pdf", "mp4", "mov"):
            out.add("size")
        if lossy or fid == "webp":
            out.add("quality")
    if job.is_audio and fid in AUDIO_BITRATE_IDS:
        out.add("bitrate")
    if job.is_textual and cat in ("text", "data", "document"):
        out.add("encoding")
    return out


class ConverterModel:
    """轉換清單、選取、格式、選項、輸出位置與執行狀態。"""

    def __init__(
        self,
        settings_loader: Callable[[], dict[str, Any]] | None = None,
        settings_saver: Callable[[dict[str, Any]], Any] | None = None,
    ) -> None:
        from ..core import settings  # noqa: PLC0415 — 測試可以換掉讀寫

        loaded = (settings_loader or settings.load)()
        self._save = settings_saver or settings.save
        self.jobs: tuple[ConvJob, ...] = ()
        self.selection: frozenset[int] = frozenset()
        self.options: dict[str, Any] = dict(OPTION_DEFAULTS)
        mode = loaded.get("converter.output_mode", "same")
        self.output_mode = mode if mode in ("same", "folder") else "same"
        folder = str(loaded.get("converter.output_folder") or "")
        self.output_folder = Path(folder) if folder and Path(folder).is_dir() else None
        self.running = False
        self.banner: Banner | None = None
        self.engine_missing = False
        self._next_uid = 1
        self._last_chosen: str | None = None
        self._run: dict[str, Any] = {}

    # ------------------------------------------------------------ 加入與移除

    def job(self, uid: int) -> ConvJob | None:
        return next((j for j in self.jobs if j.uid == uid), None)

    def _update(self, uid: int, **changes: Any) -> None:
        self.jobs = tuple(replace(j, **changes) if j.uid == uid else j for j in self.jobs)

    def add(self, paths: Sequence[Path]) -> list[ConvJob]:
        known = {os.path.normpath(j.path) for j in self.jobs}
        added: list[ConvJob] = []
        for raw in paths:
            p = Path(os.path.normpath(Path(raw).absolute()))
            if str(p) in known or not p.exists():
                continue
            known.add(str(p))
            added.append(_make_job(self._next_uid, p))
            self._next_uid += 1
        if added:
            self.jobs = (*self.jobs, *added)
            self.selection = frozenset(j.uid for j in added)
            self.banner = None
        return added

    def apply_analysis(self, uid: int, a: Analysis) -> None:
        job = self.job(uid)
        if job is None:
            return
        ids = {t.id for t in a.targets}
        target = job.target_id if job.target_id in ids else None
        if target is None and self._last_chosen in ids:
            target = self._last_chosen
        self._update(
            uid,
            analyzing=False,
            targets=a.targets,
            unavailable=a.unavailable,
            kind=a.kind or job.kind,
            detail=a.detail if a.detail is not None else job.detail,
            target_id=target,
            is_image=a.is_image,
            is_pdf=a.is_pdf,
            is_video=a.is_video,
            is_audio=a.is_audio,
            is_textual=a.is_textual,
        )
        if a.engine_missing:
            self.engine_missing = True

    def remove(self, uids: Sequence[int]) -> None:
        drop = {u for u in uids if (j := self.job(u)) and j.status.kind != "running"}
        self.jobs = tuple(j for j in self.jobs if j.uid not in drop)
        self.selection = self.selection - drop

    def remove_selected(self) -> None:
        self.remove(list(self.selection))

    def remove_all(self) -> None:
        if self.running:
            return
        self.jobs, self.selection, self.banner = (), frozenset(), None

    def clear_finished(self) -> None:
        self.remove([j.uid for j in self.jobs if j.status.kind == "done"])

    # ------------------------------------------------------------ 選取與範圍

    def select(self, uid: int, extend: bool) -> None:
        if extend:
            self.selection = self.selection ^ {uid}
        else:
            self.selection = frozenset({uid})

    def select_all(self) -> None:
        self.selection = frozenset(j.uid for j in self.jobs)

    def scope_jobs(self) -> list[ConvJob]:
        picked = [j for j in self.jobs if j.uid in self.selection]
        return picked or list(self.jobs)

    def scope_description(self) -> str:
        picked = [j for j in self.jobs if j.uid in self.selection]
        if not picked or len(picked) == len(self.jobs):
            return "這個檔案" if len(self.jobs) == 1 else f"全部 {len(self.jobs)} 個檔案"
        return f"「{picked[0].name}」" if len(picked) == 1 else f"選取的 {len(picked)} 個檔案"

    def analyzing_scope(self) -> bool:
        return any(j.analyzing for j in self.scope_jobs())

    def common_targets(self) -> list[Target]:
        lists = [j.targets for j in self.scope_jobs() if not j.analyzing]
        if not lists:
            return []
        rest = [{t.id for t in lst} for lst in lists[1:]]
        return [t for t in lists[0] if all(t.id in ids for ids in rest)]

    def grouped_targets(self) -> list[tuple[FormatCategory, list[Target]]]:
        common = self.common_targets()
        out = []
        for cat in sorted(FORMAT_CATEGORIES, key=lambda c: c.order):
            lst = [t for t in common if t.format.category.id == cat.id]
            if lst:
                out.append((cat, lst))
        return out

    def scope_is_mixed(self) -> bool:
        lists = [frozenset(t.id for t in j.targets) for j in self.scope_jobs() if not j.analyzing]
        return bool(lists) and any(s != lists[0] for s in lists)

    def unavailable_hints(self) -> list[Unavailable]:
        seen: set[tuple[str, str]] = set()
        out: list[Unavailable] = []
        for job in self.scope_jobs():
            for u in job.unavailable:
                if (u.formats, u.reason) not in seen:
                    seen.add((u.formats, u.reason))
                    out.append(u)
        return out

    def selection_state(self, format_id: str) -> tuple[bool, bool]:
        scope = self.scope_jobs()
        if not scope:
            return False, False
        count = sum(1 for j in scope if j.target_id == format_id)
        return count == len(scope), count > 0

    def choose(self, format_id: str) -> None:
        """為範圍內的檔案指定格式；全部都已經是這個格式時取消。"""
        all_set, _some = self.selection_state(format_id)
        ids = {j.uid for j in self.scope_jobs()}
        new_target = None if all_set else format_id
        jobs = []
        for j in self.jobs:
            if j.uid in ids and j.status.kind not in ("running", "queued"):
                status = JobStatus() if j.status.finished else j.status
                j = replace(j, target_id=new_target, status=status)
            jobs.append(j)
        self.jobs = tuple(jobs)
        self._last_chosen = new_target
        self.banner = None

    # ------------------------------------------------------------ 選項

    def relevant_sections(self) -> list[str]:
        found: set[str] = set()
        for job in self.scope_jobs():
            found |= _sections_for(job)
        return [s for s in SECTIONS if s in found]

    def detected_encoding(self) -> str | None:
        details = [j.detail for j in self.scope_jobs() if j.is_textual and j.detail]
        if not details:
            return None
        return details[0] if all(d == details[0] for d in details) else "多種編碼"

    def set_option(self, key: str, value: Any) -> None:
        if key not in OPTION_DEFAULTS:
            raise KeyError(key)
        self.options = {**self.options, key: value}

    # ------------------------------------------------------------ 輸出位置

    def set_output_mode(self, mode: str) -> None:
        if mode not in ("same", "folder"):
            raise ValueError(mode)
        self.output_mode = mode
        self._persist({"converter.output_mode": mode})

    def set_output_folder(self, folder: Path) -> None:
        self.output_folder = Path(folder)
        self.output_mode = "folder"
        self._persist(
            {"converter.output_folder": str(self.output_folder), "converter.output_mode": "folder"}
        )

    def _persist(self, changes: dict[str, Any]) -> None:
        try:
            self._save(changes)
        except (OSError, KeyError, TypeError):
            LOG.exception("無法儲存轉換設定")

    def needs_folder(self) -> bool:
        return self.output_mode == "folder" and self.output_folder is None

    def output_folder_spec(self) -> str | None:
        """JobSpec 的 output_folder：None＝與原始檔相同。"""
        if self.output_mode == "folder" and self.output_folder is not None:
            return str(self.output_folder)
        return None

    # ------------------------------------------------------------ 合併

    def mergeable_jobs(self) -> list[ConvJob]:
        return [j for j in self.jobs if not j.analyzing and j.mergeable]

    def merge_finished(self, result: dict[str, Any], count: int) -> None:
        outputs = [Path(p) for p in result.get("outputs") or []]
        if result.get("error") or not outputs:
            reason = result.get("error") or "沒有產生檔案"
            self.banner = Banner(f"合併失敗：{reason}", (), True)
        else:
            self.banner = Banner(f"已將 {count} 個檔案合併成「{outputs[0].name}」", tuple(outputs))

    # ------------------------------------------------------------ 轉換

    def ready_jobs(self) -> list[ConvJob]:
        return [
            j
            for j in self.jobs
            if j.target is not None
            and not j.analyzing
            and j.status.kind in ("idle", "failed", "cancelled")
        ]

    def finished_count(self) -> int:
        return sum(1 for j in self.jobs if j.status.kind == "done")

    def convert_title(self) -> str:
        n = len(self.ready_jobs())
        return "轉換" if n <= 1 else f"轉換 {n} 個檔案"

    def overall_progress(self) -> float | None:
        active = [j for j in self.jobs if j.status.kind in ("queued", "running")]
        if not self.running or not active:
            return None
        return sum(j.status.fraction for j in active) / len(active)

    def begin_convert(self) -> list[int]:
        ids = [j.uid for j in self.ready_jobs()]
        if self.running or not ids:
            return []
        for uid in ids:
            self._update(uid, status=JobStatus("queued"))
        self.running = True
        self.banner = None
        self._run = {"ids": ids, "ok": 0, "failed": 0, "cancelled": False}
        return ids

    def next_queued(self) -> int | None:
        if not self._run or self._run["cancelled"]:
            return None
        for uid in self._run["ids"]:
            job = self.job(uid)
            if job is not None and job.status.kind == "queued":
                return uid
        return None

    def mark_running(self, uid: int) -> None:
        self._update(uid, status=JobStatus("running", 0.0))

    def set_progress(self, uid: int, fraction: float) -> None:
        job = self.job(uid)
        if job is None or job.status.kind != "running":
            return
        value = min(1.0, max(0.0, fraction))
        if value - job.status.fraction >= PROGRESS_STEP or value >= 1:
            self._update(uid, status=JobStatus("running", value))

    def finish(self, uid: int, result: dict[str, Any]) -> None:
        if result.get("cancelled"):
            self._update(uid, status=JobStatus("cancelled"))
        elif result.get("error"):
            self._update(uid, status=JobStatus("failed", error=str(result["error"])))
            self._run["failed"] = self._run.get("failed", 0) + 1
        else:
            outs = tuple(Path(p) for p in result.get("outputs") or [])
            self._update(uid, status=JobStatus("done", 1.0, outs))
            self._run["ok"] = self._run.get("ok", 0) + 1

    def cancel_run(self) -> None:
        if self._run:
            self._run["cancelled"] = True

    def end_convert(self) -> None:
        self.jobs = tuple(
            replace(j, status=JobStatus()) if j.status.kind == "queued" else j for j in self.jobs
        )
        self.running = False
        ok, failed = self._run.get("ok", 0), self._run.get("failed", 0)
        if ok + failed > 1:
            ids = set(self._run.get("ids", []))
            outs = tuple(p for j in self.jobs if j.uid in ids for p in j.status.outputs)
            msg = (
                f"全部完成，共 {ok} 個檔案。" if failed == 0 else f"完成 {ok} 個，{failed} 個失敗。"
            )
            self.banner = Banner(msg, outs, failed > 0)
        self._run = {}


# ---------------------------------------------------------------- 顯示文字


def quality_text(q: float) -> str:
    return f"{int(round(q * 100))}%"


def format_tooltip(target: Target) -> str:
    name = target.format.display_name
    if target.backend and target.backend in backends.BACKENDS:
        return f"轉換成 {name}（使用 {backends.BACKENDS[target.backend].title}）"
    return f"轉換成 {name}"


def status_text(status: JobStatus) -> str:
    if status.kind == "queued":
        return "等待中…"
    if status.kind == "running":
        return f"{int(status.fraction * 100)}%" if status.fraction > 0 else "轉換中…"
    if status.kind == "done":
        outs = status.outputs
        return f"完成：{outs[0].name}" if len(outs) == 1 else f"完成：{len(outs)} 個項目"
    if status.kind == "failed":
        return status.error or ""
    if status.kind == "cancelled":
        return "已取消"
    return ""
