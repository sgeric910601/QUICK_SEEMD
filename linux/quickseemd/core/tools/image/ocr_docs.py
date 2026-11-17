"""文字辨識結果的輸出檔：TXT、Word（python-docx）、RTF（自行產生，非 ASCII 用 \\uN? 跳脫）。

字型：Noto Sans CJK TC，內文 12 pt、標題 15 pt 粗體；段後 4 pt（對應原版的 PingFang TC 設定）。
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path

FONT_NAME = "Noto Sans CJK TC"
BODY_PT = 12
HEADING_PT = 15
SPACE_AFTER_PT = 4
LINE_SPACING = 1.2
EMPTY_TEXT = "（沒有辨識到文字）"

Section = tuple[str, str]  # （檔名, 文字）


def _paragraphs(sections: Sequence[Section], headings: bool) -> list[tuple[str, bool]]:
    """（文字, 是否標題）清單：標題前空一行（第一個除外），內文每行一段。"""
    out: list[tuple[str, bool]] = []
    for i, (name, text) in enumerate(sections):
        if headings:
            if i > 0:
                out.append(("", False))
            out.append((name, True))
        out.extend((line, False) for line in (text or EMPTY_TEXT).split("\n"))
    return out


def txt_document(sections: Sequence[Section], headings: bool) -> str:
    parts = [
        f"＝＝ {name} ＝＝\n{text or EMPTY_TEXT}" if headings else text for name, text in sections
    ]
    return "\n\n".join(parts) + "\n"


def write_docx(sections: Sequence[Section], headings: bool, path: Path) -> None:
    import docx  # noqa: PLC0415 — 用到才載入
    from docx.oxml.ns import qn  # noqa: PLC0415
    from docx.shared import Pt  # noqa: PLC0415

    document = docx.Document()
    normal = document.styles["Normal"]
    normal.font.name = FONT_NAME
    normal.font.size = Pt(BODY_PT)
    normal.element.get_or_add_rPr().get_or_add_rFonts().set(qn("w:eastAsia"), FONT_NAME)
    for text, is_heading in _paragraphs(sections, headings):
        para = document.add_paragraph()
        para.paragraph_format.space_after = Pt(SPACE_AFTER_PT)
        para.paragraph_format.line_spacing = LINE_SPACING
        run = para.add_run(text)
        if is_heading:
            run.bold = True
            run.font.size = Pt(HEADING_PT)
    document.save(str(path))


def _rtf_char(ch: str) -> str:
    """一個字元的 RTF 寫法；非 ASCII 依 UTF-16 拆成 \\uN?（N 是有號 16 位元）。"""
    if ch in "\\{}":
        return "\\" + ch
    if ch == "\t":
        return "\\tab "
    if ord(ch) < 128:
        return ch
    data = ch.encode("utf-16-be")
    units = (int.from_bytes(data[i : i + 2], "big") for i in range(0, len(data), 2))
    return "".join(f"\\u{u - 65536 if u >= 32768 else u}?" for u in units)


def rtf_escape(text: str) -> str:
    return "".join(_rtf_char(ch) for ch in text)


def rtf_document(sections: Sequence[Section], headings: bool) -> str:
    head = (
        "{\\rtf1\\ansi\\ansicpg1252\\deff0"
        f"{{\\fonttbl{{\\f0\\fnil\\fcharset136 {FONT_NAME};}}}}\n"
    )
    body = []
    for text, is_heading in _paragraphs(sections, headings):
        size = HEADING_PT * 2 if is_heading else BODY_PT * 2
        bold = "\\b " if is_heading else ""
        body.append(
            f"{{\\pard\\sa{SPACE_AFTER_PT * 20}\\f0\\fs{size} {bold}{rtf_escape(text)}\\par}}\n"
        )
    return head + "".join(body) + "}\n"


def write(sections: Sequence[Section], kind: str, headings: bool, path: Path) -> None:
    """依格式寫出（path 必須是新檔）。"""
    if kind == "docx":
        write_docx(sections, headings, path)
    elif kind == "rtf":
        with open(path, "x", encoding="ascii") as fh:
            fh.write(rtf_document(sections, headings))
    else:
        with open(path, "x", encoding="utf-8") as fh:
            fh.write(txt_document(sections, headings))
