"""處理圖片時的像素上限（防止檔案很小、解開卻要好幾 GB 記憶體的「解壓縮炸彈」）。

- 每張圖（動畫是所有畫格合計）最多 16384 × 16384 ≈ 2.68 億像素。
- Pillow 的 MAX_IMAGE_PIXELS 設成同一個值，且 DecompressionBombWarning 一律當成錯誤，
  整個程序裡任何地方用 Pillow 開超大的圖都會在解碼前失敗。
- 原版的「單邊 32768 px」是另一個檢查（縮放輸出用），不在這裡。
"""

from __future__ import annotations

import warnings

from PIL import Image

from ... import errors

MAX_PIXELS = 16384 * 16384

Image.MAX_IMAGE_PIXELS = MAX_PIXELS
warnings.filterwarnings("error", category=Image.DecompressionBombWarning)

BOMB_ERRORS = (Image.DecompressionBombWarning, Image.DecompressionBombError)


def too_large(pixels: int) -> errors.QSError:
    megapixels = max(1, round(pixels / 1_000_000))
    return errors.QSError(f"圖片太大（{megapixels} 百萬像素），超過可以處理的上限。")


def check_pixels(width: int, height: int, frames: int = 1) -> None:
    """超過上限時丟出中文錯誤（在配置記憶體之前呼叫）。"""
    pixels = int(width) * int(height) * max(1, int(frames))
    if pixels > MAX_PIXELS:
        raise too_large(pixels)
