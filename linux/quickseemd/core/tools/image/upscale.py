"""放大圖片（對應原版 ITUpscaleTool）：Lanczos ×2／×4 再銳利化，可先降低雜訊。"""

from __future__ import annotations

from pathlib import Path

import numpy as np
from PIL import Image, ImageFilter

from ... import errors
from ...model import ToolContext
from ...options import Choice, Info, Kind, Option, Values
from . import pipeline, writer
from .common import define

MAX_SIDE = 16384
SHARPEN: dict[str, float] = {"soft": 0.25, "standard": 0.55, "strong": 0.95}


def _denoise(image: Image.Image) -> Image.Image:
    """溫和降噪（對應 CINoiseReduction 0.02）；透明度不動。"""
    import cv2  # noqa: PLC0415 — OpenCV 較大，用到才載入

    rgb = np.asarray(image.convert("RGB"))
    out = cv2.fastNlMeansDenoisingColored(np.ascontiguousarray(rgb[..., ::-1]), None, 3, 3, 7, 21)
    result = Image.fromarray(np.ascontiguousarray(out[..., ::-1]))
    if image.mode == "RGBA":
        result.putalpha(image.getchannel("A"))
    return result


def upscale(image: Image.Image, factor: int, amount: float, denoise: bool) -> Image.Image:
    w, h = image.width * factor, image.height * factor
    if max(w, h) > MAX_SIDE:
        raise errors.SkipFile(f"放大後為 {w} × {h} px，超過上限 {MAX_SIDE} px，請改用較小的倍數")
    src = _denoise(image) if denoise else image
    big = src.resize((w, h), Image.Resampling.LANCZOS)
    mask = ImageFilter.UnsharpMask(radius=1.2 * factor, percent=round(amount * 100), threshold=0)
    return big.filter(mask)


def run(files: list[Path], values: Values, ctx: ToolContext) -> list[Path]:
    factor = int(values.get("factor", 2))
    amount = SHARPEN.get(str(values.get("sharpen")), SHARPEN["standard"])
    denoise = bool(values.get("denoise", False))

    def output_for(source: pipeline.Source) -> writer.Output:
        fid = source.preferred_format()
        fid = "png" if fid == "gif" else fid
        return writer.Output(fid, 0.92, writer.KEEP, keeps_alpha=source.has_alpha)

    def body(f: Path, sub: ToolContext) -> list[Path]:
        transform = lambda im, _s: upscale(im, factor, amount, denoise)  # noqa: E731
        return [pipeline.run_file(f, sub, f"放大{factor}倍", output_for, transform, animated=False)]

    outputs, skipped = pipeline.each_file(files, ctx, body)
    if skipped:
        ctx.progress(1, "已略過：" + "；".join(skipped))
    return outputs


def validate(files: list[Path], values: Values, info: Info) -> str | None:
    size = pipeline.first_size(info)
    if len(files) == 1 and size and max(size) * int(values.get("factor", 2)) > MAX_SIDE:
        return f"放大後超過 {MAX_SIDE} px，請改用較小的倍數"
    return None


def _factor_choices(values: Values, info: Info) -> tuple[Choice, ...]:
    size = pipeline.first_size(info)

    def sub(f: int) -> str:
        return f"{size[0] * f} × {size[1] * f} px" if size else f"寬高各放大 {f} 倍"

    return (
        Choice(2, "2 倍", sub(2), "2.square"),
        Choice(4, "4 倍", sub(4), "4.square"),
    )


OPTIONS: tuple[Option, ...] = (
    Option(
        "factor",
        Kind.CARDS,
        "放大倍數",
        2,
        _factor_choices({}, {}),
        group="放大倍數",
        group_icon="arrow.up.backward.and.arrow.down.forward",
        dynamic_choices=_factor_choices,
    ),
    Option(
        "sharpen",
        Kind.SEGMENTED,
        "銳利度",
        "standard",
        (Choice("soft", "柔和"), Choice("standard", "標準"), Choice("strong", "銳利")),
        group="銳利度",
        group_icon="wand.and.rays",
    ),
    Option(
        "denoise",
        Kind.TOGGLE,
        "降低雜訊",
        False,
        caption="適合手機照片或壓縮過的 JPG",
        group="銳利度",
    ),
    Option(
        "_note_detail",
        Kind.NOTE,
        "放大無法憑空產生細節，但能讓小圖在大尺寸顯示或列印時更平滑清楚。上限為 16384 px。",
    ),
)

TOOL = define(
    "image.upscale",
    "放大圖片",
    "以高品質 Lanczos 演算法把小圖放大 2 倍或 4 倍，並銳利化讓邊緣更清楚。",
    "arrow.up.backward.and.arrow.down.forward.square",
    run,
    tint="video",
    badge="2×",
    action_title="放大圖片",
    keywords=("upscale", "enlarge", "super resolution", "enhance", "放大", "提高解析度", "變清楚"),
    options=OPTIONS,
    validate=validate,
)
