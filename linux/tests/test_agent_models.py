"""系統匣選單、浮動暫存、輪盤內容與幾何的純邏輯。"""

from __future__ import annotations

from pathlib import Path

import pytest
from agent_fakes import fake_tools, no_tools  # noqa: F401 — pytest fixture

from quickseemd.agent import shelf_model as sm
from quickseemd.agent import tray_model as tm
from quickseemd.agent import wheel_model as wm
from quickseemd.core.model import TINTS

# ------------------------------------------------------------------ 系統匣選單


def _labels(entries):
    return [e.label if e.kind != "separator" else "—" for e in entries]


def test_tray_menu_structure(fake_tools):
    entries = tm.build_menu(wheel_enabled=True, autostart_on=False)
    assert _labels(entries) == [
        "開啟 QUICK_SEEMD 工具箱",
        "選擇檔案…",
        "—",
        "常用工具",
        "所有工具",
        "—",
        "拖曳轉換輪盤",
        "登入時自動啟動",
        "設定…",
        "—",
        "結束 QUICK_SEEMD",
    ]
    by_label = {e.label: e for e in entries}
    assert by_label["拖曳轉換輪盤"].kind == "check"
    assert by_label["拖曳轉換輪盤"].active is True
    assert by_label["登入時自動啟動"].active is False
    assert by_label["結束 QUICK_SEEMD"].action == "quit"
    assert by_label["設定…"].action == "settings"


def test_tray_favorites_only_existing_tools(fake_tools):
    entries = tm.build_menu(wheel_enabled=False, autostart_on=True)
    fav = next(e for e in entries if e.label == "常用工具")
    # 假工具裡只有 video.compress 在常用清單內
    assert [(c.label, c.action) for c in fav.children] == [("壓縮影片", "tool:video.compress")]


def test_tray_all_tools_grouped_with_headers(fake_tools):
    entries = tm.build_menu(wheel_enabled=False, autostart_on=True)
    all_tools = next(e for e in entries if e.label == "所有工具")
    kinds = [(c.kind, c.label) for c in all_tools.children]
    assert kinds == [
        ("header", "圖片工具"),
        ("item", "旋轉圖片"),
        ("header", "影音工具"),
        ("item", "壓縮影片"),
        ("header", "其他工具"),
        ("item", "壓縮檔案"),
        ("item", "讀取 QR Code"),
    ]


def test_tray_empty_submenus_without_tools(no_tools):
    entries = tm.build_menu(wheel_enabled=True, autostart_on=True)
    assert next(e for e in entries if e.label == "所有工具").children == ()


def test_mnemonic_escape():
    assert tm.escape_mnemonic("結束 QUICK_SEEMD") == "結束 QUICK__SEEMD"
    assert tm.escape_mnemonic("沒有底線") == "沒有底線"


# ------------------------------------------------------------------ 浮動暫存


def test_shelf_add_dedupes_and_keeps_order():
    s = sm.ShelfModel().add([Path("/a"), Path("/b")]).add([Path("/b"), Path("/c")])
    assert s.items == (Path("/a"), Path("/b"), Path("/c"))


def test_shelf_remove_and_clear():
    s = sm.ShelfModel().add([Path("/a"), Path("/b")])
    assert s.remove(Path("/a")).items == (Path("/b"),)
    assert s.remove(Path("/zzz")).items == s.items
    assert s.clear().items == ()
    assert s.items == (Path("/a"), Path("/b"))  # 不可變


@pytest.mark.parametrize(("count", "height"), [(1, 104), (3, 196), (7, 380), (12, 380)])
def test_shelf_height(count, height):
    assert sm.shelf_height(count) == height


def test_shelf_origin():
    assert sm.shelf_origin((0, 27, 1920, 1053)) == (1920 - 270, 27 + 1053 // 2)


def test_shelf_header_count():
    assert sm.SHELF_TITLE == "浮動暫存"
    assert sm.CLEAR_TOOLTIP == "清空"
    assert sm.ROW_TOOLTIP == "拖到任何地方使用；雙按打開"


# ------------------------------------------------------------------ 輪盤：拖曳資料


def test_parse_uri_list():
    data = (
        b"# comment\r\nfile:///tmp/a%20b.txt\r\nfile://localhost/tmp/%E4%B8%AD.pdf\r\n"
        b"https://example.com/x\r\nfile://otherhost/x\r\n\r\n"
    )
    assert wm.parse_uri_list(data) == [Path("/tmp/a b.txt"), Path("/tmp/中.pdf")]


def test_parse_uri_list_text_and_garbage():
    assert wm.parse_uri_list("file:///x\n") == [Path("/x")]
    assert wm.parse_uri_list(b"\xff\xfe") == []
    assert wm.parse_uri_list("file:///a%00b") == []
    assert wm.parse_uri_list("file://") == []


def test_owner_is_ours():
    mask = 0x1FFFFF
    assert wm.owner_is_ours(0x4200012, own_xid=0x4200001, resource_mask=mask)
    assert not wm.owner_is_ours(0x2000012, own_xid=0x4200001, resource_mask=mask)
    assert not wm.owner_is_ours(0, own_xid=0x4200001, resource_mask=mask)
    assert not wm.owner_is_ours(0x4200012, own_xid=None, resource_mask=mask)


# ------------------------------------------------------------------ 輪盤：幾何


def test_ring_origin_centered_when_room():
    assert wm.ring_origin(640, 420, (0, 0, 1280, 800)) == (420, 200)


def test_ring_origin_clamped_to_workarea():
    assert wm.ring_origin(10, 10, (0, 27, 1280, 773)) == (0, 27)
    assert wm.ring_origin(1270, 790, (0, 27, 1280, 773)) == (1280 - 440, 27 + 773 - 440)


def test_ring_origin_on_second_monitor():
    assert wm.ring_origin(1930, 100, (1920, 0, 1920, 1080)) == (1920, 0)


def test_device_to_logical():
    assert wm.device_to_logical(1000, 600, 2) == (500, 300)
    assert wm.device_to_logical(1000, 600, 1) == (1000, 600)
    assert wm.device_to_logical(1001, 601, 0) == (1001, 601)


# ------------------------------------------------------------------ 輪盤：內容


def test_catalog_tools_wheel_for_text_file(fake_tools, tmp_path):
    f = tmp_path / "a.txt"
    f.write_text("x")
    items = wm.build_catalog("tools", [f])
    assert [(i.kind, i.title) for i in items] == [("action", "壓縮成 ZIP"), ("more", "更多工具")]
    assert items[0].tint == TINTS["teal"]


def test_catalog_skips_actions_without_tools(fake_tools, tmp_path):
    f = tmp_path / "a.png"
    from conftest import make_image  # noqa: PLC0415

    make_image(f)
    titles = [i.title for i in wm.build_catalog("tools", [f])]
    assert titles == ["向右旋轉 90°", "壓縮成 ZIP", "讀取 QR Code", "更多工具"]
    convert = [i.title for i in wm.build_catalog("convert", [f])]
    assert convert == ["更多格式"]


def test_catalog_actions_wheel_is_fixed(tmp_path):
    items = wm.build_catalog("actions", [tmp_path])
    assert [i.title for i in items] == [
        "用其他程式開啟",
        "浮動暫存",
        "拷貝",
        "移動到…",
        "拷貝路徑",
        "終端機",
    ]
    assert all(i.kind == "file" for i in items)


def test_catalog_without_files_lists_generic_items():
    assert [i.title for i in wm.build_catalog("convert", [])] == ["更多格式"]
    assert [i.title for i in wm.build_catalog("tools", [])] == ["更多工具"]


def test_catalog_survives_converter_errors(monkeypatch, tmp_path, fake_tools):
    def boom(_paths):
        raise RuntimeError("引擎壞了")

    monkeypatch.setattr(wm.qs_wheel, "converter_targets", boom)
    f = tmp_path / "a.txt"
    f.write_text("x")
    assert [i.title for i in wm.build_catalog("convert", [f])] == ["更多格式"]


def test_hint_and_center_title():
    keys = {"convert": "shift", "tools": "alt", "actions": "control"}
    assert wm.hint("convert", keys) == "⌥ 工具  ⌃ 動作  esc 取消"
    assert wm.hint("actions", keys) == "⇧ 轉換  ⌥ 工具  esc 取消"
    assert wm.center_title("tools", 1) == "工具"
    assert wm.center_title("tools", 3) == "3 個檔案・工具"


def test_hit_index():
    assert wm.hit_at(220, 220 - 138, 4) == 0
    assert wm.hit_at(220 + 138, 220, 4) == 1
    assert wm.hit_at(220, 220, 4) == "center"
    assert wm.hit_at(0, 0, 4) is None


def test_label_center_positions():
    x, y = wm.label_center(0, 4)
    assert (round(x), round(y)) == (220, 220 - 138)
    x, y = wm.label_center(1, 4)
    assert (round(x), round(y)) == (220 + 138, 220)
