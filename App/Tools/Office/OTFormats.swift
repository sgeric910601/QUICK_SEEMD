import AppKit
import Foundation

// 文件與試算表格式的內建讀寫（macOS 內建引擎）。

// MARK: - Word 文件（NSAttributedString）

enum OTWordBuiltIn {
    static let readable: Set<String> = ["doc", "dot", "docx", "docm", "dotx", "dotm", "rtf", "odt"]
    static let writable: Set<String> = ["docx", "doc", "odt", "rtf", "txt", "html", "md"]

    static func documentType(_ ext: String) -> NSAttributedString.DocumentType? {
        switch ext {
        case "doc", "dot": return .docFormat
        case "docx", "docm", "dotx", "dotm": return .officeOpenXML
        case "rtf": return .rtf
        case "odt": return .openDocument
        default: return nil
        }
    }

    @MainActor
    static func load(_ url: URL) throws -> CVDocumentConverter.Loaded {
        guard let type = documentType(OTKind.ext(url)) else { throw ConversionError.unreadable("系統無法讀取 .\(OTKind.ext(url)) 文件。") }
        var attrs: NSDictionary?
        do {
            let text = try NSAttributedString(url: url, options: [.documentType: type], documentAttributes: &attrs)
            return CVDocumentConverter.Loaded(text: text, attributes: (attrs as? [NSAttributedString.DocumentAttributeKey: Any]) ?? [:])
        } catch {
            throw ConversionError.unreadable("系統無法解析這份文件（\(error.localizedDescription)）。")
        }
    }

    /// 以系統文字引擎轉換 Word 類文件（DOCX／DOC／ODT／RTF／TXT／HTML／Markdown）。
    /// DOC／DOCX 轉 HTML、Markdown 時先用 系統預覽 的排版結果（保留標題樣式、表格與圖片），失敗才用文字引擎。
    static func convert(_ input: URL, to target: String) async throws -> URL {
        let out = try OTPDFConvert.outputURL(for: input, ext: target)
        if ["html", "md"].contains(target), OTBuiltIn.systemPreviewWordExtensions.contains(OTKind.ext(input)) {
            let work = try CVFileNaming.makeScratchDirectory()
            defer { try? FileManager.default.removeItem(at: work) }
            if let preview = try? await OTSystemPreview.preview(input, work: work) {
                let html = preview.appendingPathComponent("Preview.html")
                if target == "html" {
                    try OTHTMLTools.inlineResources(html, to: out)
                    let title = CVXML.escape(CVFileNaming.baseName(of: input))
                    if var text = try? String(contentsOf: out, encoding: .utf8) {
                        text = text.replacingOccurrences(of: #"<title>[^<]*</title>"#, with: "<title>\(title)</title>", options: .regularExpression)
                        try Data(text.utf8).write(to: out)
                    }
                    return out
                }
                let md: String? = await MainActor.run {
                    guard let data = try? Data(contentsOf: html) else { return nil }
                    let options: [NSAttributedString.DocumentReadingOptionKey: Any] = [
                        .documentType: NSAttributedString.DocumentType.html,
                        .characterEncoding: String.Encoding.utf8.rawValue,
                        .baseURL: preview, .timeout: 20.0,
                    ]
                    guard let text = try? NSAttributedString(data: data, options: options, documentAttributes: nil) else { return nil }
                    return OTMarkdown.from(text)
                }
                if let md, !md.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                    try Data(md.utf8).write(to: out)
                    return out
                }
            }
        }
        try await MainActor.run {
            let loaded = try load(input)
            if target == "md" {
                let md = OTMarkdown.from(loaded.text)
                guard !md.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty else { throw ConversionError.emptyContent("文件沒有文字。") }
                try Data(md.utf8).write(to: out)
            } else {
                try CVDocumentConverter.write(loaded, formatID: target, to: out)
            }
        }
        return out
    }
}

/// NSAttributedString → Markdown（標題、粗斜體、清單、表格、連結）。
enum OTMarkdown {
    static func from(_ text: NSAttributedString) -> String {
        let ns = text.string as NSString
        // 內文字級：以字數加權的中位數
        var sizes: [CGFloat] = []
        text.enumerateAttribute(.font, in: NSRange(location: 0, length: text.length)) { value, range, _ in
            if let f = value as? NSFont { sizes += Array(repeating: f.pointSize, count: min(range.length, 400)) }
        }
        let body = sizes.sorted().dropFirst(sizes.count / 2).first ?? 12

        var output: [String] = []
        var tableRows: [Int: [Int: String]] = [:]
        var currentTable: NSTextTable?

        func flushTable() {
            guard currentTable != nil, !tableRows.isEmpty else { currentTable = nil; tableRows = [:]; return }
            let columns = (tableRows.values.flatMap(\.keys).max() ?? 0) + 1
            let rows = tableRows.keys.sorted().map { r in (0..<columns).map { tableRows[r]?[$0] ?? "" } }
            var lines: [String] = []
            for (i, row) in rows.enumerated() {
                lines.append("| " + row.map { $0.replacingOccurrences(of: "|", with: "\\|").replacingOccurrences(of: "\n", with: "<br>") }.joined(separator: " | ") + " |")
                if i == 0 { lines.append("|" + Array(repeating: " --- |", count: columns).joined()) }
            }
            output.append(lines.joined(separator: "\n"))
            currentTable = nil
            tableRows = [:]
        }

        var location = 0
        while location < ns.length {
            let range = ns.paragraphRange(for: NSRange(location: location, length: 0))
            location = NSMaxRange(range)
            var content = range
            while content.length > 0, let c = Unicode.Scalar(ns.character(at: NSMaxRange(content) - 1)), CharacterSet.newlines.contains(c) {
                content.length -= 1
            }
            let style = text.attribute(.paragraphStyle, at: range.location, effectiveRange: nil) as? NSParagraphStyle
            // 表格儲存格
            if let block = style?.textBlocks.last as? NSTextTableBlock {
                if currentTable !== block.table { flushTable(); currentTable = block.table }
                let cell = inline(text.attributedSubstring(from: content)).trimmingCharacters(in: .whitespaces)
                let existing = tableRows[block.startingRow]?[block.startingColumn]
                tableRows[block.startingRow, default: [:]][block.startingColumn] = existing.map { $0.isEmpty ? cell : $0 + "\n" + cell } ?? cell
                continue
            }
            flushTable()
            let plain = ns.substring(with: content).replacingOccurrences(of: "\u{FFFC}", with: "").trimmingCharacters(in: .whitespaces)
            if plain.isEmpty { continue }
            var line = inline(text.attributedSubstring(from: content))
            // 標題：看字級與粗體
            var size: CGFloat = 0
            var allBold = true
            text.enumerateAttribute(.font, in: content) { value, _, _ in
                if let f = value as? NSFont {
                    size = max(size, f.pointSize)
                    if !f.fontDescriptor.symbolicTraits.contains(.bold) { allBold = false }
                }
            }
            var prefix = ""
            if let lists = style?.textLists, let list = lists.last {
                let ordered = list.markerFormat.rawValue.contains("decimal") || list.markerFormat.rawValue.contains("roman") || list.markerFormat.rawValue.contains("alpha")
                prefix = String(repeating: "  ", count: max(0, lists.count - 1)) + (ordered ? "1. " : "- ")
                line = stripMarker(line)
            } else if plain.hasPrefix("•") || plain.hasPrefix("●") || plain.hasPrefix("▪") || plain.hasPrefix("◦") {
                prefix = "- "
                line = stripMarker(line)
            } else if size >= body * 1.6 {
                prefix = "# "
            } else if size >= body * 1.3 {
                prefix = "## "
            } else if size >= body * 1.12 || (allBold && plain.count <= 60 && !plain.hasSuffix("。")) {
                prefix = "### "
            }
            if prefix.hasPrefix("#") { line = line.replacingOccurrences(of: "**", with: "") }
            output.append(prefix + line.trimmingCharacters(in: .whitespaces))
        }
        flushTable()
        // 清單項目之間不空行，其他段落空一行
        var result = ""
        for (i, block) in output.enumerated() {
            if i > 0 {
                let prevList = output[i - 1].range(of: #"^\s*(- |1\. )"#, options: .regularExpression) != nil
                let thisList = block.range(of: #"^\s*(- |1\. )"#, options: .regularExpression) != nil
                result += prevList && thisList ? "\n" : "\n\n"
            }
            result += block
        }
        return result + "\n"
    }

    private static func stripMarker(_ s: String) -> String {
        s.replacingOccurrences(of: #"^\s*(\\?[•●▪◦■\-–]|\d+[\.\)])\s*"#, with: "", options: .regularExpression)
    }

    private static func escape(_ s: String) -> String {
        var out = ""
        for c in s {
            if "\\`*_[]".contains(c) { out.append("\\") }
            out.append(c)
        }
        return out
    }

    /// 段落內的粗體、斜體、連結。
    private static func inline(_ text: NSAttributedString) -> String {
        var out = ""
        text.enumerateAttributes(in: NSRange(location: 0, length: text.length)) { attrs, range, _ in
            var s = (text.string as NSString).substring(with: range)
                .replacingOccurrences(of: "\u{FFFC}", with: "").replacingOccurrences(of: "\u{2028}", with: "\n")
            if s.isEmpty { return }
            let leading = String(s.prefix { $0 == " " || $0 == "\t" })
            let trailing = String(s.reversed().prefix { $0 == " " || $0 == "\t" || $0 == "\n" }.reversed())
            s = s.trimmingCharacters(in: .whitespacesAndNewlines)
            if s.isEmpty { out += leading; return }
            s = escape(s)
            let traits = (attrs[.font] as? NSFont)?.fontDescriptor.symbolicTraits ?? []
            if traits.contains(.bold) && traits.contains(.italic) { s = "***\(s)***" }
            else if traits.contains(.bold) { s = "**\(s)**" }
            else if traits.contains(.italic) { s = "*\(s)*" }
            if let link = attrs[.link] {
                let url = (link as? URL)?.absoluteString ?? (link as? String) ?? ""
                if !url.isEmpty { s = "[\(s)](\(url))" }
            }
            out += leading + s + trailing.replacingOccurrences(of: "\n", with: " ")
        }
        return out.replacingOccurrences(of: "****", with: "")
    }
}

// MARK: - 試算表

enum OTCSVEncoding: String, CaseIterable, Sendable {
    case utf8BOM, utf8, big5

    var title: String {
        switch self {
        case .utf8BOM: return "UTF-8（含 BOM）"
        case .utf8: return "UTF-8"
        case .big5: return "Big5"
        }
    }
}

enum OTSheetScope: String, Sendable {
    case first, each
}

enum OTSpreadsheet {
    /// 內建引擎讀得懂的試算表格式。
    static let readable: Set<String> = ["xlsx", "xlsm", "xltx", "xls", "csv", "tsv"]

    /// 讀出所有工作表的儲存格文字。
    static func readTables(_ input: URL) async throws -> [CVDataConverter.Table] {
        let ext = OTKind.ext(input)
        switch ext {
        case "csv", "tsv":
            return [try CVDataConverter.readDelimited(input, tsv: ext == "tsv", options: ConversionOptions())]
        case "xlsx", "xlsm", "xltx":
            return try await readXLSX(input)
        case "xls":
            let work = try CVFileNaming.makeScratchDirectory()
            defer { try? FileManager.default.removeItem(at: work) }
            guard let preview = try await OTSystemPreview.preview(input, work: work) else {
                throw ConversionError.unreadable("系統無法解析這個 .xls 檔案。")
            }
            let tables = OTSheetHTML.tables(in: preview)
            if tables.isEmpty { throw ConversionError.emptyContent("活頁簿中沒有工作表。") }
            return tables
        default:
            throw ConversionError.unreadable("macOS 內建引擎無法讀取 .\(ext) 試算表。")
        }
    }

    /// 由表格寫出 CSV／TSV／JSON／HTML／XLSX。多個工作表的 CSV／TSV 會放進資料夾（每個工作表一檔）。
    static func write(_ tablesIn: [CVDataConverter.Table], baseName: String, target: String, encoding: OTCSVEncoding,
                      scope: OTSheetScope) async throws -> URL {
        let work = try CVFileNaming.makeScratchDirectory()
        var tables = tablesIn.filter { !$0.rows.isEmpty }
        if tables.isEmpty { tables = Array(tablesIn.prefix(1)) }
        if scope == .first { tables = Array(tables.prefix(1)) }
        let base = CVFileNaming.sanitize(baseName)
        switch target {
        case "csv", "tsv":
            let delimiter: Character = target == "csv" ? "," : "\t"
            func data(_ t: CVDataConverter.Table) throws -> Data {
                let text = CVDataConverter.delimited(t, delimiter: delimiter).replacingOccurrences(of: "\n", with: "\r\n")
                switch encoding {
                case .utf8BOM: return try CVTextCodec.encode(text, as: .utf8, bom: true)
                case .utf8: return Data(text.utf8)
                case .big5: return try CVTextCodec.encode(text, as: CVTextCodec.big5CP950, bom: false)
                }
            }
            if tables.count <= 1 {
                let out = work.appendingPathComponent(CVFileNaming.fileName(base, target))
                try data(tables.first ?? CVDataConverter.Table(name: base, rows: [])).write(to: out)
                return out
            }
            let folder = work.appendingPathComponent("\(base)-\(target.uppercased())", isDirectory: true)
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            for t in tables {
                let file = CVFileNaming.uniqueURL(in: folder, fileName: CVFileNaming.fileName(CVFileNaming.sanitize(t.name), target))
                try data(t).write(to: file)
            }
            return folder
        case "json":
            let value: CVJSONValue = tables.count == 1 ? CVDataConverter.json(from: tables[0]) : .object(tables.map { ($0.name, CVDataConverter.json(from: $0)) })
            let out = work.appendingPathComponent(CVFileNaming.fileName(base, "json"))
            try Data((CVJSONWriter.write(value, pretty: true) + "\n").utf8).write(to: out)
            return out
        case "html":
            let out = work.appendingPathComponent(CVFileNaming.fileName(base, "html"))
            try Data(CVDataConverter.htmlTables(tables, title: baseName).utf8).write(to: out)
            return out
        case "xlsx":
            let out = work.appendingPathComponent(CVFileNaming.fileName(base, "xlsx"))
            let sheets = tables.map { t -> OTSheet in
                let rows = t.rows.map { $0.map { OTCell.parse($0, convertNumbers: true) } }
                return OTSheet(name: t.name, rows: rows, headerRow: OTPDFConvert.headerRow(rows))
            }
            try await OTXLSXWriter.write(sheets, to: out, title: baseName)
            return out
        default:
            throw ConversionError.other("內建引擎不支援輸出 \(target.uppercased())。")
        }
    }

    /// 讀 XLSX（轉換器的讀取器）。它用 Process.waitUntilExit 解壓縮，系統忙碌時偶爾會卡住不回來，
    /// 所以加上逾時，逾時就重試一次。
    static func readXLSX(_ input: URL) async throws -> [CVDataConverter.Table] {
        for _ in 0..<2 {
            let box = OTSendable(input)
            if let tables = try await OTTimeout.run(45, { try await CVDataConverter.readXLSX(box.value) }) { return tables }
            try Task.checkCancellation()
        }
        throw ConversionError.unreadable("讀取試算表逾時，請再試一次。")
    }

    /// CSV／TSV → 暫存 XLSX（讓 Excel 開啟時中文不會變亂碼、數字是數值）。
    static func csvToXLSX(_ input: URL) async throws -> URL {
        let tables = try await readTables(input)
        return try await write(tables, baseName: CVFileNaming.baseName(of: input), target: "xlsx", encoding: .utf8, scope: .each)
    }
}

// MARK: - XLSX 版面設定

/// 直接修改 XLSX 裡每個工作表的版面設定（方向、縮放成一頁寬、列印格線）。
/// Excel 的 AppleScript 在沒有設定印表機時無法調整版面，改檔案最可靠；Excel 轉 PDF 時會照這些設定排版。
enum OTXLSXPageSetup {
    static func apply(to input: URL, orientation: String, fitWidth: Bool, gridlines: Bool) async throws -> URL {
        let work = try CVFileNaming.makeScratchDirectory()
        let root = work.appendingPathComponent("x", isDirectory: true)
        let unzip = try await OTProcess.run("/usr/bin/ditto", ["-x", "-k", input.path, root.path], timeout: 300)
        guard unzip.succeeded else { throw ConversionError.unreadable("無法解開試算表。") }
        let sheets = root.appendingPathComponent("xl/worksheets")
        let files = ((try? FileManager.default.contentsOfDirectory(at: sheets, includingPropertiesForKeys: nil)) ?? [])
            .filter { $0.pathExtension == "xml" }
        for file in files {
            guard var xml = try? String(contentsOf: file, encoding: .utf8) else { continue }
            xml = edit(xml, orientation: orientation, fitWidth: fitWidth, gridlines: gridlines)
            try Data(xml.utf8).write(to: file)
        }
        let out = work.appendingPathComponent(input.lastPathComponent)
        try await OTWork.zipDirectory(root, to: out)
        return out
    }

    static func edit(_ source: String, orientation: String, fitWidth: Bool, gridlines: Bool) -> String {
        var xml = source
        // 1. <sheetPr><pageSetUpPr fitToPage="1"/></sheetPr>
        if fitWidth {
            if let r = xml.range(of: #"<pageSetUpPr\b[^>]*/>"#, options: .regularExpression) {
                var tag = String(xml[r]).replacingOccurrences(of: #"\s*fitToPage="[^"]*""#, with: "", options: .regularExpression)
                tag = tag.replacingOccurrences(of: "<pageSetUpPr", with: "<pageSetUpPr fitToPage=\"1\"")
                xml.replaceSubrange(r, with: tag)
            } else if let r = xml.range(of: #"<sheetPr\b[^>]*/>"#, options: .regularExpression) {
                let open = String(xml[r]).dropLast(2)
                xml.replaceSubrange(r, with: open + "><pageSetUpPr fitToPage=\"1\"/></sheetPr>")
            } else if let r = xml.range(of: "</sheetPr>") {
                xml.replaceSubrange(r, with: "<pageSetUpPr fitToPage=\"1\"/></sheetPr>")
            } else if let r = xml.range(of: #"<worksheet\b[^>]*>"#, options: .regularExpression) {
                xml.insert(contentsOf: "<sheetPr><pageSetUpPr fitToPage=\"1\"/></sheetPr>", at: r.upperBound)
            }
        }
        // 2. <pageSetup orientation fitToWidth fitToHeight/>
        var attrs: [String: String] = [:]
        if orientation == "landscape" || orientation == "portrait" { attrs["orientation"] = orientation }
        if fitWidth { attrs["fitToWidth"] = "1"; attrs["fitToHeight"] = "0" }
        if !attrs.isEmpty {
            if let r = xml.range(of: #"<pageSetup\b[^>]*/>"#, options: .regularExpression) {
                var tag = String(xml[r])
                for key in attrs.keys { tag = tag.replacingOccurrences(of: #"\s*\#(key)="[^"]*""#, with: "", options: .regularExpression) }
                tag = tag.replacingOccurrences(of: "<pageSetup", with: "<pageSetup" + attrs.sorted { $0.key < $1.key }.map { " \($0.key)=\"\($0.value)\"" }.joined())
                xml.replaceSubrange(r, with: tag)
            } else {
                let tag = "<pageSetup" + attrs.sorted { $0.key < $1.key }.map { " \($0.key)=\"\($0.value)\"" }.joined() + "/>"
                insert(tag, after: ["pageMargins", "printOptions"], into: &xml)
            }
        }
        // 3. <printOptions gridLines="1"/>（要在 pageMargins 之前）
        if gridlines {
            if let r = xml.range(of: #"<printOptions\b[^>]*/>"#, options: .regularExpression) {
                var tag = String(xml[r]).replacingOccurrences(of: #"\s*gridLines="[^"]*""#, with: "", options: .regularExpression)
                tag = tag.replacingOccurrences(of: "<printOptions", with: "<printOptions gridLines=\"1\"")
                xml.replaceSubrange(r, with: tag)
            } else if let r = xml.range(of: #"<pageMargins\b"#, options: .regularExpression) {
                xml.insert(contentsOf: "<printOptions gridLines=\"1\"/>", at: r.lowerBound)
            } else {
                insert("<printOptions gridLines=\"1\"/>", after: [], into: &xml)
            }
        }
        return xml
    }

    /// 依 OOXML 的元素順序插入：放在指定元素之後，否則放在後段元素之前。
    private static func insert(_ tag: String, after: [String], into xml: inout String) {
        for name in after {
            if let r = xml.range(of: "<\(name)\\b[^>]*/>", options: .regularExpression) {
                xml.insert(contentsOf: tag, at: r.upperBound)
                return
            }
        }
        let later = ["headerFooter", "rowBreaks", "colBreaks", "customProperties", "cellWatches", "ignoredErrors", "smartTags",
                     "drawing", "legacyDrawing", "legacyDrawingHF", "picture", "oleObjects", "controls", "webPublishItems",
                     "tableParts", "extLst"]
        var position = xml.range(of: "</worksheet>")?.lowerBound
        for name in later {
            if let r = xml.range(of: "<\(name)\\b", options: .regularExpression), position == nil || r.lowerBound < position! {
                position = r.lowerBound
            }
        }
        if let position { xml.insert(contentsOf: tag, at: position) }
    }
}

// MARK: - 逾時

/// 執行一段非同步工作，超過時間就不再等（回傳 nil）。卡住的工作會留在背景，不會拖住呼叫端。
enum OTTimeout {
    static func run<T: Sendable>(_ seconds: Double, _ operation: @escaping @Sendable () async throws -> T) async throws -> T? {
        let once = OTResumeOnce<T?>()
        let worker = Task { try await operation() }
        return try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { (continuation: CheckedContinuation<T?, Error>) in
                once.set(continuation)
                Task {
                    do { once.resume(.success(try await worker.value)) } catch { once.resume(.failure(error)) }
                }
                Task {
                    try? await Task.sleep(nanoseconds: UInt64(max(0.1, seconds) * 1_000_000_000))
                    once.resume(.success(nil))
                }
            }
        } onCancel: {
            worker.cancel()
            once.resume(.failure(ConversionError.cancelled))
        }
    }
}

private final class OTResumeOnce<T>: @unchecked Sendable {
    private let lock = NSLock()
    private var continuation: CheckedContinuation<T, Error>?
    private var pending: Result<T, Error>?

    func set(_ c: CheckedContinuation<T, Error>) {
        lock.lock()
        if let pending {
            self.pending = nil
            lock.unlock()
            c.resume(with: pending)
            return
        }
        continuation = c
        lock.unlock()
    }

    func resume(_ result: Result<T, Error>) {
        lock.lock()
        if finished { lock.unlock(); return }
        finished = true
        if let c = continuation {
            continuation = nil
            lock.unlock()
            c.resume(with: result)
            return
        }
        // 還沒設定 continuation（例如一開始就被取消）：先記住，set() 時再交出
        pending = result
        lock.unlock()
    }

    private var finished = false
}
