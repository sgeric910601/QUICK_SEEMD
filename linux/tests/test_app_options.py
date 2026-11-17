"""選項狀態（不需要畫面）：分組、顯示條件、動態選項、數值轉換、時間與顏色格式。"""

from __future__ import annotations

import pytest

from quickseemd.app import options_model as om
from quickseemd.core.options import Choice, Kind, Option


@pytest.fixture
def all_kinds():
    from tests.app_fake_tools import TOOLS

    return next(t for t in TOOLS if t.id == "test.all-kinds")


def test_groups_consecutive(all_kinds):
    groups = om.group_options(all_kinds.options)
    titles = [g.title for g in groups]
    assert titles[:3] == ["壓縮程度", "格式", "細節"]
    fmt = groups[1]
    assert [o.key for o in fmt.options] == ["format", "codec"]
    # 沒有 group 的選項接在前一段後面（原版畫在同一段）
    assert "more" in [o.key for o in groups[2].options]


def test_groups_new_title_starts_new_group():
    a = Option("a", Kind.TOGGLE, group="甲")
    b = Option("b", Kind.TOGGLE, group="乙")
    c = Option("c", Kind.TOGGLE)
    groups = om.group_options((c, a, b))
    assert [(g.title, [o.key for o in g.options]) for g in groups] == [
        (None, ["c"]),
        ("甲", ["a"]),
        ("乙", ["b"]),
    ]


def test_state_set_value_notifies_once(all_kinds):
    st = om.OptionsState(all_kinds)
    seen: list[str | None] = []
    unsub = st.subscribe(seen.append)
    st.set_value("text", "abc")
    st.set_value("text", "abc")  # 沒變不通知
    assert seen == ["text"]
    st.merge({"width": 640, "nope": 1})
    assert st.values["width"] == 640 and "nope" not in st.values
    st.set_info({"page_count": 7})
    assert st.info == {"page_count": 7}
    unsub()
    st.set_value("text", "x")
    assert seen == ["text", None, None]


def test_state_visibility_enabled_dynamic(all_kinds):
    st = om.OptionsState(all_kinds)
    dyn = all_kinds.option("dyn")
    assert not st.visible(dyn)
    st.set_value("more", True)
    assert st.visible(dyn)
    assert [c.value for c in st.choices(dyn)] == ["a", "b", "c"]
    assert not st.enabled(all_kinds.option("disabled_toggle"))
    assert st.enabled(all_kinds.option("text"))
    assert st.action_title() == "開始處理（更多）"


def test_state_tolerates_broken_callables():
    def boom(v, i):  # noqa: ANN001, ANN202
        raise KeyError("x")

    opt = Option("x", Kind.SEGMENTED, default="a", choices=(Choice("a", "甲"),), visible=boom)
    opt2 = Option("y", Kind.NOTE, note=boom)
    opt3 = Option("z", Kind.CHIPS, default="a", dynamic_choices=boom, choices=(Choice("a", "A"),))
    from quickseemd.core.model import OTHER, ToolDefinition

    tool = ToolDefinition("test.x", OTHER, "x", "x", "questionmark", lambda f, v, c: [])
    tool = ToolDefinition(
        "test.x", OTHER, "x", "x", "questionmark", lambda f, v, c: [], options=(opt, opt2, opt3)
    )
    st = om.OptionsState(tool)
    assert st.visible(opt) is True
    assert st.note(opt2) is None
    assert [c.value for c in st.choices(opt3)] == ["a"]


def test_note_text_for_note_kind(all_kinds):
    st = om.OptionsState(all_kinds)
    warn = all_kinds.option("warn")
    assert st.note(warn) is None
    assert not st.visible(warn)
    st.set_value("secret", "ab")
    assert st.note(warn) == ("密碼太短", True)
    fixed = next(o for o in all_kinds.options if o.key.startswith("_note_"))
    assert st.note(fixed) == ("這是一則固定說明。", False)


def test_page_range_note(all_kinds):
    st = om.OptionsState(all_kinds)
    rng = all_kinds.option("range")
    assert st.note(rng) == ("例如：1-3, 5, 8-", False)
    st.set_info({"page_count": 4})
    assert st.note(rng) == ("例如：1-3, 5, 8-　共 4 頁", False)
    st.set_value("range", "9")
    assert st.note(rng) == ("範圍不正確（共 4 頁）", True)


def test_slider_and_number_coercion(all_kinds):
    q = all_kinds.option("quality")
    assert om.slider_text(q, 75) == "75%"
    plain = Option("s", Kind.SLIDER, default=0.5, minimum=0, maximum=1, step=0.05)
    assert om.slider_text(plain, 0.35) == "0.35"
    ints = Option("i", Kind.SLIDER, default=3, minimum=1, maximum=9, step=1)
    assert om.slider_text(ints, 4.0) == "4"
    assert om.coerce_number(q, 77.4) == 75
    assert om.coerce_number(q, 300) == 100
    assert om.coerce_number(plain, 0.333) == pytest.approx(0.35)
    w = all_kinds.option("width")
    assert om.coerce_number(w, 12.7) == 13
    assert om.digits_for(plain) == 2 and om.digits_for(w) == 0
    assert om.number_range(Option("n", Kind.NUMBER, default=1)) == (0.0, 1_000_000.0)


def test_time_parse_and_format():
    assert om.parse_time("75") == 75
    assert om.parse_time("1:05.5") == pytest.approx(65.5)
    assert om.parse_time("0:01:15") == 75
    assert om.parse_time("1：05。5") == pytest.approx(65.5)
    assert om.parse_time("12秒") == 12
    for bad in ("", "1:60", "a", "1.5:00", "1:2:3:4", "-1", "1::2"):
        assert om.parse_time(bad) is None, bad
    assert om.format_time(65.25) == "1:05.3"
    assert om.format_time(3725) == "1:02:05.0"
    assert om.format_time(5, decimals=2) == "0:05.00"
    assert om.format_time(-3) == "0:00.0"
    assert om.format_time(59.99, decimals=1) == "1:00.0"
    assert om.time_ok("") and om.time_ok("1:00") and not om.time_ok("x")


def test_color_helpers():
    assert om.hex_to_rgb("#ff8000") == (1.0, pytest.approx(0.502, abs=1e-3), 0.0)
    assert om.hex_to_rgb("bad") is None
    assert om.rgb_to_hex(1.0, 0.5, 0.0) == "#ff8000"
    assert om.rgb_to_hex(2, -1, 0.5) == "#ff0080"


def test_grid_rows_and_checks(all_kinds):
    pos = all_kinds.option("position")
    rows = om.grid_rows(pos, pos.choices)
    assert len(rows) == 3 and [c.value for c in rows[0]] == ["tl", "tc", "tr"]
    assert rows[2][0].value is None
    assert om.toggle_check(["a"], "b", True) == ["a", "b"]
    assert om.toggle_check(["a", "b"], "a", False) == ["b"]
    assert om.toggle_check(["a"], "a", True) == ["a"]
    assert om.toggle_check(None, "a", True) == ["a"]


def test_snapshot_is_json_safe(all_kinds):
    import json

    st = om.OptionsState(all_kinds)
    st.set_value("algos", ["md5"])
    snap = st.snapshot()
    snap["algos"].append("x")
    assert st.values["algos"] == ["md5"]
    json.dumps(snap)


def test_choice_signature_changes(all_kinds):
    st = om.OptionsState(all_kinds)
    dyn = all_kinds.option("dyn")
    a = om.choices_signature(st.choices(dyn))
    st.set_value("more", True)
    assert om.choices_signature(st.choices(dyn)) != a


def test_parse_ratio():
    assert om.parse_ratio("free", "自由") is None
    assert om.parse_ratio(None, None) is None
    assert om.parse_ratio("16:9", "16:9") == pytest.approx(16 / 9)
    assert om.parse_ratio("a4-portrait", "A4 直") == pytest.approx(210 / 297)
    assert om.parse_ratio("a4l", "A4 橫") == pytest.approx(297 / 210)
    assert om.parse_ratio(1.5, None) == 1.5
    assert om.parse_ratio("square", "1:1") == 1
    assert om.parse_ratio("r3x2", None) == 1.5
    assert om.parse_ratio("3/4", None) == 0.75
    assert om.parse_ratio(0, None) is None


def test_dynamic_range_and_suggestions():
    from quickseemd.core.model import OTHER, ToolDefinition

    opt = Option(
        "start",
        Kind.SLIDER,
        default=0.0,
        minimum=0,
        maximum=10,
        step=0.5,
        dynamic_range=lambda v, i: (0, i.get("duration", 10)),
    )
    bad = Option("b", Kind.NUMBER, default=1, dynamic_range=lambda v, i: 1 / 0)
    tool = ToolDefinition(
        "test.r", OTHER, "r", "r", "questionmark", lambda f, v, c: [], options=(opt, bad)
    )
    st = om.OptionsState(tool)
    assert st.range(opt) == (0.0, 10.0)
    st.set_info({"duration": 4.0})
    assert st.range(opt) == (0.0, 4.0)
    assert om.coerce_number(opt, 9.0, st.range(opt)) == 4.0
    assert st.range(bad) == om.NUMBER_DEFAULT_RANGE


def test_adjust_hook_merges_changes_once(all_kinds):
    st = om.OptionsState(all_kinds)
    assert st.values["custom_color"] == "#3366cc"
    seen: list = []
    st.subscribe(seen.append)
    st.set_value("format", "png")
    assert st.values["quality"] == 100 and "nope" not in st.values
    assert seen == ["format"]
    st.set_value("quality", 50)
    assert st.values["quality"] == 50


def test_adjust_hook_errors_are_ignored():
    from quickseemd.core.model import OTHER, ToolDefinition

    def boom(v, k, i):  # noqa: ANN001, ANN202
        raise RuntimeError("x")

    opt = Option("a", Kind.TOGGLE, default=False)
    tool = ToolDefinition(
        "test.adj", OTHER, "a", "a", "questionmark", lambda f, v, c: [], options=(opt,), adjust=boom
    )
    st = om.OptionsState(tool)
    st.set_value("a", True)
    assert st.values["a"] is True
