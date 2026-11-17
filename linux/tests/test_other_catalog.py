"""其他類工具清單：順序、萬用轉換頁、右鍵預設值與登記。"""

from __future__ import annotations

from pathlib import Path

import pytest

from quickseemd.core import errors, quickactions, registry
from quickseemd.core.model import OTHER, TINTS, ToolContext
from quickseemd.core.tools.other import TOOLS


def test_tool_ids_in_original_order():
    assert [t.id for t in TOOLS] == list(registry.ORDER["other"])
    assert all(t.category is OTHER for t in TOOLS)


def test_registry_loads_other_tools():
    registry.reload()
    assert [t.id for t in registry.tools_in(OTHER)] == list(registry.ORDER["other"])


def test_converter_is_custom_page():
    t = registry.tool("other.convert")
    assert t is not None
    assert t.custom_page == "converter"
    assert t.title == "萬用格式轉換"
    assert t.badge == "ALL"
    assert t.tint == TINTS["teal"]
    with pytest.raises(errors.QSError) as exc:
        t.run([Path("/tmp/x")], {}, ToolContext())
    assert str(exc.value) == "請在主程式的「萬用格式轉換」選擇要轉成的格式。"


def test_tints():
    tints = {t.id: t.tint for t in TOOLS}
    assert tints["other.compress"] == TINTS["slate"]
    assert tints["other.extract"] == TINTS["slate"]
    assert tints["other.text-encoding"] == TINTS["teal"]
    assert tints["other.subtitles"] == TINTS["video"]
    assert tints["other.read-qr"] == TINTS["image"]
    assert tints["other.checksum"] == TINTS["slate"]


@pytest.mark.parametrize(
    "action_id", ["other.zip", "other.unzip", "other.to-utf8", "other.read-qr"]
)
def test_quick_action_presets(action_id):
    act = quickactions.action(action_id)
    tool = registry.tool(act.tool_id)
    assert tool.presets[action_id] == act.preset
    resolved = registry.resolve(action_id)
    # 「other.read-qr」同時是工具代碼與動作代碼，先認成工具（預設值相同）
    assert resolved == (tool, None if action_id == tool.id else action_id)
    assert tool.values_for_preset(action_id) == {**tool.defaults(), **act.preset}
