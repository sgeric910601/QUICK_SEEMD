"""預覽核心：語言定義、語言偵測、語法上色。"""

from __future__ import annotations

from pathlib import Path

import pytest

from quickseemd.core.preview import detect, highlight, languages
from quickseemd.core.preview.page import FileInfo

L = languages.BY_ID


def test_language_table() -> None:
    ids = [lang.id for lang in languages.ALL]
    assert len(ids) == len(set(ids)) == 59
    assert ids[:3] == ["swift", "c", "java"]
    assert ids[-3:] == ["ignore", "subtitles", "text"]
    assert {lang.id for lang in languages.ALL if lang.wrap} == {
        "latex",
        "log",
        "subtitles",
        "text",
    }
    assert L["json"].name == "JSON"
    assert L["ini"].name == "INI / 設定檔"
    assert L["dotenv"].name == "環境變數 (.env)"
    assert L["log"].name == "記錄檔"
    assert L["text"].name == "純文字"
    assert L["html"].embedded == (("script", "javascript"), ("style", "css"))


def test_python_code_view() -> None:
    html, lines = highlight.code_view("def foo(x):\n    return 'a' # c\n", L["python"])
    assert lines == 2
    assert html == (
        '<div class="code"><div class="gut">1\n2</div><pre class="src">'
        '<span class="k">def</span> <span class="f">foo</span>(x):\n'
        '    <span class="k">return</span> <span class="s">\'a\'</span> '
        '<span class="c"># c</span></pre></div>'
    )


@pytest.mark.parametrize(
    ("lang", "src", "expected"),
    [
        (
            "json",
            '{"a": "b", "n": -1.5e3, "t": true, "z": null}',
            '{<span class="y">"a"</span>: <span class="s">"b"</span>, '
            '<span class="y">"n"</span>: -<span class="n">1.5e3</span>, '
            '<span class="y">"t"</span>: <span class="lit">true</span>, '
            '<span class="y">"z"</span>: <span class="lit">null</span>}',
        ),
        (
            "swift",
            "struct Foo {}\nfunc bar() -> Int { let x = 0x1F }",
            '<span class="k">struct</span> <span class="t">Foo</span> {}\n'
            '<span class="k">func</span> <span class="f">bar</span>() -&gt; '
            '<span class="t">Int</span> { <span class="k">let</span> x = '
            '<span class="n">0x1F</span> }',
        ),
        (
            "c",
            "#include <stdio.h>\nint main() { return 'ab'; }",
            '<span class="p">#include &lt;stdio.h&gt;</span>\n<span class="k">int</span> '
            '<span class="f">main</span>() { <span class="k">return</span> '
            "<span class=\"s\">'ab'</span>; }",
        ),
        ("c", "x = 'abcdefghij';", "x = 'abcdefghij';"),
        (
            "rust",
            '#[derive(Debug)]\nfn main() { println!("hi"); let r: &\'a str; }',
            '<span class="a">#[derive(Debug)]</span>\n<span class="k">fn</span> '
            '<span class="f">main</span>() { <span class="p">println!</span>'
            '(<span class="s">"hi"</span>); <span class="k">let</span> r: &amp;\'a '
            '<span class="t">str</span>; }',
        ),
        (
            "shell",
            "echo $HOME ${X:-y} a#b # c\n",
            '<span class="f">echo</span> <span class="a">$HOME</span> '
            '<span class="a">${X:-y}</span> a#b <span class="c"># c</span>',
        ),
        (
            "diff",
            "diff --git a b\n@@ -1 +1 @@\n-old\n+new\n ctx\n",
            '<span class="dm">diff --git a b</span>\n<span class="dh">@@ -1 +1 @@</span>\n'
            '<span class="dd">-old</span>\n<span class="da">+new</span>\n ctx',
        ),
        (
            "yaml",
            '# c\n---\nkey: value # note\nlist:\n  - &a 1\n  - *a\nq: "x: y"\n',
            '<span class="c"># c</span>\n<span class="k">---</span>\n'
            '<span class="y">key</span>: value <span class="c"># note</span>\n'
            '<span class="y">list</span>:\n  <span class="p">- </span>'
            '<span class="a">&amp;a</span> <span class="n">1</span>\n'
            '  <span class="p">- </span><span class="a">*a</span>\n'
            '<span class="y">q</span>: <span class="s">"x: y"</span>',
        ),
        (
            "ini",
            "; c\n[General]\nLanguage=zh-TW\nWidth = 1920\n",
            '<span class="c">; c</span>\n<span class="hd">[General]</span>\n'
            '<span class="y">Language</span>=zh-TW\n<span class="y">Width </span>= '
            '<span class="n">1920</span>',
        ),
        (
            "dotenv",
            'export API="x" # c\nPORT=3000\n',
            # 原版只把「export」6 個字元標成關鍵字，後面的空白會併進鍵名
            '<span class="k">export</span><span class="y"> API</span>='
            '<span class="s">"x"</span> <span class="c"># c</span>\n'
            '<span class="y">PORT</span>=<span class="n">3000</span>',
        ),
        (
            "html",
            "<script>var x = 1;</script>",
            '&lt;<span class="g">script</span>&gt;<span class="k">var</span> x = '
            '<span class="n">1</span>;&lt;/<span class="g">script</span>&gt;',
        ),
        (
            "xml",
            '<!-- c --><a href="x" b=c/>&amp;<![CDATA[z]]><?pi x?>',
            '<span class="c">&lt;!-- c --&gt;</span>&lt;<span class="g">a</span> '
            '<span class="a">href</span>=<span class="s">"x"</span> <span class="a">b</span>='
            '<span class="s">c</span>/&gt;<span class="lit">&amp;amp;</span>'
            '<span class="s">&lt;![CDATA[z]]&gt;</span><span class="p">&lt;?pi x?&gt;</span>',
        ),
        (
            "css",
            "#app .button:hover { color: #fff !important; }",
            '<span class="a">#app</span> <span class="t">.button</span>:'
            '<span class="g">hover</span> { <span class="y">color</span>: '
            '<span class="n">#fff</span> <span class="k">!important</span>; }',
        ),
        (
            "sql",
            "SELECT count(*) FROM t -- c",
            '<span class="k">SELECT</span> <span class="f">count</span>(*) '
            '<span class="k">FROM</span> t <span class="c">-- c</span>',
        ),
        (
            "python",
            "@app.route('/')\nclass A(B): pass",
            '<span class="a">@app.route</span>(<span class="s">\'/\'</span>)\n'
            '<span class="k">class</span> <span class="t">A</span>(<span class="t">B</span>): '
            '<span class="k">pass</span>',
        ),
        ("ruby", "x = :sym; y = a::b", 'x = <span class="lit">:sym</span>; y = a::b'),
        (
            "make",
            "all: build\n\t$(CC) -o app\n",
            '<span class="f">all</span>: build\n\t<span class="a">$(</span>CC) -o app',
        ),
        (
            "batch",
            "REM comment\n@echo off\nset X=%PATH%",
            '<span class="c">REM comment</span>\n@<span class="k">echo</span> off\n'
            '<span class="k">set</span> X=<span class="a">%PATH%</span>',
        ),
        (
            "asm",
            ".text\n_start:\n  mov rax, 0x3c ; exit\n",
            '<span class="p">.text</span>\n<span class="f">_start</span>:\n  mov rax, '
            '<span class="n">0x3c</span> <span class="c">; exit</span>',
        ),
        (
            "go",
            "s := `raw\nline` + 'c' // x",
            's := <span class="s">`raw\nline`</span> + <span class="s">\'c\'</span> '
            '<span class="c">// x</span>',
        ),
        (
            "typescript",
            "type T = string; /* a\nb */",
            '<span class="k">type</span> <span class="t">T</span> = <span class="t">string</span>;'
            ' <span class="c">/* a\nb */</span>',
        ),
        (
            "latex",
            "\\section{A} % c",
            '<span class="k">\\section</span>{A} <span class="c">% c</span>',
        ),
        ("text", "<b>&</b>", "&lt;b&gt;&amp;&lt;/b&gt;"),
    ],
)
def test_inline_highlighting(lang: str, src: str, expected: str) -> None:
    assert highlight.inline(src, L[lang]) == expected


@pytest.mark.parametrize(
    ("lang", "src", "expected"),
    [
        ("yaml", "a:b: c", '<span class="y">a:b</span>: c'),
        ("yaml", "- #x: y", '<span class="p">- </span><span class="y">#x</span>: y'),
        ("yaml", "k #c: v", 'k <span class="c">#c: v</span>'),
        ("yaml", '"a: b": c', '<span class="y">"a: b"</span>: c'),
        ("yaml", "'open: x", '<span class="s">\'open: x</span>'),
        ("ini", 'a"b=c', '<span class="y">a"b</span>=c'),
        (
            "toml",
            "[[bin]]\nname = 'x'",
            '<span class="hd">[[bin]]</span>\n'
            '<span class="y">name </span>= <span class="s">\'x\'</span>',
        ),
        ("properties", "! c\nk:v", '<span class="c">! c</span>\n<span class="y">k</span>:v'),
    ],
)
def test_key_value_edge_cases(lang: str, src: str, expected: str) -> None:
    assert highlight.inline(src, L[lang]) == expected


def test_both_renderers_agree() -> None:
    src = 'x = """a\n<b>&\n"""  # c\ny = 1\n'
    b = src.encode()
    toks = highlight.tokens(b, L["python"])
    for rows in (False, True):
        assert highlight.render(b, toks, rows) == highlight._render_pieces(b, toks, rows)
    # 文字用光了所有可當標記的控制字元時，改用逐段跳脫
    crowded = bytes(range(1, 32)) + b" 'a'\n"
    toks = highlight.tokens(crowded, L["python"])
    assert highlight._free_marks(crowded) is None
    assert highlight.render(crowded, toks, False) == highlight._render_pieces(crowded, toks, False)


def test_log_rows_layout() -> None:
    src = (
        "2026-10-18 09:12:01.123 INFO  [main] started\n"
        "[2026-10-18T09:16:00Z] FATAL Out of memory\n"
    )
    html, lines = highlight.code_view(src, L["log"])
    assert lines == 2
    assert html == (
        '<div class="code wrap" style="--gw:2ch"><div class="l">'
        '<span class="lt">2026-10-18 09:12:01.123</span> <span class="li">INFO</span>'
        '  [main] started</div><div class="l"><span class="lt">[2026-10-18T09:16:00Z]</span> '
        '<span class="le">FATAL</span> Out of memory</div></div>'
    )


def test_rows_layout_basic_and_spans_across_lines() -> None:
    assert highlight.code_view("a\nb\n", L["text"]) == (
        '<div class="code wrap" style="--gw:2ch"><div class="l">a</div><div class="l">b</div></div>',
        2,
    )
    html, lines = highlight.code_view('x = """a\nb"""\n', L["python"], wrap=True)
    assert lines == 2
    assert html == (
        '<div class="code wrap" style="--gw:2ch"><div class="l">x = <span class="s">"""a</span>'
        '</div><div class="l"><span class="s">b"""</span></div></div>'
    )
    html, lines = highlight.code_view("x\n" * 100, L["text"])
    assert lines == 100
    assert 'style="--gw:3ch"' in html


def test_nogutter_and_empty_layouts() -> None:
    assert highlight.code_view("x", L["python"], wrap=False, gutter=False) == (
        '<div class="code nogutter"><div class="gut"></div><pre class="src">x</pre></div>',
        1,
    )
    assert highlight.code_view("", L["text"]) == (
        '<div class="code wrap" style="--gw:2ch"><div class="l"></div></div>',
        0,
    )
    assert highlight.code_view("", L["python"]) == (
        '<div class="code"><div class="gut">1</div><pre class="src"></pre></div>',
        0,
    )


def test_plainwrap_for_huge_lines() -> None:
    src = "x" * 9000 + "\n" + "<a&b>\n" * 12000
    html, lines = highlight.code_view(src, L["python"])
    assert lines == 12001
    assert html.startswith('<div class="code plainwrap"><pre class="src">xxx')
    assert "&lt;a&amp;b&gt;" in html
    assert "<span" not in html


def test_many_lines_switch_to_gutter_layout() -> None:
    html, lines = highlight.code_view("a\n" * 4001, L["text"])
    assert lines == 4001
    assert html.startswith('<div class="code"><div class="gut">1\n2\n3')
    html, _ = highlight.code_view("a\n" * 4000, L["text"])
    assert html.startswith('<div class="code wrap"')


def test_unterminated_constructs_run_to_end() -> None:
    assert highlight.inline("x /* open", L["c"]) == 'x <span class="c">/* open</span>'
    assert highlight.inline('s = "open\nnext', L["python"]) == (
        's = <span class="s">"open</span>\nnext'
    )
    assert highlight.inline("<!-- open", L["xml"]) == '<span class="c">&lt;!-- open</span>'


def test_keyword_highlighting_is_case_insensitive_where_defined() -> None:
    assert highlight.inline("select", L["sql"]) == '<span class="k">select</span>'
    assert highlight.inline("Select", L["python"]) == '<span class="t">Select</span>'


# ------------------------------------------------------------------ 語言偵測


@pytest.mark.parametrize(
    ("name", "content", "lang"),
    [
        ("Makefile", "", "make"),
        ("Dockerfile.prod", "", "docker"),
        ("api.dockerfile", "", "docker"),
        (".env.local", "", "dotenv"),
        ("build.gradle", "", "java"),
        ("Cargo.lock", "", "toml"),
        ("deps.lock", '{"a": 1}', "json"),
        ("deps.lock", "plain", "yaml"),
        ("noext", "#!/usr/bin/env -S python3.11\nprint()", "python"),
        ("noext", "#!/bin/bash\necho", "shell"),
        ("noext", "#!/usr/local/bin/node\n", "javascript"),
        ("README", "", "text"),
        (".bashrc", "", "shell"),
        ("x.unknown", '{"a": [1, 2]}', "json"),
        ("x.unknown", "[[a]]\nb = 1", "toml"),
        ("x.unknown", '<?xml version="1.0"?><a/>', "xml"),
        ("x.unknown", "<!DOCTYPE html><html></html>", "html"),
        ("x.unknown", "diff --git a b", "diff"),
        ("x.unknown", "--- a\n+++ b\n", "diff"),
        ("x.unknown", "---\nfoo", "yaml"),
        ("x.unknown", "key: v\nk2: v", "yaml"),
        ("x.unknown", "plain words", "text"),
        ("main.rs", "", "rust"),
        ("index.tsx", "", "typescript"),
        ("x.h", "", "c"),
        ("notes.txt", "{}", "text"),
    ],
)
def test_detect(name: str, content: str, lang: str) -> None:
    assert detect.detect(FileInfo.virtual(name), content).id == lang


def test_detect_uses_mime_type(tmp_path: Path) -> None:
    f = tmp_path / "module.jsm"
    f.write_text("var x = 1;\n", "utf-8")
    info = FileInfo.of(f)
    assert info.mime is not None
    assert detect.detect(info, "var x = 1;\n").id == "javascript"


@pytest.mark.parametrize(
    ("hint", "lang"),
    [
        ("js", "javascript"),
        ("py", "python"),
        ("swift", "swift"),
        ("C++", "c"),
        ("js {1,3}", "javascript"),
        ("lock", "yaml"),
        ("groovy", "java"),
        ("ts", "typescript"),
        ("json", "json"),
        ("tf", "hcl"),
        ("mjs", "javascript"),
        ("zzzz", None),
        ("", None),
        (None, None),
        ("txt", "text"),
    ],
)
def test_for_hint(hint: str | None, lang: str | None) -> None:
    found = detect.for_hint(hint)
    assert (found.id if found else None) == lang


def test_value_language() -> None:
    yaml = detect.value_language("yaml")
    assert b"yes" in yaml.literals and b"~" in yaml.literals
    assert yaml.sigils == b"&*"
    assert detect.value_language("ini").line_comments == ()
    assert detect.value_language("dotenv").sigils == b"$"
    assert detect.value_language("toml").literals == frozenset({b"true", b"false"})


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (b"a" * 8001, True),
        (b"a" * 8000, False),
        (b"\n" + b"a" * 8001 + b"\n", True),
        ((b"a" * 7999 + b"\n") * 20, False),
        (b"x" * 4000 + b"\n" + b"y" * 8001 + b"\n" * 3, True),
        (b"\n" * 50000, False),
        (b"", False),
    ],
)
def test_has_long_line(text: bytes, expected: bool) -> None:
    assert highlight.has_long_line(text, 8000) is expected
