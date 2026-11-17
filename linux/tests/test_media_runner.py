"""透過工作程序的入口（runner.run_spec）執行影音工具：選項經過 JSON、一鍵動作用預設值。"""

from __future__ import annotations

import json

from test_media_support import streams

from quickseemd.core import runner


def _roundtrip(spec: runner.JobSpec) -> runner.JobSpec:
    return runner.JobSpec.from_dict(json.loads(json.dumps(spec.to_dict())))


def test_action_through_runner(video_file, outdir):
    spec = _roundtrip(
        runner.JobSpec("action", "video.audio-m4a", [str(video_file)], {}, str(outdir))
    )
    result = runner.run_spec(spec)
    assert result.error is None
    assert [p.name for p in result.outputs] == ["影片.m4a"]
    assert result.summary.startswith("已擷取 0:02 的音訊，存成 M4A (AAC)（")
    assert result.to_json()["action"] == "video.audio-m4a"


def test_tool_options_survive_json(video_file, outdir):
    options = {"rate": 0.5, "keep_pitch": False, "keep_audio": True}
    spec = _roundtrip(
        runner.JobSpec("tool", "video.speed", [str(video_file)], options, str(outdir))
    )
    result = runner.run_spec(spec)
    assert result.error is None
    assert result.outputs[0].name == "影片-0.5倍速.mp4"
    assert streams(result.outputs[0], "audio")


def test_runner_reports_validation_error(video_file, outdir):
    options = {"start": "5", "end": ""}
    spec = runner.JobSpec("tool", "video.trim", [str(video_file)], options, str(outdir))
    result = runner.run_spec(spec)
    assert result.error == "開始時間超過檔案長度（0:02.0）"
    assert result.outputs == []


def test_runner_reports_tool_error(audio_file, outdir):
    spec = runner.JobSpec("tool", "video.mute", [str(audio_file)], {}, str(outdir))
    result = runner.run_spec(spec)
    assert result.error == "這個檔案沒有影像軌，無法輸出影片或畫面。"
    assert list(outdir.iterdir()) == []
