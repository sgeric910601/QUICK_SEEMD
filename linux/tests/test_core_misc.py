"""設定、完成頁文字、輪盤、外部程式、工作程序。"""

from __future__ import annotations

import json
import os
import sys
import threading
import time
from pathlib import Path

import pytest

from quickseemd.core import errors, process, result, settings, wheel
from quickseemd.core.filetypes import FileRef
from quickseemd.core.jobs import WorkerJob
from quickseemd.core.model import TINTS, ToolContext, per_file
from quickseemd.core.runner import JobSpec, run_spec

# ------------------------------------------------------------------ 設定


def test_settings_defaults_and_save():
    s = settings.load()
    assert s["wheel.key.convert"] == "shift"
    settings.save({"wheel.key.tools": "super"})
    assert settings.get("wheel.key.tools") == "super"
    stored = json.loads(settings.settings_path().read_text("utf-8"))
    assert stored == {"wheel.key.tools": "super"}  # 只存和預設不同的值
    with pytest.raises(KeyError):
        settings.save({"nope": 1})
    with pytest.raises(TypeError):
        settings.save({"wheel.enabled": "yes"})


def test_settings_corrupt_file_falls_back():
    settings.config_dir().mkdir(parents=True, exist_ok=True)
    settings.settings_path().write_text("{壞掉", "utf-8")
    assert settings.load()["wheel.enabled"] is True


# ------------------------------------------------------------------ 完成頁


def test_format_size():
    assert result.format_size(512) == "512 位元組"
    assert result.format_size(8_000) == "8 KB"
    assert result.format_size(2_100_000) == "2.1 MB"
    assert result.format_size(10_600_000) == "10.6 MB"


def test_summary_filters_progress_messages(tmp_path):
    f = tmp_path / "a.pdf"
    assert result.summary_text("處理中：a.pdf", [f]) is None
    assert result.summary_text("a.pdf", [f]) is None
    assert result.summary_text("已壓縮：1 MB → 500 KB（減少 50%）", [f]).startswith("已壓縮")


def test_detail_lines_savings(tmp_path):
    a = tmp_path / "a.jpg"
    a.write_bytes(b"x" * 1000)
    b = tmp_path / "b.jpg"
    b.write_bytes(b"x" * 400)
    lines = result.detail_lines([a], [b], None)
    assert lines[0] == ("從 1 KB 縮小到 400 位元組，省下 60%", "good")
    assert lines[-1][0] == f"儲存在「{tmp_path.name}」資料夾"


def test_headline():
    assert result.headline([]) == "完成！"
    assert result.headline([Path("a")]) == "檔案已經準備好了！"
    assert result.headline([Path("a"), Path("b")]) == "2 個檔案已經準備好了！"


# ------------------------------------------------------------------ 輪盤


def test_hit_test_geometry():
    assert wheel.hit_test(0, 0, 6) == "center"
    assert wheel.hit_test(0, -150, 6) == 0  # 正上方
    assert wheel.hit_test(150, 0, 4) == 1  # 右邊（順時針第 2 格）
    assert wheel.hit_test(0, 150, 4) == 2
    assert wheel.hit_test(-150, 0, 4) == 3
    assert wheel.hit_test(0, -300, 6) is None


def test_short_title():
    assert wheel.short_title("轉成 Word（.docx）") == "Word"
    assert wheel.short_title("轉成JPG") == "JPG"
    assert wheel.short_title("壓縮 PDF（建議）") == "壓縮 PDF"


def _cat(mode, refs, targets=()):
    return wheel.catalog(
        mode,
        refs,
        minimum_files={"pdf.merge": 2},
        tint_for_tool=lambda _t: TINTS["pdf"],
        targets=lambda: targets,
    )


def test_tools_wheel_single_pdf():
    titles = [i.title for i in _cat("tools", [FileRef("/a.pdf", "application/pdf")])]
    # 「轉成可搜尋的 PDF」以「轉成」開頭，歸轉換輪盤
    assert titles == [
        "壓縮 PDF",
        "每一頁拆成一個 PDF",
        "向右旋轉 90°",
        "加上頁碼",
        "壓縮成 ZIP",
        "更多工具",
    ]


def test_convert_wheel_dedupes_and_ends_with_more():
    refs = [FileRef("/a.png", "image/png")]
    targets = [
        wheel.TargetLike("jpeg", "JPEG", "image", 0, "photo"),
        wheel.TargetLike("webp", "WebP", "image", 0, "photo"),
        wheel.TargetLike("pdf", "PDF", "document", 1, "doc.text"),
        wheel.TargetLike("zip", "ZIP", "archive", 6, "archivebox"),
    ]
    items = _cat("convert", refs, targets)
    titles = [i.title for i in items]
    assert titles[:3] == ["JPG", "PNG", "HEIC"]
    assert "JPEG" not in titles  # 和「轉成 JPG」重複
    assert "PDF" in titles and "WebP" in titles and "ZIP" not in titles
    assert items[-1].kind == "more" and len(items) <= wheel.MAX_SECTORS


def test_actions_wheel_fixed():
    items = _cat("actions", [FileRef("/a", "text/plain")])
    assert [i.payload for i in items] == [
        "open-with",
        "shelf",
        "copy",
        "move",
        "copy-path",
        "terminal",
    ]


def test_hint_line():
    keys = {"convert": "shift", "tools": "alt", "actions": "control"}
    labels = dict(settings.WHEEL_KEYS)
    assert wheel.hint_line("convert", keys, labels) == "⌥ 工具  ⌃ 動作  esc 取消"


# ------------------------------------------------------------------ 外部程式與取消


def test_process_requires_absolute_path():
    with pytest.raises(ValueError):
        process.run(["sleep", "1"])


def test_process_failure_message():
    with pytest.raises(errors.QSError, match="測試工具 轉換失敗"):
        process.run_checked(["/usr/bin/false"], "測試工具")


def test_process_cancel_kills_group():
    cancel = threading.Event()
    threading.Timer(0.3, cancel.set).start()
    started = time.monotonic()
    with pytest.raises(errors.Cancelled):
        process.run(["/usr/bin/sleep", "30"], cancel=cancel)
    assert time.monotonic() - started < 5
    assert not process.SPAWNED


def test_context_progress_segments_and_per_file(tmp_path):
    seen = []
    ctx = ToolContext(None, lambda f, m: seen.append((round(f, 2), m)))
    files = [tmp_path / "a.txt", tmp_path / "b.txt"]

    def body(f, sub):
        sub.progress(0.5)
        if f.name == "b.txt":
            raise errors.SkipFile("略過 b")
        return [f]

    out = per_file(files, ctx, body)
    assert out == [files[0]]
    assert ctx.skipped == ["略過 b"]
    assert (0.25, "處理中：a.txt") in seen and seen[-1][0] == 1.0


# ------------------------------------------------------------------ 工作程序

SLEEP_TOOL = """
import time
from pathlib import Path
from quickseemd.core.model import OTHER, ToolDefinition

def _run(files, values, ctx):
    for i in range(300):
        ctx.check_cancelled()
        ctx.progress(i / 300, "睡覺中")
        time.sleep(0.05)
    return []

def _echo(files, values, ctx):
    ctx.progress(1, "完成了")
    return [Path(files[0])]

TOOLS = [
    ToolDefinition("test.sleep", OTHER, "睡", "測試用", "bolt", _run),
    ToolDefinition("test.echo", OTHER, "回聲", "測試用", "bolt", _echo),
]
"""


@pytest.fixture
def extra_tools(tmp_path, monkeypatch):
    pkg = tmp_path / "qs_extra"
    pkg.mkdir()
    (pkg / "qs_test_tools.py").write_text(SLEEP_TOOL, "utf-8")
    monkeypatch.setenv("PYTHONPATH", str(pkg))
    monkeypatch.setenv("QUICKSEEMD_EXTRA_TOOLS", "qs_test_tools")
    sys.path.insert(0, str(pkg))
    from quickseemd.core import registry

    registry.reload()
    yield
    sys.path.remove(str(pkg))
    registry.reload()


def test_runner_in_process(extra_tools, tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("x")
    res = run_spec(JobSpec("tool", "test.echo", [str(f)]))
    assert res.error is None and res.outputs == [f] and res.summary == "完成了"
    assert res.to_json()["outputs"] == [str(f)]


def test_runner_unknown_action():
    res = run_spec(JobSpec("action", "nope.nope", ["/tmp"]))
    assert res.error == "這個版本的 QUICK_SEEMD 不認得動作「nope.nope」。"


def test_worker_roundtrip(extra_tools, tmp_path):
    f = tmp_path / "x.txt"
    f.write_text("x")
    job = WorkerJob(JobSpec("tool", "test.echo", [str(f)]), python=sys.executable)
    job.start()
    res = job.wait(30)
    assert res is not None and res.get("outputs") == [str(f)], res


def test_worker_cancel(extra_tools, tmp_path):
    progress = []
    job = WorkerJob(
        JobSpec("tool", "test.sleep", [str(tmp_path)]),
        on_progress=lambda f, m: progress.append(f),
        python=sys.executable,
    )
    job.start()
    deadline = time.monotonic() + 15
    while not progress and time.monotonic() < deadline:
        time.sleep(0.05)
    job.cancel()
    res = job.wait(15)
    assert res is not None and res.get("cancelled") is True
    assert job._proc is not None and job._proc.returncode is not None  # 程序已結束並回收


# ------------------------------------------------------------------ 剪貼簿與動態範圍


def test_clipboard_travels_in_result(extra_tools, tmp_path):
    from quickseemd.core.model import ToolContext as Ctx

    ctx = Ctx(None)
    ctx.segment(0, 2).set_clipboard("內容")
    assert ctx.clipboard == "內容"
    res = run_spec(JobSpec("tool", "test.echo", [str(tmp_path)]))
    assert "clipboard" not in res.to_json()
    res.clipboard = "A\nB"
    assert res.to_json()["clipboard"] == "A\nB"


def test_option_dynamic_range_and_suggestions():
    from quickseemd.core.options import Choice, Kind, Option

    opt = Option(
        "start",
        Kind.SLIDER,
        minimum=0,
        maximum=10,
        dynamic_range=lambda v, i: (0, i.get("duration", 10) - 0.2),
    )
    assert opt.current_range({}, {"duration": 5}) == (0.0, 4.8)
    plain = Option("x", Kind.TEXT, suggestions=(Choice("25", "25 MB"),))
    assert plain.current_range({}, {}) == (None, None)
    assert plain.suggestions[0].value == "25"


def test_custom_color_default_and_adjust_hook():
    from quickseemd.core.model import OTHER, ToolDefinition
    from quickseemd.core.options import Choice, Kind, Option

    color = Option(
        "bg",
        Kind.COLOR,
        default="#ffffff",
        custom_key="bg_custom",
        custom_default="#e3332e",
        choices=(
            Choice("#ffffff", "白色"),
            Choice("transparent", "透明"),
            Choice("custom", "自訂"),
        ),
    )
    tool = ToolDefinition(
        "test.adjust",
        OTHER,
        "t",
        "t",
        "bolt",
        lambda f, v, c: [],
        options=(color,),
        adjust=lambda v, k, i: {"bg_custom": "#000000"} if k == "bg" else {},
    )
    assert tool.defaults() == {"bg": "#ffffff", "bg_custom": "#e3332e"}
    assert tool.adjust({"bg": "custom"}, "bg", {}) == {"bg_custom": "#000000"}


def test_converter_describe_text_and_folder(tmp_path):
    from quickseemd.core import converter

    f = tmp_path / "a.txt"
    f.write_bytes("中文\r\n第二行\r\n".encode("utf-8"))
    kind, detail = converter.describe(f)
    assert kind and detail and "UTF-8" in detail and "CRLF" in detail
    (tmp_path / "d").mkdir()
    (tmp_path / "d" / "x").write_text("1")
    assert converter.describe(tmp_path / "d")[1] == "1 個項目"


def test_dynamic_placeholder_field():
    from quickseemd.core.options import Kind, Option

    opt = Option(
        "name",
        Kind.TEXT,
        placeholder="檔名-已合併",
        dynamic_placeholder=lambda v, i: f"{i.get('first_base', '檔名')}-已合併",
    )
    assert opt.dynamic_placeholder({}, {"first_base": "報告"}) == "報告-已合併"


# ------------------------------------------------------------------ 外部程式的邊界情況


def test_process_callback_error_does_not_hang(tmp_path):
    script = tmp_path / "many.sh"
    script.write_text("#!/bin/sh\nseq 1 200000\n")
    script.chmod(0o755)

    def boom(_line):
        raise ValueError("回呼壞了")

    started = time.monotonic()
    with pytest.raises(ValueError, match="回呼壞了"):
        process.run([str(script)], on_line=boom, timeout=30)
    assert time.monotonic() - started < 20
    assert not process.SPAWNED


def test_process_stdin_to_child_that_does_not_read():
    started = time.monotonic()
    res = process.run(["/usr/bin/true"], stdin_bytes=b"x" * (8 * 1024 * 1024), timeout=10)
    assert res.returncode == 0 and time.monotonic() - started < 10


def test_process_timeout_is_reported_even_for_exit_zero_children():
    with pytest.raises(errors.QSError, match="執行逾時"):
        process.run(["/usr/bin/sleep", "5"], timeout=0.3)
    assert not process.SPAWNED


def test_process_not_executable_closes_output_file(tmp_path):
    exe = tmp_path / "not-exec"
    exe.write_text("x")
    out = tmp_path / "out.bin"
    with pytest.raises(errors.QSError, match="轉換失敗"):
        process.run([str(exe)], stdout_path=out)
    assert out.exists() and out.stat().st_size == 0


def test_skipped_files_appear_in_summary(extra_tools, tmp_path):
    from quickseemd.core.model import ToolContext
    from quickseemd.core.runner import _summary_with_skips
    from quickseemd.core.model import RunResult

    res = RunResult(
        action="x", outputs=[tmp_path / "a.png"], skipped=["在「b」裡找不到臉孔，未產生檔案。"]
    )
    text = _summary_with_skips("處理中：b.png", res, [str(tmp_path / "b.png")])
    assert text == "已略過 1 個檔案：在「b」裡找不到臉孔，未產生檔案。"
    text = _summary_with_skips("共處理 2 張臉", res, [])
    assert text.startswith("共處理 2 張臉；已略過 1 個檔案：")


# ------------------------------------------------------------------ 安全檢查修正


def test_output_directory_does_not_follow_symlinks(tmp_path):
    from quickseemd.core.model import ToolContext as Ctx

    real = tmp_path / "real"
    real.mkdir()
    (real / "a.txt").write_text("x")
    link_dir = tmp_path / "links"
    link_dir.mkdir()
    (link_dir / "a.txt").symlink_to(real / "a.txt")
    assert Ctx(None).output_directory(link_dir / "a.txt") == link_dir


def test_action_refuses_wrong_file_type(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("純文字")
    res = run_spec(JobSpec("action", "pdf.compress-recommended", [str(f)]))
    assert res.error == "「壓縮 PDF（建議）」不適用於這些檔案。"
    res = run_spec(JobSpec("action", "office.to-pdf", [str(f)]))
    assert "不認得動作" in res.error


def test_worker_caps_its_memory(monkeypatch):
    import resource

    from quickseemd import worker

    monkeypatch.setenv(worker.MEM_LIMIT_ENV, "1")
    soft_before, hard_before = resource.getrlimit(resource.RLIMIT_DATA)
    try:
        limit = worker.cap_memory()
        assert limit <= 1024**3
        assert resource.getrlimit(resource.RLIMIT_DATA)[0] == limit
    finally:
        resource.setrlimit(resource.RLIMIT_DATA, (soft_before, hard_before))


def test_move_keeps_source_when_it_changes_during_copy(tmp_path, monkeypatch):
    import errno as _errno

    from quickseemd.core import naming

    src = tmp_path / "src"
    src.mkdir()
    (src / "a.txt").write_text("1")
    dest = tmp_path / "dest"
    dest.mkdir()
    real_copy = naming._copy_tree_exclusive

    def copy_then_add(s, d):
        real_copy(s, d)
        (s / "late.txt").write_text("複製期間新增")

    def exdev(s, d):
        raise OSError(_errno.EXDEV, "跨磁碟")

    monkeypatch.setattr(naming, "_rename_noreplace_or_link", exdev)
    monkeypatch.setattr(naming, "_copy_tree_exclusive", copy_then_add)
    with pytest.raises(errors.QSError):
        naming.move_no_overwrite(src, dest)
    assert (src / "late.txt").exists() and (src / "a.txt").exists()


def test_worker_applies_image_budget_to_every_tool(tmp_path):
    """工作程序啟動時就載入像素上限：直接 Image.open 的工具（例如讀 QR）也會擋下超大圖。"""
    from PIL import Image

    big = tmp_path / "巨大.png"
    Image.new("1", (20000, 20000)).save(big)  # 檔案很小，但有 4 億像素
    job = WorkerJob(JobSpec("action", "other.read-qr", [str(big)]), python=sys.executable)
    job.start()
    res = job.wait(60)
    assert res is not None and "上限" in res.get("error", ""), res
