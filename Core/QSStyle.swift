import Foundation

/// 所有預覽共用的樣式表：乾淨的內容區、半透明工具列、跟隨系統深淺色。
enum QSStyle {
    static let css = #"""
:root {
  color-scheme: light dark;
  --bg: #ffffff; --bg-2: #f6f6f8; --bg-3: #ececf0; --hover: #f1f5fd;
  --line: rgba(0,0,0,.08); --line-2: rgba(0,0,0,.14);
  --fg: #1d1d1f; --fg-2: #6e6e73; --fg-3: #a8a8ad;
  --accent: #0a7aff; --accent-soft: rgba(10,122,255,.10);
  --bar: rgba(248,248,250,.80);
  --ok: #1f9d55; --warn: #c27c0e; --err: #d93025;
  --mono: "SF Mono", ui-monospace, Menlo, Monaco, monospace;
  --sans: -apple-system, BlinkMacSystemFont, "SF Pro Text", "PingFang TC", "Helvetica Neue", sans-serif;
  --s-kw: #9b2393; --s-str: #c41a16; --s-com: #6c7986; --s-num: #1c00cf; --s-type: #0b4f79;
  --s-fn: #326d74; --s-attr: #815f03; --s-pre: #643820; --s-key: #0451a5; --s-tag: #0b4f79;
  --add: rgba(46,160,67,.13); --add-fg: #1a7f37; --del: rgba(248,81,73,.13); --del-fg: #cf222e; --hunk: #8250df;
  --hx-null: #b8b8be; --hx-ascii: #1d1d1f; --hx-ws: #1f9d55; --hx-ctl: #b4529c; --hx-high: #c27c0e;
}
@media (prefers-color-scheme: dark) {
  :root {
    --bg: #1e1e20; --bg-2: #252528; --bg-3: #313135; --hover: #2a2d33;
    --line: rgba(255,255,255,.08); --line-2: rgba(255,255,255,.15);
    --fg: #e8e8ed; --fg-2: #a1a1a8; --fg-3: #626269;
    --accent: #4c9dff; --accent-soft: rgba(76,157,255,.16);
    --bar: rgba(38,38,41,.80);
    --ok: #4cc38a; --warn: #e5a13a; --err: #ff6b5e;
    --s-kw: #fc5fa3; --s-str: #fc6a5d; --s-com: #7f8c98; --s-num: #d0bf69; --s-type: #5dd8ff;
    --s-fn: #67b7a4; --s-attr: #d9a066; --s-pre: #fd8f3f; --s-key: #9cdcfe; --s-tag: #5dd8ff;
    --add: rgba(63,185,80,.16); --add-fg: #56d364; --del: rgba(248,81,73,.16); --del-fg: #ff7b72; --hunk: #bc8cff;
    --hx-null: #4a4a50; --hx-ascii: #e8e8ed; --hx-ws: #4cc38a; --hx-ctl: #d68bd0; --hx-high: #e5a13a;
  }
}
* { box-sizing: border-box; }
html, body { margin: 0; padding: 0; background: var(--bg); color: var(--fg); }
body { font: 13px/1.5 var(--sans); -webkit-font-smoothing: antialiased; }
a { color: var(--accent); text-decoration: none; }
a:hover { text-decoration: underline; }

/* ── 工具列 ───────────────────────────── */
.qs-bar {
  position: sticky; top: 0; left: 0; z-index: 20;
  display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
  padding: 7px 14px; min-height: 36px;
  background: var(--bar);
  -webkit-backdrop-filter: saturate(180%) blur(20px); backdrop-filter: saturate(180%) blur(20px);
  border-bottom: 1px solid var(--line);
  font-size: 12px; color: var(--fg-2);
}
.qs-bar img.ico { width: 18px; height: 18px; }
.qs-bar .kind { font-weight: 600; color: var(--fg); margin-right: 4px; }
.chip {
  display: inline-flex; align-items: center; gap: 4px;
  padding: 1px 8px; border-radius: 999px; background: var(--bg-3);
  font-size: 11px; color: var(--fg-2); white-space: nowrap;
}
.chip.ok { color: var(--ok); background: color-mix(in srgb, var(--ok) 14%, transparent); }
.chip.warn { color: var(--warn); background: color-mix(in srgb, var(--warn) 14%, transparent); }
.chip.err { color: var(--err); background: color-mix(in srgb, var(--err) 14%, transparent); }
.qs-bar .spacer { flex: 1; }
.notice {
  margin: 10px 14px 0; padding: 7px 12px; border-radius: 8px;
  background: var(--accent-soft); color: var(--fg-2); font-size: 12px;
}
.notice.warn { background: color-mix(in srgb, var(--warn) 13%, transparent); }

/* ── 程式碼檢視：不換行時行號欄固定在左側；換行模式每行一列 ───────── */
.code { display: flex; align-items: flex-start; font: 12px/1.65 var(--mono); tab-size: 4; width: max-content; min-width: 100%; padding: 8px 0 28px; }
.code .gut { position: sticky; left: 0; z-index: 1; flex: none; white-space: pre; text-align: right; padding: 0 12px 0 14px; color: var(--fg-3); background: var(--bg); border-right: 1px solid var(--line); -webkit-user-select: none; user-select: none; }
.code .src { margin: 0; padding: 0 28px 0 14px; white-space: pre; font: inherit; flex: 1; }
.code.wrap { display: block; width: auto; counter-reset: ln; }
.code.wrap .l { position: relative; white-space: pre-wrap; overflow-wrap: anywhere; padding: 0 20px 0 calc(var(--gw, 3ch) + 40px); min-height: 1.65em; }
.code.wrap .l::before { counter-increment: ln; content: counter(ln); position: absolute; left: 0; top: 0; bottom: 0; width: calc(var(--gw, 3ch) + 26px); padding-right: 12px; text-align: right; color: var(--fg-3); border-right: 1px solid var(--line); }
.code.wrap .l:hover { background: var(--hover); }
.code.nogutter .gut { display: none; }
.code.plainwrap { display: block; width: auto; }
.code.plainwrap .src { white-space: pre-wrap; overflow-wrap: anywhere; padding: 0 20px; }

.k { color: var(--s-kw); font-weight: 600; }
.s { color: var(--s-str); }
.c { color: var(--s-com); font-style: italic; }
.n { color: var(--s-num); }
.t { color: var(--s-type); }
.f { color: var(--s-fn); }
.a { color: var(--s-attr); }
.p { color: var(--s-pre); }
.y { color: var(--s-key); }
.g { color: var(--s-tag); }
.lit { color: var(--s-kw); }
.hd { color: var(--s-type); font-weight: 700; }
.da, .dd, .dh, .dm { display: inline-block; min-width: calc(100% - 4px); }
.da { background: var(--add); color: var(--add-fg); }
.dd { background: var(--del); color: var(--del-fg); }
.dh { color: var(--hunk); background: color-mix(in srgb, var(--hunk) 9%, transparent); }
.dm { color: var(--fg); font-weight: 700; }
.le { color: var(--err); font-weight: 700; }
.lw { color: var(--warn); font-weight: 700; }
.li { color: var(--ok); font-weight: 600; }
.ld { color: var(--fg-3); }
.lt { color: var(--fg-3); }

/* ── 資訊面板 ───────── */
.info { display: flex; gap: 28px; align-items: center; padding: 34px 40px 26px; }
.info img.big { width: 128px; height: 128px; flex: 0 0 128px; filter: drop-shadow(0 6px 14px rgba(0,0,0,.18)); }
.info .name { font-size: 20px; font-weight: 600; word-break: break-all; margin-bottom: 4px; }
.info .sub { color: var(--fg-2); margin-bottom: 12px; }
.kv { display: grid; grid-template-columns: max-content 1fr; gap: 4px 18px; font-size: 12.5px; }
.kv dt { color: var(--fg-2); }
.kv dd { margin: 0; word-break: break-all; }
.kv dd.mono { font-family: var(--mono); font-size: 11.5px; }

.section-title { margin: 18px 20px 8px; font-size: 11px; font-weight: 600; letter-spacing: .06em; text-transform: uppercase; color: var(--fg-2); }
.card { margin: 0 16px 16px; border: 1px solid var(--line); border-radius: 10px; background: var(--bg-2); overflow: hidden; }

/* ── 十六進位 ───────── */
.hex { font: 11.5px/1.7 var(--mono); padding: 10px 14px; white-space: pre; overflow-x: auto; }
.hex .o { color: var(--fg-3); }
.hex .z { color: var(--hx-null); }
.hex .p { color: var(--hx-ascii); }
.hex .w { color: var(--hx-ws); }
.hex .x { color: var(--hx-ctl); }
.hex .h { color: var(--hx-high); }
.hex .sep { color: var(--line-2); }

/* ── 表格（CSV / SQLite） ───────── */
.grid-wrap { padding: 0; }
table.grid { border-collapse: separate; border-spacing: 0; font-size: 12px; min-width: 100%; }
table.grid th, table.grid td { padding: 5px 12px; border-bottom: 1px solid var(--line); border-right: 1px solid var(--line); white-space: pre; max-width: 480px; overflow: hidden; text-overflow: ellipsis; text-align: left; vertical-align: top; }
table.grid thead th { position: sticky; top: 36px; z-index: 5; background: var(--bg-2); font-weight: 600; color: var(--fg); border-bottom: 1px solid var(--line-2); }
table.grid td.rn, table.grid th.rn { position: sticky; left: 0; z-index: 4; background: var(--bg-2); color: var(--fg-3); text-align: right; font-family: var(--mono); font-size: 11px; min-width: 44px; }
table.grid thead th.rn { z-index: 6; }
table.grid tbody tr:nth-child(even) td { background: color-mix(in srgb, var(--bg-2) 55%, var(--bg)); }
table.grid tbody tr:nth-child(even) td.rn { background: var(--bg-2); }
table.grid tbody tr:hover td { background: var(--hover); }
table.grid td.num { text-align: right; font-family: var(--mono); font-size: 11.5px; }
table.grid td.null { color: var(--fg-3); font-style: italic; }
.ctype { display: block; font-weight: 400; font-size: 10px; color: var(--fg-3); font-family: var(--mono); }

/* ── 壓縮檔樹狀清單 ───────── */
.arc { font-size: 12.5px; padding-bottom: 20px; }
.arc .hdr, .arc .row { display: grid; grid-template-columns: minmax(260px, 1fr) 100px 100px 150px; align-items: center; }
.arc .hdr { position: sticky; top: 36px; z-index: 5; background: var(--bg-2); border-bottom: 1px solid var(--line-2); font-weight: 600; font-size: 11.5px; color: var(--fg-2); padding: 5px 0; }
.arc .hdr span, .arc .row span { padding: 0 12px; white-space: nowrap; overflow: hidden; text-overflow: ellipsis; }
.arc .row { padding: 3px 0; border-bottom: 1px solid color-mix(in srgb, var(--line) 50%, transparent); }
.arc .row:hover { background: var(--hover); }
.arc .row .nm { display: flex; align-items: center; gap: 7px; }
.arc .row img { width: 18px; height: 18px; flex: 0 0 18px; }
.arc .num { text-align: right; font-variant-numeric: tabular-nums; color: var(--fg-2); }
.arc .dt { color: var(--fg-2); font-variant-numeric: tabular-nums; }
.arc details > summary { list-style: none; cursor: default; }
.arc details > summary::-webkit-details-marker { display: none; }
.arc .tw { width: 10px; flex: 0 0 10px; height: 10px; display: inline-flex; align-items: center; justify-content: center; }
.arc summary .tw::before { content: ""; width: 5px; height: 5px; border-right: 1.5px solid var(--fg-3); border-bottom: 1.5px solid var(--fg-3); transform: rotate(-45deg); transition: transform .12s; }
.arc details[open] > summary .tw::before { transform: rotate(45deg); }
.arc .enc { color: var(--warn); font-size: 10px; }

/* ── Markdown ───────── */
.md { max-width: 880px; margin: 0 auto; padding: 28px 40px 60px; font-size: 14.5px; line-height: 1.7; word-wrap: break-word; }
.md > :first-child { margin-top: 0; }
.md h1, .md h2, .md h3, .md h4, .md h5, .md h6 { margin: 1.5em 0 .6em; line-height: 1.3; font-weight: 650; }
.md h1 { font-size: 2em; padding-bottom: .3em; border-bottom: 1px solid var(--line); }
.md h2 { font-size: 1.5em; padding-bottom: .3em; border-bottom: 1px solid var(--line); }
.md h3 { font-size: 1.25em; } .md h4 { font-size: 1.05em; } .md h5, .md h6 { font-size: .95em; color: var(--fg-2); }
.md p, .md ul, .md ol, .md blockquote, .md table, .md pre { margin: 0 0 1em; }
.md ul, .md ol { padding-left: 2em; }
.md li + li { margin-top: .25em; }
.md li > p { margin: 0; }
.md li:has(> p > input.task) { list-style: none; margin-left: -1.4em; }
.md input.task { margin: 0 .5em 0 0; vertical-align: middle; }
.md blockquote { padding: .2em 1em; color: var(--fg-2); border-left: .25em solid var(--line-2); }
.md blockquote > :last-child { margin-bottom: 0; }
.md code { font-family: var(--mono); font-size: 85%; padding: .15em .4em; border-radius: 6px; background: var(--bg-3); }
.md pre { padding: 14px 16px; border-radius: 10px; background: var(--bg-2); border: 1px solid var(--line); overflow-x: auto; font: 12.5px/1.6 var(--mono); }
.md pre code { padding: 0; background: none; font-size: inherit; }
.md table { border-collapse: collapse; display: block; overflow-x: auto; width: max-content; max-width: 100%; }
.md th, .md td { padding: 6px 13px; border: 1px solid var(--line-2); }
.md th { background: var(--bg-2); font-weight: 600; text-align: left; }
.md tr:nth-child(even) td { background: color-mix(in srgb, var(--bg-2) 60%, var(--bg)); }
.md hr { height: 2px; border: 0; background: var(--line); margin: 1.8em 0; }
.md img { max-width: 100%; border-radius: 6px; }
.md .img-missing { display: inline-block; padding: 2px 8px; border: 1px dashed var(--line-2); border-radius: 6px; color: var(--fg-2); font-size: 12px; }
.md .front { margin-bottom: 1.5em; }
.md .front table { font-size: 12.5px; }
.md del { color: var(--fg-2); }
.md kbd { font: 11px var(--mono); padding: 2px 5px; border: 1px solid var(--line-2); border-bottom-width: 2px; border-radius: 5px; background: var(--bg-2); }

/* ── Jupyter Notebook ───────── */
.nb { max-width: 980px; margin: 0 auto; padding: 18px 28px 60px; }
.nb .cell { display: grid; grid-template-columns: 64px 1fr; gap: 8px; margin-bottom: 14px; }
.nb .prompt { font: 11px/1.9 var(--mono); color: var(--fg-3); text-align: right; padding-top: 8px; }
.nb .prompt.in { color: var(--accent); }
.nb .cellsrc { border: 1px solid var(--line); border-radius: 8px; background: var(--bg-2); overflow-x: auto; }
.nb .cellsrc .code { padding: 6px 0; min-width: 0; }
.nb .out { padding: 6px 2px; overflow-x: auto; }
.nb .out pre { margin: 0; font: 12px/1.55 var(--mono); white-space: pre-wrap; }
.nb .out pre.err { color: var(--err); }
.nb .out img { max-width: 100%; background: #fff; border-radius: 4px; }
.nb .md { padding: 0 6px; max-width: none; margin: 0; }
.nb .out table { border-collapse: collapse; font-size: 12px; }
.nb .out th, .nb .out td { border: 1px solid var(--line-2); padding: 4px 9px; text-align: right; }

/* ── SQLite ───────── */
.toc { display: flex; flex-wrap: wrap; gap: 6px; padding: 12px 16px 4px; }
.toc a { padding: 3px 10px; border-radius: 999px; background: var(--bg-3); color: var(--fg); font-size: 12px; }
.toc a:hover { background: var(--accent-soft); text-decoration: none; }
.tbl-title { display: flex; align-items: baseline; gap: 10px; margin: 22px 16px 8px; scroll-margin-top: 44px; }
.tbl-title h2 { margin: 0; font-size: 15px; }
.tbl-title .sub { color: var(--fg-2); font-size: 12px; }
.schema { margin: 0 16px 10px; font: 11.5px/1.6 var(--mono); color: var(--fg-2); white-space: pre-wrap; }
.scroll-x { overflow-x: auto; margin: 0 16px; border: 1px solid var(--line); border-radius: 10px; }
.scroll-x table.grid thead th { top: 0; }

.empty { padding: 60px 20px; text-align: center; color: var(--fg-2); }
"""#
}
