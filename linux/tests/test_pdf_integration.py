"""經過執行器（右鍵動作與工具頁面的同一條路）跑 PDF 工具。"""

from __future__ import annotations

from conftest import make_pdf
from quickseemd.core.runner import JobSpec, run_spec
from test_pdf_helpers import encrypt, pdftotext


def test_quick_actions_through_runner(tmp_path):
    a = make_pdf(tmp_path / "甲.pdf", pages=2)
    b = make_pdf(tmp_path / "乙.pdf", pages=1)
    merged = run_spec(JobSpec("action", "pdf.merge-pdfs", [str(a), str(b)]))
    assert merged.error is None and merged.outputs[0].name == "甲-已合併.pdf"
    rotated = run_spec(JobSpec("action", "pdf.rotate-right", [str(a)]))
    assert rotated.outputs[0].name == "甲-已旋轉.pdf"
    numbered = run_spec(JobSpec("action", "pdf.add-page-numbers", [str(b)]))
    assert "第 1 頁" in pdftotext(numbered.outputs[0])
    split = run_spec(JobSpec("action", "pdf.split-pages", [str(a)]))
    assert split.outputs[0].name == "甲-已拆分"


def test_tool_validation_through_runner(tmp_path, pdf_file):
    locked = encrypt(pdf_file, tmp_path / "鎖.pdf", user="u")
    res = run_spec(JobSpec("tool", "pdf.split", [str(locked)]))
    assert res.error == "「鎖.pdf」受密碼保護，請先用「解鎖 PDF」移除密碼"
    bad = run_spec(JobSpec("tool", "pdf.rotate", [str(pdf_file)], {"pages": "1-99"}))
    assert bad.error == "頁碼範圍不正確"
    ok = run_spec(JobSpec("tool", "pdf.unlock", [str(locked)], {"password": "u"}))
    assert ok.error is None and ok.outputs[0].name == "鎖-已解鎖.pdf"
    action = run_spec(JobSpec("action", "pdf.compress-recommended", [str(locked)]))
    assert action.error == "「鎖.pdf」受密碼保護，請先用「解鎖 PDF」工具移除密碼後再試。"
