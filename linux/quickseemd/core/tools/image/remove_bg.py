"""移除背景（對應原版 ITRemoveBackgroundTool）。

主體辨識改用 rembg（isnet-general-use 模型），在獨立的 Python 環境以子程序執行
（rembg_helper.py）；所有圖片的遮罩一次算完，模型只載入一次。
背景：透明／白色／自訂顏色／模糊（σ＝長邊 × 0.012）；可裁切到主體（留邊＝主體長邊 × 4%）。
"""

from __future__ import annotations

import math
from pathlib import Path

from PIL import Image, ImageChops, ImageFilter

from ... import backends, errors, process
from ...model import ToolContext
from ...options import Choice, Info, Kind, Option, Values
from . import colors, formats, loader, pipeline, writer
from .common import color_option, color_value, define

HELPER = Path(__file__).resolve().with_name("rembg_helper.py")
CUSTOM_COLOR = colors.Rgba(0.85, 0.92, 1)
NEED_ENGINE = "需要安裝去背元件"
_FORMATS = (Choice("png", "PNG"), Choice("heic", "HEIC"), Choice("jpeg", "JPG"))

Bounds = tuple[int, int, int, int]  # x0, y0, x1, y1（不含 x1、y1）


# ---------------------------------------------------------------- 遮罩


def make_masks(inputs: list[Path], outputs: list[Path], ctx: ToolContext) -> None:
    """用去背元件算出每張圖的遮罩（白色＝主體）。"""
    python = backends.rembg_python()
    if python is None:
        raise errors.QSError(NEED_ENGINE)
    args: list[str] = [str(python), "-I", str(HELPER)]
    for src, dst in zip(inputs, outputs):
        args += [str(src), str(dst)]
    done = 0

    def on_line(line: str) -> None:
        nonlocal done
        if line.startswith("done "):
            done += 1
            ctx.progress(done / max(len(inputs), 1), "辨識主體…")

    env = {"U2NET_HOME": str(backends.models_dir()), "MODEL_CHECKSUM_DISABLED": "1"}
    res = process.run(args, cancel=ctx.cancel_event, on_line=on_line, env=env)
    if res.returncode != 0:
        detail = process.last_lines(res.stderr) or f"結束代碼 {res.returncode}"
        if res.returncode == 3:
            raise errors.QSError(f"無法辨識主體：{detail}")
        raise errors.QSError(f"無法產生去背遮罩：{detail}")


def subject_bounds(mask: Image.Image) -> Bounds | None:
    """遮罩中「是主體」（> 0.5）的範圍。"""
    return mask.point(lambda v: 255 if v > 127 else 0).getbbox()


def crop_box(bounds: Bounds, size: tuple[int, int]) -> Bounds:
    x0, y0, x1, y1 = bounds
    pad = max(x1 - x0, y1 - y0) * 0.04
    return (
        max(0, math.floor(x0 - pad)),
        max(0, math.floor(y0 - pad)),
        min(size[0], math.ceil(x1 + pad)),
        min(size[1], math.ceil(y1 + pad)),
    )


# ---------------------------------------------------------------- 合成


def composite(image: Image.Image, mask: Image.Image, backdrop: str, color: colors.Rgba) -> Image.Image:
    rgba = image.convert("RGBA")
    if mask.size != rgba.size:
        mask = mask.resize(rgba.size, Image.Resampling.BILINEAR)
    if backdrop == "transparent":
        out = rgba.copy()
        out.putalpha(ImageChops.multiply(rgba.getchannel("A"), mask))
        return out
    if backdrop == "blur":
        background = rgba.filter(ImageFilter.GaussianBlur(max(rgba.size) * 0.012))
    else:
        fill = colors.WHITE if backdrop == "white" else color
        background = Image.new("RGBA", rgba.size, fill.to8())
    return Image.composite(rgba, background, mask)


def _output_format(values: Values, transparent: bool) -> str:
    fid = str(values.get("format", "png"))
    if fid not in formats.FORMATS or not formats.is_writable(fid):
        return "png"
    if transparent and not formats.get(fid).alpha:
        return "png"
    return fid


def _finish(f: Path, mask_path: Path, values: Values, ctx: ToolContext) -> Path:
    source = loader.load(f)
    with Image.open(mask_path) as m:
        mask = m.convert("L")
    bounds = subject_bounds(mask)
    if bounds is None:
        raise errors.SkipFile(f"在「{f.name}」裡找不到明顯的主體（人物、動物或物品）。")
    ctx.progress(0.6, "合成背景…")
    backdrop = str(values.get("background", "transparent"))
    image = composite(source.image, mask, backdrop, color_value(values, "color", CUSTOM_COLOR))
    if values.get("crop_to_subject"):
        image = image.crop(crop_box(bounds, image.size))
    ctx.check_cancelled()
    transparent = backdrop == "transparent"
    out = writer.Output(_output_format(values, transparent), 0.9, writer.KEEP, colors.WHITE, transparent)
    return pipeline.deliver_frames([loader.Frame(image, 0.0)], source, out, ctx, "去背")


# ---------------------------------------------------------------- 工具


def _prepare(files: list[Path], ctx: ToolContext) -> tuple[list[Path], list[Path]]:
    """每張圖轉正後存成暫存 PNG（給去背元件讀）。"""
    work = pipeline.work_dir(ctx, files[0])
    inputs, masks = [], []
    for i, f in enumerate(files):
        ctx.check_cancelled()
        ctx.progress(i / len(files), f"讀取：{f.name}")
        src = work / f"in-{i}.png"
        loader.load(f).image.save(src, "PNG", compress_level=1)
        inputs.append(src)
        masks.append(work / f"mask-{i}.png")
    return inputs, masks


def run(files: list[Path], values: Values, ctx: ToolContext) -> list[Path]:
    if not backends.rembg_model_ready():
        raise errors.QSError(NEED_ENGINE)
    files = [Path(f) for f in files]
    # 三段進度：讀取並轉正 → 辨識主體（一次算完所有遮罩）→ 逐張合成背景
    inputs, masks = _prepare(files, ctx.segment(0, 3))
    phase = ctx.segment(1, 3)
    phase.progress(0, "辨識主體…")
    make_masks(inputs, masks, phase)
    by_file = dict(zip(files, masks))
    outputs, skipped = pipeline.each_file(
        files, ctx.segment(2, 3), lambda f, sub: [_finish(f, by_file[f], values, sub)]
    )
    if skipped:
        ctx.progress(1, f"已略過 {len(skipped)} 張：" + "；".join(skipped))
    return outputs


def validate(files: list[Path], values: Values, info: Info) -> str | None:
    return None if backends.rembg_model_ready() else NEED_ENGINE


def _format_choices(values: Values, info: Info) -> tuple[Choice, ...]:
    allowed = ("png", "heic") if values.get("background", "transparent") == "transparent" else None
    return tuple(
        c for c in _FORMATS if (allowed is None or c.value in allowed) and formats.is_writable(c.value)
    )


OPTIONS: tuple[Option, ...] = (
    Option(
        "background",
        Kind.CARDS,
        "新的背景",
        "transparent",
        (
            Choice("transparent", "透明", "去背後可疊在任何設計上", "checkerboard.rectangle", "PNG"),
            Choice("white", "白色", "商品照、證件照", "square"),
            Choice("custom", "自訂顏色", "換成任何單色背景", "paintpalette"),
            Choice("blur", "模糊背景", "像人像模式一樣淡化背景", "camera.aperture"),
        ),
        group="新的背景",
        group_icon="person.and.background.dotted",
    ),
    color_option(
        "color",
        "背景顏色",
        (colors.CUSTOM_CHOICE,),
        "custom",
        group="新的背景",
        visible=lambda v, i: v.get("background") == "custom",
    ),
    Option(
        "format",
        Kind.CHIPS,
        "輸出格式",
        "png",
        _FORMATS,
        group="輸出格式",
        group_icon="doc.badge.gearshape",
        dynamic_choices=_format_choices,
        note=lambda v, i: (
            ("PNG 相容性最好；HEIC 檔案較小。兩者都保留透明。", False)
            if v.get("background", "transparent") == "transparent"
            else ("背景不透明時也可以存成 JPG。", False)
        ),
    ),
    Option(
        "crop_to_subject",
        Kind.TOGGLE,
        "裁切到主體",
        False,
        caption="去掉多餘的空白，只留主體與一點邊距",
        group="選項",
        group_icon="slider.horizontal.3",
    ),
    Option(
        "_note_local",
        Kind.NOTE,
        "使用內建的主體辨識模型，完全在這台電腦上處理、不會上傳。人物、動物、商品照效果最好。",
        group_icon="lock.shield",
    ),
)

TOOL = define(
    "image.remove-bg",
    "移除背景",
    "自動辨識人物、動物或商品，一鍵去背成透明 PNG，或換成白色、自訂顏色、模糊背景。",
    "person.and.background.dotted",
    run,
    tint="video",
    badge="PNG",
    action_title="移除背景",
    keywords=(
        "remove background",
        "background",
        "cutout",
        "transparent",
        "去背",
        "背景",
        "透明",
        "摳圖",
        "商品照",
    ),
    options=OPTIONS,
    presets={"image.remove-bg-transparent": {"background": "transparent", "format": "png"}},
    validate=validate,
)
