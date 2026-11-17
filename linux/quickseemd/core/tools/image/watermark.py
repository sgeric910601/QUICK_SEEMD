"""加入浮水印（對應原版 ITWatermarkTool）。

- 文字：Noto Sans CJK TC（粗體／一般），字高＝短邊 × 百分比（至少 6 px）。
- 陰影（只有文字）：往下偏移 字高 × 0.05、模糊 字高 × 0.18；淺色字配黑色 50%，深色字配白色 50%。
- 單一位置：九宮格，留邊＝短邊 × 0.04，以旋轉後的外框計算。
- 平鋪：橫向間距 max(寬 × 0.6, 短邊 × 0.08)，縱向 max(高 × 2.2, 短邊 × 0.08)，奇數列錯開半格；
  整個格線繞圖片中心旋轉。正角度＝逆時針。
"""

from __future__ import annotations

import logging
import math
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

from ... import errors, process
from ...model import ToolContext, per_file
from ...options import Choice, Info, Kind, Option, Values
from . import colors, loader, pipeline, writer
from .common import color_option, color_value, define, quality_text

log = logging.getLogger(__name__)

NOTO_DIRS = (Path("/usr/share/fonts/opentype/noto"), Path("/usr/share/fonts/truetype/noto"))
TC_INDEX = 3  # NotoSansCJK-*.ttc 的第 4 個字型是繁體中文
FC_MATCH = Path("/usr/bin/fc-match")
CUSTOM_COLOR = colors.Rgba(0.89, 0.2, 0.18)
POSITIONS = ("左上", "上方", "右上", "左側", "正中央", "右側", "左下", "下方", "右下")


# ---------------------------------------------------------------- 字型


def _font_file(bold: bool) -> tuple[str, int] | None:
    name = "NotoSansCJK-Bold.ttc" if bold else "NotoSansCJK-Regular.ttc"
    for folder in NOTO_DIRS:
        if (folder / name).is_file():
            return str(folder / name), TC_INDEX
    if not FC_MATCH.is_file():
        return None
    pattern = f"Noto Sans CJK TC:{'bold' if bold else 'regular'}"
    res = process.run([str(FC_MATCH), "-f", "%{file}|%{index}", pattern], timeout=10)
    path, _sep, index = res.stdout.strip().partition("|")
    if res.returncode == 0 and path and Path(path).is_file():
        return path, int(index or 0)
    return None


def load_font(size: int, bold: bool) -> ImageFont.FreeTypeFont | ImageFont.ImageFont:
    found = _font_file(bold)
    if found is not None:
        try:
            return ImageFont.truetype(found[0], max(6, size), index=found[1])
        except OSError as exc:
            log.warning("無法載入字型 %s：%s", found[0], exc)
    return ImageFont.load_default(max(6, size))


# ---------------------------------------------------------------- 印章


def _text_stamp(text: str, size: float, values: Values) -> tuple[Image.Image, tuple[float, float]]:
    """文字印章（RGBA，含陰影的留白）與文字本身的寬高。"""
    font = load_font(round(size), bool(values.get("bold", True)))
    ascent, descent = font.getmetrics()
    width, height = font.getlength(text), ascent + descent
    pad = math.ceil(size * 0.5)
    layer_size = (math.ceil(width) + pad * 2, height + pad * 2)
    color = color_value(values, "color", CUSTOM_COLOR)
    text_layer = Image.new("RGBA", layer_size, (0, 0, 0, 0))
    ImageDraw.Draw(text_layer).text((pad, pad), text, font=font, fill=color.opacity(1).to8())
    if not values.get("shadow", True):
        return text_layer, (width, height)
    shade = (0, 0, 0, 128) if color.is_light else (255, 255, 255, 128)
    shadow = Image.new("RGBA", layer_size, (0, 0, 0, 0))
    ImageDraw.Draw(shadow).text((pad, pad + size * 0.05), text, font=font, fill=shade)
    shadow = shadow.filter(ImageFilter.GaussianBlur(size * 0.18 / 2))
    return Image.alpha_composite(shadow, text_layer), (width, height)


def _logo_stamp(
    logo: Image.Image, image_width: int, scale: float
) -> tuple[Image.Image, tuple[float, float]]:
    lw = max(4.0, image_width * scale / 100)
    lh = lw * logo.height / max(logo.width, 1)
    resized = logo.convert("RGBA").resize(
        (max(1, round(lw)), max(1, round(lh))), Image.Resampling.LANCZOS
    )
    return resized, (lw, lh)


def _faded(stamp: Image.Image, opacity: float) -> Image.Image:
    alpha = stamp.getchannel("A").point(lambda a: round(a * opacity))
    out = stamp.copy()
    out.putalpha(alpha)
    return out


def _paste(canvas: Image.Image, stamp: Image.Image, cx: float, cy: float) -> None:
    """以 (cx, cy) 為中心疊上去，超出畫面的部分裁掉。"""
    x0, y0 = round(cx - stamp.width / 2), round(cy - stamp.height / 2)
    sx, sy = max(0, -x0), max(0, -y0)
    dx, dy = max(0, x0), max(0, y0)
    w = min(stamp.width - sx, canvas.width - dx)
    h = min(stamp.height - sy, canvas.height - dy)
    if w > 0 and h > 0:
        canvas.alpha_composite(stamp, dest=(dx, dy), source=(sx, sy, sx + w, sy + h))


def angle_of(values: Values) -> float:
    choice = values.get("rotation", 0)
    if choice == "custom":
        return float(values.get("rotation_custom", 0) or 0)
    try:
        return float(choice)
    except (TypeError, ValueError):
        return 0.0


def _single(
    canvas: Image.Image, stamp: Image.Image, size: tuple[float, float], angle: float, pos: int
) -> None:
    rad = math.radians(angle)
    c, s = abs(math.cos(rad)), abs(math.sin(rad))
    bw, bh = size[0] * c + size[1] * s, size[0] * s + size[1] * c
    w, h = canvas.size
    margin = min(w, h) * 0.04
    col, row = pos % 3, pos // 3
    cx = (margin + bw / 2, w / 2, w - margin - bw / 2)[col]
    cy = (margin + bh / 2, h / 2, h - margin - bh / 2)[row]
    _paste(canvas, stamp.rotate(angle, Image.Resampling.BICUBIC, expand=True), cx, cy)


def _tiled(
    canvas: Image.Image, stamp: Image.Image, size: tuple[float, float], angle: float
) -> None:
    w, h = canvas.size
    short = min(w, h)
    step_x = size[0] + max(size[0] * 0.6, short * 0.08)
    step_y = size[1] + max(size[1] * 2.2, short * 0.08)
    reach = math.hypot(w, h) / 2 + max(step_x, step_y)
    rotated = stamp.rotate(angle, Image.Resampling.BICUBIC, expand=True)
    rad = math.radians(angle)
    cos, sin = math.cos(rad), math.sin(rad)
    half = max(rotated.width, rotated.height) / 2
    row, y = 0, -reach
    while y <= reach:
        x = -reach + (step_x / 2 if row % 2 else 0)
        while x <= reach:
            px = w / 2 + x * cos - y * sin
            py = h / 2 - (x * sin + y * cos)  # 原版座標 y 向上
            if -half <= px <= w + half and -half <= py <= h + half:
                _paste(canvas, rotated, px, py)
            x += step_x
        y += step_y
        row += 1


def apply(image: Image.Image, values: Values, logo: Image.Image | None) -> Image.Image:
    w, h = image.size
    short = min(w, h)
    if values.get("kind") == "image" and logo is not None:
        stamp, size = _logo_stamp(logo, w, float(values.get("logo_scale", 20)))
    else:
        font_size = max(6.0, short * float(values.get("text_size", 5)) / 100)
        stamp, size = _text_stamp(str(values.get("text", "")), font_size, values)
    if size[0] <= 0 or size[1] <= 0:
        return image
    stamp = _faded(stamp, max(0.05, min(1.0, float(values.get("opacity", 0.6)))))
    canvas = image.convert("RGBA")
    if values.get("tiled"):
        _tiled(canvas, stamp, size, angle_of(values))
    else:
        _single(canvas, stamp, size, angle_of(values), int(values.get("position", 8)))
    return canvas if image.mode == "RGBA" else canvas.convert("RGB")


# ---------------------------------------------------------------- 工具


def _logo_path(values: Values) -> Path | None:
    raw = values.get("logo")
    if not raw:
        return None
    p = Path(str(raw))
    return p if p.is_absolute() and p.is_file() else None


def run(files: list[Path], values: Values, ctx: ToolContext) -> list[Path]:
    logo = None
    if values.get("kind") == "image":
        path = _logo_path(values)
        if path is None:
            raise errors.QSError("請先選擇標誌圖片。")
        logo = loader.load(path).image

    def output_for(source: pipeline.Source) -> writer.Output:
        return writer.Output(
            source.preferred_format(), 0.92, writer.KEEP, keeps_alpha=source.has_alpha
        )

    def body(f: Path, sub: ToolContext) -> list[Path]:
        return [
            pipeline.run_file(f, sub, "浮水印", output_for, lambda im, _s: apply(im, values, logo))
        ]

    return per_file(files, ctx, body, skip_errors=False)


def validate(files: list[Path], values: Values, info: Info) -> str | None:
    if values.get("kind") == "image":
        return None if _logo_path(values) else "請先選擇標誌圖片"
    return None if str(values.get("text", "")).strip() else "請輸入浮水印文字"


def _is_text(v: Values, i: Info) -> bool:
    return v.get("kind", "text") == "text"


def _is_logo(v: Values, i: Info) -> bool:
    return v.get("kind") == "image"


OPTIONS: tuple[Option, ...] = (
    Option(
        "kind",
        Kind.SEGMENTED,
        "浮水印類型",
        "text",
        (Choice("text", "文字"), Choice("image", "標誌圖片")),
        group="浮水印類型",
        group_icon="signature",
    ),
    Option(
        "text",
        Kind.TEXT,
        "浮水印文字",
        "© 版權所有",
        placeholder="例如：© 2026 我的工作室",
        group="文字",
        group_icon="pencil.line",
        visible=_is_text,
    ),
    Option(
        "text_size",
        Kind.SLIDER,
        "文字大小",
        5,
        caption="以圖片短邊的百分比計算，大小不同的圖片看起來一致。",
        minimum=1,
        maximum=25,
        step=0.5,
        display=lambda v: f"{float(v):.1f}%",
        group="文字",
        visible=_is_text,
    ),
    Option("bold", Kind.TOGGLE, "粗體", True, group="文字", visible=_is_text),
    color_option(
        "color",
        "顏色",
        (colors.WHITE_CHOICE, colors.BLACK_CHOICE, colors.CUSTOM_CHOICE),
        "#ffffff",
        group="文字",
        visible=_is_text,
    ),
    Option(
        "logo",
        Kind.FILE,
        "標誌圖片",
        "",
        caption="建議使用背景透明的 PNG 標誌。",
        placeholder="選擇標誌圖片…",
        file_mime="image/*",
        group="標誌圖片",
        group_icon="photo",
        visible=_is_logo,
    ),
    Option(
        "logo_scale",
        Kind.SLIDER,
        "標誌大小",
        20,
        minimum=3,
        maximum=80,
        step=1,
        display=lambda v: f"圖寬 {round(float(v))}%",
        group="標誌圖片",
        visible=_is_logo,
    ),
    Option(
        "opacity",
        Kind.SLIDER,
        "不透明度",
        0.6,
        minimum=0.1,
        maximum=1,
        step=0.05,
        display=quality_text,
        group="外觀",
        group_icon="circle.lefthalf.filled",
    ),
    Option(
        "rotation",
        Kind.CHIPS,
        "旋轉角度",
        0,
        (
            Choice(0, "水平"),
            Choice(45, "斜 45°"),
            Choice(-45, "斜 −45°"),
            Choice(90, "直立"),
            Choice("custom", "自訂"),
        ),
        group="外觀",
    ),
    Option(
        "rotation_custom",
        Kind.SLIDER,
        "自訂角度",
        0,
        minimum=-90,
        maximum=90,
        step=5,
        display=lambda v: f"{int(float(v))}°",
        group="外觀",
        visible=lambda v, i: v.get("rotation") == "custom",
    ),
    Option(
        "shadow",
        Kind.TOGGLE,
        "加上陰影",
        True,
        caption="在任何背景上都清楚可見",
        group="外觀",
        visible=_is_text,
    ),
    Option(
        "tiled",
        Kind.SEGMENTED,
        "位置",
        False,
        (Choice(False, "單一位置"), Choice(True, "整張平鋪")),
        group="位置",
        group_icon="square.grid.3x3",
        note=lambda v, i: ("重複鋪滿整張圖片，較難被裁掉。", False) if v.get("tiled") else None,
    ),
    Option(
        "position",
        Kind.GRID,
        "位置",
        8,
        tuple(Choice(i, t) for i, t in enumerate(POSITIONS)),
        columns=3,
        group="位置",
        visible=lambda v, i: not v.get("tiled"),
    ),
)

TOOL = define(
    "image.watermark",
    "加入浮水印",
    "在圖片加上文字或標誌浮水印：可調大小、顏色、透明度、角度，放在九宮格任一位置或整張平鋪。",
    "signature",
    run,
    tint="audio",
    badge="IMG",
    action_title="加入浮水印",
    keywords=("watermark", "logo", "copyright", "stamp", "浮水印", "版權", "標誌", "商標"),
    options=OPTIONS,
    validate=validate,
)
