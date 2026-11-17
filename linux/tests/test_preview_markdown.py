"""預覽核心：Markdown 轉 HTML、原始 HTML 過濾。"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from quickseemd.core.preview import markdown, sanitize

PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    b"\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00"
    b"\x00\x00IEND\xaeB`\x82"
)


def md(src: str, base: Path | None = None) -> str:
    return markdown.render(src, base).html


@pytest.mark.parametrize(
    ("src", "expected"),
    [
        ("# T\n\npara\n", "<h1>T</h1>\n<p>para</p>\n"),
        ("###### six\n", "<h6>six</h6>\n"),
        (
            "a **b** *c* `d` ~~e~~ [l](https://x.y)\n",
            "<p>a <strong>b</strong> <em>c</em> <code>d</code> <del>e</del> "
            '<a href="https://x.y">l</a></p>\n',
        ),
        ("**b *c***\n", "<p><strong>b </strong><strong><em>c</em></strong></p>\n"),
        (
            "- [x] done\n- [ ] todo\n",
            '<ul><li><p><input type="checkbox" class="task" disabled checked>done</p>\n</li>\n'
            '<li><p><input type="checkbox" class="task" disabled>todo</p>\n</li>\n</ul>\n',
        ),
        ("- a [x] b\n", "<ul><li><p>a [x] b</p>\n</li>\n</ul>\n"),
        ("[ ] not a list\n", "<p>[ ] not a list</p>\n"),
        (
            "3. a\n4. b\n",
            '<ol start="3"><li><p>a</p>\n</li>\n<li><p>b</p>\n</li>\n</ol>\n',
        ),
        ("1. a\n", "<ol><li><p>a</p>\n</li>\n</ol>\n"),
        (
            "```python\nx = 1\n```\n",
            '<pre><code>x = <span class="n">1</span></code></pre>\n',
        ),
        ('```zzz\n<a>"\n```\n', "<pre><code>&lt;a&gt;&quot;</code></pre>\n"),
        ("    x\n", "<pre><code>x</code></pre>\n"),
        (
            "| a | b | c |\n|:--|:-:|--:|\n| 1 | 2 | 3 |\n",
            '<table><thead><tr><th>a</th><th style="text-align:center">b</th>'
            '<th style="text-align:right">c</th></tr></thead>\n<tr><td>1</td>'
            '<td style="text-align:center">2</td><td style="text-align:right">3</td></tr>\n'
            "</table>\n",
        ),
        ("a\n\n---\n\nb\n", "<p>a</p>\n<hr><p>b</p>\n"),
        ("> q\n", "<blockquote><p>q</p>\n</blockquote>\n"),
        ("a\\\nb\n", "<p>a<br>b</p>\n"),
        ("a\nb\n", "<p>a\nb</p>\n"),
        ('a < b & "c"\n', "<p>a &lt; b &amp; &quot;c&quot;</p>\n"),
        ("![r](https://e.com/x.png)\n", '<p><img src="https://e.com/x.png" alt="r"></p>\n'),
    ],
)
def test_markdown_blocks(src: str, expected: str) -> None:
    assert md(src) == expected


def test_front_matter_table() -> None:
    src = "---\ntitle: T <x>\ntags:\n  - a\n  - b\nloose\n---\n# H\n"
    assert md(src) == (
        '<div class="front"><table><tr><th>title</th><td>T &lt;x&gt;</td></tr>'
        "<tr><th>tags</th><td><br>- a<br>- b<br>loose</td></tr></table></div><h1>H</h1>\n"
    )


def test_front_matter_needs_close_within_200_lines() -> None:
    src = "---\n" + "a: b\n" * 250 + "---\ntext\n"
    assert 'class="front"' not in md(src)
    assert markdown.split_front_matter("no front matter") is None
    assert markdown.split_front_matter("---\nk: v\n...\nrest") == (
        '<div class="front"><table><tr><th>k</th><td>v</td></tr></table></div>',
        "rest",
    )


def test_relative_link_is_resolved(tmp_path: Path) -> None:
    html = md("[a](docs/x.md) [b](#top)\n", tmp_path)
    assert f'<a href="{(tmp_path / "docs" / "x.md").as_uri()}">a</a>' in html
    assert f'<a href="{tmp_path.as_uri()}/#top">b</a>' in html


def test_local_images_become_attachments(tmp_path: Path) -> None:
    (tmp_path / "img").mkdir()
    (tmp_path / "img" / "a.png").write_bytes(PNG)
    result = markdown.render("![logo](img/a.png) ![two](img/a.png)\n", tmp_path)
    assert result.html == (
        '<p><img src="cid:md-img-1" alt="logo"> <img src="cid:md-img-2" alt="two"></p>\n'
    )
    att = result.attachments["md-img-1"]
    assert att.name == "md-img-1"
    assert att.mime == "image/png"
    assert att.data == PNG
    assert result.images == 2


def test_missing_and_unsafe_local_images(tmp_path: Path) -> None:
    big = tmp_path / "big.png"
    with open(big, "wb") as fh:
        fh.truncate(15_000_000)
    os.mkfifo(tmp_path / "pipe.png")
    html = md("![gone](nope.png) ![](nope.png) ![b](big.png) ![p](pipe.png)\n", tmp_path)
    assert html == (
        '<p><span class="img-missing">🖼 gone</span> <span class="img-missing">🖼 nope.png</span>'
        ' <span class="img-missing">🖼 b</span> <span class="img-missing">🖼 p</span></p>\n'
    )


def test_image_numbering_can_continue(tmp_path: Path) -> None:
    (tmp_path / "a.png").write_bytes(PNG)
    result = markdown.render("![x](a.png)\n", tmp_path, first_image=3)
    assert "cid:md-img-4" in result.html
    assert set(result.attachments) == {"md-img-4"}


def test_raw_html_is_passed_through_but_sanitized() -> None:
    html = md(
        '<div class="x">ok</div>\n\n<script>alert(1)</script>\n\ntext <b onclick="x()">b</b>\n'
    )
    assert '<div class="x">ok</div>' in html
    assert "<b>b</b>" in html
    assert "<script" not in html.lower()
    assert "alert(1)" not in html
    assert "onclick" not in html


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("<p>a &amp; b</p>", "<p>a &amp; b</p>"),
        ("<script>x()</script>after", "after"),
        ('<img src="x.png" onerror="alert(1)">', '<img src="x.png">'),
        ('<a href="javascript:alert(1)">l</a>', "<a>l</a>"),
        ('<a href=" JaVaScRiPt:alert(1)">l</a>', "<a>l</a>"),
        ('<a href="https://e.com/?a=1&b=2">l</a>', '<a href="https://e.com/?a=1&amp;b=2">l</a>'),
        ('<iframe src="https://e.com"></iframe>', ""),
        ("<!-- <script>x</script> -->c", "c"),
        ('<svg onload="x()"><circle r="1"/></svg>', '<svg><circle r="1"/></svg>'),
        ('<meta http-equiv="refresh" content="0;url=x">', ""),
        ("<br>", "<br>"),
        ("<span>", "<span>"),
        ("</span>", "</span>"),
        ('<object data="x"><p>fallback</p></object>', "<p>fallback</p>"),
        ('<img src="data:text/html;base64,xx">', "<img>"),
        ('<img src="data:image/png;base64,xx">', '<img src="data:image/png;base64,xx">'),
    ],
)
def test_sanitize(raw: str, expected: str) -> None:
    assert sanitize.sanitize_html(raw) == expected


# ------------------------------------------------------------------ 惡意內容不可拖垮預覽

MIB2 = 2 * 1024 * 1024


def _paragraphs(unit: str, size: int = 19990) -> str:
    one = (unit * (size // len(unit)))[:size] + "\n\n"
    return one * (MIB2 // (size + 2))


@pytest.mark.parametrize(
    "text",
    [
        "_a" * (MIB2 // 2),  # 單一超長段落（markdown-it 會變平方時間）
        _paragraphs("["),  # 每段都接近上限、內容很密
        _paragraphs("_a"),
        _paragraphs("<"),
    ],
    ids=["one-paragraph", "brackets", "underscores", "angles"],
)
def test_hostile_markdown_is_bounded(text: str) -> None:
    import time

    start = time.perf_counter()
    result = markdown.render(text, None)
    assert time.perf_counter() - start < 2.5
    assert result.degraded is True


def test_normal_markdown_is_not_degraded() -> None:
    result = markdown.render("# T\n\n" + "一般的 **段落**。\n\n" * 500, None)
    assert result.degraded is False
    assert result.html.count("<strong>") == 500
