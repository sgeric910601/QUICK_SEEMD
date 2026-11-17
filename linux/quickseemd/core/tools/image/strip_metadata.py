"""移除相片資訊（對應原版 ITStripMetadataTool）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from ...model import ToolContext, per_file
from ...options import Choice, Info, Kind, Option, Values
from . import metadata, pipeline
from .common import count, define, dynamic_note


def run(files: list[Path], values: Values, ctx: ToolContext) -> list[Path]:
    mode = metadata.LOCATION if values.get("mode") == "location" else metadata.ALL
    tag = "已移除位置" if mode == metadata.LOCATION else "已移除資訊"

    def body(f: Path, sub: ToolContext) -> list[Path]:
        sub.progress(0.1, f"讀取：{f.name}")
        work = pipeline.work_dir(sub, f)
        report = metadata.strip(f, mode, work / "stripped", sub.cancel_event)
        sub.check_cancelled()
        if report.removed:
            message = "已移除：" + "、".join(report.removed)
            message += "（未重新壓縮）" if report.lossless else ""
        elif mode == metadata.LOCATION:
            message = "沒有找到位置資訊，已輸出副本"
        else:
            message = "沒有找到相片資訊，已輸出副本"
        sub.progress(0.95, message)
        url = pipeline.deliver_file(report.file, f, sub, tag, report.ext)
        sub.progress(1, message)
        return [url]

    return per_file(files, ctx, body)


def inspect(files: list[Path]) -> dict[str, Any]:
    info = pipeline.image_info(files)
    if len(files) == 1:
        info["found"] = metadata.categories(Path(files[0]), metadata.ALL)
    return info


def _found_note(values: Values, info: Info) -> tuple[str, bool] | None:
    found = info.get("found")
    if found is None:
        return None
    return ("、".join(found), False) if found else ("沒有找到相片資訊。", False)


OPTIONS: tuple[Option, ...] = (
    Option(
        "mode",
        Kind.CARDS,
        "要移除的資訊",
        "all",
        (
            Choice(
                "location",
                "只移除位置資訊",
                "清掉 GPS 座標與地點，保留拍攝時間、相機等資訊。",
                "location.slash",
            ),
            Choice(
                "all",
                "移除全部",
                "清掉 EXIF、GPS、相機、拍攝時間、作者等所有資訊。",
                "trash.slash",
                "最安全",
            ),
        ),
        group="要移除的資訊",
        group_icon="eye.slash",
    ),
    dynamic_note(
        "_found",
        _found_note,
        group="這張圖片裡有",
        group_icon="doc.text.magnifyingglass",
        visible=lambda v, i: count(i) == 1,
    ),
    Option(
        "_note_lossless",
        Kind.NOTE,
        "JPG、TIFF 直接移除資訊、不重新壓縮；PNG 以無損方式重新儲存，畫質都完全不變。"
        "HEIC 只移除位置時不重新壓縮。圖片方向與色彩描述檔都會保留。",
    ),
)

TOOL = define(
    "image.strip-metadata",
    "移除相片資訊",
    "清除 EXIF、GPS 位置、相機型號、拍攝時間等隱私資訊；JPG 等格式直接移除、不重新壓縮。",
    "location.slash",
    run,
    tint="teal",
    badge="EXIF",
    action_title=lambda v, i: "移除位置資訊" if v.get("mode") == "location" else "移除相片資訊",
    keywords=("exif", "metadata", "gps", "location", "privacy", "隱私", "定位", "位置", "中繼資料"),
    options=OPTIONS,
    presets={"image.strip-location": {"mode": "location"}},
    inspect=inspect,
)
