import Foundation
import UniformTypeIdentifiers

/// Markdown → HTML。用 Foundation 內建的 CommonMark 解析器（AttributedString），再把區塊結構轉回 HTML。
final class QSMarkdown {
    private let baseURL: URL?
    private(set) var attachments: [String: QSAttachment] = [:]
    private var html = ""
    private var open: [PresentationIntent.IntentType] = []
    private var tableColumns: [[PresentationIntent.TableColumn]] = []
    private var codeBuffer: String?
    private var codeLanguage: String?
    private var taskCheck = false
    private var imageCount = 0

    init(baseURL: URL?) { self.baseURL = baseURL }

    static func render(_ source: String, baseURL: URL?) -> (html: String, attachments: [String: QSAttachment]) {
        let r = QSMarkdown(baseURL: baseURL)
        let body = r.convert(source)
        return (body, r.attachments)
    }

    func convert(_ source: String) -> String {
        var text = source
        var front = ""
        if let (fm, rest) = Self.splitFrontMatter(text) {
            front = fm
            text = rest
        }
        let options = AttributedString.MarkdownParsingOptions(
            allowsExtendedAttributes: false,
            interpretedSyntax: .full,
            failurePolicy: .returnPartiallyParsedIfPossible
        )
        guard let attr = try? AttributedString(markdown: text, options: options, baseURL: baseURL) else {
            return front + "<pre>\(QSHTML.escape(text))</pre>"
        }
        html = front
        for run in attr.runs {
            let comps = Array((run.presentationIntent?.components ?? []).reversed())
            var k = 0
            while k < open.count && k < comps.count && open[k].identity == comps[k].identity { k += 1 }
            while open.count > k { closeBlock(open.removeLast()) }
            for idx in k..<comps.count {
                openBlock(comps[idx], parent: idx > 0 ? comps[idx - 1] : nil, child: idx + 1 < comps.count ? comps[idx + 1] : nil)
                open.append(comps[idx])
            }
            if case .thematicBreak = comps.last?.kind { continue }   // 系統會塞一個「⸻」字元
            let piece = String(attr[run.range].characters)
            if codeBuffer != nil { codeBuffer! += piece; continue }
            html += inline(piece, run: run)
        }
        while let last = open.popLast() { closeBlock(last) }
        return html
    }

    // MARK: 區塊

    private func openBlock(_ intent: PresentationIntent.IntentType, parent: PresentationIntent.IntentType?, child: PresentationIntent.IntentType?) {
        switch intent.kind {
        case .paragraph:
            html += "<p>"
            if case .listItem = parent?.kind { taskCheck = true }
        case .header(let level): html += "<h\(level)>"
        case .orderedList:
            if case .listItem(let ordinal) = child?.kind, ordinal != 1 { html += "<ol start=\"\(ordinal)\">" } else { html += "<ol>" }
        case .unorderedList: html += "<ul>"
        case .listItem: html += "<li>"
        case .blockQuote: html += "<blockquote>"
        case .codeBlock(let hint):
            codeBuffer = ""
            codeLanguage = hint
        case .thematicBreak: html += "<hr>"
        case .table(let columns):
            tableColumns.append(columns)
            html += "<table>"
        case .tableHeaderRow: html += "<thead><tr>"
        case .tableRow: html += "<tr>"
        case .tableCell(let column):
            var header = false
            if case .tableHeaderRow = parent?.kind { header = true }
            var style = ""
            if let cols = tableColumns.last, column < cols.count {
                switch cols[column].alignment {
                case .center: style = " style=\"text-align:center\""
                case .right: style = " style=\"text-align:right\""
                default: break
                }
            }
            html += header ? "<th\(style)>" : "<td\(style)>"
        @unknown default: break
        }
    }

    private func closeBlock(_ intent: PresentationIntent.IntentType) {
        switch intent.kind {
        case .paragraph: html += "</p>\n"; taskCheck = false
        case .header(let level): html += "</h\(level)>\n"
        case .orderedList: html += "</ol>\n"
        case .unorderedList: html += "</ul>\n"
        case .listItem: html += "</li>\n"
        case .blockQuote: html += "</blockquote>\n"
        case .codeBlock:
            let code = codeBuffer ?? ""
            codeBuffer = nil
            if let lang = QSLanguages.forHint(codeLanguage) {
                html += "<pre><code>\(QSHighlighter.inline(code, language: lang))</code></pre>\n"
            } else {
                html += "<pre><code>\(QSHTML.escape(code.hasSuffix("\n") ? String(code.dropLast()) : code))</code></pre>\n"
            }
        case .thematicBreak: break
        case .table: tableColumns.removeLast(); html += "</table>\n"
        case .tableHeaderRow: html += "</tr></thead>\n"
        case .tableRow: html += "</tr>\n"
        case .tableCell:
            if case .tableHeaderRow = open.last?.kind { html += "</th>" } else { html += "</td>" }
        @unknown default: break
        }
    }

    // MARK: 行內

    private func inline(_ text: String, run: AttributedString.Runs.Run) -> String {
        let ipi = run.inlinePresentationIntent ?? []
        if ipi.contains(.lineBreak) { return "<br>" }
        if ipi.contains(.softBreak) { return "\n" }
        if ipi.contains(.inlineHTML) || ipi.contains(.blockHTML) { return text }
        if let img = run.imageURL { taskCheck = false; return image(img, alt: text) }

        var t = text
        var prefix = ""
        if taskCheck {
            taskCheck = false
            for (marker, checked) in [("[ ] ", false), ("[x] ", true), ("[X] ", true)] where t.hasPrefix(marker) {
                prefix = "<input type=\"checkbox\" class=\"task\" disabled\(checked ? " checked" : "")>"
                t = String(t.dropFirst(marker.count))
                break
            }
        }
        var s = QSHTML.escape(t)
        if ipi.contains(.code) { s = "<code>\(s)</code>" }
        if ipi.contains(.strikethrough) { s = "<del>\(s)</del>" }
        if ipi.contains(.emphasized) { s = "<em>\(s)</em>" }
        if ipi.contains(.stronglyEmphasized) { s = "<strong>\(s)</strong>" }
        if let link = run.link { s = "<a href=\"\(QSHTML.escape(link.absoluteString))\">\(s)</a>" }
        return prefix + s
    }

    private func image(_ url: URL, alt: String) -> String {
        let altEsc = QSHTML.escape(alt)
        if url.isFileURL {
            let path = url.standardizedFileURL
            if let size = (try? path.resourceValues(forKeys: [.fileSizeKey]))?.fileSize, size < 15_000_000,
               let data = try? Data(contentsOf: path) {
                let type = UTType(filenameExtension: path.pathExtension.lowercased()) ?? .image
                imageCount += 1
                let name = "md-img-\(imageCount)"
                attachments[name] = QSAttachment(data: data, contentType: type)
                return "<img src=\"cid:\(name)\" alt=\"\(altEsc)\">"
            }
            return "<span class=\"img-missing\">🖼 \(altEsc.isEmpty ? QSHTML.escape(url.lastPathComponent) : altEsc)</span>"
        }
        return "<img src=\"\(QSHTML.escape(url.absoluteString))\" alt=\"\(altEsc)\">"
    }

    // MARK: Front matter（YAML 標頭）

    static func splitFrontMatter(_ text: String) -> (String, String)? {
        guard text.hasPrefix("---\n") || text.hasPrefix("---\r\n") else { return nil }
        let lines = text.components(separatedBy: "\n")
        guard let endIdx = lines.dropFirst().prefix(200).firstIndex(where: { $0.trimmingCharacters(in: .whitespaces) == "---" || $0.trimmingCharacters(in: .whitespaces) == "..." }) else { return nil }
        let fmLines = lines[1..<endIdx]
        let rest = lines[(endIdx + 1)...].joined(separator: "\n")
        var rows = ""
        for line in fmLines {
            guard let colon = line.firstIndex(of: ":"), !line.hasPrefix(" "), !line.hasPrefix("-") else {
                if !line.trimmingCharacters(in: .whitespaces).isEmpty, !rows.isEmpty {
                    rows = String(rows.dropLast("</td></tr>".count)) + "<br>" + QSHTML.escape(line.trimmingCharacters(in: .whitespaces)) + "</td></tr>"
                }
                continue
            }
            let key = line[..<colon].trimmingCharacters(in: .whitespaces)
            let value = line[line.index(after: colon)...].trimmingCharacters(in: .whitespaces)
            rows += "<tr><th>\(QSHTML.escape(key))</th><td>\(QSHTML.escape(value))</td></tr>"
        }
        let table = rows.isEmpty ? "" : "<div class=\"front\"><table>\(rows)</table></div>"
        return (table, rest)
    }
}

extension QSLanguages {
    /// Markdown 程式碼區塊的語言提示 → 語言。
    static func forHint(_ hint: String?) -> QSLanguage? {
        guard var h = hint?.lowercased().trimmingCharacters(in: .whitespaces), !h.isEmpty else { return nil }
        if let space = h.firstIndex(where: { $0 == " " || $0 == "{" }) { h = String(h[..<space]) }
        let aliases: [String: String] = [
            "js": "javascript", "node": "javascript", "jsx": "javascript", "ts": "typescript", "tsx": "typescript",
            "py": "python", "python3": "python", "rb": "ruby", "sh": "shell", "bash": "shell", "zsh": "shell",
            "console": "shell", "shellscript": "shell", "terminal": "shell", "objc": "c", "objective-c": "c", "cpp": "c", "c++": "c",
            "rs": "rust", "kt": "kotlin", "cs": "csharp", "c#": "csharp", "yml": "yaml", "dockerfile": "docker",
            "makefile": "make", "ps1": "powershell", "pwsh": "powershell", "sass": "scss", "less": "scss",
            "jsonc": "jsonc", "json5": "jsonc", "tf": "hcl", "terraform": "hcl", "patch": "diff", "plaintext": "text", "txt": "text",
            "svg": "xml", "plist": "xml", "golang": "go", "elisp": "clojure", "lisp": "clojure", "fsharp": "ocaml", "f#": "ocaml",
        ]
        if let id = aliases[h], let l = byID[id] { return l }
        if let l = byID[h] { return l }
        let fake = QSFile(url: URL(fileURLWithPath: "/tmp/x.\(h)"))
        let l = detect(file: fake, text: "")
        return l.id == "text" ? nil : l
    }
}
