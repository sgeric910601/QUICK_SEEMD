"""進度面板每一列的文字與按鈕（對應原版 QuickActionRow）。"""

from __future__ import annotations

from pathlib import Path

import pytest
from agent_fakes import fake_tools  # noqa: F401 — pytest fixture

from quickseemd.agent import hud_model as hm
from quickseemd.agent.queue import QueueItem, Status
from quickseemd.core.model import TINTS


def _item(**kw):
    base = dict(
        id=1,
        title="壓縮成 ZIP",
        icon="doc.zipper",
        tint=TINTS["teal"],
        files=(Path("/tmp/a.txt"),),
        tool_id="other.compress",
    )
    base.update(kw)
    return QueueItem(**base)


def test_waiting_line_single_and_many():
    assert hm.state_line(_item()) == "等待中：a.txt"
    many = _item(files=(Path("/a"), Path("/b"), Path("/c")))
    assert hm.state_line(many) == "等待中：3 個檔案"


def test_running_line_prefers_message():
    assert hm.state_line(_item(status=Status.RUNNING)) == "a.txt"
    assert hm.state_line(_item(status=Status.RUNNING, message="處理中：a.txt")) == "處理中：a.txt"


def test_done_lines():
    assert hm.state_line(_item(status=Status.DONE)) == "完成"
    assert hm.state_line(_item(status=Status.DONE, message="已拷貝")) == "已拷貝"
    one = _item(status=Status.DONE, outputs=(Path("/tmp/a.zip"),))
    assert hm.state_line(one) == "a.zip"
    four = _item(status=Status.DONE, outputs=tuple(Path(f"/tmp/{i}") for i in range(4)))
    assert hm.state_line(four) == "完成 4 個檔案"


def test_failed_line_is_reason():
    assert hm.state_line(_item(status=Status.FAILED, reason="壞了")) == "壞了"


def test_buttons_for_done_outputs():
    one = _item(status=Status.DONE, outputs=(Path("/tmp/a.zip"),))
    assert hm.buttons(one) == ("reveal", "open")
    three = _item(status=Status.DONE, outputs=tuple(Path(f"/tmp/{i}") for i in range(3)))
    assert hm.buttons(three) == ("reveal", "open")
    four = _item(status=Status.DONE, outputs=tuple(Path(f"/tmp/{i}") for i in range(4)))
    assert hm.buttons(four) == ("reveal",)
    assert hm.buttons(_item(status=Status.DONE)) == ()


def test_buttons_running_has_cancel():
    assert hm.buttons(_item(status=Status.RUNNING)) == ("cancel",)
    assert hm.buttons(_item()) == ()


def test_adjust_link_only_for_real_tools(fake_tools):
    failed = _item(status=Status.FAILED, reason="x")
    assert hm.buttons(failed) == ("adjust",)
    assert hm.buttons(_item(status=Status.FAILED, reason="x", tool_id=None)) == ()
    assert hm.buttons(_item(status=Status.FAILED, reason="x", tool_id="no.such")) == ()


def test_button_labels_are_original_text():
    assert hm.BUTTON_LABELS == {
        "reveal": "在資料夾中顯示",
        "open": "開啟",
        "cancel": "取消",
        "adjust": "在 QUICK_SEEMD 中調整選項…",
    }


@pytest.mark.parametrize(
    ("status", "kind"),
    [
        (Status.WAITING, "secondary"),
        (Status.RUNNING, "progress"),
        (Status.DONE, "success"),
        (Status.FAILED, "warning"),
    ],
)
def test_line_style(status, kind):
    assert hm.line_style(_item(status=status)) == kind


def test_hud_position_top_right_of_workarea():
    assert hm.hud_origin((0, 27, 1920, 1053), 360) == (1920 - 360 - 14, 27 + 10)
    assert hm.hud_origin((1920, 0, 1280, 800), 360) == (1920 + 1280 - 360 - 14, 10)


def test_rgb_hex():
    assert hm.rgb_hex((1.0, 0.5, 0.0)) == "#ff8000"
    assert hm.rgb_hex((0.10, 0.60, 0.62)) == "#1a999e"


def test_qr_payload_strips_headers():
    text = "# a.png\nhttps://example.com\n第二行\n\n# b.png\nB 的內容\n"
    assert hm.qr_payload(text) == "https://example.com\n第二行\nB 的內容"


def test_qr_payload_single_file_without_header():
    assert hm.qr_payload("只有一行\n") == "只有一行"


def test_qr_clipboard_text_reads_output(tmp_path):
    out = tmp_path / "a-QR內容.txt"
    out.write_text("# a.png\nhello\n", "utf-8")
    item = _item(status=Status.DONE, outputs=(out,), action_id="other.read-qr")
    assert hm.qr_clipboard_text(item) == "hello"
    other = _item(status=Status.DONE, outputs=(out,), action_id="other.zip")
    assert hm.qr_clipboard_text(other) is None
    missing = _item(status=Status.DONE, outputs=(tmp_path / "x.txt",), action_id="other.read-qr")
    assert hm.qr_clipboard_text(missing) is None


def test_qr_clipboard_text_ignores_huge_files(tmp_path):
    out = tmp_path / "big.txt"
    out.write_bytes(b"a" * (hm.QR_MAX_BYTES + 1))
    item = _item(status=Status.DONE, outputs=(out,), action_id="other.read-qr")
    assert hm.qr_clipboard_text(item) is None


def test_file_summary():
    assert hm.file_summary(()) == "0 個檔案"
    assert hm.file_summary((Path("/x/y.pdf"),)) == "y.pdf"
