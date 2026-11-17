"""圖片工具測試共用的小工具（其他 test_image_*.py 匯入使用）。"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from quickseemd.core import backends, process, registry
from quickseemd.core.model import ToolContext

ASTRONAUT_REL = "venv-rembg/lib/python3.12/site-packages/skimage/data/astronaut.png"


def run_tool(
    tool_id: str,
    files: list[Path],
    outdir: Path | None = None,
    action: str | None = None,
    **values: Any,
) -> tuple[list[Path], ToolContext]:
    """用工具預設值（或右鍵動作的預設）加上 values 執行，回傳（輸出, 執行環境）。"""
    tool = registry.tool(tool_id)
    assert tool is not None, f"沒有登記工具 {tool_id}"
    vals = tool.values_for_preset(action) if action else tool.defaults()
    vals.update(values)
    ctx = ToolContext(outdir)
    try:
        outs = tool.run([Path(f) for f in files], vals, ctx)
    finally:
        ctx.cleanup()
    return outs, ctx


def tool(tool_id: str):
    t = registry.tool(tool_id)
    assert t is not None
    return t


def digest(path: Path) -> str:
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def exiftool_json(path: Path) -> dict[str, Any]:
    """exiftool -j -G0 讀出來的標籤（群組:名稱 → 值）。"""
    exe = backends.require("exiftool")
    res = process.run_checked([exe, "-j", "-G0", "-a", str(path)], "exiftool")
    return json.loads(res.stdout)[0]


def tag_names(path: Path) -> set[str]:
    return {k.split(":", 1)[-1] for k in exiftool_json(path)}


def with_metadata(src: Path, dst: Path, *tags: str) -> Path:
    """用 exiftool 複製一份加上相片資訊的圖片（-o 寫新檔，不動原檔）。"""
    exe = backends.require("exiftool")
    process.run_checked([exe, "-q", "-m", "-o", str(dst), *tags, str(src)], "exiftool")
    return dst


CAMERA_TAGS = (
    "-GPSLatitude=25.03",
    "-GPSLatitudeRef=N",
    "-GPSLongitude=121.56",
    "-GPSLongitudeRef=E",
    "-Make=Acme",
    "-Model=Cam 1",
    "-DateTimeOriginal=2024:01:02 03:04:05",
    "-ExposureTime=0.01",
    "-Artist=小明",
    "-Software=Editor",
    "-IPTC:City=Taipei",
    "-XMP-photoshop:City=Taipei",
    "-XMP-iptcCore:Location=Here",
)


def astronaut() -> Path | None:
    """有人臉的公有領域測試照片（去背元件環境裡附帶的）。"""
    p = backends.data_dir() / ASTRONAUT_REL
    return p if p.is_file() else None


def test_kit_run_tool_rejects_unknown():
    assert registry.tool("image.nope") is None
