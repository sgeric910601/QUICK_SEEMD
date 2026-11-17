"""字幕轉換（對應原版 OTSubtitleSession 與 OTSubtitles）。

SRT ⇄ WebVTT，可整體提前或延後。規則照原版：
- 以空行切成區塊，沒有「-->」的區塊（標頭、NOTE、STYLE）丟掉。
- 時間格式 (時:)?分:秒[,.]毫秒；位移後不小於 0，輸出一律補成 HH:MM:SS.mmm（VTT）或 ,mmm（SRT）。
- SRT 輸出去掉結束時間後面的位置設定，並重新編號 1…n；VTT 輸出開頭是「WEBVTT」，不保留 cue 名稱。
- 輸出 UTF-8。副檔名相同時檔名加「-已調整」。
"""

from __future__ import annotations

import math
import re
import unicodedata
from pathlib import Path

from ... import errors
from ...model import OTHER, TINTS, ToolContext, ToolDefinition, per_file
from ...options import Choice, Kind, Option, Values
from . import textcodec

SHIFT_LIMIT = 10.0
_STAMP = re.compile(r"(\d{1,2}:)?(\d{1,2}):(\d{2})[,.](\d{3})")


def shift_label(value: float) -> str:
    v = float(value)
    return "不調整" if round(v, 1) == 0 else f"{v:+.1f} 秒"


OPTIONS: tuple[Option, ...] = (
    Option(
        key="target",
        kind=Kind.CARDS,
        label="轉成",
        default="vtt",
        group="轉成",
        group_icon="captions.bubble",
        choices=(
            Choice("vtt", "WebVTT（.vtt）", "網頁播放器、YouTube、HTML5 影片", "globe"),
            Choice("srt", "SubRip（.srt）", "最常見，播放器與剪輯軟體都支援", "film"),
        ),
    ),
    Option(
        key="shift",
        kind=Kind.SLIDER,
        label="整體提前 / 延後",
        default=0.0,
        minimum=-SHIFT_LIMIT,
        maximum=SHIFT_LIMIT,
        step=0.1,
        display=shift_label,
        group="時間軸",
        group_icon="clock.arrow.2.circlepath",
    ),
)


def _is_space(ch: str) -> bool:
    return ch == "\t" or unicodedata.category(ch) == "Zs"


def _strip_spaces(text: str) -> str:
    """去掉頭尾的空白與 Tab（同原版 .whitespaces，不含換行）。"""
    start, end = 0, len(text)
    while start < end and _is_space(text[start]):
        start += 1
    while end > start and _is_space(text[end - 1]):
        end -= 1
    return text[start:end]


def _format_stamp(match: re.Match[str], shift: float, to_vtt: bool) -> str:
    hours = float(match.group(1)[:-1]) if match.group(1) else 0.0
    t = hours * 3600 + float(match.group(2)) * 60 + float(match.group(3))
    t = max(0.0, t + float(match.group(4)) / 1000 + shift)
    hh = int(t / 3600)
    t -= hh * 3600
    mm = int(t / 60)
    t -= mm * 60
    ss = int(t)
    ms = min(math.floor((t - ss) * 1000 + 0.5), 999)
    return f"{hh:02d}:{mm:02d}:{ss:02d}{'.' if to_vtt else ','}{ms:03d}"


def _srt_time_line(line: str) -> str:
    """SRT 不支援 VTT 的位置設定，只保留「開始 --> 結束」。"""
    arrow = line.find("-->")
    after = _strip_spaces(line[arrow + 3 :])
    words = [w for w in after.split(" ") if w]
    end = words[0] if words else after
    return f"{_strip_spaces(line[:arrow])} --> {end}"


def _convert_block(lines: list[str], index: int, to_vtt: bool, shift: float) -> str | None:
    time_idx = next((i for i, ln in enumerate(lines) if "-->" in ln), None)
    if time_idx is None:
        return None
    line = _STAMP.sub(lambda m: _format_stamp(m, shift, to_vtt), lines[time_idx])
    if not to_vtt:
        line = _srt_time_line(line)
    content = "\n".join(lines[time_idx + 1 :])
    return f"{line}\n{content}" if to_vtt else f"{index}\n{line}\n{content}"


def convert(text: str, *, to_vtt: bool, shift: float) -> str:
    """SRT ↔ VTT，並整體位移 shift 秒。"""
    blocks: list[str] = []
    for raw in text.replace("\r\n", "\n").split("\n\n"):
        lines = [ln for ln in raw.split("\n") if ln]
        if lines and lines[0].startswith("WEBVTT"):
            lines = lines[1:]
            if not lines:
                continue
        block = _convert_block(lines, len(blocks) + 1, to_vtt, shift)
        if block is not None:
            blocks.append(block)
    return ("WEBVTT\n\n" if to_vtt else "") + "\n\n".join(blocks) + "\n"


def convert_one(path: Path, target: str, shift: float, ctx: ToolContext) -> Path:
    decoded = textcodec.decode_lenient(textcodec.read_bytes(path))
    if decoded is None:
        raise errors.unreadable(path.name)
    text = convert(decoded.text, to_vtt=target == "vtt", shift=shift)
    tmp = ctx.make_work_dir(ctx.output_directory(path)) / f"sub.{target}"
    with open(tmp, "xb") as out:
        out.write(text.encode("utf-8"))
    ext = path.suffix.lower().lstrip(".")
    tag = "已調整" if ext == target else None
    return ctx.deliver(tmp, ctx.output_name(path, tag, target), path)


def run(files: list[Path], values: Values, ctx: ToolContext) -> list[Path]:
    target = str(values.get("target") or "vtt")
    if target not in ("vtt", "srt"):
        raise errors.QSError(f"不支援的字幕格式：{target}")
    shift = max(-SHIFT_LIMIT, min(SHIFT_LIMIT, float(values.get("shift") or 0.0)))
    return per_file(files, ctx, lambda f, sub: [convert_one(f, target, shift, sub)])


TOOL = ToolDefinition(
    id="other.subtitles",
    category=OTHER,
    title="字幕轉換",
    subtitle="SRT 與 WebVTT 字幕互轉，也能整體提前或延後時間軸。",
    icon="captions.bubble",
    run=run,
    input_types=(".srt", ".vtt", "text/vtt"),
    input_label="字幕檔",
    action_title="轉換字幕",
    keywords=("srt", "vtt", "subtitle", "字幕"),
    options=OPTIONS,
    tint=TINTS["video"],
    badge="SRT",
)
