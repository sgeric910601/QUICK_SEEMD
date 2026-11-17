"""文字辨識（對應原版 CVImageConverter.recognizeText／ocr）。

原版用 Vision（繁中、簡中、英、日、韓）；這裡用 tesseract 的 TSV 輸出，
再照原版的規則排列：由上而下、由左而右，中線相差不到半個字高的片段算同一行、以空白相連。
"""

from __future__ import annotations

import functools
import tempfile
import threading
from dataclasses import dataclass
from pathlib import Path

from .. import backends, errors, naming, process
from .formats import Format
from .image import flatten, load_image
from .profile import Profile

LANGUAGES = ("chi_tra", "chi_sim", "eng", "jpn", "kor")
OCR_MAX_PIXEL = 4096
_lang_lock = threading.Lock()
_lang_cache: dict[str, tuple[str, ...]] = {}


@dataclass(frozen=True)
class Fragment:
    left: float
    top: float
    width: float
    height: float
    text: str

    @property
    def mid_y(self) -> float:
        return self.top + self.height / 2


def _installed_languages(tool: str) -> tuple[str, ...]:
    with _lang_lock:
        if tool in _lang_cache:
            return _lang_cache[tool]
    res = process.run([tool, "--list-langs"], timeout=30)
    langs = tuple(line.strip() for line in (res.stdout + res.stderr).splitlines() if line.strip())
    with _lang_lock:
        _lang_cache[tool] = langs
    return langs


def languages(tool: str) -> str:
    have = set(_installed_languages(tool))
    wanted = [lang for lang in LANGUAGES if lang in have]
    return "+".join(wanted) or "eng"


def _is_cjk(ch: str) -> bool:
    o = ord(ch)
    return (
        0x2E80 <= o <= 0x9FFF
        or 0xAC00 <= o <= 0xD7AF
        or 0xF900 <= o <= 0xFAFF
        or 0xFF00 <= o <= 0xFFEF
        or 0x20000 <= o <= 0x3FFFF
    )


def join_words(words: list[str]) -> str:
    """同一行的字詞相連：兩邊都是中日韓文字時不加空白。"""
    out = ""
    for w in words:
        if out and not (_is_cjk(out[-1]) and _is_cjk(w[0])):
            out += " "
        out += w
    return out


def parse_tsv(tsv: str) -> list[Fragment]:
    """tesseract TSV → 每一行一個片段（字詞合併、外框取聯集）。"""
    lines: dict[tuple[str, ...], list[tuple[int, int, int, int, str]]] = {}
    for row in tsv.splitlines()[1:]:
        cols = row.split("\t")
        if len(cols) < 12 or cols[0] != "5" or not cols[11].strip():
            continue
        key = tuple(cols[1:5])
        box = tuple(int(c) for c in cols[6:10])
        lines.setdefault(key, []).append((*box, cols[11].strip()))
    fragments = []
    for words in lines.values():
        left = min(w[0] for w in words)
        top = min(w[1] for w in words)
        right = max(w[0] + w[2] for w in words)
        bottom = max(w[1] + w[3] for w in words)
        text = join_words([w[4] for w in words])
        fragments.append(Fragment(left, top, right - left, bottom - top, text))
    return fragments


def _compare(a: Fragment, b: Fragment) -> int:
    if abs(a.mid_y - b.mid_y) > min(a.height, b.height) * 0.5:
        return -1 if a.mid_y < b.mid_y else 1
    return -1 if a.left < b.left else (1 if a.left > b.left else 0)


def arrange(fragments: list[Fragment]) -> str:
    """依位置排序；中線相差不到半個字高時接在同一行（以空白相連）。"""
    ordered = sorted(fragments, key=functools.cmp_to_key(_compare))
    lines: list[str] = []
    last_mid: float | None = None
    last_height = 0.0
    for frag in ordered:
        if last_mid is not None and lines:
            if abs(frag.mid_y - last_mid) <= min(frag.height, last_height) * 0.5:
                lines[-1] += " " + frag.text
                last_mid, last_height = frag.mid_y, frag.height
                continue
        lines.append(frag.text)
        last_mid, last_height = frag.mid_y, frag.height
    return "\n".join(lines)


def recognize_file(image: Path, ctx) -> str:  # noqa: ANN001
    """辨識一張 PNG 圖片中的文字。"""
    tool = backends.require("tesseract")
    args = [tool, str(image), "stdout", "-l", languages(tool), "tsv"]
    res = ctx.run_process(args, "Tesseract", env={"OMP_THREAD_LIMIT": "4"})
    return arrange(parse_tsv(res.stdout))


def image_to_text(
    path: Path, profile: Profile, fmt: Format, options: dict, work: Path, ctx
) -> list[Path]:  # noqa: ANN001, ARG001, E501
    out = work / naming.join_name(naming.base_name(path), "txt")
    scratch = Path(tempfile.mkdtemp(prefix=".ocr-", dir=work))
    loaded = load_image(path, scratch, ctx, OCR_MAX_PIXEL)
    png = scratch / "ocr.png"
    flatten(loaded.image).save(png, "PNG")
    ctx.progress(0.2)
    text = recognize_file(png, ctx)
    if not text.strip():
        raise errors.empty_content("在圖片中找不到可辨識的文字。")
    out.write_text(text + "\n", "utf-8")
    ctx.progress(1)
    return [out]
