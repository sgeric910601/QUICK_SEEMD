"""預覽網頁的自訂網址：頁面與附件的對應、cid: 改寫、內容過濾規則。"""

from __future__ import annotations

import json

from quickseemd.core.preview import Attachment, Document
from quickseemd.previewer import scheme


def _doc(html: str, **atts: bytes) -> Document:
    return Document(
        html=html,
        kind_label="測試",
        attachments={k: Attachment(k, v, "image/png") for k, v in atts.items()},
    )


def test_page_and_attachment_uris():
    assert scheme.page_uri(7) == "qs-preview://doc/index.html?v=7"
    assert scheme.attachment_uri("file-icon", 7) == "qs-preview://doc/att/file-icon?v=7"
    # 名稱裡的特殊字元要編碼
    assert scheme.attachment_uri("a b/c", 1) == "qs-preview://doc/att/a%20b%2Fc?v=1"


def test_rewrite_cid_references():
    html = '<img src="cid:file-icon"><img src=\'cid:nb-img-1\'><div style="background:url(cid:nb-img-1)">'
    out = scheme.rewrite_cid(html, ["file-icon", "nb-img-1"], 3)
    assert 'src="qs-preview://doc/att/file-icon?v=3"' in out
    assert "src='qs-preview://doc/att/nb-img-1?v=3'" in out
    assert "url(qs-preview://doc/att/nb-img-1?v=3)" in out
    assert "cid:" not in out


def test_rewrite_does_not_touch_prefix_matches_or_unknown_names():
    html = (
        '<img src="cid:img-10"><img src="cid:img-1"><p>cid:img-1 是文字</p><img src="cid:unknown">'
    )
    out = scheme.rewrite_cid(html, ["img-1", "img-10"], 1)
    assert 'src="qs-preview://doc/att/img-10?v=1"' in out
    assert 'src="qs-preview://doc/att/img-1?v=1"' in out
    assert 'src="cid:unknown"' in out


def test_rewrite_handles_html_escaped_names():
    html = '<img src="cid:a&amp;b">'
    out = scheme.rewrite_cid(html, ["a&b"], 2)
    assert "qs-preview://doc/att/a%26b?v=2" in out


def test_build_page_and_lookup_index():
    page = scheme.build_page(_doc('<p>中文</p><img src="cid:x">', x=b"PNG"), 5)
    res = scheme.lookup("qs-preview://doc/index.html?v=5", {5: page})
    assert res is not None
    assert res.mime == "text/html; charset=utf-8"
    assert "中文" in res.data.decode("utf-8")
    assert "qs-preview://doc/att/x?v=5" in res.data.decode("utf-8")


def test_lookup_attachment():
    page = scheme.build_page(_doc("<p></p>", **{"a b": b"DATA"}), 9)
    res = scheme.lookup("qs-preview://doc/att/a%20b?v=9", {9: page})
    assert res is not None and res.data == b"DATA" and res.mime == "image/png"


def test_lookup_rejects_unknown_or_stale():
    page = scheme.build_page(_doc("<p></p>", x=b"1"), 2)
    pages = {2: page}
    assert scheme.lookup("qs-preview://doc/index.html?v=1", pages) is None  # 舊的頁面
    assert scheme.lookup("qs-preview://doc/index.html", pages) is None
    assert scheme.lookup("qs-preview://doc/att/y?v=2", pages) is None
    assert scheme.lookup("qs-preview://other/index.html?v=2", pages) is None
    assert scheme.lookup("file:///etc/passwd", pages) is None
    assert scheme.lookup("qs-preview://doc/att/../index.html?v=2", pages) is None
    assert scheme.lookup("qs-preview://doc/index.html?v=abc", pages) is None
    assert scheme.lookup("qs-preview://doc/index.html?v=2&v=3", pages) is None


def test_is_page_navigation_allows_only_the_page_and_its_anchors():
    uri = scheme.page_uri(4)
    assert scheme.is_allowed_navigation(uri, uri)
    assert scheme.is_allowed_navigation(uri + "#t1", uri)
    assert not scheme.is_allowed_navigation("https://example.com/", uri)
    assert not scheme.is_allowed_navigation("file:///etc/passwd", uri)
    assert not scheme.is_allowed_navigation(scheme.page_uri(5), uri)
    assert not scheme.is_allowed_navigation("qs-preview://doc/att/x?v=4", uri)


def test_filter_rules_block_everything_but_the_scheme():
    rules = json.loads(scheme.filter_rules())
    assert rules[0] == {"trigger": {"url-filter": ".*"}, "action": {"type": "block"}}
    assert rules[1]["action"] == {"type": "ignore-previous-rules"}
    assert rules[1]["trigger"]["url-filter"] == "^qs-preview://doc/"


def test_real_font_specimen_is_served_with_font_mime(tmp_path):
    """預覽核心的字型樣張以 @font-face 引用 cid:font-file：改寫後要能用自訂網址拿到字型檔。"""
    import shutil  # noqa: PLC0415
    from pathlib import Path  # noqa: PLC0415

    from quickseemd.core import preview  # noqa: PLC0415

    system_font = Path("/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf")
    if not system_font.is_file():
        import pytest  # noqa: PLC0415

        pytest.skip("沒有可以借用的 TTF")
    font = tmp_path / "樣張.ttf"
    shutil.copyfile(system_font, font)
    page = scheme.build_page(preview.render_font(font), 11)
    assert "url(qs-preview://doc/att/font-file?v=11)" in page.html
    assert "cid:font-file" not in page.html
    res = scheme.lookup("qs-preview://doc/att/font-file?v=11", {11: page})
    assert res is not None
    assert res.mime == "font/ttf"
    assert res.data == font.read_bytes()
