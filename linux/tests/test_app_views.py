"""自訂畫面的純邏輯：整理 PDF 的頁面清單、裁剪框、剪輯範圍與波形。"""

from __future__ import annotations

import json

import pytest

from quickseemd.app.views import crop_model as cm
from quickseemd.app.views import organize_model as orgm
from quickseemd.app.views import trim_model as tm

# ---------------------------------------------------------------- 整理 PDF


def _srcs(m: orgm.OrganizeModel) -> list:
    return [p.src for p in m.pages]


def test_organize_load_and_json():
    m = orgm.OrganizeModel()
    assert m.to_value() == ""
    m.load(3)
    assert _srcs(m) == [0, 1, 2] and m.original_count == 3 and not m.is_modified
    data = json.loads(m.to_value())
    assert data == [{"src": 0, "rotate": 0}, {"src": 1, "rotate": 0}, {"src": 2, "rotate": 0}]


def test_organize_rotate_delete_insert_move_reverse_reset():
    m = orgm.OrganizeModel()
    m.load(4)
    ids = [p.uid for p in m.pages]
    m.rotate({ids[0]}, 90)
    m.rotate({ids[0]}, 90)
    m.rotate({ids[1]}, -90)
    assert [p.rotate for p in m.pages][:2] == [180, 270]
    m.delete({ids[2]})
    assert _srcs(m) == [0, 1, 3]
    m.insert_blank(ids[0])
    assert _srcs(m) == [0, None, 1, 3]
    m.insert_blank(None)
    assert _srcs(m) == [0, None, 1, 3, None]
    m.move(ids[3], ids[0])
    assert _srcs(m)[:2] == [3, 0]
    m.reverse()
    assert _srcs(m)[0] is None
    assert m.is_modified
    data = json.loads(m.to_value())
    assert data[-1] == {"src": 3, "rotate": 0}
    m.reset()
    assert _srcs(m) == [0, 1, 2, 3] and not m.is_modified and m.selection == frozenset()


def test_organize_selection_rules():
    m = orgm.OrganizeModel()
    m.load(5)
    ids = [p.uid for p in m.pages]
    m.tap(ids[1])
    assert m.selection == {ids[1]}
    m.tap(ids[1])
    assert m.selection == frozenset()
    m.tap(ids[1])
    m.tap(ids[3], ctrl=True)
    assert m.selection == {ids[1], ids[3]}
    m.tap(ids[3], ctrl=True)
    assert m.selection == {ids[1]}
    m.tap(ids[4], shift=True)
    assert m.selection == set(ids[1:5])
    m.delete(m.selection)
    assert _srcs(m) == [0] and m.selection == frozenset()
    m.clear_selection()


def test_organize_labels_and_validation():
    m = orgm.OrganizeModel()
    m.load(3)
    ids = [p.uid for p in m.pages]
    m.move(ids[2], ids[0])
    assert orgm.cell_label(m.pages[0], 1) == ("1", "原第 3 頁")
    assert orgm.cell_label(m.pages[1], 2) == ("2", "原第 1 頁")
    same = orgm.OrganizeModel()
    same.load(2)
    assert orgm.cell_label(same.pages[1], 2) == ("2", None)
    m.insert_blank(None)
    assert orgm.cell_label(m.pages[-1], 4) == ("4", "空白頁")
    assert orgm.cell_tooltip(m.pages[0]) == "原檔第 3 頁"
    assert orgm.cell_tooltip(m.pages[-1]) == "空白頁"
    assert m.validate() is None
    m.delete({p.uid for p in m.pages})
    assert m.validate() == "至少要保留一頁"
    assert orgm.empty_text(m.original_count) == "所有頁面都已刪除"
    assert orgm.empty_text(0) == "無法讀取這份 PDF 的頁面"
    assert orgm.selection_title(2) == "已選取 2 頁"


def test_organize_shown_size():
    assert orgm.fit_size((595, 842), 0, (132, 150)) == pytest.approx((106.06, 150), abs=0.1)
    w, h = orgm.fit_size((595, 842), 90, (132, 150))
    assert w == pytest.approx(132) and h < 150


# ---------------------------------------------------------------- 裁剪


def test_crop_centered_and_default():
    r = cm.centered(16 / 9, 4 / 3)
    assert r.x == 0 and r.w == 1 and r.h == pytest.approx(0.75)
    r2 = cm.centered(1.0, 2.0)
    assert r2.w == pytest.approx(0.5) and r2.x == pytest.approx(0.25)
    assert cm.default_rect(None, 400, 300) == cm.Rect(0, 0, 1, 1)
    assert cm.default_rect(1.0, 0, 300) == cm.Rect(0, 0, 1, 1)


def test_crop_pixels_and_json():
    r = cm.Rect(0.25, 0.1, 0.5, 0.5)
    assert cm.pixel_rect(r, 400, 300) == (100, 30, 200, 150)
    data = json.loads(cm.to_value(r, 400, 300))
    assert data == {"x": 100, "y": 30, "w": 200, "h": 150}
    assert cm.pixel_rect(cm.Rect(0.999, 0.999, 0.5, 0.5), 10, 10) == (9, 9, 1, 1)
    assert cm.from_value('{"x": 100, "y": 30, "w": 200, "h": 150}', 400, 300) == r
    assert cm.from_value("not json", 400, 300) is None
    assert cm.from_value('{"x": 1}', 400, 300) is None
    assert cm.size_text(r, 400, 300) == "200 × 150 px"


def test_crop_move_clamped():
    r = cm.Rect(0.1, 0.1, 0.5, 0.5)
    assert cm.move(r, 0.2, 0.0) == cm.Rect(pytest.approx(0.3), 0.1, 0.5, 0.5)
    m = cm.move(r, 2.0, -2.0)
    assert m.x == pytest.approx(0.5) and m.y == 0


def test_crop_resize_free_and_ratio():
    r = cm.Rect(0.2, 0.2, 0.4, 0.4)
    free = cm.resize(r, "br", 0.1, 0.2, None, 1.0, 0.05)
    assert (free.w, free.h) == (pytest.approx(0.5), pytest.approx(0.6))
    left = cm.resize(r, "l", 0.1, 0.0, None, 1.0, 0.05)
    assert left.x == pytest.approx(0.3) and left.w == pytest.approx(0.3)
    # 固定比例（1:1、圖片 2:1）：正規化高度 = 寬 × 2
    sq = cm.resize(cm.Rect(0.1, 0.1, 0.2, 0.4), "br", 0.1, 0.0, 1.0, 2.0, 0.02)
    assert sq.w == pytest.approx(0.3) and sq.h == pytest.approx(0.6)
    big = cm.resize(cm.Rect(0.1, 0.1, 0.2, 0.4), "br", 5.0, 5.0, 1.0, 2.0, 0.02)
    assert big.y + big.h <= 1.0 + 1e-9 and big.x + big.w <= 1.0 + 1e-9
    assert big.w * 2 == pytest.approx(big.h)
    tiny = cm.resize(r, "tl", 1.0, 1.0, None, 1.0, 0.05)
    assert tiny.w == pytest.approx(0.05) and tiny.h == pytest.approx(0.05)
    tl = cm.resize(cm.Rect(0.4, 0.4, 0.2, 0.2), "tl", -0.1, 0.0, 1.0, 1.0, 0.02)
    assert tl.x + tl.w == pytest.approx(0.6) and tl.w == pytest.approx(0.3)
    assert tl.y + tl.h == pytest.approx(0.6)
    with pytest.raises(ValueError):
        cm.resize(r, "zz", 0, 0, None, 1.0, 0.05)
    assert cm.edge_handles_allowed(None) and not cm.edge_handles_allowed(1.0)


def test_crop_hit_test():
    r = cm.Rect(0.2, 0.2, 0.4, 0.4)
    assert cm.hit_test(r, 0.2, 0.2, 0.02) == "tl"
    assert cm.hit_test(r, 0.6, 0.6, 0.02) == "br"
    assert cm.hit_test(r, 0.4, 0.2, 0.02) == "t"
    assert cm.hit_test(r, 0.4, 0.4, 0.02) == "move"
    assert cm.hit_test(r, 0.9, 0.9, 0.02) is None
    assert cm.hit_test(r, 0.4, 0.2, 0.02, edges=False) == "move"


# ---------------------------------------------------------------- 剪輯


def test_trim_model_ranges():
    t = tm.TrimModel()
    assert t.effective_end == 0
    t.load(10.0)
    assert t.start == 0 and t.end == 10.0
    t.set_start(4)
    t.set_end(3)
    assert t.end == pytest.approx(4.1)
    t.set_start(20)
    assert t.start == pytest.approx(4.0)
    t.set_end(99)
    assert t.end == 10.0
    assert t.values() == {"start": "0:04.00", "end": "0:10.00"}
    t.from_values({"start": "1:00", "end": ""})
    assert t.start == pytest.approx(9.9) and t.end == 10.0
    t.from_values({"start": "2", "end": "5.5"})
    assert (t.start, t.end) == (2, 5.5)


def test_trim_summary_texts():
    t = tm.TrimModel()
    t.load(65.0)
    t.set_start(5)
    t.set_end(15)
    assert t.summary("selection") == (
        "選取 0:05.0 – 0:15.0",
        "輸出長度約 0:10.0（在左側拖曳時間軸的把手調整）",
    )
    assert t.summary("remove")[1].startswith("輸出長度約 0:55.0")
    assert tm.length_title("selection") == "保留長度"
    assert tm.length_title("remove") == "刪除長度"
    assert tm.keep_mode({"keep": "remove"}) == "remove"
    assert tm.keep_mode({"mode": "delete"}) == "remove"
    assert tm.keep_mode({}) == "selection"


def test_trim_validation():
    t = tm.TrimModel()
    t.load(10)
    assert t.validate("selection") is None
    t.from_values({"start": "11", "end": ""})
    t2 = tm.TrimModel()
    t2.start, t2.end, t2.duration = 12.0, None, 10.0
    assert t2.validate("selection") == "開始時間超過檔案長度（0:10.0）"
    t3 = tm.TrimModel()
    t3.load(10)
    assert t3.validate("remove") == "不能刪除整段內容"
    t3.start, t3.end = 5.0, 5.05
    assert t3.validate("selection") == "結束時間必須比開始時間晚至少 0.1 秒"


def test_peaks_and_strip_times():
    import numpy as np

    samples = np.concatenate([np.zeros(1000, dtype=np.int16), np.full(1000, 16000, np.int16)])
    peaks = tm.compute_peaks(samples, 4)
    assert peaks[0] == 0 and peaks[-1] == pytest.approx(1.0)
    assert tm.compute_peaks(np.zeros(0, np.int16), 3) == [0.0, 0.0, 0.0]
    assert tm.strip_times(12.0, 4) == pytest.approx([1.5, 4.5, 7.5, 10.5])
    assert tm.strip_times(0, 4) == []
