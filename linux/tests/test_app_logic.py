"""主程式的非介面邏輯：命令列、轉送規則、大廳篩選、工具頁的檔案處理與狀態。"""

from __future__ import annotations

from pathlib import Path

import pytest

from quickseemd.app import cmdline, routing, state
from quickseemd.core import registry
from quickseemd.core.filetypes import file_ref
from quickseemd.core.model import CATEGORIES, IMAGE, PDF


@pytest.fixture
def fake_tools(monkeypatch):
    monkeypatch.setenv("QUICKSEEMD_EXTRA_TOOLS", "tests.app_fake_tools")
    registry.reload()
    yield registry.by_id()
    monkeypatch.delenv("QUICKSEEMD_EXTRA_TOOLS")
    registry.reload()


@pytest.fixture
def files(tmp_path: Path) -> dict[str, Path]:
    from conftest import make_image, make_pdf

    return {
        "a.pdf": make_pdf(tmp_path / "a.pdf", pages=1),
        "b.pdf": make_pdf(tmp_path / "b.pdf", pages=1),
        "c.png": make_image(tmp_path / "c.png", size=(32, 24)),
        "d.txt": _write(tmp_path / "d.txt", "hello"),
    }


def _write(p: Path, text: str) -> Path:
    p.write_text(text, "utf-8")
    return p


# ---------------------------------------------------------------- 命令列


def test_parse_empty_and_plain_open():
    assert cmdline.parse_open_args([]) == cmdline.OpenRequest(None, ())
    assert cmdline.parse_open_args(["open"]) == cmdline.OpenRequest(None, ())


def test_parse_tool_and_files(files, tmp_path):
    req = cmdline.parse_open_args(["open", "--tool", "pdf.merge", "a.pdf", "b.pdf"], cwd=tmp_path)
    assert req.tool_id == "pdf.merge"
    assert req.files == (files["a.pdf"], files["b.pdf"])


def test_parse_tool_equals_and_double_dash(files, tmp_path):
    weird = _write(tmp_path / "--weird", "x")
    req = cmdline.parse_open_args(["open", "--tool=x.y", "--", "--weird"], cwd=tmp_path)
    assert req.tool_id == "x.y"
    assert req.files == (weird,)


def test_parse_drops_missing_duplicates_and_bad_ids(files, tmp_path):
    a = str(files["a.pdf"])
    req = cmdline.parse_open_args(
        ["open", "--tool", "BAD id!", a, a, "missing.pdf", "--unknown"], cwd=tmp_path
    )
    assert req.tool_id is None
    assert req.files == (files["a.pdf"],)


def test_parse_file_uri_and_nul(files, tmp_path):
    uri = files["c.png"].as_uri()
    req = cmdline.parse_open_args(["open", uri, "bad\0name", "http://example.com/x"], cwd=tmp_path)
    assert req.files == (files["c.png"],)


def test_parse_tool_flag_without_value():
    assert cmdline.parse_open_args(["open", "--tool"]).tool_id is None


# ---------------------------------------------------------------- 轉送規則


def test_route_tool_accepting_all(fake_tools, files):
    tools = registry.all_tools()
    req = cmdline.OpenRequest("test.copy", (files["a.pdf"],))
    route = routing.route_open(req, tools)
    assert route.kind == "tool" and route.tool.id == "test.copy"
    assert route.files == (files["a.pdf"],)


def test_route_tool_without_files_opens_tool(fake_tools):
    route = routing.route_open(cmdline.OpenRequest("test.copy", ()), registry.all_tools())
    assert route.kind == "tool" and route.files == ()


def test_route_falls_back_to_same_category_tool(fake_tools, files):
    # 原版第一步只看「每個檔案都接受」：單檔工具照樣打開（工具頁只留第一個）
    two = (files["a.pdf"], files["b.pdf"])
    route = routing.route_open(cmdline.OpenRequest("test.organize", two), registry.all_tools())
    assert route.kind == "tool" and route.tool.id == "test.organize"
    # 裁剪不收 PDF：改用同分類第一個收得下、不是整頁自訂的工具
    req = cmdline.OpenRequest("test.crop", (files["a.pdf"],))
    route = routing.route_open(req, registry.all_tools())
    assert route.kind == "tool" and route.tool.id == "test.all-kinds"


def test_route_to_inbox_when_nothing_fits(fake_tools, files):
    req = cmdline.OpenRequest("test.copy", (files["d.txt"],))
    assert routing.route_open(req, registry.all_tools()).kind == "inbox"
    req2 = cmdline.OpenRequest("no.such", (files["d.txt"],))
    assert routing.route_open(req2, registry.all_tools()).kind == "inbox"
    assert routing.route_open(cmdline.OpenRequest("no.such", ()), []).kind == "show"


def test_route_without_tool(files):
    assert routing.route_open(cmdline.OpenRequest(None, ()), []).kind == "show"
    route = routing.route_open(cmdline.OpenRequest(None, (files["a.pdf"],)), [])
    assert route.kind == "inbox" and route.files == (files["a.pdf"],)


# ---------------------------------------------------------------- 大廳


def test_inbox_fits_rules(fake_tools, files):
    t = fake_tools
    pdfs = [file_ref(files["a.pdf"]), file_ref(files["b.pdf"])]
    assert routing.inbox_fits(t["test.copy"], [])
    assert routing.inbox_fits(t["test.copy"], pdfs)
    assert not routing.inbox_fits(t["test.organize"], pdfs)  # 只收一個
    assert routing.inbox_fits(t["test.organize"], pdfs[:1])
    assert not routing.inbox_fits(t["test.copy"], [file_ref(files["d.txt"])])
    assert routing.inbox_fits(t["test.convert"], [file_ref(files["d.txt"])])  # 萬用轉換一律可以
    assert routing.inbox_files_for(t["test.copy"], [files["d.txt"]]) == []
    assert routing.inbox_files_for(t["test.copy"], [files["a.pdf"]]) == [files["a.pdf"]]


def test_hub_sections_filter_by_query_and_inbox(fake_tools, files):
    tools = registry.all_tools()
    secs = routing.hub_sections(None, tools, "全部", [])
    assert [t.id for _, lst in secs for t in lst] == ["test.all-kinds"]
    secs = routing.hub_sections(PDF, tools, "", [file_ref(files["c.png"])])
    assert secs == []
    secs = routing.hub_sections(IMAGE, tools, "", [file_ref(files["c.png"])])
    ids = [t.id for _, lst in secs for t in lst]
    assert "test.crop" in ids and "test.all-kinds" in ids
    cats = [c.id for c, _ in routing.hub_sections(None, tools, "", [])]
    assert cats == [c.id for c in CATEGORIES if c.id in cats]


def test_hub_texts():
    assert routing.hub_title(None) == "你需要的檔案工具，全都在這裡"
    assert routing.hub_title(PDF) == "PDF 工具"
    assert "這台電腦" in routing.hub_subtitle(None)
    assert routing.hub_subtitle(PDF) == PDF.subtitle
    assert routing.empty_message("abc", False) == "找不到符合「abc」的工具"
    assert routing.empty_message("abc", True) == "沒有工具能同時處理這些檔案"
    assert routing.inbox_title(3) == "已選取 3 個檔案：要對它們做什麼？"
    assert routing.inbox_names([Path("/x/a.pdf"), Path("/y/b.png")]) == "a.pdf、b.png"


# ---------------------------------------------------------------- 工具頁檔案


def test_initial_files_filters_and_single(fake_tools, files):
    t = fake_tools
    got = routing.initial_files(t["test.copy"], [files["a.pdf"], files["c.png"], files["b.pdf"]])
    assert got == [files["a.pdf"], files["b.pdf"]]
    assert routing.initial_files(t["test.organize"], [files["a.pdf"], files["b.pdf"]]) == [
        files["a.pdf"]
    ]


def test_add_files_skips_unsupported_and_duplicates(fake_tools, files):
    t = fake_tools["test.copy"]
    cur = [files["a.pdf"]]
    new, note = routing.add_files(t, cur, [files["a.pdf"], files["b.pdf"], files["c.png"]])
    assert new == [files["a.pdf"], files["b.pdf"]]
    assert note == "已略過 1 個不支援的檔案（需要PDF 檔案）"
    new2, note2 = routing.add_files(t, new, [files["b.pdf"]])
    assert new2 == new and note2 is None


def test_add_files_single_tool_replaces(fake_tools, files):
    t = fake_tools["test.organize"]
    new, note = routing.add_files(t, [files["a.pdf"]], [files["b.pdf"]])
    assert new == [files["b.pdf"]] and note is None


def test_sort_and_move():
    fs = [Path("/a/檔案10.pdf"), Path("/a/檔案2.pdf"), Path("/a/b.pdf")]
    az = routing.sort_files(fs, "az")
    assert az.index(Path("/a/檔案2.pdf")) < az.index(Path("/a/檔案10.pdf"))
    assert routing.sort_files(fs, "za") == list(reversed(az))
    assert routing.sort_files(fs, "reverse") == list(reversed(fs))
    assert routing.move_item(["a", "b", "c", "d"], 0, 2) == ["b", "c", "a", "d"]
    assert routing.move_item(["a", "b", "c", "d"], 3, 0) == ["d", "a", "b", "c"]
    assert routing.move_item(["a", "b"], 5, 0) == ["a", "b"]
    with pytest.raises(ValueError):
        routing.sort_files(fs, "nope")


def test_validation_order(fake_tools, files):
    merge = fake_tools["test.merge"]
    assert routing.validation_reason(merge, [files["a.pdf"]], {}, {}) == "至少需要 2 個檔案"
    org = fake_tools["test.organize"]
    two = [files["a.pdf"], files["b.pdf"]]
    assert routing.validation_reason(org, two, {}, {}) == "這個工具一次只能處理 1 個檔案"
    allk = fake_tools["test.all-kinds"]
    assert routing.validation_reason(allk, [files["c.png"]], {"text": "壞"}, {}) == (
        "文字不可以是「壞」"
    )
    assert routing.validation_reason(allk, [files["c.png"]], {"text": ""}, {}) is None


def test_landing_texts(fake_tools):
    t = fake_tools
    assert routing.choose_button_title(t["test.copy"]) == "選擇PDF 檔案"
    assert routing.landing_hint(t["test.copy"]) == "或把檔案拖到這裡，可以一次選多個"
    assert routing.landing_hint(t["test.organize"]) == "或把檔案拖到這裡"
    assert routing.picker_message(t["test.copy"]) == "選擇要用「測試複製 PDF」處理的PDF 檔案"
    assert routing.accepts_folders(t["test.folder"])
    assert not routing.accepts_folders(t["test.copy"])


# ---------------------------------------------------------------- 狀態


def test_state_navigation(fake_tools, files):
    s = state.AppState()
    seen: list[str] = []
    s.subscribe(lambda: seen.append(s.page_key()))
    s.select("cat:pdf")
    assert s.page_key() == "cat:pdf"
    s.receive([files["a.pdf"]])
    assert s.inbox == (files["a.pdf"],) and s.sidebar == "all"
    s.open(fake_tools["test.copy"], [files["a.pdf"]])
    assert s.open_tool_id == "test.copy" and s.inbox == ()
    token = s.page_token
    s.open(fake_tools["test.copy"], [])
    assert s.page_token != token
    s.close_tool()
    assert s.open_tool_id is None and s.pending_files == ()
    s.receive([])
    assert s.inbox == ()
    assert seen  # 有通知


def test_state_apply_route_and_converter(fake_tools, files):
    s = state.AppState()
    added: list[list[Path]] = []
    s.converter_sink = added.append
    s.apply_route(routing.Route("inbox", None, (files["d.txt"],)))
    assert s.inbox == (files["d.txt"],)
    s.apply_route(routing.Route("tool", fake_tools["test.convert"], (files["d.txt"],)))
    assert added == [[files["d.txt"]]]
    s.apply_route(routing.Route("show"))
    assert s.open_tool_id == "test.convert"
    s.clear_inbox()
    with pytest.raises(ValueError):
        s.select("bogus")
