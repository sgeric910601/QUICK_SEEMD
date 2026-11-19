"""合約測試：動作清單、工具定義、選項與圖示彼此一致。

工具還沒全部實作時，只檢查已登記的工具；設定 QS_EXPECT_COMPLETE=1 時要求 PR1 的工具全部存在。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from quickseemd.core import icons, quickactions, registry
from quickseemd.core.model import CATEGORIES
from quickseemd.core.options import Kind

PR1_CATEGORIES = ("pdf", "image", "media", "other")
ADWAITA = Path("/usr/share/icons/Adwaita")


def _icon_exists(name: str) -> bool:
    return any(ADWAITA.rglob(f"{name}.svg")) or any(ADWAITA.rglob(f"{name}.png"))


def test_registered_tools_are_known_and_unique():
    ids = [t.id for t in registry.all_tools() if not t.id.startswith("test.")]
    assert len(ids) == len(set(ids))
    known = {tid for order in registry.ORDER.values() for tid in order}
    assert set(ids) <= known
    for t in registry.all_tools():
        if t.id.startswith("test."):
            continue
        assert t.id in registry.ORDER[t.category.id], f"{t.id} 分類不對"


@pytest.mark.skipif(not os.environ.get("QS_EXPECT_COMPLETE"), reason="工具尚未全部合併")
def test_pr1_tools_complete():
    have = {t.id for t in registry.all_tools()}
    for cat in PR1_CATEGORIES:
        missing = set(registry.ORDER[cat]) - have
        assert not missing, f"缺少工具：{sorted(missing)}"


def test_every_enabled_action_points_to_valid_preset():
    for act in quickactions.enabled_actions():
        tool = registry.tool(act.tool_id)
        if tool is None:
            if os.environ.get("QS_EXPECT_COMPLETE"):
                pytest.fail(f"{act.id} 對應的工具 {act.tool_id} 不存在")
            continue
        assert act.id in tool.presets or not act.preset, f"{tool.id} 沒有登記 {act.id} 的預設值"
        for key, value in act.preset.items():
            opt = tool.option(key)
            assert opt is not None, f"{act.id}：工具 {tool.id} 沒有選項 {key}"
            if opt.kind in (Kind.CARDS, Kind.CHIPS, Kind.SEGMENTED) and opt.dynamic_choices is None:
                assert value in [
                    c.value for c in opt.choices
                ], f"{act.id}：{key}={value!r} 不是合法選項"
        # 工具登記的預設值必須和清單一致
        if act.id in tool.presets:
            assert tool.presets[act.id] == act.preset, f"{act.id} 的預設值和動作清單不同"


def test_option_keys_unique_and_defaults_valid():
    for t in registry.all_tools():
        keys = [o.key for o in t.options]
        assert len(keys) == len(set(keys)), f"{t.id} 有重複的選項鍵"
        for o in t.options:
            if o.kind in (Kind.CARDS, Kind.CHIPS, Kind.SEGMENTED) and o.dynamic_choices is None:
                assert o.default in [c.value for c in o.choices], f"{t.id}.{o.key} 預設值不在選項中"


def test_icons_map_to_existing_names():
    names = set(icons.SF_TO_FREEDESKTOP.values()) | {icons.FALLBACK}
    missing = sorted(n for n in names if not _icon_exists(n))
    assert not missing, f"圖示主題裡沒有：{missing}"


def test_all_symbols_have_mapping():
    symbols = {c.icon for c in CATEGORIES} | {a.icon for a in quickactions.ACTIONS}
    symbols |= {t.icon for t in registry.all_tools() if not t.id.startswith("test.")}
    unmapped = sorted(s for s in symbols if s not in icons.SF_TO_FREEDESKTOP)
    assert not unmapped, f"沒有對照的 SF Symbol：{unmapped}"
