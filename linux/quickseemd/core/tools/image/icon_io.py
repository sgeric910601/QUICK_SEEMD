"""ICO／ICNS 圖示檔與縮放（對應原版 ITWriter.writeIcon、ITScale）。

- ICO：16、32、48、64、128、256 px（Pillow 寫出，每個尺寸各自以 Lanczos 縮放）。
- ICNS：與 iconutil 由 .iconset（16–512 pt，各含 @2x）產生的內容相同，自行組合檔案。
"""

from __future__ import annotations

import io
from collections.abc import Callable
from pathlib import Path

from PIL import Image

from ... import errors

MAX_SIDE = 32768
ICO_SIDES: tuple[int, ...] = (16, 32, 48, 64, 128, 256)
ICNS_SIDES: tuple[int, ...] = (16, 32, 64, 128, 256, 512, 1024)
# iconutil 產生的 ICNS 區塊：16、32 pt 與 128–512 pt 的 1x 與 @2x（全部是 PNG）
ICNS_ENTRIES: tuple[tuple[bytes, int], ...] = (
    (b"icp4", 16),
    (b"icp5", 32),
    (b"ic11", 32),
    (b"ic12", 64),
    (b"ic07", 128),
    (b"ic13", 256),
    (b"ic08", 256),
    (b"ic14", 512),
    (b"ic09", 512),
    (b"ic10", 1024),
)

Progress = Callable[[int, int, int], None]  # （第幾個, 總數, 邊長）


def resample(image: Image.Image, width: int, height: int, smooth: bool = True) -> Image.Image:
    """縮放到指定像素：平滑＝Lanczos，否則最近鄰（像素風）。"""
    if (width, height) == image.size:
        return image
    if width <= 0 or height <= 0:
        raise errors.QSError("輸出尺寸不正確。")
    if width > MAX_SIDE or height > MAX_SIDE:
        raise errors.QSError(f"輸出尺寸 {width} × {height} 太大（上限 {MAX_SIDE} px）。")
    method = Image.Resampling.LANCZOS if smooth else Image.Resampling.NEAREST
    return image.resize((width, height), method)


def square_icon(image: Image.Image, side: int) -> Image.Image:
    """非正方形的圖置中放進透明正方形。"""
    rgba = _plain_rgba(image)
    scale = side / max(rgba.width, rgba.height)
    w = max(1, round(rgba.width * scale))
    h = max(1, round(rgba.height * scale))
    canvas = Image.new("RGBA", (side, side), (0, 0, 0, 0))
    canvas.paste(resample(rgba, w, h), ((side - w) // 2, (side - h) // 2))
    return canvas


def _plain_rgba(image: Image.Image) -> Image.Image:
    """RGBA 複本，不帶 info（避免 Pillow 把註解等資訊寫進圖示）。"""
    rgba = image.convert("RGBA")
    rgba.info = {}
    return rgba


def _png_bytes(image: Image.Image) -> bytes:
    buf = io.BytesIO()
    image.save(buf, "PNG")
    return buf.getvalue()


def write_ico(base: Image.Image, path: Path, progress: Progress | None = None) -> None:
    """寫出多尺寸 ICO；base 是正方形的母版。"""
    images: dict[int, Image.Image] = {}
    rgba = _plain_rgba(base)
    for i, side in enumerate(ICO_SIDES):
        images[side] = resample(rgba, side, side)
        if progress:
            progress(i + 1, len(ICO_SIDES), side)
    first = images[ICO_SIDES[-1]]
    others = [images[s] for s in ICO_SIDES[:-1]]
    try:
        first.save(path, "ICO", sizes=[(s, s) for s in ICO_SIDES], append_images=others)
    except (OSError, ValueError) as exc:
        raise errors.write_failed("圖示編碼失敗。") from exc


def write_icns(base: Image.Image, path: Path, progress: Progress | None = None) -> None:
    """寫出 ICNS（各尺寸 PNG 區塊）；base 是正方形的母版。"""
    cache: dict[int, bytes] = {}
    chunks: list[bytes] = []
    rgba = _plain_rgba(base)
    for i, (kind, side) in enumerate(ICNS_ENTRIES):
        if side not in cache:
            cache[side] = _png_bytes(resample(rgba, side, side))
            if progress:
                progress(len(cache), len(set(s for _k, s in ICNS_ENTRIES)), side)
        data = cache[side]
        chunks.append(kind + (len(data) + 8).to_bytes(4, "big") + data)
    body = b"".join(chunks)
    with open(path, "xb") as fh:
        fh.write(b"icns" + (len(body) + 8).to_bytes(4, "big") + body)
