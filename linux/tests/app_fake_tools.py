"""主程式測試用的假工具（以 QUICKSEEMD_EXTRA_TOOLS=tests.app_fake_tools 載入）。

涵蓋每一種選項控制項與每一種自訂畫面；執行函式只在暫存資料夾產生新檔，
經 ctx.deliver 交付，不會改動輸入檔。
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

from quickseemd.core import errors
from quickseemd.core.model import IMAGE, MEDIA, OTHER, PDF, ToolDefinition, per_file
from quickseemd.core.options import Choice, Kind, Option, Values, note


def _copy_run(tag: str):  # noqa: ANN202
    def run(files: list[Path], values: Values, ctx) -> list[Path]:  # noqa: ANN001
        def body(f: Path, sub) -> list[Path]:  # noqa: ANN001
            work = sub.make_work_dir(f)
            tmp = work / "out"
            shutil.copyfile(f, tmp)
            name = sub.output_name(f, tag, f.suffix.lstrip("."))
            return [sub.deliver(tmp, name, f)]

        out = per_file(files, ctx, body)
        ctx.progress(1, f"完成 {len(out)} 個：{json.dumps(values, ensure_ascii=False)[:60]}")
        return out

    return run


def _fail_run(files: list[Path], values: Values, ctx) -> list[Path]:  # noqa: ANN001, ARG001
    ctx.progress(0.3, "處理中：故意失敗")
    raise errors.QSError("故意失敗：測試用的錯誤訊息")


def _slow_run(files: list[Path], values: Values, ctx) -> list[Path]:  # noqa: ANN001, ARG001
    for i in range(200):
        ctx.check_cancelled()
        ctx.progress(i / 200, f"處理中：第 {i} 步")
        time.sleep(0.05)
    return []


def _inspect_pages(files: list[Path]) -> dict:
    return {"page_count": 5 + len(files), "pixel": (640, 480)}


def _prefill(files: list[Path], info: dict) -> Values:  # noqa: ARG001
    if "pixel" not in info:
        return {}
    return {"width": info["pixel"][0]}


def _validate(files: list[Path], values: Values, info: dict) -> str | None:  # noqa: ARG001
    if values.get("text") == "壞":
        return "文字不可以是「壞」"
    return None


def _level_choices(values: Values, info: dict) -> tuple[Choice, ...]:  # noqa: ARG001
    base = (Choice("a", "甲"), Choice("b", "乙"))
    if values.get("more"):
        return (*base, Choice("c", "丙"))
    return base


ALL_KINDS_OPTIONS: tuple[Option, ...] = (
    Option(
        "level",
        Kind.CARDS,
        default="balanced",
        group="壓縮程度",
        group_icon="arrow.down.right.and.arrow.up.left",
        choices=(
            Choice("small", "高度壓縮", "檔案最小", "arrow.down.right.and.arrow.up.left", "推薦"),
            Choice("balanced", "建議壓縮", "畫質與大小平衡"),
            Choice("quality", "高畫質", "幾乎看不出差異"),
        ),
    ),
    Option(
        "format",
        Kind.CHIPS,
        default="jpeg",
        group="格式",
        choices=(
            Choice("jpeg", "JPEG", "相容性最佳"),
            Choice("png", "PNG"),
            Choice("webp", "WebP"),
            Choice("heic", "HEIC"),
        ),
    ),
    Option(
        "codec",
        Kind.SEGMENTED,
        default="h264",
        group="格式",
        choices=(Choice("h264", "H.264"), Choice("hevc", "HEVC")),
    ),
    Option(
        "quality",
        Kind.SLIDER,
        label="品質",
        default=80,
        minimum=10,
        maximum=100,
        step=5,
        display=lambda v: f"{int(v)}%",
        group="細節",
    ),
    Option("more", Kind.TOGGLE, label="更多選擇", caption="打開後多一個選項", default=False),
    Option(
        "dyn",
        Kind.SEGMENTED,
        default="a",
        dynamic_choices=_level_choices,
        visible=lambda v, i: bool(v.get("more")),
    ),
    Option("text", Kind.TEXT, label="文字", default="", placeholder="輸入文字"),
    Option("secret", Kind.PASSWORD, label="密碼", default=""),
    Option("width", Kind.NUMBER, label="寬度", default=100, minimum=1, maximum=10000),
    Option("range", Kind.PAGE_RANGE, label="頁碼範圍", default=""),
    Option(
        "color",
        Kind.COLOR,
        label="顏色",
        default="#e53935",
        custom_key="custom_color",
        choices=(
            Choice("#e53935", "紅"),
            Choice("#1e88e5", "藍"),
            Choice("#000000", "黑"),
            Choice("custom", "自訂"),
        ),
    ),
    Option(
        "position",
        Kind.GRID,
        label="位置",
        default="mc",
        columns=3,
        choices=tuple(
            Choice(v, t)
            for v, t in (
                ("tl", "左上"),
                ("tc", "上"),
                ("tr", "右上"),
                ("ml", "左"),
                ("mc", "中"),
                ("mr", "右"),
                (None, ""),
                ("bc", "下"),
                ("br", "右下"),
            )
        ),
    ),
    Option("logo", Kind.FILE, label="標誌圖片", default="", file_mime="image/*"),
    Option("start", Kind.TIME, label="開始", default="0:00", placeholder="0:00.0"),
    Option(
        "algos",
        Kind.CHECKS,
        label="演算法",
        default=["sha256"],
        choices=(Choice("md5", "MD5"), Choice("sha1", "SHA-1"), Choice("sha256", "SHA-256")),
    ),
    Option(
        "warn",
        Kind.NOTE,
        note=lambda v, i: ("密碼太短", True) if 0 < len(v.get("secret") or "") < 4 else None,
    ),
    note("這是一則固定說明。"),
    Option(
        "disabled_toggle",
        Kind.TOGGLE,
        label="無法使用的開關",
        default=False,
        enabled=lambda v, i: False,
    ),
)

TOOLS: list[ToolDefinition] = [
    ToolDefinition(
        id="test.all-kinds",
        category=IMAGE,
        title="測試全部選項",
        subtitle="每一種選項控制項都有一個。",
        icon="photo.stack",
        run=_copy_run("全選項"),
        input_types=("image/*", "application/pdf"),
        input_label="圖片或 PDF",
        action_title=lambda v, i: "開始處理" if not v.get("more") else "開始處理（更多）",
        keywords=("allkinds", "全部"),
        options=ALL_KINDS_OPTIONS,
        inspect=_inspect_pages,
        prefill=_prefill,
        validate=_validate,
        files_note=lambda f, v, i: "第一個檔案很大" if len(f) > 3 else None,
    ),
    ToolDefinition(
        id="test.copy",
        category=PDF,
        title="測試複製 PDF",
        subtitle="把 PDF 複製一份。",
        icon="doc.richtext",
        run=_copy_run("已複製"),
        input_types=("application/pdf",),
        input_label="PDF 檔案",
        reorderable=True,
        minimum_files=1,
        action_title="複製",
        keywords=("copy",),
    ),
    ToolDefinition(
        id="test.merge",
        category=PDF,
        title="測試合併",
        subtitle="至少兩個檔案。",
        icon="rectangle.stack.badge.plus",
        run=_copy_run("已合併"),
        input_types=("application/pdf",),
        input_label="PDF 檔案",
        minimum_files=2,
        reorderable=True,
        action_title="合併",
    ),
    ToolDefinition(
        id="test.fail",
        category=OTHER,
        title="測試失敗",
        subtitle="一定會失敗。",
        icon="questionmark",
        run=_fail_run,
        action_title="失敗",
    ),
    ToolDefinition(
        id="test.slow",
        category=OTHER,
        title="測試慢工作",
        subtitle="可以取消。",
        icon="hare",
        run=_slow_run,
        action_title="慢慢做",
    ),
    ToolDefinition(
        id="test.folder",
        category=OTHER,
        title="測試資料夾",
        subtitle="接受檔案與資料夾。",
        icon="doc.zipper",
        run=_copy_run("資料夾"),
        input_types=("item", "folder"),
        input_label="檔案或資料夾",
    ),
    ToolDefinition(
        id="test.organize",
        category=PDF,
        title="測試整理 PDF",
        subtitle="拖曳頁面縮圖調整順序。",
        icon="square.grid.3x3.square",
        run=_copy_run("已整理"),
        input_types=("application/pdf",),
        input_label="PDF 檔案",
        allows_multiple=False,
        action_title="儲存整理後的 PDF",
        options=(Option("pages", Kind.TEXT, default="", visible=lambda v, i: False),),
        custom_view="pdf-organize",
    ),
    ToolDefinition(
        id="test.crop",
        category=IMAGE,
        title="測試裁剪",
        subtitle="拖曳框選要保留的範圍。",
        icon="crop",
        run=_copy_run("已裁剪"),
        input_types=("image/*",),
        input_label="圖片",
        action_title="裁剪圖片",
        options=(
            Option(
                "ratio",
                Kind.CHIPS,
                default="free",
                group="比例",
                group_icon="aspectratio",
                choices=(
                    Choice("free", "自由", "自由拖曳"),
                    Choice("1:1", "1:1", "大頭貼、IG 貼文"),
                    Choice("16:9", "16:9", "影片、簡報、桌布"),
                    Choice("a4-portrait", "A4 直", "列印 A4 紙張"),
                ),
            ),
            Option("crop", Kind.TEXT, default="", visible=lambda v, i: False),
        ),
        custom_view="image-crop",
    ),
    ToolDefinition(
        id="test.trim",
        category=MEDIA,
        title="測試剪輯影片",
        subtitle="剪下需要的片段。",
        icon="scissors",
        run=_copy_run("剪輯"),
        input_types=("video/*",),
        input_label="影片",
        action_title="剪輯影片",
        options=(
            Option("start", Kind.TIME, label="開始", default="0", group="時間範圍"),
            Option("end", Kind.TIME, label="結束", default="", placeholder="結尾"),
        ),
        custom_view="media-trim",
    ),
    ToolDefinition(
        id="test.atrim",
        category=MEDIA,
        title="測試剪輯音訊",
        subtitle="剪下需要的音訊片段。",
        icon="waveform.badge.minus",
        run=_copy_run("剪輯"),
        input_types=("audio/*",),
        input_label="音訊",
        action_title="剪輯音訊",
        options=(
            Option("start", Kind.TIME, label="開始", default="0"),
            Option("end", Kind.TIME, label="結束", default=""),
        ),
        custom_view="audio-trim",
    ),
    ToolDefinition(
        id="test.convert",
        category=OTHER,
        title="測試萬用格式轉換",
        subtitle="整頁自訂。",
        icon="arrow.triangle.2.circlepath",
        run=_copy_run("轉換"),
        custom_page="converter",
    ),
]
