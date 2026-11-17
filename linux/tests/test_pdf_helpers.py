"""PDF 工具測試共用的小工具（這個檔案沒有測試，只給其他 test_pdf_*.py 匯入）。"""

from __future__ import annotations

import hashlib
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pikepdf
from pikepdf import Name

from conftest import make_pdf
from quickseemd.core.model import ToolContext


def pdftotext(path: Path, page: int | None = None) -> str:
    args = ["/usr/bin/pdftotext", "-enc", "UTF-8"]
    if page is not None:
        args += ["-f", str(page), "-l", str(page)]
    args += [str(Path(path).resolve()), "-"]
    return subprocess.run(args, capture_output=True, check=True).stdout.decode("utf-8")


def digest(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


@dataclass
class Run:
    outputs: list[Path]
    ctx: ToolContext
    messages: list[str] = field(default_factory=list)

    @property
    def summary(self) -> str | None:
        return self.ctx.last_message


def run_tool(
    tool_id: str, files: list[Path], values: dict[str, Any] | None = None, out: Path | None = None
) -> Run:
    """以工具預設值＋覆蓋值執行（不經過檢查，和原版背景工作相同）。"""
    from quickseemd.core.tools.pdf import TOOLS

    tool = {t.id: t for t in TOOLS}[tool_id]
    vals = tool.defaults()
    vals.update(values or {})
    messages: list[str] = []

    def reporter(_f: float, msg: str | None) -> None:
        if msg and (not messages or messages[-1] != msg):
            messages.append(msg)

    ctx = ToolContext(out, reporter)
    try:
        outputs = tool.run([Path(f) for f in files], vals, ctx)
    finally:
        ctx.cleanup()
    return Run(outputs, ctx, messages)


def tool(tool_id: str):
    from quickseemd.core.tools.pdf import TOOLS

    return {t.id: t for t in TOOLS}[tool_id]


def make_rich_pdf(path: Path, pages: int = 6) -> Path:
    """有書籤、內部連結、具名目的地、頁碼標籤與結構樹的 PDF。"""
    make_pdf(path, pages=pages)
    pdf = pikepdf.open(path, allow_overwriting_input=True)
    p = [pg.obj for pg in pdf.pages]
    # 書籤：「第一章」→ 第 1 頁（子書籤「1.1」→ 第 2 頁），「第二章」→ 最後一頁
    root = pdf.make_indirect(pikepdf.Dictionary(Type=Name.Outlines))
    ch1 = pdf.make_indirect(
        pikepdf.Dictionary(Title="第一章", Parent=root, Dest=[p[0], Name.XYZ, 0, 800, None])
    )
    sub = pdf.make_indirect(pikepdf.Dictionary(Title="1.1", Parent=ch1, Dest=[p[1], Name.Fit]))
    ch2 = pdf.make_indirect(
        pikepdf.Dictionary(Title="第二章", Parent=root, Dest=pikepdf.String("end"))
    )
    ch1.First = ch1.Last = sub
    ch1.Count = 1
    ch1.Next = ch2
    ch2.Prev = ch1
    root.First, root.Last, root.Count = ch1, ch2, 3
    pdf.Root.Outlines = root
    # 具名目的地 "end" → 最後一頁
    pdf.Root.Names = pikepdf.Dictionary(
        Dests=pikepdf.Dictionary(Names=[pikepdf.String("end"), [p[-1], Name.Fit]])
    )
    # 連結：第 1 頁 → 最後一頁（具名）、第 2 頁 → 第 3 頁（直接）
    link1 = pdf.make_indirect(
        pikepdf.Dictionary(
            Type=Name.Annot, Subtype=Name.Link, Rect=[72, 72, 200, 100], Dest=pikepdf.String("end")
        )
    )
    link2 = pdf.make_indirect(
        pikepdf.Dictionary(
            Type=Name.Annot,
            Subtype=Name.Link,
            Rect=[72, 72, 200, 100],
            A=pikepdf.Dictionary(S=Name.GoTo, D=[p[2], Name.Fit]),
        )
    )
    p[0].Annots = pikepdf.Array([link1])
    p[1].Annots = pikepdf.Array([link2])
    pdf.Root.PageLabels = pikepdf.Dictionary(Nums=[0, pikepdf.Dictionary(S=Name.r)])
    pdf.Root.Lang = pikepdf.String("zh-TW")
    pdf.Root.MarkInfo = pikepdf.Dictionary(Marked=True)
    pdf.Root.StructTreeRoot = pdf.make_indirect(
        pikepdf.Dictionary(
            Type=Name.StructTreeRoot,
            K=pikepdf.Array([pikepdf.Dictionary(Type=Name.StructElem, S=Name.P, Pg=p[0])]),
        )
    )
    pdf.docinfo["/Title"] = pikepdf.String("原始標題")
    pdf.docinfo["/Author"] = pikepdf.String("作者甲")
    pdf.save(path)
    pdf.close()
    return path


def outline_titles(path: Path) -> list[tuple[int, str, int | None]]:
    """（深度, 標題, 頁索引）清單。"""
    out: list[tuple[int, str, int | None]] = []
    with pikepdf.open(path) as pdf:
        index = {pg.obj.objgen: i for i, pg in enumerate(pdf.pages)}

        def walk(item: Any, depth: int) -> None:
            while isinstance(item, pikepdf.Dictionary):
                dest = item.get("/Dest")
                page = index.get(dest[0].objgen) if isinstance(dest, pikepdf.Array) else None
                out.append((depth, str(item.Title), page))
                walk(item.get("/First"), depth + 1)
                item = item.get("/Next")

        root = pdf.Root.get("/Outlines")
        if root is not None:
            walk(root.get("/First"), 0)
    return out


def page_rotations(path: Path) -> list[int]:
    with pikepdf.open(path) as pdf:
        return [int(p.obj.get("/Rotate", 0)) for p in pdf.pages]


def page_count(path: Path) -> int:
    with pikepdf.open(path) as pdf:
        return len(pdf.pages)


def encrypt(src: Path, dst: Path, user: str = "", owner: str = "owner", **allow: bool) -> Path:
    flags = {"modify_assembly": True, **allow}  # pikepdf 預設不允許重組頁面，這裡預設全開
    with pikepdf.open(src) as pdf:
        pdf.save(
            dst,
            encryption=pikepdf.Encryption(
                owner=owner, user=user, R=6, allow=pikepdf.Permissions(**flags)
            ),
        )
    return dst
