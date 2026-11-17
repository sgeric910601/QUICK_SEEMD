"""Nemo 傳來的網址 → 實際檔案（trash://、recent://、sftp:// 盡量轉成本機路徑）。"""

from __future__ import annotations

import os
import threading
from pathlib import Path

import pytest
from gi.repository import Gio, GLib

from quickseemd.previewer.uri import MAX_URI_LENGTH, Target, TargetKind, resolve


class FakeInfo:
    def __init__(self, target: str | None, display: str | None) -> None:
        self._target = target
        self._display = display

    def get_attribute_string(self, name: str) -> str | None:
        return self._target if name == "standard::target-uri" else None

    def get_display_name(self) -> str | None:
        return self._display


class FakeFile:
    def __init__(self, path=None, target=None, error=None, basename="遠端.txt", display=None):
        self._path = path
        self._target = target
        self._error = error
        self._basename = basename
        self._display = display

    def get_path(self):
        return self._path

    def get_basename(self):
        return self._basename

    def query_info(self, attributes, flags, cancellable):  # noqa: ARG002
        if self._error is not None:
            raise self._error
        return FakeInfo(self._target, self._display)


def _factory(fake: FakeFile):
    return lambda uri: fake  # noqa: ARG005


def test_plain_file_uri(tmp_path: Path):
    f = tmp_path / "說明 文件.txt"
    f.write_text("hi", "utf-8")
    t = resolve(Gio.File.new_for_path(str(f)).get_uri())
    assert t.kind is TargetKind.FILE
    assert t.path == f
    assert t.name == "說明 文件.txt"


def test_directory(tmp_path: Path):
    t = resolve(Gio.File.new_for_path(str(tmp_path)).get_uri())
    assert t.kind is TargetKind.FOLDER
    assert t.path == tmp_path


def test_missing_file(tmp_path: Path):
    t = resolve(Gio.File.new_for_path(str(tmp_path / "不存在.txt")).get_uri())
    assert t.kind is TargetKind.MISSING
    assert t.name == "不存在.txt"


def test_broken_symlink_is_missing(tmp_path: Path):
    link = tmp_path / "壞掉的連結"
    link.symlink_to(tmp_path / "nowhere")
    assert resolve(Gio.File.new_for_path(str(link)).get_uri()).kind is TargetKind.MISSING


def test_fifo_is_special_and_never_opened(tmp_path: Path):
    fifo = tmp_path / "管線"
    os.mkfifo(fifo)
    result: list[Target] = []
    worker = threading.Thread(target=lambda: result.append(resolve(f"file://{fifo}")))
    worker.start()
    worker.join(timeout=5)
    assert not worker.is_alive(), "解析管線檔時卡住了（不能打開它）"
    assert result[0].kind is TargetKind.SPECIAL
    assert "管線" in result[0].detail


def test_symlink_to_fifo_is_special(tmp_path: Path):
    fifo = tmp_path / "p"
    os.mkfifo(fifo)
    link = tmp_path / "連到管線"
    link.symlink_to(fifo)
    assert resolve(f"file://{link}").kind is TargetKind.SPECIAL


def test_char_device_is_special():
    t = resolve("file:///dev/null")
    assert t.kind is TargetKind.SPECIAL
    assert "裝置" in t.detail


def test_trash_uri_uses_target_uri(tmp_path: Path):
    real = tmp_path / "已刪除.pdf"
    real.write_bytes(b"%PDF-1.4")
    fake = FakeFile(path=None, target=real.as_uri(), display="已刪除.pdf")
    t = resolve("trash:///已刪除.pdf", file_for_uri=_factory(fake))
    assert t.kind is TargetKind.FILE
    assert t.path == real
    assert t.uri == "trash:///已刪除.pdf"
    assert t.name == "已刪除.pdf"


def test_recent_uri_uses_target_uri(tmp_path: Path):
    real = tmp_path / "最近.txt"
    real.write_text("x", "utf-8")
    fake = FakeFile(target=real.as_uri())
    t = resolve("recent:///abc", file_for_uri=_factory(fake))
    assert t.kind is TargetKind.FILE and t.path == real


def test_sftp_with_fuse_path(tmp_path: Path):
    real = tmp_path / "遠端.txt"
    real.write_text("x", "utf-8")
    fake = FakeFile(path=str(real))
    t = resolve("sftp://host/home/u/遠端.txt", file_for_uri=_factory(fake))
    assert t.kind is TargetKind.FILE and t.path == real


def test_sftp_without_local_path_is_remote():
    fake = FakeFile(path=None, target=None, basename="報告.docx")
    t = resolve("sftp://host/報告.docx", file_for_uri=_factory(fake))
    assert t.kind is TargetKind.REMOTE
    assert t.path is None
    assert t.name == "報告.docx"


def test_query_error_is_remote():
    err = GLib.Error.new_literal(Gio.io_error_quark(), "無法連線", Gio.IOErrorEnum.FAILED)
    fake = FakeFile(error=err)
    assert resolve("smb://x/y", file_for_uri=_factory(fake)).kind is TargetKind.REMOTE


def test_target_uri_that_is_not_local_is_remote():
    fake = FakeFile(target="https://example.com/a.txt")
    assert resolve("recent:///a", file_for_uri=_factory(fake)).kind is TargetKind.REMOTE


@pytest.mark.parametrize(
    "bad",
    ["", "no-scheme-here", "file:///a\0b", "x" * (MAX_URI_LENGTH + 1), "file:///a\nb"],
)
def test_invalid_uris(bad: str):
    assert resolve(bad).kind is TargetKind.INVALID


def test_non_string_is_invalid():
    assert resolve(None).kind is TargetKind.INVALID  # type: ignore[arg-type]


def test_local_uri_property(tmp_path: Path):
    f = tmp_path / "a.txt"
    f.write_text("x", "utf-8")
    t = resolve(f.as_uri())
    assert t.local_uri == f.as_uri()
    assert Target(TargetKind.REMOTE, "sftp://h/a", "a").local_uri == "sftp://h/a"
