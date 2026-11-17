"""預覽核心：Markdown 轉 HTML、原始 HTML 過濾。"""

from __future__ import annotations

import sys
import os
from pathlib import Path

import pytest

from quickseemd.core.preview import markdown, sanitize

PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    b"\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00"
    b"\x00\x00IEND\xaeB`\x82"
)

# 覆蓋率量測會讓 Python 慢好幾倍；那時放寬時間上限，平常照原本的預算檢查
TIME_SCALE = 8.0 if sys.gettrace() is not None else 1.0


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
    (tmp_path / "img" / "b.png").write_bytes(PNG)
    result = markdown.render("![logo](img/a.png) ![two](img/b.png)\n", tmp_path)
    assert result.html == (
        '<p><img src="cid:md-img-1" alt="logo"> <img src="cid:md-img-2" alt="two"></p>\n'
    )
    att = result.attachments["md-img-1"]
    assert att.name == "md-img-1"
    assert att.mime == "image/png"
    assert att.data == PNG
    assert result.images == 2


def test_same_image_is_read_once(tmp_path: Path) -> None:
    (tmp_path / "a.png").write_bytes(PNG)
    result = markdown.render("![x](a.png)\n" * 500, tmp_path)
    assert set(result.attachments) == {"md-img-1"}
    assert result.html.count('src="cid:md-img-1"') == 500


def test_image_count_and_size_budget(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    for i in range(5):
        (tmp_path / f"{i}.png").write_bytes(PNG)
    monkeypatch.setattr(markdown, "MAX_IMAGES", 3)
    result = markdown.render("".join(f"![i{i}]({i}.png) " for i in range(5)), tmp_path)
    assert len(result.attachments) == 3
    assert result.html.count("img-missing") == 2
    monkeypatch.setattr(markdown, "MAX_IMAGES", 200)
    monkeypatch.setattr(markdown, "MAX_IMAGE_TOTAL", len(PNG) * 2)
    result = markdown.render("".join(f"![i{i}]({i}.png) " for i in range(5)), tmp_path)
    assert len(result.attachments) == 2


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
    assert time.perf_counter() - start < 2.5 * TIME_SCALE
    assert result.degraded is True


def test_long_documents_are_truncated_to_plain_text(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(markdown, "MAX_LINES", 3)
    result = markdown.render("# a\n\nb\n**c**\n<d>\n", None)
    assert result.truncated is True
    assert result.html == "<h1>a</h1>\n<p>b</p>\n<pre>**c**\n&lt;d&gt;\n</pre>\n"


def test_table_cell_amplification_is_bounded() -> None:
    import time

    wide = "|" + "a|" * 200 + "\n|" + "-|" * 200 + "\n" + "x\n" * 50_000
    start = time.perf_counter()
    result = markdown.render(wide, None)
    assert time.perf_counter() - start < 2.0 * TIME_SCALE
    assert result.degraded is True
    assert "<table>" not in result.html
    small = markdown.render("| a | b |\n|---|---|\n| 1 | 2 |\n", None)
    assert "<table>" in small.html and small.degraded is False


def test_normal_markdown_is_not_degraded() -> None:
    result = markdown.render("# T\n\n" + "一般的 **段落**。\n\n" * 500, None)
    assert result.degraded is False
    assert result.html.count("<strong>") == 500


# ------------------------------------------------------------------ 允許清單：SVG 動畫等繞過手法都要失效

INERT_CASES = [
    '<svg><animate attributeName="href" to="javascript:alert(1)"/><text>t</text></svg>',
    '<svg><a><set attributeName="href" to="javascript:alert(1)"/><text>t</text></a></svg>',
    '<svg><animateTransform attributeName="transform"/><animateMotion path="M0 0"/></svg>',
    '<svg><discard begin="0s"/><handler type="text/ecmascript">alert(1)</handler></svg>',
    '<svg><listener event="click" handler="#h"/></svg>',
    '<svg><use href="https://evil.example/x.svg#a"/><use xlink:href="file:///etc/passwd#x"/></svg>',
    "<svg><foreignObject><script>alert(1)</script><p>inside</p></foreignObject></svg>",
    '<svg><image href="https://evil.example/x.png"/><image xlink:href="javascript:x"/></svg>',
    '<form action="javascript:x"><input formaction="javascript:y"><button>b</button></form>',
    '<a href="javascript:alert(1)" xlink:href="javascript:x">l</a>',
    '<iframe srcdoc="<script>alert(1)</script>"></iframe>',
    '<math><maction actiontype="statusline" xlink:href="javascript:x">m</maction></math>',
]


@pytest.mark.parametrize("raw", INERT_CASES)
def test_allowlist_makes_bypasses_inert(raw: str) -> None:
    out = sanitize.sanitize_html(raw).lower()
    for bad in ("javascript:", "attributename", "<animate", "<set", "<discard", "<handler",
                "<listener", "<use", "<foreignobject", "<script", "<iframe", "srcdoc",
                "formaction", "<form", "<input", "xlink:href", "evil.example", "file:///",
                "inside", "alert(1)"):  # fmt: skip
        assert bad not in out, (bad, out)


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ('<p style="color:red">a</p>', '<p style="color:red">a</p>'),
        ('<p style="background:url(http://evil.example/x.png)">a</p>', "<p>a</p>"),
        ('<p style="width: expression(alert(1))">a</p>', "<p>a</p>"),
        ("<p style=\"@import 'x.css'\">a</p>", "<p>a</p>"),
        ('<p style="background:u\\72l(http://x)">a</p>', "<p>a</p>"),
        (
            '<p style="background:url(cid:md-img-1)">a</p>',
            '<p style="background:url(cid:md-img-1)">a</p>',
        ),
        (
            '<p style="background:url(data:image/png;base64,AA)">a</p>',
            '<p style="background:url(data:image/png;base64,AA)">a</p>',
        ),
        (
            '<svg viewBox="0 0 10 10"><circle r="1" fill="url(#g)" stroke="url(http://x/y)"/></svg>',
            '<svg viewbox="0 0 10 10"><circle r="1" fill="url(#g)"/></svg>',
        ),
        (
            '<svg><image href="data:image/png;base64,AA" width="4"/></svg>',
            '<svg><image href="data:image/png;base64,AA" width="4"/></svg>',
        ),
        (
            '<svg><image xlink:href="cid:md-img-1"/></svg>',
            '<svg><image xlink:href="cid:md-img-1"/></svg>',
        ),
        ("<blink>hi</blink> <marquee>there</marquee>", "hi there"),
        (
            '<a href="https://e.com" target="_blank" onclick="x">l</a>',
            '<a href="https://e.com">l</a>',
        ),
        (
            '<a href="mailto:a@b.c">m</a><a href="#top">t</a>',
            '<a href="mailto:a@b.c">m</a><a href="#top">t</a>',
        ),
        ('<img src="javascript:alert(1)" alt="x">', '<img alt="x">'),
        (
            '<table border="1" class="dataframe"><tr style="text-align: right;"><td colspan="2">1</td></tr></table>',
            '<table border="1" class="dataframe"><tr style="text-align: right;"><td colspan="2">1</td></tr></table>',
        ),
        ("<div><style scoped>.x{color:red}</style><b>t</b></div>", "<div><b>t</b></div>"),
        (
            "<details open><summary>s</summary>d</details>",
            "<details open><summary>s</summary>d</details>",
        ),
        ("<br/><hr>", "<br/><hr>"),
        ("</img></br>", ""),
    ],
)
def test_allowlist_keeps_safe_markup(raw: str, expected: str) -> None:
    assert sanitize.sanitize_html(raw) == expected
