"""影音工具測試共用的小工具（產生測試影片、用 ffprobe 檢查輸出、執行工具）。

這個檔案沒有測試本身，只提供給 test_media_*.py 匯入。
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from quickseemd.core import backends, process, registry
from quickseemd.core.model import ToolContext


def ffmpeg() -> str:
    return backends.require("ffmpeg")


def ffprobe() -> str:
    return backends.require("ffprobe")


def run_ffmpeg(*args: str) -> None:
    process.run_checked([ffmpeg(), "-hide_banner", "-nostdin", "-n", "-v", "error", *args], "ffmpeg")


def probe(path: Path) -> dict[str, Any]:
    """ffprobe 的完整 JSON（格式與所有串流）。"""
    res = process.run_checked(
        [
            ffprobe(),
            "-v",
            "error",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            str(path),
        ],
        "ffprobe",
    )
    return json.loads(res.stdout)


def streams(path: Path, kind: str) -> list[dict[str, Any]]:
    return [s for s in probe(path)["streams"] if s.get("codec_type") == kind]


def video_stream(path: Path) -> dict[str, Any]:
    return streams(path, "video")[0]


def duration(path: Path) -> float:
    return float(probe(path)["format"]["duration"])


def rotation(stream: dict[str, Any]) -> int:
    for item in stream.get("side_data_list", []) or []:
        if "rotation" in item:
            return int(item["rotation"])
    return 0


def count_frames(path: Path) -> int:
    res = process.run_checked(
        [
            ffprobe(),
            "-v",
            "error",
            "-count_frames",
            "-select_streams",
            "v:0",
            "-show_entries",
            "stream=nb_read_frames",
            "-of",
            "csv=p=0",
            str(path),
        ],
        "ffprobe",
    )
    return int(res.stdout.strip().split(",")[0])


def sha(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def make_noisy_video(
    path: Path, seconds: float = 2.0, size=(320, 240), bitrate="2M", audio_bitrate="128k"
) -> Path:
    """高位元率、畫面雜訊多的影片（壓縮測試用，才壓得下來）。"""
    run_ffmpeg(
        "-f",
        "lavfi",
        "-i",
        f"testsrc2=duration={seconds}:size={size[0]}x{size[1]}:rate=25,noise=alls=30:allf=t",
        "-f",
        "lavfi",
        "-i",
        f"sine=frequency=330:duration={seconds}",
        "-c:v",
        "libx264",
        "-b:v",
        bitrate,
        "-minrate",
        bitrate,
        "-maxrate",
        bitrate,
        "-bufsize",
        "1M",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        audio_bitrate,
        "-shortest",
        str(path),
    )
    return path


def make_rotated(src: Path, dest: Path, ccw: int = -90) -> Path:
    """複製一份帶方向標記的影片（不重新編碼）。"""
    process.run_checked(
        [
            ffmpeg(),
            "-hide_banner",
            "-nostdin",
            "-n",
            "-v",
            "error",
            "-display_rotation:v:0",
            str(ccw),
            "-i",
            str(src),
            "-c",
            "copy",
            str(dest),
        ],
        "ffmpeg",
    )
    return dest


def encode_audio(src: Path, dest: Path, *codec_args: str) -> Path:
    run_ffmpeg("-i", str(src), *codec_args, str(dest))
    return dest


def tool(tool_id: str):  # noqa: ANN201 — 測試用
    t = registry.tool(tool_id)
    assert t is not None, f"沒有登記 {tool_id}"
    return t


def run_tool(
    tool_id: str,
    files: list[Path],
    outdir: Path | None,
    preset: str | None = None,
    **values: Any,
) -> tuple[list[Path], ToolContext]:
    """用預設值（或動作預設值）加上覆蓋值執行工具；回傳輸出與 context（看摘要用）。"""
    t = tool(tool_id)
    vals = t.values_for_preset(preset) if preset else t.defaults()
    vals.update(values)
    ctx = ToolContext(outdir)
    try:
        outputs = t.run([Path(f) for f in files], vals, ctx)
    finally:
        ctx.cleanup()
    return outputs, ctx


def validate(tool_id: str, files: list[Path], **values: Any) -> str | None:
    t = tool(tool_id)
    vals = t.defaults()
    vals.update(values)
    info = t.inspect([Path(f) for f in files]) if t.inspect else {}
    return t.validate([Path(f) for f in files], vals, info) if t.validate else None


def note_texts(tool_id: str, files: list[Path], **values: Any) -> list[str]:
    """所有說明（NOTE 與動態 note）目前顯示的文字。"""
    t = tool(tool_id)
    vals = t.defaults()
    vals.update(values)
    info = t.inspect([Path(f) for f in files]) if t.inspect else {}
    out: list[str] = []
    for opt in t.options:
        if not opt.is_visible(vals, info):
            continue
        if opt.note is not None:
            res = opt.note(vals, info)
            if res:
                out.append(res[0])
        elif opt.label and opt.kind.value == "note":
            out.append(opt.label)
    return out
