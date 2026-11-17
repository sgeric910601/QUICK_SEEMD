"""旋轉／翻轉（對應原版 ITRotateTool）。

順序：先水平翻轉、再垂直翻轉、最後旋轉；合起來一定等於 8 種搬移之一，一次做完。
JPG（方向為正、尺寸可整除時）用 jpegtran -copy all -perfect 無損搬移；
其他情況以 Pillow 搬移像素（JPG 以 0.92 品質重新儲存）。
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from pathlib import Path

import numpy as np
from PIL import Image, UnidentifiedImageError

from ... import backends, process
from ...model import ToolContext, per_file
from ...options import Choice, Kind, Option, Values
from . import loader, pipeline, writer
from .common import define

log = logging.getLogger(__name__)

T = Image.Transpose
# 搬移名稱 →（Pillow 的搬移, jpegtran 參數, 用 numpy 模擬）
OPS: dict[str, tuple[T | None, tuple[str, ...], Callable[[np.ndarray], np.ndarray]]] = {
    "identity": (None, (), lambda a: a),
    "flip_h": (T.FLIP_LEFT_RIGHT, ("-flip", "horizontal"), lambda a: a[:, ::-1]),
    "flip_v": (T.FLIP_TOP_BOTTOM, ("-flip", "vertical"), lambda a: a[::-1]),
    "rot90": (T.ROTATE_270, ("-rotate", "90"), lambda a: np.rot90(a, -1)),
    "rot180": (T.ROTATE_180, ("-rotate", "180"), lambda a: np.rot90(a, 2)),
    "rot270": (T.ROTATE_90, ("-rotate", "270"), lambda a: np.rot90(a, 1)),
    "transpose": (T.TRANSPOSE, ("-transpose",), lambda a: a.T),
    "transverse": (T.TRANSVERSE, ("-transverse",), lambda a: np.rot90(a, 2).T),
}


def combined_op(angle: int, flip_h: bool, flip_v: bool) -> str:
    """翻轉＋順時針旋轉合成一個搬移。"""
    probe = np.arange(6).reshape(2, 3)
    result = probe[:, ::-1] if flip_h else probe
    result = result[::-1] if flip_v else result
    result = np.rot90(result, -((int(angle) % 360) // 90))
    for name, (_t, _args, simulate) in OPS.items():
        expected = simulate(probe)
        if expected.shape == result.shape and np.array_equal(expected, result):
            return name
    return "identity"


def apply_op(image: Image.Image, op: str) -> Image.Image:
    method = OPS[op][0]
    return image if method is None else image.transpose(method)


def _jpegtran_ok(f: Path) -> bool:
    """純 JPG（不是 MPO）且方向為正時才能直接無損搬移。"""
    try:
        with Image.open(f) as im:
            if im.format != "JPEG":
                return False
            return loader.exif_orientation(im) == 1
    except (UnidentifiedImageError, OSError, ValueError, SyntaxError):
        return False


def _lossless(f: Path, op: str, tag: str, ctx: ToolContext) -> Path | None:
    exe = backends.find("jpegtran")
    if exe is None or op == "identity" or not _jpegtran_ok(f):
        return None
    work = pipeline.work_dir(ctx, f)
    ext = f.suffix.lstrip(".").lower() or "jpg"
    tmp = work / f"output.{ext}"
    ctx.progress(0.1, f"處理中：{f.name}")
    args = [exe, "-copy", "all", "-perfect", *OPS[op][1], "-outfile", str(tmp), str(f.resolve())]
    res = process.run(args, cancel=ctx.cancel_event)
    if res.returncode != 0 or not tmp.is_file() or tmp.stat().st_size == 0:
        log.info("jpegtran 無法無損搬移「%s」，改為重新儲存：%s", f.name, res.stderr.strip())
        tmp.unlink(missing_ok=True)
        return None
    ctx.progress(0.85, "寫入 JPG…")
    url = pipeline.deliver_file(tmp, f, ctx, tag, ext)
    ctx.progress(1, f"已儲存：{url.name}")
    return url


def run(files: list[Path], values: Values, ctx: ToolContext) -> list[Path]:
    angle = int(values.get("angle", 90))
    op = combined_op(angle, bool(values.get("flip_h")), bool(values.get("flip_v")))
    tag = "已翻轉" if angle == 0 else "已旋轉"

    def output_for(source: pipeline.Source) -> writer.Output:
        return writer.Output(
            source.preferred_format(), 0.92, writer.KEEP, keeps_alpha=source.has_alpha
        )

    def body(f: Path, sub: ToolContext) -> list[Path]:
        url = _lossless(f, op, tag, sub)
        if url is not None:
            return [url]
        return [pipeline.run_file(f, sub, tag, output_for, lambda im, _s: apply_op(im, op))]

    return per_file(files, ctx, body)


def validate(files: list[Path], values: Values, info: dict) -> str | None:
    if int(values.get("angle", 90)) == 0 and not values.get("flip_h") and not values.get("flip_v"):
        return "請選擇旋轉或翻轉方式"
    return None


def action_title(values: Values, info: dict) -> str:
    if int(values.get("angle", 90)) == 0:
        return "翻轉圖片"
    return "旋轉並翻轉" if values.get("flip_h") or values.get("flip_v") else "旋轉圖片"


OPTIONS: tuple[Option, ...] = (
    Option(
        "angle",
        Kind.CARDS,
        "旋轉",
        90,
        (
            Choice(90, "向右轉 90°", "順時針", "rotate.right"),
            Choice(270, "向左轉 90°", "逆時針", "rotate.left"),
            Choice(180, "轉 180°", "上下顛倒", "arrow.up.arrow.down"),
            Choice(0, "不旋轉", "只翻轉", "nosign"),
        ),
        group="旋轉",
        group_icon="arrow.clockwise",
    ),
    Option(
        "flip_h",
        Kind.TOGGLE,
        "水平翻轉",
        False,
        caption="左右對調，像照鏡子",
        group="翻轉（鏡像）",
        group_icon="arrow.left.and.right.righttriangle.left.righttriangle.right",
    ),
    Option("flip_v", Kind.TOGGLE, "垂直翻轉", False, caption="上下對調", group="翻轉（鏡像）"),
    Option(
        "_note_pixels",
        Kind.NOTE,
        "只搬移像素、不重新取樣，畫面不會變模糊（JPG 會以高品質重新儲存）；相片資訊與色彩描述檔會保留。",
    ),
)

TOOL = define(
    "image.rotate",
    "旋轉／翻轉",
    "把圖片轉 90°、180°，或水平、垂直翻轉（鏡像），可一次處理多張。",
    "rotate.right",
    run,
    tint="slate",
    badge="IMG",
    action_title=action_title,
    keywords=("rotate", "flip", "mirror", "turn", "旋轉", "翻轉", "鏡像", "轉向"),
    options=OPTIONS,
    presets={"image.rotate-right": {"angle": 90, "flip_h": False, "flip_v": False}},
    validate=validate,
)
