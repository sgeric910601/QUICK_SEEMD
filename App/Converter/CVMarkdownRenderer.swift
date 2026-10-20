import Foundation

/// 輕量 Markdown → HTML 轉換器（CommonMark／GFM 常用子集）：
/// 標題、段落、強調、刪除線、行內程式碼、程式碼區塊、引言、巢狀清單、待辦清單、表格、分隔線、連結、圖片、自動連結。
struct CVMarkdownRenderer {
    // MARK: - 公開介面

    /// 只產生 <body> 內容。
    static func renderBody(_ markdown: String) -> String {
        let normalized = markdown.replacingOccurrences(of: "\r\n", with: "\n").replacingOccurrences(of: "\r", with: "\n")
        var lines = normalized.components(separatedBy: "\n")
        // 去除 YAML front matter
        if lines.first?.trimmingCharacters(in: .whitespaces) == "---",
           let end = lines.dropFirst().firstIndex(where: { $0.trimmingCharacters(in: .whitespaces) == "---" || $0.trimmingCharacters(in: .whitespaces) == "..." }) {
            lines.removeSubrange(0...end)
        }
        var renderer = CVMarkdownRenderer()
        renderer.collectReferences(&lines)
        return renderer.renderBlocks(lines)
    }

    /// 完整 HTML 文件。`forImport` 為 true 時使用適合匯入 Word／PDF 的樣式（不含深色模式）。
    static func renderDocument(_ markdown: String, title: String, forImport: Bool) -> String {
        let body = renderBody(markdown)
        return """
        <!DOCTYPE html>
        <html lang="zh-Hant">
        <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>\(escapeHTML(title))</title>
        <style>
        \(forImport ? importCSS : screenCSS)
        </style>
        </head>
        <body>
        <article class="markdown-body">
        \(body)
        </article>
        </body>
        </html>
        """
    }

    static let screenCSS = """
    :root { color-scheme: light dark; }
    body { margin: 0; background: #fff; color: #1f2328; font: 16px/1.65 -apple-system, BlinkMacSystemFont, "PingFang TC", "Helvetica Neue", "Microsoft JhengHei", sans-serif; }
    .markdown-body { max-width: 860px; margin: 0 auto; padding: 40px 32px 80px; }
    h1, h2, h3, h4, h5, h6 { line-height: 1.3; margin: 1.6em 0 0.6em; font-weight: 650; }
    h1 { font-size: 2em; border-bottom: 1px solid #d1d9e0; padding-bottom: .3em; }
    h2 { font-size: 1.5em; border-bottom: 1px solid #d1d9e0; padding-bottom: .3em; }
    h3 { font-size: 1.25em; }
    p, ul, ol, blockquote, pre, table { margin: 0 0 1em; }
    a { color: #0969da; text-decoration: none; } a:hover { text-decoration: underline; }
    code { font: 0.875em/1.5 "SF Mono", Menlo, Consolas, monospace; background: rgba(129,139,152,.15); padding: .15em .4em; border-radius: 6px; }
    pre { background: #f6f8fa; padding: 14px 16px; border-radius: 10px; overflow: auto; }
    pre code { background: none; padding: 0; font-size: 0.85em; }
    blockquote { color: #59636e; border-left: 4px solid #d1d9e0; padding: 0 1em; margin-left: 0; }
    table { border-collapse: collapse; display: block; overflow: auto; }
    th, td { border: 1px solid #d1d9e0; padding: 6px 13px; }
    th { background: #f6f8fa; font-weight: 600; }
    tr:nth-child(2n) td { background: #f6f8fa80; }
    hr { border: 0; height: 2px; background: #d1d9e0; margin: 2em 0; }
    img { max-width: 100%; }
    li > ul, li > ol { margin: .25em 0 0; }
    li + li { margin-top: .2em; }
    li.task { list-style: none; margin-left: -1.2em; }
    @media (prefers-color-scheme: dark) {
      body { background: #0d1117; color: #e6edf3; }
      h1, h2, th, td, blockquote { border-color: #3d444d; }
      hr { background: #3d444d; }
      a { color: #4493f8; }
      pre, th { background: #151b23; }
      tr:nth-child(2n) td { background: #151b2380; }
      blockquote { color: #9198a1; }
    }
    """

    static let importCSS = """
    body { font-family: "Helvetica Neue", "PingFang TC", sans-serif; font-size: 11pt; line-height: 1.5; color: #1f2328; }
    h1 { font-size: 22pt; margin: 14pt 0 8pt; } h2 { font-size: 17pt; margin: 12pt 0 6pt; } h3 { font-size: 14pt; margin: 10pt 0 4pt; }
    h4, h5, h6 { font-size: 12pt; margin: 8pt 0 4pt; }
    p { margin: 0 0 8pt; }
    li > ul, li > ol { margin: 0; }
    code { font-family: Menlo, monospace; font-size: 9.5pt; color: #b3261e; }
    pre { font-family: Menlo, monospace; font-size: 9.5pt; background-color: #f3f4f6; padding: 8pt; margin: 0 0 8pt; }
    pre code { color: #1f2328; }
    blockquote { color: #555555; margin: 0 0 8pt 12pt; padding-left: 8pt; border-left: 3px solid #cccccc; }
    table { border-collapse: collapse; margin: 0 0 8pt; }
    th, td { border: 1px solid #bbbbbb; padding: 4pt 8pt; }
    th { background-color: #eeeeee; }
    a { color: #0969da; }
    """

    // MARK: - 實作

    private var references: [String: (url: String, title: String?)] = [:]

    private mutating func collectReferences(_ lines: inout [String]) {
        let pattern = try? NSRegularExpression(pattern: #"^ {0,3}\[([^\]]+)\]:\s*<?(\S+?)>?(?:\s+["'(](.+)["')])?\s*$"#)
        var kept: [String] = []
        var inFence = false
        for line in lines {
            if line.trimmingCharacters(in: .whitespaces).hasPrefix("```") || line.trimmingCharacters(in: .whitespaces).hasPrefix("~~~") { inFence.toggle() }
            if !inFence, let pattern, let m = pattern.firstMatch(in: line, range: NSRange(line.startIndex..., in: line)),
               let k = Range(m.range(at: 1), in: line), let u = Range(m.range(at: 2), in: line) {
                let title = Range(m.range(at: 3), in: line).map { String(line[$0]) }
                references[String(line[k]).lowercased()] = (String(line[u]), title)
                continue
            }
            kept.append(line)
        }
        lines = kept
    }

    private static func indentWidth(_ line: Substring) -> Int {
        var w = 0
        for c in line {
            if c == " " { w += 1 } else if c == "\t" { w += 4 - (w % 4) } else { break }
        }
        return w
    }

    private static func stripIndent(_ line: String, _ count: Int) -> String {
        var removed = 0
        var idx = line.startIndex
        while idx < line.endIndex, removed < count {
            let c = line[idx]
            if c == " " { removed += 1 } else if c == "\t" { removed += 4 - (removed % 4) } else { break }
            idx = line.index(after: idx)
        }
        return String(line[idx...])
    }

    private static func isBlank(_ s: String) -> Bool { s.trimmingCharacters(in: .whitespaces).isEmpty }

    private static func isHorizontalRule(_ line: String) -> Bool {
        let t = line.trimmingCharacters(in: .whitespaces)
        guard let first = t.first, "-*_".contains(first) else { return false }
        let chars = t.filter { $0 != " " }
        return chars.count >= 3 && chars.allSatisfy { $0 == first }
    }

    private struct ListMarker {
        let ordered: Bool
        let start: Int
        let contentIndent: Int
        let content: String
    }

    private static func listMarker(_ line: String) -> ListMarker? {
        let indent = indentWidth(Substring(line))
        guard indent < 4 + 100 else { return nil }
        let t = line.drop(while: { $0 == " " || $0 == "\t" })
        if let c = t.first, "-*+".contains(c) {
            let rest = t.dropFirst()
            if rest.isEmpty { return ListMarker(ordered: false, start: 1, contentIndent: indent + 2, content: "") }
            guard rest.first == " " || rest.first == "\t" else { return nil }
            let spaces = rest.prefix(while: { $0 == " " }).count
            let content = String(rest.dropFirst(min(max(spaces, 1), 4)))
            return ListMarker(ordered: false, start: 1, contentIndent: indent + 1 + min(max(spaces, 1), 4), content: content)
        }
        let digits = t.prefix(while: { $0.isASCII && $0.isNumber })
        if !digits.isEmpty, digits.count <= 9 {
            let afterDigits = t.dropFirst(digits.count)
            if let d = afterDigits.first, d == "." || d == ")" {
                let rest = afterDigits.dropFirst()
                if rest.isEmpty { return ListMarker(ordered: true, start: Int(digits) ?? 1, contentIndent: indent + digits.count + 2, content: "") }
                guard rest.first == " " else { return nil }
                let spaces = rest.prefix(while: { $0 == " " }).count
                let content = String(rest.dropFirst(min(spaces, 4)))
                return ListMarker(ordered: true, start: Int(digits) ?? 1, contentIndent: indent + digits.count + 1 + min(spaces, 4), content: content)
            }
        }
        return nil
    }

    private static func headingLevel(_ line: String) -> (Int, String)? {
        let t = line.drop(while: { $0 == " " })
        guard indentWidth(Substring(line)) < 4, t.hasPrefix("#") else { return nil }
        let hashes = t.prefix(while: { $0 == "#" }).count
        guard hashes <= 6 else { return nil }
        let rest = t.dropFirst(hashes)
        guard rest.isEmpty || rest.first == " " || rest.first == "\t" else { return nil }
        var text = rest.trimmingCharacters(in: .whitespaces)
        // 移除結尾的 #
        if let r = text.range(of: #"\s+#+\s*$"#, options: .regularExpression) { text.removeSubrange(r) }
        else if text.allSatisfy({ $0 == "#" }) { text = "" }
        return (hashes, text)
    }

    private static func fenceInfo(_ line: String) -> (marker: String, info: String)? {
        guard indentWidth(Substring(line)) < 4 else { return nil }
        let t = line.trimmingCharacters(in: .whitespaces)
        for ch in ["`", "~"] where t.hasPrefix(ch + ch + ch) {
            let count = t.prefix(while: { String($0) == ch }).count
            let info = String(t.dropFirst(count)).trimmingCharacters(in: .whitespaces)
            if ch == "`" && info.contains("`") { return nil }
            return (String(repeating: ch, count: count), info)
        }
        return nil
    }

    private static func isTableSeparator(_ line: String) -> Bool {
        let t = line.trimmingCharacters(in: .whitespaces)
        guard t.contains("-"), t.contains("|") || t.hasPrefix(":") || t.hasPrefix("-") else { return false }
        let cells = splitTableRow(t)
        guard !cells.isEmpty else { return false }
        return cells.allSatisfy { cell in
            let c = cell.trimmingCharacters(in: .whitespaces)
            return !c.isEmpty && c.allSatisfy { $0 == "-" || $0 == ":" } && c.contains("-")
        }
    }

    private static func splitTableRow(_ line: String) -> [String] {
        var t = line.trimmingCharacters(in: .whitespaces)
        if t.hasPrefix("|") { t.removeFirst() }
        if t.hasSuffix("|") && !t.hasSuffix("\\|") { t.removeLast() }
        var cells: [String] = []
        var current = ""
        var inCode = false
        var escaped = false
        for c in t {
            if escaped { current.append(c == "|" ? "|" : "\\\(c)"); escaped = false; continue }
            if c == "\\" { escaped = true; continue }
            if c == "`" { inCode.toggle() }
            if c == "|" && !inCode { cells.append(current); current = ""; continue }
            current.append(c)
        }
        if escaped { current.append("\\") }
        cells.append(current)
        return cells.map { $0.trimmingCharacters(in: .whitespaces) }
    }

    /// 是否開始一個新的區塊（用來結束段落）。
    private static func startsBlock(_ line: String) -> Bool {
        if headingLevel(line) != nil || fenceInfo(line) != nil || isHorizontalRule(line) { return true }
        let t = line.trimmingCharacters(in: .whitespaces)
        if t.hasPrefix(">") { return true }
        if let m = listMarker(line), !m.content.isEmpty, indentWidth(Substring(line)) < 4 {
            return !m.ordered || m.start == 1
        }
        if t.hasPrefix("<") && Self.htmlBlockStart(t) { return true }
        return false
    }

    private static func htmlBlockStart(_ t: String) -> Bool {
        let tags = ["div", "table", "pre", "p", "ul", "ol", "dl", "section", "article", "header", "footer", "nav", "details",
                    "summary", "figure", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6", "hr", "center", "iframe", "video", "audio", "!--"]
        let lower = t.lowercased()
        return tags.contains { lower.hasPrefix("<" + $0) || lower.hasPrefix("</" + $0) }
    }

    private func renderBlocks(_ lines: [String]) -> String {
        var html: [String] = []
        var i = 0
        while i < lines.count {
            let line = lines[i]
            if Self.isBlank(line) { i += 1; continue }

            // 程式碼區塊（```）
            if let fence = Self.fenceInfo(line) {
                let indent = Self.indentWidth(Substring(line))
                var body: [String] = []
                i += 1
                while i < lines.count {
                    let l = lines[i]
                    let t = l.trimmingCharacters(in: .whitespaces)
                    if t.hasPrefix(fence.marker), t.allSatisfy({ String($0) == String(fence.marker.first!) }) { i += 1; break }
                    body.append(Self.stripIndent(l, indent))
                    i += 1
                }
                let lang = fence.info.split(separator: " ").first.map(String.init) ?? ""
                let cls = lang.isEmpty ? "" : " class=\"language-\(Self.escapeHTML(lang))\""
                html.append("<pre><code\(cls)>\(Self.escapeHTML(body.joined(separator: "\n")))\n</code></pre>")
                continue
            }

            // 縮排程式碼
            if Self.indentWidth(Substring(line)) >= 4 {
                var body: [String] = []
                while i < lines.count, Self.indentWidth(Substring(lines[i])) >= 4 || Self.isBlank(lines[i]) {
                    body.append(Self.stripIndent(lines[i], 4))
                    i += 1
                }
                while let last = body.last, Self.isBlank(last) { body.removeLast() }
                html.append("<pre><code>\(Self.escapeHTML(body.joined(separator: "\n")))\n</code></pre>")
                continue
            }

            // 標題
            if let (level, text) = Self.headingLevel(line) {
                html.append("<h\(level) id=\"\(Self.slug(text))\">\(renderInline(text))</h\(level)>")
                i += 1
                continue
            }

            // 分隔線
            if Self.isHorizontalRule(line) {
                html.append("<hr>")
                i += 1
                continue
            }

            // 引言
            if line.trimmingCharacters(in: .whitespaces).hasPrefix(">") {
                var inner: [String] = []
                while i < lines.count {
                    let t = lines[i].drop(while: { $0 == " " })
                    if t.hasPrefix(">") {
                        var rest = t.dropFirst()
                        if rest.first == " " { rest = rest.dropFirst() }
                        inner.append(String(rest))
                    } else if !Self.isBlank(lines[i]) && !inner.isEmpty && !Self.isBlank(inner.last ?? "") && !Self.startsBlock(lines[i]) {
                        inner.append(lines[i]) // 延續段落
                    } else {
                        break
                    }
                    i += 1
                }
                html.append("<blockquote>\n\(renderBlocks(inner))\n</blockquote>")
                continue
            }

            // 清單
            if let first = Self.listMarker(line) {
                let (listHTML, next) = renderList(lines, from: i, first: first)
                html.append(listHTML)
                i = next
                continue
            }

            // HTML 區塊直接保留
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            if trimmed.hasPrefix("<"), Self.htmlBlockStart(trimmed) {
                var block: [String] = []
                while i < lines.count, !Self.isBlank(lines[i]) {
                    block.append(lines[i])
                    i += 1
                }
                html.append(block.joined(separator: "\n"))
                continue
            }

            // 表格
            if line.contains("|"), i + 1 < lines.count, Self.isTableSeparator(lines[i + 1]) {
                let header = Self.splitTableRow(line)
                let aligns = Self.splitTableRow(lines[i + 1]).map { cell -> String in
                    let c = cell.trimmingCharacters(in: .whitespaces)
                    if c.hasPrefix(":") && c.hasSuffix(":") { return " style=\"text-align:center\"" }
                    if c.hasSuffix(":") { return " style=\"text-align:right\"" }
                    if c.hasPrefix(":") { return " style=\"text-align:left\"" }
                    return ""
                }
                var rows: [[String]] = []
                i += 2
                while i < lines.count, !Self.isBlank(lines[i]), lines[i].contains("|") || !Self.startsBlock(lines[i]) {
                    if Self.startsBlock(lines[i]) { break }
                    rows.append(Self.splitTableRow(lines[i]))
                    i += 1
                }
                var t = "<table>\n<thead>\n<tr>"
                for (n, cell) in header.enumerated() {
                    t += "<th\(n < aligns.count ? aligns[n] : "")>\(renderInline(cell))</th>"
                }
                t += "</tr>\n</thead>\n<tbody>\n"
                for row in rows {
                    t += "<tr>"
                    for n in 0..<header.count {
                        let cell = n < row.count ? row[n] : ""
                        t += "<td\(n < aligns.count ? aligns[n] : "")>\(renderInline(cell))</td>"
                    }
                    t += "</tr>\n"
                }
                t += "</tbody>\n</table>"
                html.append(t)
                continue
            }

            // 段落（含 Setext 標題）
            var para: [String] = [line]
            i += 1
            var setextLevel: Int?
            while i < lines.count {
                let l = lines[i]
                if Self.isBlank(l) { break }
                let t = l.trimmingCharacters(in: .whitespaces)
                if !t.isEmpty, t.allSatisfy({ $0 == "=" }) { setextLevel = 1; i += 1; break }
                if !t.isEmpty, t.allSatisfy({ $0 == "-" }), t.count >= 2 { setextLevel = 2; i += 1; break }
                if Self.startsBlock(l) { break }
                if l.contains("|"), i + 1 < lines.count, Self.isTableSeparator(lines[i + 1]) { break }
                para.append(l)
                i += 1
            }
            if let level = setextLevel {
                let text = para.map { $0.trimmingCharacters(in: .whitespaces) }.joined(separator: " ")
                html.append("<h\(level) id=\"\(Self.slug(text))\">\(renderInline(text))</h\(level)>")
            } else {
                html.append("<p>\(renderParagraph(para))</p>")
            }
        }
        return html.joined(separator: "\n")
    }

    private func renderParagraph(_ lines: [String]) -> String {
        var out: [String] = []
        for (n, raw) in lines.enumerated() {
            var line = raw.drop(while: { $0 == " " || $0 == "\t" })
            var hardBreak = false
            if n < lines.count - 1 {
                if line.hasSuffix("  ") { hardBreak = true }
                else if line.hasSuffix("\\") { hardBreak = true; line = line.dropLast() }
            }
            let text = String(line).trimmingCharacters(in: .whitespaces)
            out.append(renderInline(text) + (hardBreak ? "<br>" : ""))
        }
        return out.joined(separator: "\n")
    }

    private func renderList(_ lines: [String], from start: Int, first: ListMarker) -> (String, Int) {
        let ordered = first.ordered
        let baseIndent = Self.indentWidth(Substring(lines[start]))
        var items: [[String]] = []
        var looseFlags: [Bool] = []
        var i = start
        var sawBlankBetween = false
        while i < lines.count {
            let line = lines[i]
            guard let marker = Self.listMarker(line), marker.ordered == ordered,
                  Self.indentWidth(Substring(line)) >= baseIndent, Self.indentWidth(Substring(line)) < baseIndent + 4 else { break }
            var item: [String] = [marker.content]
            let contentIndent = marker.contentIndent
            i += 1
            var blankPending = false
            var loose = false
            while i < lines.count {
                let l = lines[i]
                if Self.isBlank(l) { blankPending = true; item.append(""); i += 1; continue }
                let indent = Self.indentWidth(Substring(l))
                if indent >= contentIndent || (indent > baseIndent && Self.listMarker(l) != nil) {
                    if blankPending, Self.listMarker(Self.stripIndent(l, min(indent, contentIndent))) == nil { loose = true }
                    blankPending = false
                    item.append(Self.stripIndent(l, min(indent, contentIndent)))
                    i += 1
                } else if !blankPending, Self.listMarker(l) == nil, !Self.startsBlock(l) {
                    item.append(l.trimmingCharacters(in: .whitespaces)) // 延續行
                    i += 1
                } else {
                    break
                }
            }
            while let last = item.last, Self.isBlank(last) { item.removeLast() }
            if blankPending, i < lines.count, let next = Self.listMarker(lines[i]), next.ordered == ordered { sawBlankBetween = true }
            items.append(item)
            looseFlags.append(loose)
        }
        let isLoose = sawBlankBetween || looseFlags.contains(true)
        var html = ordered ? (first.start != 1 ? "<ol start=\"\(first.start)\">" : "<ol>") : "<ul>"
        for item in items {
            var content = item
            var taskPrefix = ""
            var liClass = ""
            if let firstLine = content.first {
                let lower = firstLine.lowercased()
                if lower.hasPrefix("[ ] ") || lower.hasPrefix("[x] ") || lower == "[ ]" || lower == "[x]" {
                    let checked = lower.hasPrefix("[x]")
                    liClass = " class=\"task\""
                    content[0] = String(firstLine.dropFirst(3)).trimmingCharacters(in: .whitespaces)
                    taskPrefix = checked ? "☑︎ " : "☐ "
                }
            }
            let inner: String
            if isLoose {
                inner = renderBlocks(content)
            } else {
                // 緊湊清單：第一段不包 <p>
                var paraLines: [String] = []
                var restStart = content.count
                for (n, l) in content.enumerated() {
                    if n > 0 && (Self.startsBlock(l) || Self.listMarker(l) != nil) { restStart = n; break }
                    paraLines.append(l)
                }
                let rest = Array(content[restStart...])
                inner = renderParagraph(paraLines) + (rest.isEmpty ? "" : "\n" + renderBlocks(rest))
            }
            html += "\n<li\(liClass)>\(taskPrefix)\(inner)</li>"
        }
        html += ordered ? "\n</ol>" : "\n</ul>"
        return (html, i)
    }

    // MARK: - 行內語法

    func renderInline(_ text: String) -> String {
        var out = ""
        let chars = Array(text)
        var i = 0
        func peek(_ offset: Int) -> Character? { i + offset < chars.count ? chars[i + offset] : nil }

        while i < chars.count {
            let c = chars[i]
            // 跳脫字元
            if c == "\\", let n = peek(1), "\\`*_{}[]()#+-.!|~<>\"'".contains(n) {
                out += Self.escapeHTML(String(n))
                i += 2
                continue
            }
            // 行內程式碼
            if c == "`" {
                var ticks = 0
                while let t = peek(ticks), t == "`" { ticks += 1 }
                let fence = String(repeating: "`", count: ticks)
                let rest = String(chars[(i + ticks)...])
                if let r = rest.range(of: fence) {
                    var code = String(rest[rest.startIndex..<r.lowerBound])
                    if code.hasPrefix(" ") && code.hasSuffix(" ") && code.count > 2 { code = String(code.dropFirst().dropLast()) }
                    out += "<code>\(Self.escapeHTML(code))</code>"
                    i += ticks + rest.distance(from: rest.startIndex, to: r.upperBound)
                    continue
                }
                out += fence
                i += ticks
                continue
            }
            // 圖片與連結
            if c == "!" && peek(1) == "[" || c == "[" {
                let isImage = c == "!"
                let open = isImage ? i + 1 : i
                if let (label, close) = Self.matchBracket(chars, from: open) {
                    var j = close + 1
                    if j < chars.count, chars[j] == "(" , let (dest, end) = Self.matchParen(chars, from: j) {
                        let (url, title) = Self.parseDestination(dest)
                        out += makeLink(isImage: isImage, label: label, url: url, title: title)
                        i = end + 1
                        continue
                    }
                    // 參考式連結 [text][ref] 或 [text]
                    var refKey = label
                    if j < chars.count, chars[j] == "[", let (ref, end) = Self.matchBracket(chars, from: j) {
                        if !ref.isEmpty { refKey = ref }
                        j = end + 1
                    }
                    if let ref = references[refKey.lowercased()] {
                        out += makeLink(isImage: isImage, label: label, url: ref.url, title: ref.title)
                        i = j
                        continue
                    }
                }
            }
            // 自動連結 <https://…>
            if c == "<" {
                let rest = String(chars[(i + 1)...])
                if let end = rest.firstIndex(of: ">") {
                    let inside = String(rest[rest.startIndex..<end])
                    if inside.hasPrefix("http://") || inside.hasPrefix("https://") || inside.hasPrefix("mailto:") {
                        out += "<a href=\"\(Self.escapeAttr(inside))\">\(Self.escapeHTML(inside))</a>"
                        i += 2 + inside.count
                        continue
                    }
                    if inside.contains("@"), !inside.contains(" ") {
                        out += "<a href=\"mailto:\(Self.escapeAttr(inside))\">\(Self.escapeHTML(inside))</a>"
                        i += 2 + inside.count
                        continue
                    }
                    // 行內 HTML 標籤原樣保留
                    if let first = inside.first, first.isLetter || first == "/" || first == "!" {
                        out += "<" + inside + ">"
                        i += 2 + inside.count
                        continue
                    }
                }
            }
            // 裸網址
            if c == "h", (String(chars[i..<min(i + 8, chars.count)]).hasPrefix("https://") || String(chars[i..<min(i + 7, chars.count)]).hasPrefix("http://")),
               i == 0 || !(chars[i - 1].isLetter || chars[i - 1].isNumber || chars[i - 1] == "\"" || chars[i - 1] == "=") {
                var j = i
                while j < chars.count, !chars[j].isWhitespace, chars[j] != "<", chars[j] != ")" { j += 1 }
                var url = String(chars[i..<j])
                while let last = url.last, ".,;:!?。，、".contains(last) { url.removeLast(); j -= 1 }
                out += "<a href=\"\(Self.escapeAttr(url))\">\(Self.escapeHTML(url))</a>"
                i = j
                continue
            }
            // 強調／刪除線
            if c == "*" || c == "_" || c == "~" {
                var run = 0
                while let t = peek(run), t == c { run += 1 }
                if c == "~" && run < 2 { out += "~"; i += 1; continue }
                let n = c == "~" ? 2 : min(run, 3)
                let delim = String(repeating: c, count: n)
                // 底線在字中間不算強調（snake_case）
                let prevIsWord = i > 0 && (chars[i - 1].isLetter || chars[i - 1].isNumber)
                if c == "_" && prevIsWord { out += String(repeating: "_", count: run); i += run; continue }
                let after = i + n
                if after < chars.count, !chars[after].isWhitespace,
                   let close = Self.findClosing(chars, delim: delim, from: after) {
                    let inner = String(chars[after..<close])
                    let rendered = renderInline(inner)
                    switch (c, n) {
                    case ("~", _): out += "<del>\(rendered)</del>"
                    case (_, 1): out += "<em>\(rendered)</em>"
                    case (_, 2): out += "<strong>\(rendered)</strong>"
                    default: out += "<strong><em>\(rendered)</em></strong>"
                    }
                    i = close + n
                    continue
                }
                out += Self.escapeHTML(String(repeating: c, count: run))
                i += run
                continue
            }
            out += Self.escapeHTML(String(c))
            i += 1
        }
        return out
    }

    private func makeLink(isImage: Bool, label: String, url: String, title: String?) -> String {
        let titleAttr = title.map { " title=\"\(Self.escapeAttr($0))\"" } ?? ""
        if isImage {
            return "<img src=\"\(Self.escapeAttr(url))\" alt=\"\(Self.escapeAttr(label))\"\(titleAttr)>"
        }
        return "<a href=\"\(Self.escapeAttr(url))\"\(titleAttr)>\(renderInline(label))</a>"
    }

    private static func findClosing(_ chars: [Character], delim: String, from start: Int) -> Int? {
        let d = Array(delim)
        var i = start
        while i <= chars.count - d.count {
            if chars[i] == "`" {
                // 跳過行內程式碼
                if let end = chars[(i + 1)...].firstIndex(of: "`") { i = end + 1; continue }
            }
            if chars[i] == "\\" { i += 2; continue }
            if Array(chars[i..<(i + d.count)]) == d, i > start, !chars[i - 1].isWhitespace {
                let next = i + d.count
                if next < chars.count, chars[next] == d[0] { i += 1; continue }
                if d[0] == "_", next < chars.count, chars[next].isLetter || chars[next].isNumber { i += 1; continue }
                return i
            }
            i += 1
        }
        return nil
    }

    private static func matchBracket(_ chars: [Character], from open: Int) -> (String, Int)? {
        guard open < chars.count, chars[open] == "[" else { return nil }
        var depth = 0
        var i = open
        while i < chars.count {
            if chars[i] == "\\" { i += 2; continue }
            if chars[i] == "[" { depth += 1 }
            if chars[i] == "]" { depth -= 1; if depth == 0 { return (String(chars[(open + 1)..<i]), i) } }
            i += 1
        }
        return nil
    }

    private static func matchParen(_ chars: [Character], from open: Int) -> (String, Int)? {
        guard open < chars.count, chars[open] == "(" else { return nil }
        var depth = 0
        var i = open
        while i < chars.count {
            if chars[i] == "\\" { i += 2; continue }
            if chars[i] == "(" { depth += 1 }
            if chars[i] == ")" { depth -= 1; if depth == 0 { return (String(chars[(open + 1)..<i]), i) } }
            i += 1
        }
        return nil
    }

    private static func parseDestination(_ raw: String) -> (String, String?) {
        let t = raw.trimmingCharacters(in: .whitespaces)
        if t.hasPrefix("<"), let end = t.firstIndex(of: ">") {
            let url = String(t[t.index(after: t.startIndex)..<end])
            let rest = t[t.index(after: end)...].trimmingCharacters(in: .whitespaces)
            return (url, rest.count >= 2 ? String(rest.dropFirst().dropLast()) : nil)
        }
        if let space = t.firstIndex(where: { $0 == " " || $0 == "\t" }) {
            let url = String(t[..<space])
            let rest = t[space...].trimmingCharacters(in: .whitespaces)
            if rest.count >= 2, let f = rest.first, "\"'(".contains(f) { return (url, String(rest.dropFirst().dropLast())) }
            return (url, nil)
        }
        return (t, nil)
    }

    // MARK: - 工具

    static func escapeHTML(_ s: String) -> String {
        var out = ""
        out.reserveCapacity(s.count)
        for c in s {
            switch c {
            case "&": out += "&amp;"
            case "<": out += "&lt;"
            case ">": out += "&gt;"
            default: out.append(c)
            }
        }
        return out
    }

    static func escapeAttr(_ s: String) -> String {
        escapeHTML(s).replacingOccurrences(of: "\"", with: "&quot;")
    }

    static func slug(_ text: String) -> String {
        let lowered = text.lowercased()
        var out = ""
        for c in lowered {
            if c.isLetter || c.isNumber { out.append(c) } else if c == " " || c == "-" { out.append("-") }
        }
        return escapeAttr(out)
    }

    /// 取第一個標題作為文件標題。
    static func firstHeading(_ markdown: String) -> String? {
        for line in markdown.components(separatedBy: .newlines) {
            if let (_, text) = headingLevel(line), !text.isEmpty { return text.replacingOccurrences(of: "*", with: "").replacingOccurrences(of: "`", with: "") }
        }
        return nil
    }
}
