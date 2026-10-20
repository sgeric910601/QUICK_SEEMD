import Foundation

/// 語法標記的種類，對應 QSStyle 裡的 CSS class。
enum QSTok: Int, CaseIterable {
    case k, s, c, n, t, f, a, p, y, g, lit, hd, da, dd, dh, dm, le, lw, li, ld, lt

    static let opens: [[UInt8]] = allCases.map { Array("<span class=\"\($0)\">".utf8) }
}

/// 把位元組寫成 HTML：跳脫特殊字元、處理換行（rows 模式每行包成一個 div）。
struct QSWriter {
    private(set) var out: [UInt8] = []
    let rows: Bool
    private(set) var newlines = 0

    private static let rowOpen = Array("<div class=\"l\">".utf8)
    private static let rowClose = Array("</div>".utf8)
    private static let close = Array("</span>".utf8)
    private static let amp = Array("&amp;".utf8)
    private static let lt = Array("&lt;".utf8)
    private static let gt = Array("&gt;".utf8)

    init(rows: Bool, capacity: Int) {
        self.rows = rows
        out.reserveCapacity(capacity + capacity / 2)
        if rows { out += Self.rowOpen }
    }

    mutating func emit(_ b: [UInt8], _ s: Int, _ e: Int, _ tok: QSTok?) {
        guard s < e else { return }
        if let t = tok { out += QSTok.opens[t.rawValue] }
        var i = s
        while i < e {
            let c = b[i]
            switch c {
            case 10:
                newlines += 1
                if rows {
                    if tok != nil { out += Self.close }
                    out += Self.rowClose
                    out += Self.rowOpen
                    if let t = tok { out += QSTok.opens[t.rawValue] }
                } else {
                    out.append(10)
                }
            case 38: out += Self.amp
            case 60: out += Self.lt
            case 62: out += Self.gt
            default: out.append(c)
            }
            i += 1
        }
        if tok != nil { out += Self.close }
    }

    /// 回傳 HTML 與行數（結尾換行不算新的一行）。
    mutating func finish(endsWithNewline: Bool) -> (String, Int) {
        var lines = newlines + 1
        if endsWithNewline && newlines > 0 {
            lines -= 1
            if rows, out.count >= Self.rowOpen.count, Array(out.suffix(Self.rowOpen.count)) == Self.rowOpen {
                out.removeLast(Self.rowOpen.count)
            } else if rows {
                out += Self.rowClose
            } else if out.last == 10 {
                out.removeLast()
            }
        } else if rows {
            out += Self.rowClose
        }
        return (String(decoding: out, as: UTF8.self), lines)
    }
}

enum QSHighlighter {
    /// 每行一列（可換行）的版面在 WebKit 裡較慢，超過這個行數就改用固定行號欄＋不換行。
    static let maxWrappedLines = 4000
    /// 單行超長（壓縮過的 JS/CSS 等）時不上色，直接以自動換行的純文字顯示。
    static let maxHighlightedLineLength = 8000

    /// 將文字依語言上色，產生程式碼檢視 HTML。
    static func codeView(_ text: String, language: QSLanguage, wrap: Bool? = nil, gutter: Bool = true) -> (html: String, lines: Int) {
        let bytes = Array(text.utf8)
        var newlines = 0, longest = 0, current = 0
        for c in bytes {
            if c == 10 { newlines += 1; longest = max(longest, current); current = 0 } else { current += 1 }
        }
        longest = max(longest, current)

        if longest > maxHighlightedLineLength && bytes.count > 64 * 1024 {
            var w = QSWriter(rows: false, capacity: bytes.count)
            w.emit(bytes, 0, bytes.count, nil)
            let (body, lines) = w.finish(endsWithNewline: bytes.last == 10)
            return ("<div class=\"code plainwrap\"><pre class=\"src\">\(body)</pre></div>", lines)
        }

        let rows = (wrap ?? language.wrap) && newlines <= maxWrappedLines
        var w = QSWriter(rows: rows, capacity: bytes.count)
        let lexer = QSLexer(b: bytes, lang: language)
        lexer.run(0, bytes.count, &w)
        var (body, lines) = w.finish(endsWithNewline: bytes.last == 10)
        if bytes.isEmpty { lines = 0 }
        let digits = max(2, String(lines).count)
        if rows {
            return ("<div class=\"code wrap\(gutter ? "" : " nogutter")\" style=\"--gw:\(digits)ch\">\(body)</div>", lines)
        }
        var gut = ""
        if gutter {
            gut.reserveCapacity(lines * (digits + 1))
            for n in 1...max(1, lines) { gut += n == 1 ? "1" : "\n\(n)" }
        }
        return ("<div class=\"code\(gutter ? "" : " nogutter")\"><div class=\"gut\">\(gut)</div><pre class=\"src\">\(body)</pre></div>", lines)
    }

    /// 只回傳上色後的內文（Markdown 程式碼區塊用）。
    static func inline(_ text: String, language: QSLanguage) -> String {
        let bytes = Array(text.utf8)
        var w = QSWriter(rows: false, capacity: bytes.count)
        QSLexer(b: bytes, lang: language).run(0, bytes.count, &w)
        return w.finish(endsWithNewline: bytes.last == 10).0
    }
}

/// 以位元組為單位的通用詞法分析器，依 QSLanguage 的設定上色。
struct QSLexer {
    let b: [UInt8]
    let lang: QSLanguage

    private static let typeDecl: Set<String> = ["class", "struct", "enum", "protocol", "interface", "trait", "extension", "union", "object", "record", "actor", "type", "typealias", "impl", "module", "namespace", "data", "newtype", "mixin", "message", "service", "contract", "library", "defmodule"]
    private static let fnDecl: Set<String> = ["func", "def", "function", "fn", "fun", "sub", "proc", "defn", "defp", "macro", "rpc", "method"]

    @inline(__always) static func isDigit(_ c: UInt8) -> Bool { c >= 48 && c <= 57 }
    @inline(__always) static func isAlpha(_ c: UInt8) -> Bool { let l = c | 0x20; return l >= 97 && l <= 122 }
    @inline(__always) static func isHex(_ c: UInt8) -> Bool { isDigit(c) || ((c | 0x20) >= 97 && (c | 0x20) <= 102) }
    @inline(__always) static func isSpace(_ c: UInt8) -> Bool { c == 32 || c == 9 || c == 13 || c == 10 }
    @inline(__always) func isIdentStart(_ c: UInt8) -> Bool { Self.isAlpha(c) || c == 95 || c >= 0x80 || lang.identStartExtra.contains(c) }
    @inline(__always) func isIdent(_ c: UInt8) -> Bool { Self.isAlpha(c) || Self.isDigit(c) || c == 95 || c >= 0x80 || lang.identExtra.contains(c) }

    func has(_ p: [UInt8], at i: Int, _ end: Int) -> Bool {
        if p.isEmpty || i + p.count > end { return false }
        var k = 0
        while k < p.count { if b[i + k] != p[k] { return false }; k += 1 }
        return true
    }

    func hasCI(_ p: [UInt8], at i: Int, _ end: Int) -> Bool {
        if i + p.count > end { return false }
        for k in 0..<p.count where (b[i + k] | 0x20) != (p[k] | 0x20) { return false }
        return true
    }

    func eol(_ i: Int, _ end: Int) -> Int {
        var j = i
        while j < end && b[j] != 10 { j += 1 }
        return j
    }

    func find(_ p: [UInt8], from i: Int, _ end: Int) -> Int? {
        guard let first = p.first else { return nil }
        var j = i
        while j + p.count <= end {
            if b[j] == first && has(p, at: j, end) { return j }
            j += 1
        }
        return nil
    }

    func nextNonSpace(_ i: Int, _ end: Int) -> Int {
        var j = i
        while j < end && (b[j] == 32 || b[j] == 9) { j += 1 }
        return j
    }

    func run(_ from: Int, _ end: Int, _ w: inout QSWriter) {
        switch lang.mode {
        case .plain: w.emit(b, from, end, nil)
        case .markup: runMarkup(from, end, &w)
        case .keyValue: runKeyValue(from, end, &w)
        case .diff: runDiff(from, end, &w)
        case .generic, .css, .log: runGeneric(from, end, &w)
        }
    }

    // MARK: 通用模式

    func runGeneric(_ from: Int, _ end: Int, _ w: inout QSWriter) {
        var i = from
        var plainStart = from
        var lineStart = true
        var pending: QSTok? = nil
        var depth = 0
        let css = lang.mode == .css
        let log = lang.mode == .log

        func tok(_ s: Int, _ e: Int, _ t: QSTok?) {
            if plainStart < s { w.emit(b, plainStart, s, nil) }
            w.emit(b, s, e, t)
            plainStart = e
        }

        while i < end {
            let c = b[i]
            if c == 10 { lineStart = true; i += 1; continue }
            if c == 32 || c == 9 || c == 13 { i += 1; continue }

            // 日誌：行首時間戳記
            if log && lineStart {
                var j = i
                let bracket = c == 91
                if bracket { j += 1 }
                let s = j
                while j < end && j - s < 40, Self.isDigit(b[j]) || b[j] == 45 || b[j] == 58 || b[j] == 46 || b[j] == 47 || b[j] == 44 || b[j] == 84 || b[j] == 90 || b[j] == 43 || (b[j] == 32 && j + 1 < end && Self.isDigit(b[j + 1])) {
                    j += 1
                }
                if j - s >= 8 && b[s..<j].filter({ Self.isDigit($0) }).count >= 6 {
                    if bracket && j < end && b[j] == 93 { j += 1 }
                    tok(i, j, .lt); i = j; lineStart = false; continue
                }
            }

            // 前置處理指令（#include、#if …）
            if lang.preprocessor && lineStart && c == 35 {
                var j = eol(i, end)
                while j > i && j < end && b[j - 1] == 92 { j = eol(j + 1, end) }
                tok(i, j, .p); i = j; lineStart = false; continue
            }

            // Rust 屬性 #[...]
            if lang.hashAttributes && c == 35 && i + 1 < end && (b[i + 1] == 91 || (b[i + 1] == 33 && i + 2 < end && b[i + 2] == 91)) {
                var j = i + 1, d = 0
                while j < end {
                    if b[j] == 91 { d += 1 } else if b[j] == 93 { d -= 1; if d == 0 { j += 1; break } } else if b[j] == 10 { break }
                    j += 1
                }
                tok(i, j, .a); i = j; lineStart = false; continue
            }

            var matched = false
            for (o, cl) in lang.blockComments where has(o, at: i, end) {
                let j = find(cl, from: i + o.count, end).map { $0 + cl.count } ?? end
                tok(i, j, .c); i = j; matched = true; break
            }
            if matched { lineStart = false; continue }

            for lc in lang.lineComments where (lang.caseInsensitive ? hasCI(lc, at: i, end) : has(lc, at: i, end)) {
                if lc == [35] && lang.hashCommentNeedsSpace && i > from && !Self.isSpace(b[i - 1]) && b[i - 1] != 59 { continue }
                let j = eol(i, end)
                tok(i, j, .c); i = j; matched = true; break
            }
            if matched { continue }

            for r in lang.strings where has(r.open, at: i, end) {
                var k = i + r.open.count
                var closed = false
                while k < end {
                    let ch = b[k]
                    if let e = r.escape, ch == e { k += 2; continue }
                    if ch == r.close[0] && has(r.close, at: k, end) { k += r.close.count; closed = true; break }
                    if ch == 10 && !r.multiline { break }
                    k += 1
                }
                k = min(k, end)
                if r.maxLength > 0 && (!closed || k - i > r.maxLength) { continue }
                var t = QSTok.s
                if !lang.keySeparators.isEmpty {
                    let n = nextNonSpace(k, end)
                    if n < end && lang.keySeparators.contains(b[n]) { t = .y }
                }
                tok(i, k, t); i = k; matched = true; break
            }
            if matched { lineStart = false; pending = nil; continue }

            // 變數符號：$var ${var} $(…) %var
            if lang.sigils.contains(c) && i + 1 < end {
                let n = b[i + 1]
                var j = i + 1
                if n == 123 {
                    while j < end && b[j] != 125 && b[j] != 10 { j += 1 }
                    if j < end && b[j] == 125 { j += 1 }
                } else if isIdentStart(n) || Self.isDigit(n) {
                    j = i + 1
                    while j < end && isIdent(b[j]) { j += 1 }
                    if c == 37 && j < end && b[j] == 37 { j += 1 }   // %VAR%
                } else if c == 36 && (n == 64 || n == 63 || n == 35 || n == 36 || n == 33 || n == 42 || n == 45 || n == 40) {
                    j = i + 2
                }
                if j > i + 1 {
                    tok(i, j, lang.sigilTok); i = j; lineStart = false; continue
                }
            }

            // @decorator / Objective-C @keyword
            if c == 64 && (lang.decorators || lang.atKeywords) && i + 1 < end && isIdentStart(b[i + 1]) {
                var j = i + 1
                while j < end && (isIdent(b[j]) || b[j] == 46) { j += 1 }
                tok(i, j, lang.atKeywords ? .k : .a); i = j; lineStart = false; continue
            }

            // CSS：選擇器、色碼、!important
            if css {
                if c == 123 { depth += 1 } else if c == 125 { depth = max(0, depth - 1) }
                if c == 35 && i + 1 < end && Self.isHex(b[i + 1]) && depth > 0 {
                    var j = i + 1
                    while j < end && Self.isHex(b[j]) { j += 1 }
                    tok(i, j, .n); i = j; continue
                }
                if (c == 46 || c == 35) && depth == 0 && i + 1 < end && isIdentStart(b[i + 1]) {
                    var j = i + 1
                    while j < end && isIdent(b[j]) { j += 1 }
                    tok(i, j, c == 46 ? .t : .a); i = j; continue
                }
                if c == 33 && hasCI(Array("!important".utf8), at: i, end) {
                    tok(i, i + 10, .k); i += 10; continue
                }
            }

            // 組合語言 .directive
            if lang.dotDirectives && c == 46 && i + 1 < end && Self.isAlpha(b[i + 1]) && (i == from || !isIdent(b[i - 1])) {
                var j = i + 1
                while j < end && isIdent(b[j]) { j += 1 }
                tok(i, j, .p); i = j; lineStart = false; continue
            }

            // Ruby / Elixir :symbol
            if lang.symbols && c == 58 && i + 1 < end && isIdentStart(b[i + 1]) && (i == from || (b[i - 1] != 58 && !isIdent(b[i - 1]))) {
                var j = i + 1
                while j < end && isIdent(b[j]) { j += 1 }
                tok(i, j, .lit); i = j; lineStart = false; continue
            }

            // 數字
            if Self.isDigit(c) || (c == 46 && i + 1 < end && Self.isDigit(b[i + 1]) && (i == from || !isIdent(b[i - 1]))) {
                var j = i
                if c == 48 && i + 1 < end && (b[i + 1] | 0x20) == 120 {
                    j = i + 2
                    while j < end && (Self.isHex(b[j]) || b[j] == 95) { j += 1 }
                } else {
                    while j < end {
                        let d = b[j]
                        if Self.isDigit(d) || d == 95 { j += 1 }
                        else if d == 46 && j + 1 < end && Self.isDigit(b[j + 1]) { j += 1 }
                        else if (d | 0x20) == 101 && j + 1 < end && (Self.isDigit(b[j + 1]) || ((b[j + 1] == 43 || b[j + 1] == 45) && j + 2 < end && Self.isDigit(b[j + 2]))) { j += 2 }
                        else { break }
                    }
                }
                while j < end && (Self.isAlpha(b[j]) || (j > i && Self.isDigit(b[j]))) { j += 1 }
                if css && j < end && b[j] == 37 { j += 1 }
                tok(i, j, .n); i = j; lineStart = false; pending = nil; continue
            }

            // 識別字 / 關鍵字
            if isIdentStart(c) {
                var j = i + 1
                while j < end && isIdent(b[j]) { j += 1 }
                let word = String(decoding: b[i..<j], as: UTF8.self)
                let key = lang.caseInsensitive ? word.lowercased() : word
                var t: QSTok? = nil
                let n = j < end ? b[j] : 0
                if let wt = lang.wordToks[key] {
                    t = wt
                } else if lang.keywords.contains(key) {
                    t = .k
                    if Self.typeDecl.contains(key) { pending = .t } else if Self.fnDecl.contains(key) { pending = .f }
                } else if let p = pending {
                    t = p; pending = nil
                } else if lang.literals.contains(key) {
                    t = .lit
                } else if lang.types.contains(key) {
                    t = .t
                } else if lang.macroBang && n == 33 && !(j + 1 < end && b[j + 1] == 61) {
                    t = .p; j += 1
                } else if css {
                    let nn = nextNonSpace(j, end)
                    if depth > 0 && nn < end && b[nn] == 58 { t = .y }
                    else if depth == 0 { t = .g }
                    else if n == 40 { t = .f }
                } else if n == 40 {
                    t = .f
                } else if lang.lineLabels && lineStart && n == 58 && !(j + 1 < end && (b[j + 1] == 61 || b[j + 1] == 58)) {
                    t = .f
                } else if lang.builtins.contains(key) {
                    t = .f
                } else if lang.capitalizedTypes && c >= 65 && c <= 90 {
                    t = .t
                }
                tok(i, j, t); i = j; lineStart = false; continue
            }

            if c != 42 && c != 38 { pending = nil }   // `func *`、`&` 之類不清除
            lineStart = false
            i += 1
        }
        if plainStart < end { w.emit(b, plainStart, end, nil) }
    }

    // MARK: 標記語言（HTML / XML / plist / SVG / Vue）

    func runMarkup(_ from: Int, _ end: Int, _ w: inout QSWriter) {
        var i = from
        var plainStart = from
        func tok(_ s: Int, _ e: Int, _ t: QSTok?) {
            if plainStart < s { w.emit(b, plainStart, s, nil) }
            w.emit(b, s, e, t)
            plainStart = e
        }
        func scanTo(_ p: String, from s: Int) -> Int {
            let pat = Array(p.utf8)
            return find(pat, from: s, end).map { $0 + pat.count } ?? end
        }

        while i < end {
            let c = b[i]
            if c == 60 {
                if has(Array("<!--".utf8), at: i, end) { let j = scanTo("-->", from: i + 4); tok(i, j, .c); i = j; continue }
                if has(Array("<![CDATA[".utf8), at: i, end) { let j = scanTo("]]>", from: i + 9); tok(i, j, .s); i = j; continue }
                if i + 1 < end && (b[i + 1] == 63 || b[i + 1] == 33) {
                    let j = b[i + 1] == 63 ? scanTo("?>", from: i + 2) : scanTo(">", from: i + 2)
                    tok(i, j, .p); i = j; continue
                }
                if i + 1 < end && (Self.isAlpha(b[i + 1]) || b[i + 1] == 47) {
                    var j = i + 1
                    let closing = b[j] == 47
                    if closing { j += 1 }
                    tok(i, j, nil)
                    let ns = j
                    while j < end && (isIdent(b[j]) || b[j] == 45 || b[j] == 58 || b[j] == 46) { j += 1 }
                    tok(ns, j, .g)
                    let tagName = String(decoding: b[ns..<j], as: UTF8.self).lowercased()
                    var selfClosing = false
                    var afterEq = false
                    while j < end && b[j] != 62 {
                        let d = b[j]
                        if d == 34 || d == 39 {
                            var k = j + 1
                            while k < end && b[k] != d { k += 1 }
                            k = min(k + 1, end)
                            tok(j, k, .s); j = k; afterEq = false; continue
                        }
                        if d == 47 && j + 1 < end && b[j + 1] == 62 { selfClosing = true; j += 1; continue }
                        if d == 61 { afterEq = true; j += 1; continue }
                        if Self.isSpace(d) || d == 60 { if d == 60 { break }; j += 1; continue }
                        let s = j
                        while j < end && !Self.isSpace(b[j]) && b[j] != 61 && b[j] != 62 && b[j] != 34 && b[j] != 39 && !(b[j] == 47 && j + 1 < end && b[j + 1] == 62) { j += 1 }
                        if j == s { j += 1; continue }
                        tok(s, j, afterEq ? .s : .a)
                        afterEq = false
                    }
                    if j < end && b[j] == 62 { j += 1 }
                    i = j
                    if !closing && !selfClosing, let embedID = lang.embedded[tagName], let sub = QSLanguages.byID[embedID] {
                        let closeTag = Array("</\(tagName)".utf8)
                        var k = i
                        while k < end && !hasCI(closeTag, at: k, end) { k += 1 }
                        if plainStart < i { w.emit(b, plainStart, i, nil) }
                        QSLexer(b: b, lang: sub).run(i, k, &w)
                        plainStart = k
                        i = k
                    }
                    continue
                }
            }
            if c == 38 {
                var j = i + 1
                while j < end && j - i < 12 && (Self.isAlpha(b[j]) || Self.isDigit(b[j]) || b[j] == 35) { j += 1 }
                if j < end && b[j] == 59 && j > i + 1 { tok(i, j + 1, .lit); i = j + 1; continue }
            }
            i += 1
        }
        if plainStart < end { w.emit(b, plainStart, end, nil) }
    }

    // MARK: 鍵值設定檔（YAML / TOML / INI / properties / .env）

    func runKeyValue(_ from: Int, _ end: Int, _ w: inout QSWriter) {
        let value = QSLanguages.valueLanguage(for: lang.id)
        let yaml = lang.id == "yaml"
        let separators: [UInt8] = lang.keySeparators.isEmpty ? [61] : lang.keySeparators
        var i = from
        while i < end {
            let le = eol(i, end)
            var s = i
            while s < le && (b[s] == 32 || b[s] == 9) { s += 1 }
            w.emit(b, i, s, nil)
            if s < le {
                let c = b[s]
                if lang.lineComments.contains(where: { has($0, at: s, le) }) {
                    w.emit(b, s, le, .c)
                } else if yaml && (has(Array("---".utf8), at: s, le) || has(Array("...".utf8), at: s, le)) && s == i {
                    w.emit(b, s, le, .k)
                } else if !yaml && c == 91 {
                    var j = s
                    while j < le && b[j] != 93 { j += 1 }
                    j = min(j + 1, le)
                    if j < le && b[j] == 93 { j += 1 }
                    w.emit(b, s, j, .hd)
                    QSLexer(b: b, lang: value).run(j, le, &w)
                } else {
                    var ks = s
                    if yaml && c == 45 && s + 1 < le && b[s + 1] == 32 { w.emit(b, s, s + 2, .p); ks = s + 2 }
                    if lang.id == "dotenv" && has(Array("export ".utf8), at: ks, le) { w.emit(b, ks, ks + 6, .k); ks += 6 }
                    // 找分隔符號（略過引號內）
                    var j = ks
                    var quote: UInt8 = 0
                    var sep = -1
                    while j < le {
                        let d = b[j]
                        if quote != 0 { if d == quote { quote = 0 } }
                        else if d == 34 || d == 39 { if j == ks { quote = d } }
                        else if separators.contains(d) {
                            if yaml { if j + 1 >= le || b[j + 1] == 32 || b[j + 1] == 9 { sep = j; break } }
                            else { sep = j; break }
                        } else if d == 35 && yaml && j > ks && b[j - 1] == 32 { break }
                        j += 1
                    }
                    if sep > ks {
                        w.emit(b, ks, sep, .y)
                        w.emit(b, sep, sep + 1, nil)
                        QSLexer(b: b, lang: value).run(sep + 1, le, &w)
                    } else {
                        QSLexer(b: b, lang: value).run(ks, le, &w)
                    }
                }
            }
            if le < end { w.emit(b, le, le + 1, nil) }
            i = le + 1
        }
    }

    // MARK: diff / patch

    func runDiff(_ from: Int, _ end: Int, _ w: inout QSWriter) {
        let meta = ["diff ", "index ", "--- ", "+++ ", "new file", "deleted file", "similarity", "rename ", "old mode", "new mode", "Binary files"].map { Array($0.utf8) }
        var i = from
        while i < end {
            let le = eol(i, end)
            var t: QSTok? = nil
            if meta.contains(where: { has($0, at: i, le) }) { t = .dm }
            else if has([64, 64], at: i, le) { t = .dh }
            else if i < le && b[i] == 43 { t = .da }
            else if i < le && b[i] == 45 { t = .dd }
            w.emit(b, i, le, t)
            if le < end { w.emit(b, le, le + 1, nil) }
            i = le + 1
        }
    }
}
