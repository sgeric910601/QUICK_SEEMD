"""調色盤 PNG（對應原版 ITPalettePNG）。

- 幾乎透明（alpha < 8）的像素統一成全透明。
- 顏色不超過上限時建立完全無損的調色盤；半透明的顏色排前面，tRNS 才能截短；
  位元深度（1／2／4／8）依顏色數自動決定。
- 超過上限：用 pngquant 減色（Floyd–Steinberg 抖色 0.75）；沒有 pngquant 時用 Pillow 的八元樹減色。
- 「只做無損」時顏色超過上限就不寫出，回傳 False。
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path
from typing import Any

import numpy as np
from PIL import Image

from ... import backends, process

log = logging.getLogger(__name__)

ALPHA_FLOOR = 8
DITHER = "0.75"


def _params(dpi: float, icc: bytes | None) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if icc:
        out["icc_profile"] = icc
    if abs(dpi - 72) > 0.01:
        out["dpi"] = (dpi, dpi)
    return out


def _cleaned(image: Image.Image) -> np.ndarray:
    arr = np.array(image.convert("RGBA"), dtype=np.uint8)
    arr[arr[..., 3] < ALPHA_FLOOR] = 0
    return np.ascontiguousarray(arr)


def _write_exact(arr: np.ndarray, path: Path, params: dict[str, Any]) -> None:
    h, w, _c = arr.shape
    packed = arr.reshape(-1, 4).view(np.uint32).ravel()
    uniq, inverse = np.unique(packed, return_inverse=True)
    colours = uniq.view(np.uint8).reshape(-1, 4)
    order = np.argsort(colours[:, 3] == 255, kind="stable")  # 半透明的排前面
    remap = np.empty_like(order)
    remap[order] = np.arange(len(order))
    indices = remap[inverse].astype(np.uint8).reshape(h, w)
    pal = colours[order]
    img = Image.fromarray(indices, "P")
    img.putpalette(pal[:, :3].tobytes(), "RGB")
    semi = int((pal[:, 3] < 255).sum())
    if semi:
        params = {**params, "transparency": bytes(pal[:semi, 3])}
    img.save(path, "PNG", optimize=True, **params)


def _pngquant(
    image: Image.Image,
    limit: int,
    path: Path,
    params: dict[str, Any],
    cancel: threading.Event | None,
) -> bool:
    exe = backends.find("pngquant")
    if exe is None:
        return False
    tmp = path.with_name(f".{path.name}.in.png")
    image.save(tmp, "PNG", compress_level=1, **params)
    try:
        res = process.run(
            [exe, "--speed", "3", f"--floyd={DITHER}", "--output", str(path), str(limit), str(tmp)],
            cancel=cancel,
        )
    finally:
        tmp.unlink(missing_ok=True)
    if res.returncode == 0 and path.is_file():
        return True
    log.warning("pngquant 減色失敗（%s），改用內建減色：%s", res.returncode, res.stderr.strip())
    path.unlink(missing_ok=True)
    return False


def _pillow_quantize(image: Image.Image, limit: int, path: Path, params: dict[str, Any]) -> None:
    quantized = image.quantize(
        colors=limit, method=Image.Quantize.FASTOCTREE, dither=Image.Dither.FLOYDSTEINBERG
    )
    quantized.save(path, "PNG", optimize=True, **params)


def write(
    image: Image.Image,
    max_colors: int,
    *,
    lossless_only: bool,
    dpi: float,
    icc: bytes | None,
    path: Path,
    cancel: threading.Event | None = None,
) -> bool:
    """寫出調色盤 PNG；只做無損且顏色太多時不寫出、回傳 False。"""
    limit = max(2, min(256, int(max_colors)))
    arr = _cleaned(image)
    params = _params(dpi, icc)
    cleaned = Image.fromarray(arr, "RGBA")
    if cleaned.getcolors(limit) is not None:
        _write_exact(arr, path, params)
        return True
    if lossless_only:
        return False
    source = cleaned if image.mode == "RGBA" else cleaned.convert("RGB")
    if not _pngquant(source, limit, path, params, cancel):
        _pillow_quantize(source, limit, path, params)
    return True
