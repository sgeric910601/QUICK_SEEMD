"""PDF 工具的登記：16 個工具、預設動作、選項與圖示。"""

from __future__ import annotations

from pathlib import Path

from quickseemd.core import quickactions, registry
from quickseemd.core.options import Kind
from quickseemd.core.tools.pdf import TOOLS

EXPECTED = {
    "pdf.merge": ("合併 PDF", "rectangle.stack.badge.plus"),
    "pdf.split": ("拆分 PDF", "scissors"),
    "pdf.compress": ("壓縮 PDF", "arrow.down.right.and.arrow.up.left"),
    "pdf.to-images": ("PDF 轉圖片", "photo.on.rectangle.angled"),
    "pdf.from-images": ("圖片轉 PDF", "photo.stack"),
    "pdf.organize": ("整理 PDF", "square.grid.3x3.square"),
    "pdf.delete-pages": ("刪除頁面", "doc.badge.ellipsis"),
    "pdf.rotate": ("旋轉 PDF", "rotate.right"),
    "pdf.watermark": ("加入浮水印", "seal"),
    "pdf.page-numbers": ("加入頁碼", "number.square"),
    "pdf.protect": ("保護 PDF", "lock.doc"),
    "pdf.unlock": ("解鎖 PDF", "lock.open"),
    "pdf.ocr": ("OCR PDF", "text.viewfinder"),
    "pdf.to-text": ("PDF 轉文字", "doc.plaintext"),
    "pdf.from-html": ("HTML 轉 PDF", "globe"),
    "pdf.metadata": ("編輯 PDF 資訊", "info.circle"),
}


def test_all_sixteen_tools_registered_in_order():
    assert {t.id for t in TOOLS} == set(EXPECTED)
    ordered = [t.id for t in registry.tools_in(TOOLS[0].category)]
    assert ordered == list(registry.ORDER["pdf"])
    for t in TOOLS:
        assert (t.title, t.icon) == EXPECTED[t.id]
        assert t.subtitle.endswith("。")


def test_quick_action_presets_match():
    by_id = {t.id: t for t in TOOLS}
    for act in quickactions.ACTIONS:
        if not act.id.startswith("pdf."):
            continue
        t = by_id[act.tool_id]
        assert t.presets[act.id] == act.preset
        values = t.values_for_preset(act.id)
        for key in act.preset:
            assert t.option(key) is not None


def test_options_are_well_formed():
    for t in TOOLS:
        for opt in t.options:
            if opt.kind in (Kind.CARDS, Kind.CHIPS, Kind.SEGMENTED, Kind.COLOR, Kind.GRID):
                assert opt.default in [c.value for c in opt.choices], (t.id, opt.key)
            if opt.kind is Kind.NOTE:
                assert opt.label or opt.note is not None
            if opt.note is not None and opt.kind is not Kind.NOTE:
                opt.note(t.defaults(), {})  # 沒有檔案資訊時也不能出錯
            assert opt.is_visible(t.defaults(), {}) in (True, False)


def test_validators_accept_defaults_without_files(tmp_path: Path):
    for t in TOOLS:
        if t.validate is None or t.id in ("pdf.protect", "pdf.unlock"):
            continue
        values = t.defaults()
        if t.id == "pdf.delete-pages":
            values["pages"] = "1"
        if t.id == "pdf.split":
            values["ranges"] = "1"
        assert t.validate([], values, {"locked": []}) is None, t.id


def test_inspect_is_safe_for_missing_files(tmp_path: Path):
    for t in TOOLS:
        if t.inspect is not None:
            info = t.inspect([tmp_path / "沒有.pdf"])
            assert isinstance(info, dict)
