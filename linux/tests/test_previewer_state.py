"""空白鍵預覽的開關狀態（照 GNOME 內建預覽：同一個檔案再按一次就關、不同檔案就換內容）。"""

from __future__ import annotations

import pytest

from quickseemd.previewer.state import Decision, PreviewState, on_close, on_show_file


def test_first_request_shows_and_records_uri():
    s0 = PreviewState()
    s1, d = on_show_file(s0, "file:///a.txt", True)
    assert d is Decision.SHOW
    assert s1.current_uri == "file:///a.txt"
    assert s1.is_open
    assert s1.generation == s0.generation + 1
    # 原本的狀態不會被改動
    assert s0.current_uri is None and not s0.is_open


def test_same_uri_with_close_flag_closes():
    s1, _ = on_show_file(PreviewState(), "file:///a.txt", True)
    s2, d = on_show_file(s1, "file:///a.txt", True)
    assert d is Decision.CLOSE
    assert not s2.is_open
    assert s2.current_uri is None
    assert s2.generation > s1.generation


def test_same_uri_without_close_flag_presents():
    s1, _ = on_show_file(PreviewState(), "file:///a.txt", True)
    s2, d = on_show_file(s1, "file:///a.txt", False)
    assert d is Decision.PRESENT
    assert s2 == s1


def test_different_uri_switches_content():
    s1, _ = on_show_file(PreviewState(), "file:///a.txt", True)
    s2, d = on_show_file(s1, "file:///b.txt", True)
    assert d is Decision.SHOW
    assert s2.current_uri == "file:///b.txt"
    assert s2.generation == s1.generation + 1


def test_double_press_before_render_finishes_closes():
    """連按兩次空白鍵：第一次的內容還沒畫出來，第二次也要能關掉（網址在收到請求時就記下）。"""
    s1, d1 = on_show_file(PreviewState(), "file:///big.log", True)
    pending_generation = s1.generation
    s2, d2 = on_show_file(s1, "file:///big.log", True)
    assert (d1, d2) == (Decision.SHOW, Decision.CLOSE)
    # 第一次請求的結果回來時已經過期，不能再顯示
    assert not s2.is_current(pending_generation)


def test_stale_generation_is_not_current():
    s1, _ = on_show_file(PreviewState(), "file:///a", True)
    g1 = s1.generation
    s2, _ = on_show_file(s1, "file:///b", True)
    assert not s2.is_current(g1)
    assert s2.is_current(s2.generation)


def test_close_resets_and_reopen_shows_again():
    s1, _ = on_show_file(PreviewState(), "file:///a", True)
    s2 = on_close(s1)
    assert not s2.is_open
    s3, d = on_show_file(s2, "file:///a", True)
    assert d is Decision.SHOW
    assert s3.is_open


def test_close_when_already_closed_is_harmless():
    s = on_close(PreviewState())
    assert not s.is_open
    assert not s.is_current(s.generation)


def test_state_is_immutable():
    s = PreviewState()
    with pytest.raises(AttributeError):
        s.current_uri = "x"  # type: ignore[misc]
