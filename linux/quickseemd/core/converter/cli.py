"""萬用轉換的命令列（對應原版 qs-convert）：quickseemd convert …

  quickseemd convert --list <檔案>                         列出可轉換的格式
  quickseemd convert <檔案> <格式代碼> <輸出資料夾> [選項]    轉換並印出輸出路徑
  quickseemd convert --merge-pdf <輸出資料夾> <檔案…>        將圖片／PDF 合併成一份 PDF
  quickseemd convert --backends                            列出外部引擎偵測結果
  quickseemd convert --formats                             列出所有格式代碼
"""

from __future__ import annotations

import logging
import os
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

from .. import backends, errors
from . import FORMATS, OPTION_DEFAULTS, capabilities, engine
from .plan import EXTERNAL_TITLES

log = logging.getLogger(__name__)

USAGE = """用法：
  quickseemd convert --list <檔案>
  quickseemd convert <檔案> <格式代碼> <輸出資料夾> [--quality q] [--max-size px] [--dpi n] [--video-size 480|720|1080|2160]
             [--frame-time s] [--gif-fps n] [--gif-width px] [--gif-seconds s] [--bitrate bps] [--encoding name] [--progress]
  quickseemd convert --merge-pdf <輸出資料夾> <檔案…>
  quickseemd convert --backends
  quickseemd convert --formats"""  # noqa: E501
VIDEO_SIZES = (0, 480, 720, 1080, 2160)
EXTRA_TOOLS = ("tesseract", "heif-enc", "heif-convert", "pdftocairo", "pdftotext", "bsdtar")


class UsageError(Exception):
    """參數不正確：印出用法並以 2 結束。"""


def _out(text: str = "") -> None:
    sys.stdout.write(text + "\n")


def _err(text: str) -> None:
    sys.stderr.write(text + "\n")
    sys.stderr.flush()


def _abs(raw: str) -> Path:
    return Path(os.path.abspath(raw))


# ---------------------------------------------------------------- 選項


def _float(value: str, default: Any) -> Any:
    try:
        return float(value)
    except ValueError:
        return default


def _int(value: str, default: Any) -> Any:
    try:
        return int(value)
    except ValueError:
        return default


_PARSERS: dict[str, tuple[str, Callable[[str], Any]]] = {
    "--quality": ("image_quality", lambda v: _float(v, OPTION_DEFAULTS["image_quality"])),
    "--max-size": ("image_max_pixel", lambda v: _int(v, None)),
    "--dpi": ("pdf_dpi", lambda v: _float(v, OPTION_DEFAULTS["pdf_dpi"])),
    "--video-size": ("video_size", lambda v: _int(v, 0) if _int(v, 0) in VIDEO_SIZES else 0),
    "--frame-time": ("frame_time", lambda v: _float(v, None)),
    "--gif-fps": ("gif_fps", lambda v: _int(v, OPTION_DEFAULTS["gif_fps"])),
    "--gif-width": ("gif_width", lambda v: _int(v, OPTION_DEFAULTS["gif_width"])),
    "--gif-seconds": ("gif_seconds", lambda v: _float(v, OPTION_DEFAULTS["gif_seconds"])),
    "--bitrate": ("audio_bitrate", lambda v: _int(v, OPTION_DEFAULTS["audio_bitrate"])),
    "--encoding": ("source_encoding", lambda v: v or "auto"),
}


def parse_options(args: list[str]) -> tuple[dict[str, Any], bool]:
    """回傳（轉換選項, 是否顯示進度）；未知選項丟 UsageError。"""
    options: dict[str, Any] = {}
    show_progress = False
    i = 0
    while i < len(args):
        key = args[i]
        if key == "--progress":
            show_progress = True
            i += 1
            continue
        if key not in _PARSERS:
            _err(f"未知選項：{key}")
            raise UsageError
        value = args[i + 1] if i + 1 < len(args) else ""
        name, parse = _PARSERS[key]
        options[name] = parse(value)
        i += 2
    return options, show_progress


class ProgressPrinter:
    """進度每跨過 10% 印一次到 stderr。"""

    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.last = -1

    def __call__(self, fraction: float, _message: str | None) -> None:
        bucket = int(fraction * 10)
        with self.lock:
            if bucket <= self.last:
                return
            self.last = bucket
        _err(f"進度 {int(fraction * 100)}%")


# ---------------------------------------------------------------- 子命令


def cmd_formats() -> int:
    for f in FORMATS:
        ext = f".{f.ext}" if f.ext else ".(同來源)"
        _out(f"{f.id[:12]:<12} {f.category.id[:9]:<9} {ext}  {f.display_name}")
    return 0


def cmd_backends() -> int:
    for key in ("ffmpeg", "pandoc", "soffice", "magick", *EXTRA_TOOLS):
        title = EXTERNAL_TITLES.get(key, key)
        path = backends.find(key)
        _out(f"{title}: {path or f'未安裝（{backends.install_hint(key)}）'}")
    weasy = "可用" if backends.has_module("weasyprint") else "未安裝（sudo apt install weasyprint）"
    _out(f"WeasyPrint: {weasy}")
    _out(f"圖片可寫入：{' '.join(capabilities.native_image_formats())}")
    _out(f"音訊可輸出：{' '.join(capabilities.native_audio_formats())}")
    _out(f"影片可輸出：{' '.join(capabilities.native_video_formats())}")
    return 0


def cmd_list(raw: str) -> int:
    path = _abs(raw)
    if not path.exists():
        _err(f"找不到檔案：{path}")
        return 1
    profile = engine.analyze(path)
    summary = engine.summary(path, profile)
    detail = f"（{summary.detail}）" if summary.detail else ""
    _out(f"# {path.name} — {summary.kind}{detail}")
    targets = engine.plan(profile)
    if not targets:
        _out("（沒有可用的轉換）")
    for t in targets:
        line = f"{t.id[:12]:<12} {t.format.display_name}"
        if t.note:
            line += f"  — {t.note}"
        if t.backend:
            line += f"  [{EXTERNAL_TITLES.get(t.backend, t.backend)}]"
        _out(line)
    for u in engine.plan_unavailable(profile):
        _out(f"✕ {u.formats}：{u.reason}")
    return 0


def _context(out_dir: Path, reporter: Callable[[float, str | None], None] | None):  # noqa: ANN202
    from ..model import ToolContext  # noqa: PLC0415

    return ToolContext(out_dir, reporter)


def cmd_merge(out_raw: str, inputs: list[str]) -> int:
    ctx = _context(_abs(out_raw), None)
    try:
        result = engine.merge_to_pdf([_abs(p) for p in inputs], ctx, dict(OPTION_DEFAULTS))
    except errors.QSError as exc:
        _err(f"錯誤：{exc}")
        return 1
    finally:
        ctx.cleanup()
    _out(str(result))
    return 0


def cmd_convert(args: list[str]) -> int:
    source, format_id, out_raw = _abs(args[0]), args[1], args[2]
    options, show_progress = parse_options(args[3:])
    started = time.monotonic()
    ctx = _context(_abs(out_raw), ProgressPrinter() if show_progress else None)
    try:
        outputs = engine.convert(source, format_id, ctx, {**OPTION_DEFAULTS, **options})
    except errors.QSError as exc:
        _err(f"錯誤：{exc}")
        return 1
    except Exception as exc:  # noqa: BLE001 — 命令列一律回報成中文錯誤
        log.exception("轉換失敗")
        _err(f"錯誤：{exc}")
        return 1
    finally:
        ctx.cleanup()
    for path in outputs:
        _out(str(path))
    _err(f"完成（{time.monotonic() - started:.2f} 秒）")
    return 0


def _dispatch(argv: list[str]) -> int:
    if not argv:
        raise UsageError
    first = argv[0]
    if first == "--formats":
        return cmd_formats()
    if first == "--backends":
        return cmd_backends()
    if first == "--list":
        if len(argv) < 2:
            raise UsageError
        return cmd_list(argv[1])
    if first == "--merge-pdf":
        if len(argv) < 3:
            raise UsageError
        return cmd_merge(argv[1], argv[2:])
    if len(argv) < 3:
        raise UsageError
    return cmd_convert(argv)


def main(argv: list[str] | None = None) -> int:
    try:
        return _dispatch(list(sys.argv[1:] if argv is None else argv))
    except UsageError:
        _err(USAGE)
        return 2


if __name__ == "__main__":
    sys.exit(main())
