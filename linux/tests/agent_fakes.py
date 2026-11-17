"""背景程式測試用的假工具（測試時才寫出模組，透過 QUICKSEEMD_EXTRA_TOOLS 載入）。

- other.compress：「壓縮成 ZIP」動作，很快地在原檔旁邊寫出 {檔名}.zip（內容只是文字）
- image.rotate：「向右旋轉 90°」動作，一律失敗（測試失敗列）
- other.read-qr：「讀取 QR Code」動作，寫出 {檔名}-QR內容.txt
- video.compress：「壓縮影片（建議）」動作，跑很久且會檢查取消（測試取消）
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

MODULE_NAME = "qs_agent_fake_tools"

FAKE_TOOLS_SOURCE = '''
"""測試用假工具。"""
import time
from pathlib import Path

from quickseemd.core import errors
from quickseemd.core.model import IMAGE, MEDIA, OTHER, ToolDefinition, per_file

QR_TEXT = "https://example.com/測試\\n第二行"


def _write(ctx, f, name, text):
    work = ctx.make_work_dir(f)
    tmp = work / "out"
    tmp.write_text(text, "utf-8")
    return ctx.deliver(tmp, name, f)


def _zip(files, values, ctx):
    def body(f, sub):
        sub.progress(0.5, f"處理中：{f.name}")
        return [_write(sub, f, sub.output_name(f, None, "zip"), "fake zip")]

    return per_file(files, ctx, body)


def _fail(files, values, ctx):
    ctx.progress(0.3, "處理中：測試")
    raise errors.QSError("測試用的失敗原因")


def _qr(files, values, ctx):
    f = files[0]
    out = _write(ctx, f, ctx.output_name(f, "QR內容", "txt"), "# " + f.name + "\\n" + QR_TEXT + "\\n")
    ctx.progress(1.0, "找到 2 個，已複製到剪貼簿")
    return [out]


def _slow(files, values, ctx):
    for i in range(300):
        ctx.check_cancelled()
        ctx.progress(i / 300, f"處理中：{files[0].name}")
        time.sleep(0.05)
    return []


TOOLS = [
    ToolDefinition(
        "other.compress", OTHER, "壓縮檔案", "測試", "doc.zipper", _zip,
        presets={"other.zip": {}},
    ),
    ToolDefinition(
        "image.rotate", IMAGE, "旋轉圖片", "測試", "rotate.right", _fail,
        input_types=("image/*",), presets={"image.rotate-right": {}},
    ),
    ToolDefinition(
        "other.read-qr", OTHER, "讀取 QR Code", "測試", "qrcode.viewfinder", _qr,
        input_types=("image/*",), presets={"other.read-qr": {}},
    ),
    ToolDefinition(
        "video.compress", MEDIA, "壓縮影片", "測試", "film", _slow,
        input_types=("video/*",), presets={"video.compress-medium": {}},
    ),
]
'''


def write_fake_tools(directory: Path) -> Path:
    """把假工具模組寫到 directory，回傳檔案路徑。"""
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / f"{MODULE_NAME}.py"
    path.write_text(FAKE_TOOLS_SOURCE, "utf-8")
    return path


@pytest.fixture
def fake_tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch):
    """在這個測試裡登記假工具（本程序與工作程序都看得到），結束時還原。"""
    from quickseemd.core import registry  # noqa: PLC0415

    mod_dir = tmp_path / "fake-tools"
    write_fake_tools(mod_dir)
    monkeypatch.syspath_prepend(str(mod_dir))
    monkeypatch.setenv("PYTHONPATH", str(mod_dir))
    monkeypatch.setenv("QUICKSEEMD_EXTRA_TOOLS", MODULE_NAME)
    registry.reload()
    yield mod_dir
    monkeypatch.delenv("QUICKSEEMD_EXTRA_TOOLS")
    registry.reload()
    sys.modules.pop(MODULE_NAME, None)
