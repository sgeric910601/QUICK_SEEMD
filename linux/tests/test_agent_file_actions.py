"""「動作」輪盤的檔案動作：文字、剪貼簿內容、終端機資料夾與「移動到…」。"""

from __future__ import annotations

import os
import threading
import time
from pathlib import Path

import pytest

from quickseemd.agent import file_actions as fa
from quickseemd.core import errors


def test_count_label():
    assert fa.count_label([Path("/a/報告.pdf")]) == "「報告.pdf」"
    assert fa.count_label([Path("/a"), Path("/b")]) == "2 個項目"


def test_copy_notice_text():
    assert fa.copy_notice([Path("/a/x.txt")]) == ("已拷貝", "「x.txt」，可到檔案管理員貼上")
    assert fa.copy_notice([Path("/a"), Path("/b"), Path("/c")]) == (
        "已拷貝",
        "3 個項目，可到檔案管理員貼上",
    )


def test_copy_path_text_and_notice():
    paths = [Path("/a/x.txt"), Path("/b/有 空格.pdf")]
    assert fa.copy_path_text(paths) == "/a/x.txt\n/b/有 空格.pdf"
    assert fa.copy_path_notice(paths[:1]) == ("已拷貝路徑", "/a/x.txt")
    assert fa.copy_path_notice(paths) == ("已拷貝路徑", "2 個路徑")


def test_clipboard_payloads_for_file_manager():
    paths = [Path("/tmp/a b.txt"), Path("/tmp/中文.pdf")]
    p = fa.clipboard_payloads(paths)
    uris = ["file:///tmp/a%20b.txt", "file:///tmp/%E4%B8%AD%E6%96%87.pdf"]
    assert p["x-special/gnome-copied-files"] == ("copy\n" + "\n".join(uris)).encode()
    assert p["text/uri-list"] == ("\r\n".join(uris) + "\r\n").encode()
    assert p["UTF8_STRING"] == "/tmp/a b.txt\n/tmp/中文.pdf".encode()
    assert p["text/plain;charset=utf-8"] == p["UTF8_STRING"]
    assert list(p)[0] == "x-special/gnome-copied-files"


def test_move_prompt_and_title():
    assert fa.move_prompt([Path("/a/x.pdf")]) == "要把「x.pdf」移到哪裡？"
    assert fa.move_prompt([Path("/a"), Path("/b")]) == "要把2 個項目移到哪裡？"
    assert fa.move_title(Path("/home/u/文件")) == "移動到「文件」"
    assert fa.move_title(Path("/")) == "移動到「/」"


def test_terminal_folder(tmp_path):
    d = tmp_path / "資料夾"
    d.mkdir()
    f = d / "a.txt"
    f.write_text("x")
    assert fa.terminal_folder(d) == d
    assert fa.terminal_folder(f) == d


def test_terminal_argv_prefers_gnome_terminal(tmp_path):
    argv, cwd = fa.terminal_command(tmp_path, exists=lambda p: True)
    assert argv == ["/usr/bin/gnome-terminal", f"--working-directory={tmp_path}"]
    assert cwd == tmp_path


def test_terminal_argv_fallback(tmp_path):
    argv, cwd = fa.terminal_command(tmp_path, exists=lambda p: p == fa.FALLBACK_TERMINAL)
    assert argv == [fa.FALLBACK_TERMINAL]
    assert cwd == tmp_path


def test_terminal_argv_none(tmp_path):
    assert fa.terminal_command(tmp_path, exists=lambda p: False) is None


def _progress_log():
    seen: list[tuple[float, str | None]] = []
    return seen, lambda f, m: seen.append((f, m))


def test_move_files_moves_without_overwrite(tmp_path):
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    (src / "a.txt").write_text("新的")
    (dst / "a.txt").write_text("舊的")
    (src / "b.txt").write_text("b")
    seen, progress = _progress_log()
    moved = fa.move_files([src / "a.txt", src / "b.txt"], dst, progress, threading.Event())
    assert moved == [dst / "a (1).txt", dst / "b.txt"]
    assert (dst / "a.txt").read_text() == "舊的"
    assert (dst / "a (1).txt").read_text() == "新的"
    assert not (src / "a.txt").exists()
    assert seen[-1] == (1.0, "b.txt")


def test_move_files_skips_files_already_there(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("x")
    moved = fa.move_files([f], tmp_path, lambda *_: None, threading.Event())
    assert moved == [f]
    assert f.read_text() == "x"


def test_move_folder_into_itself_is_refused(tmp_path):
    d = tmp_path / "d"
    (d / "sub").mkdir(parents=True)
    with pytest.raises(errors.QSError, match="移到它自己裡面"):
        fa.move_files([d], d / "sub", lambda *_: None, threading.Event())
    assert (d / "sub").is_dir()


def test_move_files_honours_cancel(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("x")
    dst = tmp_path / "dst"
    dst.mkdir()
    cancel = threading.Event()
    cancel.set()
    with pytest.raises(errors.Cancelled):
        fa.move_files([f], dst, lambda *_: None, cancel)
    assert f.exists()


def test_move_job_runs_in_thread(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("x")
    dst = tmp_path / "dst"
    dst.mkdir()
    results = []
    job = fa.MoveJob([f], dst, lambda *_: None, results.append)
    job.start()
    deadline = time.monotonic() + 5
    while not results and time.monotonic() < deadline:
        time.sleep(0.01)
    assert results == [{"outputs": [str(dst / "a.txt")]}]


def test_move_job_reports_error(tmp_path):
    results = []
    job = fa.MoveJob([tmp_path / "missing.txt"], tmp_path / "nope", lambda *_: None, results.append)
    job.start()
    deadline = time.monotonic() + 5
    while not results and time.monotonic() < deadline:
        time.sleep(0.01)
    assert "error" in results[0]


def test_move_job_cancelled(tmp_path):
    files = []
    for i in range(3):
        f = tmp_path / f"{i}.txt"
        f.write_text("x")
        files.append(f)
    dst = tmp_path / "dst"
    dst.mkdir()
    results = []
    gate = threading.Event()

    def progress(_f, _m):
        gate.wait(2)

    job = fa.MoveJob(files, dst, progress, results.append)
    job.start()
    job.cancel()
    gate.set()
    deadline = time.monotonic() + 5
    while not results and time.monotonic() < deadline:
        time.sleep(0.01)
    assert results[0].get("cancelled") is True


def test_existing_paths_filters(tmp_path):
    f = tmp_path / "a"
    f.write_text("x")
    assert fa.existing_paths([str(f), str(tmp_path / "missing"), "relative"]) == [f]


@pytest.mark.skipif(os.geteuid() == 0, reason="root 不受權限限制")
def test_move_into_readonly_folder_fails(tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("x")
    ro = tmp_path / "ro"
    ro.mkdir()
    ro.chmod(0o500)
    try:
        with pytest.raises(errors.QSError):
            fa.move_files([f], ro, lambda *_: None, threading.Event())
    finally:
        ro.chmod(0o700)
    assert f.exists()
