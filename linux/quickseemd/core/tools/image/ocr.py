"""圖片文字辨識（對應原版 ITOCRTool），引擎改用 tesseract。

- tesseract 輸出 TSV（字的位置）；中日韓字之間不加空格（tesseract 的字框寬窄不一，間距不可靠），
  中日韓字與英數字之間依間距決定，英數字之間一律空一格。
- 行的排列照原版：由上而下、由左而右；高度相近（差距 ≤ 較矮者的一半）的併成一行；
  與上一行的垂直間距超過上一行高度 1.8 倍時插入空行（段落）。
- 精確模式：小圖先放大兩倍再辨識；快速模式只用於英文。關閉「自動校正拼字」＝不載入字典。
"""

from __future__ import annotations

import csv
import functools
import io
import unicodedata
from dataclasses import dataclass
from pathlib import Path

from PIL import Image

from ... import backends, errors, process
from ...model import ToolContext
from ...options import Choice, Info, Kind, Option, Values
from . import colors, loader, ocr_docs, pipeline, writer
from .common import count, define

LANGUAGES: dict[str, tuple[str, str]] = {
    "traditional": ("繁中＋英", "chi_tra+eng"),
    "simplified": ("簡中＋英", "chi_sim+eng"),
    "japanese": ("日文", "jpn+eng"),
    "korean": ("韓文", "kor+eng"),
    "english": ("英文", "eng"),
    "automatic": ("自動偵測", "chi_tra+chi_sim+eng+jpn+kor"),
}
OUTPUT_TITLES = {"txt": "純文字 TXT", "docx": "Word DOCX", "rtf": "RTF"}
MIN_CONFIDENCE = 30
UPSCALE_BELOW = 1600  # 精確模式：長邊小於這個值時放大兩倍
CJK_GAP = 0.25  # 中日韓字與英數字之間的間距超過字高 × 0.25 才算空格
PARAGRAPH_GAP = 1.8


@dataclass(frozen=True)
class Word:
    text: str
    left: float
    top: float
    width: float
    height: float


@dataclass(frozen=True)
class Line:
    text: str
    left: float
    top: float
    width: float
    height: float

    @property
    def mid_y(self) -> float:
        return self.top + self.height / 2

    @property
    def bottom(self) -> float:
        return self.top + self.height


def languages(key: str) -> str:
    return LANGUAGES.get(key, LANGUAGES["traditional"])[1]


def accurate_mode(values: Values) -> bool:
    """「快速」只支援英文，其他語言一律精確。"""
    return bool(values.get("accurate", True)) or values.get("language") != "english"


# ---------------------------------------------------------------- 排版


def _wide(ch: str) -> bool:
    return unicodedata.east_asian_width(ch) in ("W", "F")


def join_words(words: list[Word]) -> str:
    out = ""
    prev: Word | None = None
    for w in words:
        if prev is not None:
            gap = w.left - (prev.left + prev.width)
            wide = (_wide(prev.text[-1]), _wide(w.text[0]))
            if wide == (False, False):
                out += " "
            elif wide != (True, True) and gap > CJK_GAP * max(prev.height, w.height):
                out += " "
        out += w.text
        prev = w
    return out


def parse_tsv(tsv: str) -> list[Line]:
    """tesseract TSV → 行（同一個區塊、段落、行號的字）。"""
    groups: dict[tuple[str, ...], list[Word]] = {}
    for row in csv.DictReader(io.StringIO(tsv), delimiter="\t", quoting=csv.QUOTE_NONE):
        text = (row.get("text") or "").strip()
        try:
            conf = float(row.get("conf") or -1)
            box = [float(row[k]) for k in ("left", "top", "width", "height")]
        except (TypeError, ValueError, KeyError):
            continue
        if row.get("level") != "5" or not text or conf < MIN_CONFIDENCE:
            continue
        key = tuple(row.get(k, "") for k in ("page_num", "block_num", "par_num", "line_num"))
        groups.setdefault(key, []).append(Word(text, *box))
    lines = []
    for words in groups.values():
        words.sort(key=lambda w: w.left)
        x0, y0 = min(w.left for w in words), min(w.top for w in words)
        x1 = max(w.left + w.width for w in words)
        y1 = max(w.top + w.height for w in words)
        lines.append(Line(join_words(words), x0, y0, x1 - x0, y1 - y0))
    return lines


def _order(a: Line, b: Line) -> int:
    if abs(a.mid_y - b.mid_y) > min(a.height, b.height) * 0.5:
        return -1 if a.mid_y < b.mid_y else 1
    return -1 if a.left < b.left else (1 if a.left > b.left else 0)


def layout(items: list[Line]) -> str:
    """依位置排成一行一行的文字（由上而下、由左而右）。"""
    out: list[str] = []
    last: Line | None = None
    for box in sorted(items, key=functools.cmp_to_key(_order)):
        same_row = last is not None and abs(box.mid_y - last.mid_y) <= min(box.height, last.height) * 0.5
        if same_row and out:
            out[-1] += " " + box.text
        else:
            if last is not None and out and box.top - last.bottom > last.height * PARAGRAPH_GAP:
                out.append("")
            out.append(box.text)
        last = box
    return "\n".join(out).strip()


# ---------------------------------------------------------------- 辨識


def _prepared(image: Image.Image, accurate: bool, path: Path) -> None:
    flat = writer.flatten(image, colors.WHITE)
    if accurate and max(flat.size) < UPSCALE_BELOW:
        flat = flat.resize((flat.width * 2, flat.height * 2), Image.Resampling.LANCZOS)
    flat.save(path, "PNG", compress_level=1)


def recognize(image: Image.Image, values: Values, work: Path, ctx: ToolContext) -> str:
    exe = backends.require("tesseract")
    src = work / "ocr-input.png"
    src.unlink(missing_ok=True)
    _prepared(image, accurate_mode(values), src)
    args = [exe, str(src), "stdout", "-l", languages(str(values.get("language"))), "--psm", "3"]
    if not values.get("correction", True):
        args += ["-c", "load_system_dawg=0", "-c", "load_freq_dawg=0"]
    args.append("tsv")
    res = process.run(args, cancel=ctx.cancel_event)
    if res.returncode != 0:
        raise errors.QSError(f"文字辨識失敗：{process.last_lines(res.stderr) or res.returncode}")
    return layout(parse_tsv(res.stdout))


# ---------------------------------------------------------------- 工具


def _write(sections: list[tuple[str, str]], values: Values, headings: bool, for_input: Path, ctx: ToolContext) -> Path:
    kind = str(values.get("output", "txt"))
    kind = kind if kind in OUTPUT_TITLES else "txt"
    tmp = pipeline.work_dir(ctx, for_input) / f"text.{kind}"
    ocr_docs.write(sections, kind, headings, tmp)
    tag = f"文字辨識（{len(sections)} 張）" if headings and len(sections) > 1 else "文字"
    return pipeline.deliver_file(tmp, for_input, ctx, tag, kind)


def run(files: list[Path], values: Values, ctx: ToolContext) -> list[Path]:
    files = [Path(f) for f in files]
    combine = bool(values.get("combine")) and len(files) > 1
    sections: list[tuple[str, str]] = []
    outputs: list[Path] = []
    empty = 0
    for i, f in enumerate(files):
        ctx.check_cancelled()
        sub = ctx.segment(i, len(files))
        sub.progress(0.05, f"辨識中：{f.name}")
        text = recognize(loader.load(f).image, values, pipeline.work_dir(sub, f), sub)
        empty += 0 if text else 1
        if combine:
            sections.append((f.name, text))
        elif not text and len(files) == 1:
            raise errors.empty_content("在圖片中找不到可辨識的文字。")
        elif text:
            outputs.append(_write([(f.name, text)], values, False, f, sub))
        sub.progress(1, f"辨識出 {len(text)} 個字" if text else f"沒有辨識到文字：{f.name}")
    if combine:
        if not any(text for _n, text in sections):
            raise errors.empty_content("在這些圖片中找不到可辨識的文字。")
        outputs.append(_write(sections, values, True, files[0], ctx))
    if empty and len(files) > 1:
        ctx.progress(1, f"{empty} 張圖片沒有辨識到文字")
    return outputs


def _accuracy_note(values: Values, info: Info) -> tuple[str, bool] | None:
    if not values.get("accurate", True) and values.get("language") != "english":
        return ("所選語言需要精確模式，會自動改用精確辨識。", True)
    return None


OPTIONS: tuple[Option, ...] = (
    Option(
        "language",
        Kind.CHIPS,
        "語言",
        "traditional",
        tuple(Choice(k, title) for k, (title, _codes) in LANGUAGES.items()),
        group="語言",
        group_icon="character.book.closed",
    ),
    Option(
        "accurate",
        Kind.CARDS,
        "辨識方式",
        True,
        (
            Choice(True, "精確", "最準確，支援中日韓文", "scope", "推薦"),
            Choice(False, "快速", "速度快，只適用英文等拉丁字母", "hare"),
        ),
        group="辨識方式",
        group_icon="scope",
        note=_accuracy_note,
    ),
    Option(
        "correction",
        Kind.TOGGLE,
        "自動校正拼字",
        True,
        caption="依語言修正常見的辨識錯誤",
        group="辨識方式",
    ),
    Option(
        "output",
        Kind.SEGMENTED,
        "輸出",
        "txt",
        (Choice("txt", "TXT"), Choice("docx", "Word"), Choice("rtf", "RTF")),
        group="輸出",
        group_icon="doc.text",
        note=lambda v, i: (OUTPUT_TITLES.get(str(v.get("output")), OUTPUT_TITLES["txt"]), False),
    ),
    Option(
        "combine",
        Kind.TOGGLE,
        "合併成一個檔案",
        False,
        caption="依圖片順序，每張加上檔名標題",
        group="輸出",
        visible=lambda v, i: count(i) > 1,
    ),
    Option(
        "_note_offline",
        Kind.NOTE,
        "使用 Tesseract 文字辨識引擎，完全離線處理。文字清楚、對比高的圖片效果最好。",
        group_icon="lock.shield",
    ),
)

TOOL = define(
    "image.ocr",
    "圖片文字辨識（OCR）",
    "把截圖、照片、掃描裡的文字辨識成可編輯的文字，支援繁中、簡中、日文、韓文與英文，可存成 TXT 或 Word。",
    "text.viewfinder",
    run,
    tint="word",
    badge="TXT",
    action_title="辨識文字",
    keywords=(
        "ocr",
        "text recognition",
        "extract text",
        "scan",
        "文字辨識",
        "辨識",
        "擷取文字",
        "掃描",
        "轉文字",
    ),
    options=OPTIONS,
    requires=("tesseract",),
)
