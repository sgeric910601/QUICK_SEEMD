"""文字編碼與換行轉換（對應原版 CVTextConverter）。

- utf8：不含 BOM；utf8-bom：含 BOM；utf16：UTF-16 LE＋BOM；big5：CP950；gb18030（Linux 版補上）。
- lf／crlf：只換換行，保留原本的編碼與 BOM（UTF-16 BE／UTF-32 來源改成 UTF-16 LE＋BOM）。
- 輸出沿用原始檔名，所以輸出到原資料夾時會變成「名稱 (1).副檔名」。
"""

from __future__ import annotations

from pathlib import Path

from .. import errors
from . import textcodec
from .formats import Format
from .profile import Profile

_TARGETS: dict[str, tuple[str, bool]] = {
    "utf8": ("utf-8", False),
    "utf8-bom": ("utf-8", True),
    "utf16": ("utf-16-le", True),
    "big5": ("cp950", False),
    "gb18030": ("gb18030", False),
}


def convert(
    path: Path, profile: Profile, fmt: Format, options: dict, work: Path, ctx
) -> list[Path]:  # noqa: ANN001, ARG001, E501
    forced = textcodec.forced_codec(options.get("source_encoding"))
    decoded = textcodec.decode_file(path, forced)
    text, codec, bom = decoded.text, decoded.encoding, decoded.had_bom
    if fmt.id in _TARGETS:
        codec, bom = _TARGETS[fmt.id]
    elif fmt.id == "lf":
        text = textcodec.normalize_line_endings(text, textcodec.LineEnding.LF)
    elif fmt.id == "crlf":
        text = textcodec.normalize_line_endings(text, textcodec.LineEnding.CRLF)
    else:
        raise errors.unsupported_target(path.name, fmt.display_name)
    if textcodec.is_utf16(codec) or textcodec.is_utf32(codec):
        codec, bom = "utf-16-le", True
    data = textcodec.encode(text, codec, bom)
    out = work / path.name
    try:
        out.write_bytes(data)
    except OSError as exc:
        raise errors.write_failed(exc.strerror or str(exc)) from exc
    ctx.progress(1)
    return [out]
