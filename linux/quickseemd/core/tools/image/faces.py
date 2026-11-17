"""臉孔偵測與畫面重點（取代原版的 Vision）。

- 臉孔：OpenCV YuNet（OpenCV 4.6 只能讀 2022mar 模型）；沒有模型時改用 Haar 分類器。
- 畫面重點：所有臉的聯集中心；沒有臉時用頻譜殘差顯著性圖的加權重心。
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np
from PIL import Image

from ... import backends, errors

log = logging.getLogger(__name__)

Box = tuple[float, float, float, float]  # x, y, 寬, 高（左上為原點、像素）

DETECT_MAX_SIDE = 2560  # 太大的圖先縮小再偵測
SCORE_THRESHOLD = 0.75
NMS_THRESHOLD = 0.3
SALIENCY_SIDE = 256
HAAR_FILES = (
    Path("/usr/share/opencv4/haarcascades/haarcascade_frontalface_default.xml"),
    Path("/usr/share/opencv/haarcascades/haarcascade_frontalface_default.xml"),
)


def _cv2():  # noqa: ANN202 — OpenCV 較大，用到才載入
    try:
        import cv2  # noqa: PLC0415
    except ImportError as exc:
        raise errors.QSError("臉孔偵測失敗：找不到 OpenCV。") from exc
    return cv2


def _bgr(image: Image.Image, max_side: int) -> tuple[np.ndarray, float]:
    cv2 = _cv2()
    rgb = np.asarray(image.convert("RGB"))
    bgr = np.ascontiguousarray(rgb[..., ::-1])
    scale = min(1.0, max_side / max(image.width, image.height))
    if scale < 1:
        size = (max(1, round(image.width * scale)), max(1, round(image.height * scale)))
        bgr = cv2.resize(bgr, size, interpolation=cv2.INTER_AREA)
    return bgr, scale


def _yunet(bgr: np.ndarray, model: Path) -> list[Box]:
    cv2 = _cv2()
    h, w = bgr.shape[:2]
    detector = cv2.FaceDetectorYN_create(
        str(model), "", (w, h), SCORE_THRESHOLD, NMS_THRESHOLD, 5000
    )
    detector.setInputSize((w, h))
    _ok, found = detector.detect(bgr)
    if found is None:
        return []
    return [tuple(float(v) for v in row[:4]) for row in found]


def _haar(bgr: np.ndarray) -> list[Box]:
    cv2 = _cv2()
    path = next((p for p in HAAR_FILES if p.is_file()), None)
    if path is None:
        raise errors.QSError("臉孔偵測失敗：找不到臉孔偵測模型。")
    cascade = cv2.CascadeClassifier(str(path))
    gray = cv2.equalizeHist(cv2.cvtColor(bgr, cv2.COLOR_BGR2GRAY))
    found = cascade.detectMultiScale(gray, scaleFactor=1.1, minNeighbors=5, minSize=(24, 24))
    return [tuple(float(v) for v in row) for row in found]


def detect(image: Image.Image) -> list[Box]:
    """所有臉孔的範圍（原圖像素）。"""
    cv2 = _cv2()
    bgr, scale = _bgr(image, DETECT_MAX_SIDE)
    model = backends.yunet_model()
    try:
        boxes = _yunet(bgr, model) if model is not None else _haar(bgr)
    except cv2.error as exc:
        raise errors.QSError(f"臉孔偵測失敗：{exc}") from exc
    out: list[Box] = []
    for x, y, w, h in boxes:
        x0, y0 = max(0.0, x / scale), max(0.0, y / scale)
        x1, y1 = min(float(image.width), (x + w) / scale), min(float(image.height), (y + h) / scale)
        if x1 > x0 and y1 > y0:
            out.append((x0, y0, x1 - x0, y1 - y0))
    return out


def _saliency_centre(image: Image.Image) -> tuple[float, float] | None:
    cv2 = _cv2()
    bgr, _scale = _bgr(image, SALIENCY_SIDE)
    try:
        saliency = cv2.saliency.StaticSaliencySpectralResidual_create()
        ok, smap = saliency.computeSaliency(bgr)
    except (cv2.error, AttributeError) as exc:
        log.warning("顯著性偵測失敗：%s", exc)
        return None
    if not ok:
        return None
    weight = np.square(np.asarray(smap, dtype=np.float64))
    total = weight.sum()
    if total <= 0:
        return None
    h, w = weight.shape
    ys, xs = np.mgrid[0:h, 0:w]
    return float((xs + 0.5).ravel() @ weight.ravel() / total / w), float(
        (ys + 0.5).ravel() @ weight.ravel() / total / h
    )


def focus_point(image: Image.Image) -> tuple[float, float] | None:
    """畫面重點（0–1，左上為原點）：先找人臉，找不到再用顯著性。"""
    boxes = detect(image)
    if boxes:
        x0 = min(b[0] for b in boxes)
        y0 = min(b[1] for b in boxes)
        x1 = max(b[0] + b[2] for b in boxes)
        y1 = max(b[1] + b[3] for b in boxes)
        return (x0 + x1) / 2 / image.width, (y0 + y1) / 2 / image.height
    return _saliency_centre(image)
