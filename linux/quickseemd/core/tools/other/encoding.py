"""文字編碼轉換（對應原版 OTEncodingSession 與 CVTextConverter）。

- 來源編碼自動偵測（textcodec.decode）。
- 目標：UTF-8、UTF-8（含 BOM）、Big5（CP950）、GB18030、UTF-16（LE＋BOM）、CRLF、LF。
  原版因為格式清單漏了 GB18030，介面上看不到這個選項；這裡補上。
- 換行轉換保留原本的編碼與 BOM；來源是 UTF-16 BE 或 UTF-32 時改成 UTF-16 LE＋BOM。
- 目標和現在一樣時（8 MB 以內的檔案才檢查）照原版回報「無法轉換成…」。
- 輸出檔名和原檔相同，放在原檔旁時會變成「名稱 (1).txt」。
"""

from __future__ import annotations

from pathlib import Path

from ... import errors, filetypes
from ...model import OTHER, TINTS, ToolContext, ToolDefinition, per_file
from ...options import Choice, Kind, Option, Values, note
from . import textcodec as tc

OFFER_CHECK_LIMIT = 8_000_000
_HEAD = 8192

TARGET_CHOICES: tuple[Choice, ...] = (
    Choice("utf8", "UTF-8", "現代標準，任何地方都通用", "globe"),
    Choice("utf8-bom", "UTF-8（含 BOM）", "讓舊版 Excel、記事本正確顯示中文", "tablecells"),
    Choice("big5", "Big5", "舊版繁體中文 Windows 程式", "character"),
    Choice("gb18030", "GB18030", "簡體中文系統", "character"),
    Choice("utf16", "UTF-16", "部分 Windows 程式使用", "textformat"),
    Choice("crlf", "Windows 換行（CRLF）", "只改換行符號，編碼不變", "return"),
    Choice("lf", "Mac / Linux 換行（LF）", "只改換行符號，編碼不變", "return"),
)

# 原版萬用轉換的格式名稱（錯誤訊息「無法轉換成 X」用）
FORMAT_LABEL = {
    "utf8": "UTF-8",
    "utf8-bom": "UTF-8（含 BOM）",
    "utf16": "UTF-16",
    "big5": "Big5",
    "gb18030": "GB18030",
    "lf": "LF 換行（macOS／Linux）",
    "crlf": "CRLF 換行（Windows）",
}

# 目標 → （輸出編碼, BOM）；換行目標保留原本的編碼
_ENCODING_TARGETS = {
    "utf8": (tc.UTF8, False),
    "utf8-bom": (tc.UTF8, True),
    "utf16": (tc.UTF16LE, True),
    "big5": (tc.BIG5, False),
    "gb18030": (tc.GB18030, False),
}
_LINE_TARGETS = {"lf": tc.LineEnding.LF, "crlf": tc.LineEnding.CRLF}
_BIG5_FAMILY = frozenset({tc.BIG5, tc.BIG5HKSCS, "big5"})
_TO_UTF16LE = frozenset({tc.UTF16, tc.UTF16BE, tc.UTF32})

OPTIONS: tuple[Option, ...] = (
    Option(
        key="target",
        kind=Kind.CARDS,
        label="轉成",
        default="utf8",
        choices=TARGET_CHOICES,
        group="轉成",
        group_icon="character.textbox",
    ),
    note("來源編碼會自動偵測（UTF-8、Big5、GB18030、Shift-JIS、UTF-16…）。", group="轉成"),
)


def is_offered(target: str, current: tc.Decoded) -> bool:
    """原版的「可轉成」清單：和目前一樣的目標不提供。"""
    enc, bom = current.encoding, current.had_bom
    checks = {
        "utf8": not (enc == tc.UTF8 and not bom),
        "utf8-bom": not (enc == tc.UTF8 and bom),
        "utf16": enc not in (tc.UTF16, tc.UTF16LE),
        "big5": enc not in _BIG5_FAMILY,
        "gb18030": enc != tc.GB18030,
    }
    if target in checks:
        return checks[target]
    ending = current.line_ending
    if target == "lf":
        return ending not in (tc.LineEnding.LF, tc.LineEnding.NONE)
    return ending is not tc.LineEnding.CRLF


def convert_bytes(decoded: tc.Decoded, target: str) -> bytes:
    """依目標重新編碼（或只改換行）。"""
    text, enc, bom = decoded.text, decoded.encoding, decoded.had_bom
    if target in _ENCODING_TARGETS:
        enc, bom = _ENCODING_TARGETS[target]
    else:
        text = tc.normalize_line_endings(text, _LINE_TARGETS[target])
    if enc in _TO_UTF16LE:
        enc, bom = tc.UTF16LE, True
    return tc.encode(text, enc, bom)


def _is_plain_text(path: Path, head: bytes) -> bool:
    return filetypes.is_text_mime(filetypes.mime_of(path)) or tc.looks_like_text(head)


def _decode_checked(path: Path, data: bytes, target: str) -> tc.Decoded:
    label = FORMAT_LABEL[target]
    if not _is_plain_text(path, data[:_HEAD]):
        raise errors.unsupported_target(path.name, label)
    decoded = tc.decode(data)
    if len(data) <= OFFER_CHECK_LIMIT and not is_offered(target, decoded):
        raise errors.unsupported_target(path.name, label)
    return decoded


def convert_one(path: Path, target: str, ctx: ToolContext) -> Path:
    if target not in FORMAT_LABEL:
        raise errors.unsupported_target(path.name, target)
    decoded = _decode_checked(path, tc.read_bytes(path), target)
    data = convert_bytes(decoded, target)
    tmp = ctx.make_work_dir(ctx.output_directory(path)) / path.name
    with open(tmp, "xb") as out:
        out.write(data)
    return ctx.deliver(tmp, path.name, path)


def run(files: list[Path], values: Values, ctx: ToolContext) -> list[Path]:
    target = str(values.get("target") or "utf8")
    return per_file(files, ctx, lambda f, sub: [convert_one(f, target, sub)])


TOOL = ToolDefinition(
    id="other.text-encoding",
    category=OTHER,
    title="文字編碼轉換",
    subtitle="亂碼救星：Big5、GB18030、UTF-16 等編碼與 Windows / Mac 換行互轉。",
    icon="character.textbox",
    run=run,
    input_types=("text/plain",),
    input_label="文字檔",
    action_title="轉換編碼",
    keywords=("encoding", "utf8", "big5", "亂碼", "編碼", "換行", "crlf"),
    options=OPTIONS,
    tint=TINTS["teal"],
    badge="TXT",
    presets={"other.to-utf8": {"target": "utf8"}},
)
