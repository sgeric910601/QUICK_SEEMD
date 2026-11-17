"""圖片工具經過執行器與工作程序（右鍵動作、工具選項、驗證、略過）。"""

from __future__ import annotations

import sys

from conftest import make_image
from PIL import Image
from test_image_kit import CAMERA_TAGS, tag_names, with_metadata

from quickseemd.core.jobs import WorkerJob
from quickseemd.core.runner import JobSpec, run_spec


def test_action_strip_location_via_runner(tmp_path):
    src = with_metadata(
        make_image(tmp_path / "a.jpg", quality=90), tmp_path / "旅行.jpg", *CAMERA_TAGS
    )
    res = run_spec(JobSpec("action", "image.strip-location", [str(src)]))
    assert res.error is None, res.error
    assert [p.name for p in res.outputs] == ["旅行-已移除位置.jpg"]
    assert res.summary == "已移除：位置（GPS）（未重新壓縮）"
    assert "Model" in tag_names(res.outputs[0])


def test_tool_validation_error_via_runner(jpg_file):
    res = run_spec(JobSpec("tool", "image.rotate", [str(jpg_file)], {"angle": 0}))
    assert res.error == "請選擇旋轉或翻轉方式" and res.outputs == []


def test_output_folder_and_worker_process(jpg_file, outdir):
    spec = JobSpec("action", "image.to-png", [str(jpg_file)], output_folder=str(outdir))
    job = WorkerJob(spec, python=sys.executable)
    job.start()
    res = job.wait(60)
    assert res is not None and res.get("outputs") == [str(outdir / "相片.png")], res
    with Image.open(outdir / "相片.png") as im:
        assert im.format == "PNG"


def test_all_skipped_reports_first_reason(tmp_path, png_file):
    other = make_image(tmp_path / "b.png", size=(64, 64))
    res = run_spec(JobSpec("tool", "image.blur-faces", [str(png_file), str(other)]))
    assert res.outputs == []
    assert res.error == "在「透明.png」裡找不到臉孔，未產生檔案。"
