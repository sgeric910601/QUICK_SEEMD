"""模糊臉部（對應原版 ITBlurFacesTool）。

- 偵測到的臉往外擴張 expand／2，再往上多留 臉高 × expand × 0.3（頭髮）。
- 模糊 σ＝範圍 × (0.02 + 0.012 × 強度)；馬賽克方塊＝範圍 ÷ max(3, 24 − 2 × 強度)，
  以臉的中心對齊格線；黑色色塊直接塗黑。
- 遮罩：橢圓＝放射漸層（模糊時 80%→100% 柔邊，否則 99%→100%）；方形在模糊時以
  σ＝短邊 × 0.04 羽化。
"""

from __future__ import annotations

import math
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter

from ... import errors
from ...model import ToolContext
from ...options import Choice, Kind, Option, Values
from . import faces, loader, pipeline, writer
from .common import define

Rect = tuple[float, float, float, float]
TAGS = {"blur": "模糊臉部", "pixelate": "馬賽克", "blackout": "遮蓋臉部"}


def expand_box(face: Rect, expand: float, width: int, height: int) -> Rect:
    """往外擴張（頭髮、下巴、耳朵也要遮住），上方多留一點；限制在圖片內。"""
    x, y, w, h = face
    rx, ry = x - w * expand / 2, y - h * expand / 2
    rw, rh = w * (1 + expand), h * (1 + expand)
    extra = h * expand * 0.3
    ry, rh = ry - extra, rh + extra
    x0, y0 = max(0.0, rx), max(0.0, ry)
    x1, y1 = min(float(width), rx + rw), min(float(height), ry + rh)
    return (x0, y0, max(0.0, x1 - x0), max(0.0, y1 - y0))


def face_mask(size: tuple[int, int], r: Rect, shape: str, soft: bool) -> Image.Image:
    """白色＝要遮的地方；r 是相對於 size 的範圍。"""
    w, h = size
    x, y, rw, rh = r
    if shape == "rectangle":
        mask = Image.new("L", size, 0)
        ImageDraw.Draw(mask).rectangle([x, y, x + rw - 1, y + rh - 1], fill=255)
        if soft:
            mask = mask.filter(ImageFilter.GaussianBlur(min(rw, rh) * 0.04))
        return mask
    inner = 0.8 if soft else 0.99
    ys, xs = np.mgrid[0:h, 0:w]
    dx = (xs + 0.5 - (x + rw / 2)) / max(rw / 2, 1e-6)
    dy = (ys + 0.5 - (y + rh / 2)) / max(rh / 2, 1e-6)
    dist = np.sqrt(dx * dx + dy * dy)
    alpha = np.clip((1.0 - dist) / (1.0 - inner), 0.0, 1.0)
    return Image.fromarray(np.round(alpha * 255).astype(np.uint8), "L")


def _pixelate(patch: Image.Image, centre: tuple[float, float], cell: float) -> Image.Image:
    """以 centre（相對於 patch）為一格的中心對齊的馬賽克，每格取平均色。"""
    c = max(1, round(cell))
    arr = np.asarray(patch, dtype=np.float32)
    h, w = arr.shape[:2]
    start_x = int(math.floor((centre[0] - c / 2) % c)) - c if c > 1 else 0
    start_y = int(math.floor((centre[1] - c / 2) % c)) - c if c > 1 else 0
    pad_l, pad_t = -start_x, -start_y
    nx, ny = math.ceil((w + pad_l) / c), math.ceil((h + pad_t) / c)
    pad = ((pad_t, ny * c - h - pad_t), (pad_l, nx * c - w - pad_l), (0, 0))
    grid = np.pad(arr, pad, mode="edge")
    blocks = grid.reshape(ny, c, nx, c, arr.shape[2]).mean(axis=(1, 3))
    up = np.repeat(np.repeat(blocks, c, axis=0), c, axis=1)
    out = up[pad_t : pad_t + h, pad_l : pad_l + w]
    return Image.fromarray(np.clip(np.round(out), 0, 255).astype(np.uint8), patch.mode)


def _effect(patch: Image.Image, style: str, size: float, strength: float, centre: tuple[float, float]) -> Image.Image:
    if style == "blur":
        return patch.filter(ImageFilter.GaussianBlur(size * (0.02 + 0.012 * strength)))
    if style == "pixelate":
        return _pixelate(patch, centre, size / max(3, 24 - 2 * strength))
    fill = (0, 0, 0, 255) if patch.mode == "RGBA" else (0, 0, 0)
    return Image.new(patch.mode, patch.size, fill)


def apply(image: Image.Image, boxes: list[Rect], values: Values) -> Image.Image:
    style = str(values.get("style", "blur"))
    key = "block_size" if style == "pixelate" else "strength"
    strength = max(1.0, min(10.0, float(values.get(key, 6))))
    shape = str(values.get("shape", "ellipse"))
    expand = float(values.get("expand", 0.25))
    result = image.copy()
    for face in boxes:
        r = expand_box(face, expand, image.width, image.height)
        if r[2] <= 0 or r[3] <= 0:
            continue
        size = max(r[2], r[3])
        sigma = size * (0.02 + 0.012 * strength) if style == "blur" else 0.0
        margin = math.ceil(3 * max(sigma, min(r[2], r[3]) * 0.04)) + 2
        x0, y0 = max(0, math.floor(r[0]) - margin), max(0, math.floor(r[1]) - margin)
        x1 = min(image.width, math.ceil(r[0] + r[2]) + margin)
        y1 = min(image.height, math.ceil(r[1] + r[3]) + margin)
        patch = result.crop((x0, y0, x1, y1))
        local = (r[0] - x0, r[1] - y0, r[2], r[3])
        centre = (local[0] + r[2] / 2, local[1] + r[3] / 2)
        effect = _effect(patch, style, size, strength, centre)
        mask = face_mask(patch.size, local, shape, soft=style == "blur")
        result.paste(Image.composite(effect, patch, mask), (x0, y0))
    return result


def run(files: list[Path], values: Values, ctx: ToolContext) -> list[Path]:
    style = str(values.get("style", "blur"))
    total = 0

    def body(f: Path, sub: ToolContext) -> list[Path]:
        nonlocal total
        sub.progress(0.02, f"讀取：{f.name}")
        source = loader.load(f)
        sub.progress(0.1, f"尋找臉孔：{f.name}")
        boxes = faces.detect(source.image)
        if not boxes:
            raise errors.SkipFile(f"在「{f.name}」裡找不到臉孔，未產生檔案。")
        total += len(boxes)
        sub.check_cancelled()
        sub.progress(0.5, f"找到 {len(boxes)} 張臉，處理中…")
        image = apply(source.image, boxes, values)
        out = writer.Output(source.preferred_format(), 0.92, writer.KEEP, keeps_alpha=source.has_alpha)
        url = pipeline.deliver_frames(
            [loader.Frame(image, 0.0)], source, out, sub, TAGS.get(style, "模糊臉部")
        )
        sub.progress(1, f"已處理 {len(boxes)} 張臉")
        return [url]

    outputs, skipped = pipeline.each_file(files, ctx, body)
    message = f"共處理 {total} 張臉"
    if skipped:
        message += f"；{len(skipped)} 張圖片沒有找到臉孔，已略過"
    ctx.progress(1, message)
    return outputs


OPTIONS: tuple[Option, ...] = (
    Option(
        "style",
        Kind.CARDS,
        "遮蓋方式",
        "blur",
        (
            Choice("blur", "模糊", "柔和自然，看得出是人但認不出是誰", "drop"),
            Choice("pixelate", "馬賽克", "新聞畫面常見的方塊效果", "square.grid.3x3.fill"),
            Choice("blackout", "黑色色塊", "完全遮住，最安全", "rectangle.fill"),
        ),
        group="遮蓋方式",
        group_icon="face.dashed",
    ),
    Option(
        "strength",
        Kind.SLIDER,
        "模糊程度",
        6,
        minimum=1,
        maximum=10,
        step=1,
        group="強度",
        group_icon="dial.medium",
        visible=lambda v, i: v.get("style", "blur") == "blur",
    ),
    Option(
        "block_size",
        Kind.SLIDER,
        "方塊大小",
        6,
        minimum=1,
        maximum=10,
        step=1,
        group="強度",
        group_icon="dial.medium",
        visible=lambda v, i: v.get("style") == "pixelate",
    ),
    Option(
        "shape",
        Kind.SEGMENTED,
        "形狀",
        "ellipse",
        (Choice("ellipse", "橢圓"), Choice("rectangle", "方形")),
        group="形狀與範圍",
        group_icon="oval",
    ),
    Option(
        "expand",
        Kind.SLIDER,
        "遮蓋範圍",
        0.25,
        caption="比偵測到的臉再大一些，把頭髮與輪廓也遮住。",
        minimum=0,
        maximum=0.8,
        step=0.05,
        display=lambda v: f"+{round(float(v) * 100)}%",
        group="形狀與範圍",
    ),
    Option(
        "_note_offline",
        Kind.NOTE,
        "使用內建的臉孔偵測，完全離線處理。沒有找到臉孔的圖片會略過；請在分享前再確認一次結果。",
        group_icon="lock.shield",
    ),
)

TOOL = define(
    "image.blur-faces",
    "模糊臉部",
    "自動找出照片中的每一張臉，以模糊、馬賽克或黑色色塊遮住，保護隱私後再分享。",
    "face.dashed",
    run,
    tint="slate",
    badge="IMG",
    action_title=lambda v, i: {"blur": "模糊臉部", "pixelate": "加上馬賽克", "blackout": "遮住臉部"}.get(
        str(v.get("style")), "模糊臉部"
    ),
    keywords=("blur face", "anonymize", "pixelate", "mosaic", "privacy", "模糊", "馬賽克", "打碼", "臉", "隱私"),
    options=OPTIONS,
)
