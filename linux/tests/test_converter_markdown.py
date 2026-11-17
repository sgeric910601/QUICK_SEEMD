"""萬用轉換：Markdown → HTML 渲染器（對應原版 CVMarkdownRenderer）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from quickseemd.core.converter import mdrender

CHECKED = "\N{BALLOT BOX WITH CHECK}\N{VARIATION SELECTOR-15}"
UNCHECKED = "\N{BALLOT BOX}"


def body(md: str) -> str:
    return mdrender.render_body(md)


def test_front_matter_is_stripped() -> None:
    assert body("---\ntitle: x\n---\n# 標題\n") == '<h1 id="標題">標題</h1>'
    assert body("---\na: 1\n...\ntext") == "<p>text</p>"


def test_atx_headings_and_slug() -> None:
    assert body("## Hello, World-2 ##") == '<h2 id="hello-world-2">Hello, World-2</h2>'
    assert body("####### seven") == "<p>####### seven</p>"
    assert body("#nospace") == "<p>#nospace</p>"
    assert body('# a "q" & b') == '<h1 id="a-q--b">a "q" &amp; b</h1>'


def test_setext_headings() -> None:
    assert body("Title\n===") == '<h1 id="title">Title</h1>'
    assert body("Sub\n--") == '<h2 id="sub">Sub</h2>'


def test_fenced_code_is_escaped_with_language_class() -> None:
    md = "```python extra\nif a < b:\n    pass\n```"
    assert body(md) == ('<pre><code class="language-python">if a &lt; b:\n    pass\n</code></pre>')
    assert body("~~~\nx\n~~~") == "<pre><code>x\n</code></pre>"


def test_indented_code() -> None:
    assert body("    a\n    b\n\n") == "<pre><code>a\nb\n</code></pre>"


def test_blockquote_with_lazy_continuation() -> None:
    assert body("> one\ncontinued\n\nafter") == (
        "<blockquote>\n<p>one\ncontinued</p>\n</blockquote>\n<p>after</p>"
    )


def test_blockquote_with_tab_does_not_hang() -> None:
    assert "x" in body("\t> x")


def test_tight_and_loose_lists() -> None:
    assert body("- a\n- b") == "<ul>\n<li>a</li>\n<li>b</li>\n</ul>"
    assert body("- a\n\n- b") == "<ul>\n<li><p>a</p></li>\n<li><p>b</p></li>\n</ul>"


def test_ordered_list_start_and_nesting() -> None:
    assert body("3. x\n4. y") == '<ol start="3">\n<li>x</li>\n<li>y</li>\n</ol>'
    nested = body("- a\n  - b\n- c")
    assert nested == "<ul>\n<li>a\n<ul>\n<li>b</li>\n</ul></li>\n<li>c</li>\n</ul>"


def test_task_items_use_glyphs() -> None:
    out = body("- [ ] todo\n- [x] done")
    assert f'<li class="task">{UNCHECKED} todo</li>' in out
    assert f'<li class="task">{CHECKED} done</li>' in out


def test_table_alignment() -> None:
    md = "| a | b | c | d |\n|:--|--:|:-:|---|\n| 1 | 2 | 3 | `x|y` |\n| only |"
    assert body(md) == (
        "<table>\n<thead>\n<tr>"
        '<th style="text-align:left">a</th><th style="text-align:right">b</th>'
        '<th style="text-align:center">c</th><th>d</th>'
        "</tr>\n</thead>\n<tbody>\n"
        '<tr><td style="text-align:left">1</td><td style="text-align:right">2</td>'
        '<td style="text-align:center">3</td><td><code>x|y</code></td></tr>\n'
        '<tr><td style="text-align:left">only</td><td style="text-align:right"></td>'
        '<td style="text-align:center"></td><td></td></tr>\n'
        "</tbody>\n</table>"
    )


def test_raw_html_block_is_kept() -> None:
    assert body("<div class=x>\n*hi*\n</div>\n\npara") == "<div class=x>\n*hi*\n</div>\n<p>para</p>"


def test_horizontal_rule() -> None:
    assert body("a\n\n* * *\n\nb") == "<p>a</p>\n<hr>\n<p>b</p>"


@pytest.mark.parametrize(
    ("md", "html"),
    [
        (r"\*not\*", "*not*"),
        ("a `<b>` c", "a <code>&lt;b&gt;</code> c"),
        ("`` a`b ``", "<code>a`b</code>"),
        (
            "*em* **strong** ***both***",
            "<em>em</em> <strong>strong</strong> <strong><em>both</em></strong>",
        ),
        ("~~del~~ ~one~", "<del>del</del> ~one~"),
        ("snake_case_name", "snake_case_name"),
        ("_under_", "<em>under</em>"),
        ('[t](http://x.y/a "T")', '<a href="http://x.y/a" title="T">t</a>'),
        ("[t](http://x.y/a b)", '<a href="http://x.y/a">t</a>'),
        ("![alt](img.png)", '<img src="img.png" alt="alt">'),
        ("[x](<a b.html> 'T')", '<a href="a b.html" title="T">x</a>'),
        (
            "<https://e.com/?a=1&b=2>",
            '<a href="https://e.com/?a=1&amp;b=2">https://e.com/?a=1&amp;b=2</a>',
        ),
        ("<me@e.com>", '<a href="mailto:me@e.com">me@e.com</a>'),
        ("x <kbd>K</kbd> y", "x <kbd>K</kbd> y"),
        ("see https://e.com/p.", 'see <a href="https://e.com/p">https://e.com/p</a>.'),
        ("網址https://e.com，好", "網址https://e.com，好"),
        ("網址 https://e.com。 好", '網址 <a href="https://e.com">https://e.com</a>。 好'),
        ("網址 https://e.com，好", '網址 <a href="https://e.com，好">https://e.com，好</a>'),
        ("a & b < c", "a &amp; b &lt; c"),
    ],
)
def test_inline(md: str, html: str) -> None:
    assert body(md) == f"<p>{html}</p>"


def test_reference_links() -> None:
    md = '[Site][s] and [s] and ![p][s]\n\n[s]: https://x.y "Title"\n'
    assert body(md) == (
        '<p><a href="https://x.y" title="Title">Site</a> and '
        '<a href="https://x.y" title="Title">s</a> and '
        '<img src="https://x.y" alt="p" title="Title"></p>'
    )


def test_reference_inside_fence_is_not_collected() -> None:
    md = "```\n[s]: http://x\n```\n[s]"
    assert body(md) == "<pre><code>[s]: http://x\n</code></pre>\n<p>[s]</p>"


def test_hard_breaks() -> None:
    assert body("a  \nb\\\nc") == "<p>a<br>\nb<br>\nc</p>"


def test_render_document_screen_and_import_css() -> None:
    screen = mdrender.render_document("# T", "標題 <x>", for_import=False)
    assert screen.startswith(
        '<!DOCTYPE html>\n<html lang="zh-Hant">\n<head>\n<meta charset="utf-8">'
    )
    assert "<title>標題 &lt;x&gt;</title>" in screen
    assert ".markdown-body { max-width: 860px; margin: 0 auto; padding: 40px 32px 80px; }" in screen
    assert "@media (prefers-color-scheme: dark)" in screen
    assert '<article class="markdown-body">\n<h1 id="t">T</h1>\n</article>' in screen
    imp = mdrender.render_document("# T", "t", for_import=True)
    assert "font-size: 11pt" in imp
    assert "color: #b3261e" in imp
    assert "prefers-color-scheme" not in imp


def test_first_heading() -> None:
    assert mdrender.first_heading("intro\n## **Bold** `code`\n# later") == "Bold code"
    assert mdrender.first_heading("no heading") is None


def test_readme_sample_renders(samples: Path) -> None:
    html = mdrender.render_body((samples / "README.md").read_text("utf-8"))
    assert "<h1" in html
    assert "<script" not in html.lower() or "<script" in (samples / "README.md").read_text("utf-8")
