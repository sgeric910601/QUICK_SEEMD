"""製作圖示（對應原版 ITMakeIconTool）。

1024 × 1024 母版：依形狀裁切（圓角半徑＝框的 0.225）、四周留白、補底色；
「原樣」完整放入，圓角方形與圓形填滿。輸出 ICNS、ICO、.iconset 資料夾或 PNG 圖示組。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw

from ...model import ToolContext, per_file
from ...options import Choice, Kind, Option, Values
from . import colors, icon_io, loader, pipeline, writer
from .common import color_option, color_value, define, quality_text

MASTER_SIDE = 1024
_SUPERSAMPLE = 4
CORNER_RATIO = 0.225
ICONSET_POINTS: tuple[int, ...] = (16, 32, 128, 256, 512)
PNG_SIDES: tuple[int, ...] = (16, 32, 48, 64, 128, 180, 192, 256, 512, 1024)


@dataclass(frozen=True)
class IconOptions:
    output: str = "icns"  # icns／ico／iconset／pngSet
    shape: str = "rounded"  # original／rounded／circle
    padding: float = 0.1  # 0–0.3，佔邊長的比例
    background: colors.Rgba = colors.CLEAR


# ---------------------------------------------------------------- 母版


def _shape_mask(side: int, box: tuple[float, float, float, float], shape: str) -> Image.Image:
    """白色＝圖示範圍（4 倍取樣後縮小，邊緣平滑）。"""
    big = Image.new("L", (side * _SUPERSAMPLE, side * _SUPERSAMPLE), 0)
    draw = ImageDraw.Draw(big)
    x0, y0, x1, y1 = (v * _SUPERSAMPLE for v in box)
    rect = [x0, y0, x1 - 1, y1 - 1]
    if shape == "rounded":
        draw.rounded_rectangle(rect, radius=(x1 - x0) * CORNER_RATIO, fill=255)
    elif shape == "circle":
        draw.ellipse(rect, fill=255)
    else:
        draw.rectangle(rect, fill=255)
    return big.resize((side, side), Image.Resampling.BOX)


def master(image: Image.Image, o: IconOptions) -> Image.Image:
    """1024 × 1024 的母版（RGBA）。"""
    side = MASTER_SIDE
    inset = side * max(0.0, min(0.3, float(o.padding)))
    box = (inset, inset, side - inset, side - inset)
    bw = bh = side - inset * 2
    layer = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    if o.background.a > 0:
        fill = Image.new("RGBA", (round(bw), round(bh)), o.background.to8())
        layer.paste(fill, (round(inset), round(inset)))
    rgba = image.convert("RGBA")
    fit = min if o.shape == "original" else max
    scale = fit(bw / rgba.width, bh / rgba.height)
    w, h = max(1, round(rgba.width * scale)), max(1, round(rgba.height * scale))
    over = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    over.paste(icon_io.resample(rgba, w, h), (round(side / 2 - w / 2), round(side / 2 - h / 2)))
    layer = Image.alpha_composite(layer, over)
    alpha = ImageChops.multiply(layer.getchannel("A"), _shape_mask(side, box, o.shape))
    layer.putalpha(alpha)
    return layer


# ---------------------------------------------------------------- 輸出


def _step(ctx: ToolContext, lo: float, hi: float):  # noqa: ANN202
    def report(i: int, n: int, side: int) -> None:
        ctx.check_cancelled()
        ctx.progress(lo + (hi - lo) * i / n, f"產生 {side}×{side}…")

    return report


def _write_png(image: Image.Image, path: Path) -> None:
    writer.write_image(image, None, path, writer.Output("png", metadata=writer.REMOVE_ALL))


def write_iconset(base: Image.Image, folder: Path, ctx: ToolContext, lo: float, hi: float) -> None:
    """.iconset（16–512 pt，各含 @2x）。"""
    folder.mkdir()
    entries: list[tuple[str, int]] = []
    for s in ICONSET_POINTS:
        entries += [(f"icon_{s}x{s}.png", s), (f"icon_{s}x{s}@2x.png", s * 2)]
    report = _step(ctx, lo, hi)
    for i, (name, side) in enumerate(entries):
        _write_png(icon_io.resample(base, side, side), folder / name)
        report(i + 1, len(entries), side)


def write_png_set(base: Image.Image, folder: Path, ctx: ToolContext) -> None:
    folder.mkdir()
    report = _step(ctx, 0.1, 0.9)
    for i, side in enumerate(PNG_SIDES):
        name = "apple-touch-icon-180x180.png" if side == 180 else f"icon-{side}x{side}.png"
        _write_png(icon_io.resample(base, side, side), folder / name)
        report(i + 1, len(PNG_SIDES), side)


def make(file: Path, o: IconOptions, ctx: ToolContext) -> Path:
    """產生一個圖示檔（或資料夾）並交付；「轉換圖片格式」轉 ICO／ICNS 時也用這裡。"""
    ctx.progress(0.02, f"讀取：{file.name}")
    source = loader.load(file)
    base = master(source.image, o)
    ctx.check_cancelled()
    work = pipeline.work_dir(ctx, file)
    if o.output == "icns":
        tmp = work / "icon.icns"
        ctx.progress(0.1, "產生 ICNS…")
        icon_io.write_icns(base, tmp, _step(ctx, 0.1, 0.9))
        ctx.progress(0.9, "產生 ICNS…")
        return pipeline.deliver_file(tmp, file, ctx, None, "icns")
    if o.output == "ico":
        tmp = work / "icon.ico"
        icon_io.write_ico(base, tmp, _step(ctx, 0.1, 0.9))
        return pipeline.deliver_file(tmp, file, ctx, None, "ico")
    if o.output == "iconset":
        folder = work / "icon.iconset"
        write_iconset(base, folder, ctx, 0.1, 0.9)
        return pipeline.deliver_file(folder, file, ctx, None, "iconset")
    folder = work / "icons"
    write_png_set(base, folder, ctx)
    return pipeline.deliver_file(folder, file, ctx, "圖示", "")


# ---------------------------------------------------------------- 工具定義


def _options_from(values: Values) -> IconOptions:
    return IconOptions(
        output=str(values.get("output", "icns")),
        shape=str(values.get("shape", "rounded")),
        padding=float(values.get("padding", 0.1)),
        background=color_value(values, "background", colors.WHITE),
    )


def run(files: list[Path], values: Values, ctx: ToolContext) -> list[Path]:
    o = _options_from(values)
    return per_file(files, ctx, lambda f, sub: [make(f, o, sub)])


OPTIONS: tuple[Option, ...] = (
    Option(
        "output",
        Kind.CARDS,
        "輸出",
        "icns",
        (
            Choice("icns", "macOS 圖示", ".icns，16–1024 px", "macwindow", "ICNS"),
            Choice("ico", "Windows／網站圖示", ".ico，16–256 px（favicon）", "globe", "ICO"),
            Choice("iconset", "圖示組 .iconset", ".iconset 資料夾，可用 iconutil 轉換", "hammer"),
            Choice("pngSet", "PNG 圖示組", "16–1024 px 各尺寸 PNG（含 Apple Touch）", "square.stack.3d.up"),
        ),
        group="輸出",
        group_icon="app.badge",
    ),
    Option(
        "shape",
        Kind.SEGMENTED,
        "形狀",
        "rounded",
        (Choice("original", "原樣"), Choice("rounded", "圓角方形"), Choice("circle", "圓形")),
        group="形狀",
        group_icon="app",
    ),
    Option(
        "padding",
        Kind.SLIDER,
        "四周留白",
        0.1,
        minimum=0,
        maximum=0.3,
        step=0.01,
        display=quality_text,
        group="形狀",
        note=lambda v, i: (
            ("macOS 圖示建議留白約 10%。", False)
            if v.get("shape") == "rounded"
            else ("非正方形的圖片會置中。", False)
        ),
    ),
    color_option(
        "background",
        "背景",
        (colors.TRANSPARENT_CHOICE, colors.WHITE_CHOICE, colors.BLACK_CHOICE, colors.CUSTOM_CHOICE),
        "transparent",
        group="背景",
        group_icon="square.fill",
    ),
)

TOOL = define(
    "image.make-icon",
    "製作圖示",
    "把圖片做成 macOS 圖示（.icns）、Windows／網站圖示（.ico）或各種尺寸的 PNG 圖示組。",
    "app.dashed",
    run,
    badge="ICNS",
    action_title="製作圖示",
    keywords=(
        "icon",
        "icns",
        "ico",
        "favicon",
        "iconset",
        "app icon",
        "圖示",
        "圖標",
        "應用程式圖示",
    ),
    options=OPTIONS,
)
