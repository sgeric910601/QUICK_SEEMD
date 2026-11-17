"""背景工作佇列的狀態機（對應原版 QuickActionRunner）。"""

from __future__ import annotations

import time
from pathlib import Path

import pytest
from agent_fakes import fake_tools  # noqa: F401 — pytest fixture

from quickseemd.agent import queue as q
from quickseemd.agent.queue import JobQueue, Status
from quickseemd.core.model import TINTS
from quickseemd.core.runner import JobSpec


class FakeJob:
    def __init__(self, spec, progress, done):
        self.spec, self.progress, self.done = spec, progress, done
        self.started = False
        self.cancelled = False

    def start(self):
        self.started = True

    def cancel(self):
        self.cancelled = True


class Recorder:
    def __init__(self):
        self.events: list[str] = []
        self.finished: list[q.QueueItem] = []
        self.items: tuple[q.QueueItem, ...] = ()

    def queue_changed(self, items):
        self.items = items
        self.events.append("changed")

    def queue_shown(self):
        self.events.append("shown")

    def queue_idle(self):
        self.events.append("idle")

    def queue_empty(self):
        self.events.append("empty")

    def item_finished(self, item):
        self.finished.append(item)


@pytest.fixture
def setup():
    jobs: list[FakeJob] = []

    def factory(spec, progress, done):
        job = FakeJob(spec, progress, done)
        jobs.append(job)
        return job

    rec = Recorder()
    queue = JobQueue(rec, worker_factory=factory)
    return queue, rec, jobs


def test_empty_file_list_is_ignored(setup):
    queue, rec, jobs = setup
    assert queue.run_action("other.zip", []) is None
    assert queue.items == ()
    assert rec.events == []


def test_unknown_action_fails_with_original_text(setup, tmp_path):
    queue, rec, jobs = setup
    f = tmp_path / "a.txt"
    f.write_text("x")
    queue.run_action("bogus.action", [f])
    (item,) = queue.items
    assert item.title == "找不到動作"
    assert item.icon == "questionmark"
    assert item.status is Status.FAILED
    assert item.reason == "這個版本的 QUICK_SEEMD 不認得動作「bogus.action」。"
    assert item.tool_id is None
    assert jobs == []
    assert "shown" in rec.events
    assert rec.events[-1] == "idle"


def test_action_whose_tool_is_missing_is_unknown(setup, tmp_path):
    queue, rec, jobs = setup
    f = tmp_path / "a.pdf"
    f.write_text("x")
    # PR1 裡 Office 動作停用、工具不存在
    queue.run_action("office.pdf-to-docx", [f])
    assert queue.items[0].title == "找不到動作"


def test_known_action_runs_worker_spec(setup, tmp_path, fake_tools):
    queue, rec, jobs = setup
    f = tmp_path / "a.txt"
    f.write_text("x")
    item_id = queue.run_action("other.zip", [f])
    (item,) = queue.items
    assert item.id == item_id
    assert item.title == "壓縮成 ZIP"
    assert item.icon == "doc.zipper"
    assert item.tint == TINTS["teal"]
    assert item.tool_id == "other.compress"
    assert item.action_id == "other.zip"
    assert item.status is Status.RUNNING
    (job,) = jobs
    assert job.started
    assert job.spec == JobSpec("action", "other.zip", [str(f)])


def test_progress_only_moves_forward_and_keeps_message(setup, tmp_path, fake_tools):
    queue, rec, jobs = setup
    f = tmp_path / "a.txt"
    f.write_text("x")
    queue.run_action("other.zip", [f])
    job = jobs[0]
    job.progress(0.5, "處理中：a.txt")
    job.progress(0.3, None)
    item = queue.items[0]
    assert item.progress == 0.5
    assert item.message == "處理中：a.txt"


def test_jobs_run_one_at_a_time(setup, tmp_path, fake_tools):
    queue, rec, jobs = setup
    f = tmp_path / "a.txt"
    f.write_text("x")
    queue.run_action("other.zip", [f])
    queue.run_action("other.zip", [f])
    assert [i.status for i in queue.items] == [Status.RUNNING, Status.WAITING]
    assert len(jobs) == 1
    jobs[0].done({"outputs": [str(tmp_path / "a.zip")]})
    assert [i.status for i in queue.items] == [Status.DONE, Status.RUNNING]
    assert len(jobs) == 2
    assert rec.events.count("idle") == 0
    jobs[1].done({"outputs": []})
    assert rec.events[-1] == "idle"


def test_done_records_outputs_and_notifies_finish(setup, tmp_path, fake_tools):
    queue, rec, jobs = setup
    f = tmp_path / "a.txt"
    f.write_text("x")
    queue.run_action("other.zip", [f])
    jobs[0].done({"outputs": [str(tmp_path / "a.zip")], "summary": "完成摘要"})
    item = queue.items[0]
    assert item.status is Status.DONE
    assert item.outputs == (tmp_path / "a.zip",)
    assert item.progress == 1.0
    assert rec.finished == [item]


def test_failed_result(setup, tmp_path, fake_tools):
    queue, rec, jobs = setup
    f = tmp_path / "a.txt"
    f.write_text("x")
    queue.run_action("other.zip", [f])
    jobs[0].done({"error": "壞掉了"})
    assert queue.items[0].status is Status.FAILED
    assert queue.items[0].reason == "壞掉了"


def test_cancel_running_marks_cancelled(setup, tmp_path, fake_tools):
    queue, rec, jobs = setup
    f = tmp_path / "a.txt"
    f.write_text("x")
    item_id = queue.run_action("other.zip", [f])
    queue.cancel(item_id)
    assert jobs[0].cancelled
    jobs[0].done({"error": "已取消。", "cancelled": True})
    assert queue.items[0].status is Status.FAILED
    assert queue.items[0].reason == "已取消"


def test_cancel_waiting_marks_cancelled_without_running(setup, tmp_path, fake_tools):
    queue, rec, jobs = setup
    f = tmp_path / "a.txt"
    f.write_text("x")
    queue.run_action("other.zip", [f])
    second = queue.run_action("other.zip", [f])
    queue.cancel(second)
    assert queue.items[1].status is Status.FAILED
    assert queue.items[1].reason == "已取消"
    jobs[0].done({"outputs": []})
    assert len(jobs) == 1  # 取消的那個不會再跑


def test_keeps_newest_six(setup, tmp_path):
    queue, rec, jobs = setup
    f = tmp_path / "a.txt"
    f.write_text("x")
    for i in range(8):
        queue.notify(f"訊息 {i}", "link", "內容")
    assert [i.title for i in queue.items] == [f"訊息 {i}" for i in range(2, 8)]


def test_notify_is_done_with_slate_tint(setup):
    queue, rec, jobs = setup
    queue.notify("已拷貝路徑", "link", "/tmp/a")
    (item,) = queue.items
    assert item.status is Status.DONE
    assert item.tint == TINTS["slate"]
    assert item.message == "/tmp/a"
    assert item.files == ()
    assert rec.events[-2:] == ["shown", "idle"]


def test_dismiss_removes_and_reports_empty(setup):
    queue, rec, jobs = setup
    a = queue.notify("一", "link", "")
    b = queue.notify("二", "link", "")
    queue.dismiss(a)
    assert [i.title for i in queue.items] == ["二"]
    queue.dismiss(b)
    assert queue.items == ()
    assert rec.events[-1] == "empty"


def test_finish_of_dismissed_item_is_harmless(setup, tmp_path, fake_tools):
    queue, rec, jobs = setup
    f = tmp_path / "a.txt"
    f.write_text("x")
    first = queue.run_action("other.zip", [f])
    queue.run_action("other.zip", [f])
    queue.dismiss(first)
    jobs[0].done({"outputs": []})
    assert queue.items[0].status is Status.RUNNING
    assert len(jobs) == 2


def test_convert_row(setup, tmp_path):
    queue, rec, jobs = setup
    f = tmp_path / "a.png"
    f.write_bytes(b"x")
    queue.convert("jpeg", [f])
    item = queue.items[0]
    assert item.title == "轉成 JPEG"
    assert item.tint == TINTS["teal"]
    assert item.icon == "photo"
    assert item.tool_id == "other.convert"
    assert jobs[0].spec == JobSpec("convert", "jpeg", [str(f)])


def test_convert_unknown_format_raises(setup, tmp_path):
    queue, rec, jobs = setup
    with pytest.raises(ValueError):
        queue.convert("no-such-format", [tmp_path])


def test_custom_job(setup, tmp_path):
    queue, rec, jobs = setup
    calls = []

    class Custom:
        def __init__(self, progress, done):
            self.progress, self.done = progress, done

        def start(self):
            calls.append("start")
            self.done({"outputs": []})

        def cancel(self):
            calls.append("cancel")

    queue.enqueue("移動到「x」", "folder", TINTS["slate"], [tmp_path], None, Custom)
    assert calls == ["start"]
    assert queue.items[0].status is Status.DONE


def test_job_start_failure_marks_failed(setup, tmp_path):
    queue, rec, jobs = setup

    class Broken:
        def __init__(self, progress, done):
            pass

        def start(self):
            raise OSError("無法啟動")

        def cancel(self):
            pass

    queue.enqueue("x", "folder", TINTS["slate"], [tmp_path], None, Broken)
    assert queue.items[0].status is Status.FAILED
    assert "無法啟動" in queue.items[0].reason


@pytest.mark.parametrize(
    ("statuses", "hovering", "expected"),
    [
        ([Status.DONE], False, "hide"),
        ([Status.DONE], True, "wait"),
        ([Status.DONE, Status.FAILED], False, "keep"),
        ([Status.DONE, Status.RUNNING], False, "keep"),
        ([Status.WAITING], False, "keep"),
        ([], False, "hide"),
    ],
)
def test_auto_hide_decision(statuses, hovering, expected):
    items = tuple(
        q.QueueItem(i, "t", "link", TINTS["slate"], (), None, status=s)
        for i, s in enumerate(statuses)
    )
    assert q.auto_hide_decision(items, hovering) == expected


def test_dispatch_is_used_for_job_callbacks(tmp_path, fake_tools):
    pending = []
    jobs = []

    def factory(spec, progress, done):
        job = FakeJob(spec, progress, done)
        jobs.append(job)
        return job

    rec = Recorder()
    queue = JobQueue(rec, worker_factory=factory, dispatch=pending.append)
    f = tmp_path / "a.txt"
    f.write_text("x")
    queue.run_action("other.zip", [f])
    jobs[0].progress(0.5, "半")
    assert queue.items[0].progress == 0.0
    for fn in pending:
        fn()
    assert queue.items[0].progress == 0.5


def test_real_worker_job_end_to_end(tmp_path, fake_tools):
    """真的開工作程序跑假工具，輸出存在原檔旁邊。"""
    rec = Recorder()
    queue = JobQueue(rec)
    f = tmp_path / "報告.txt"
    f.write_text("內容")
    queue.run_action("other.zip", [f])
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline and queue.items[0].status is Status.RUNNING:
        time.sleep(0.05)
    item = queue.items[0]
    assert item.status is Status.DONE, item.reason
    assert item.outputs == (tmp_path / "報告.zip",)
    assert (tmp_path / "報告.zip").read_text() == "fake zip"
    assert f.read_text() == "內容"


@pytest.mark.slow
def test_real_worker_job_cancel(tmp_path, fake_tools):
    rec = Recorder()
    queue = JobQueue(rec)
    f = tmp_path / "影片.mp4"
    f.write_bytes(b"\0" * 10)
    item_id = queue.run_action("video.compress-medium", [f])
    time.sleep(1.0)
    queue.cancel(item_id)
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline and queue.items[0].status is Status.RUNNING:
        time.sleep(0.05)
    assert queue.items[0].status is Status.FAILED
    assert queue.items[0].reason == "已取消"


def test_path_objects_are_kept(setup, tmp_path, fake_tools):
    queue, rec, jobs = setup
    f = tmp_path / "a.txt"
    f.write_text("x")
    queue.run_action("other.zip", [str(f)])
    assert queue.items[0].files == (Path(f),)
