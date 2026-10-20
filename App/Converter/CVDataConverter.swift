import Foundation

/// 結構化資料互轉：JSON、Plist（XML／二進位）、YAML（輸出）、CSV、TSV、Excel (XLSX)、HTML 表格。
enum CVDataConverter {
    struct Table {
        var name: String
        var rows: [[String]]
    }

    // MARK: - 讀取

    static func readJSON(_ url: URL, options: ConversionOptions) throws -> CVJSONValue {
        let decoded = try CVTextCodec.decode(url, forced: options.sourceEncoding.encoding)
        return try CVJSONParser.parse(decoded.text)
    }

    static func readPlist(_ url: URL) throws -> CVJSONValue {
        do { return try CVPlistBridge.read(try Data(contentsOf: url)) } catch let e as ConversionError { throw e } catch {
            throw ConversionError.unreadable(error.localizedDescription)
        }
    }

    static func readDelimited(_ url: URL, tsv: Bool, options: ConversionOptions) throws -> Table {
        let decoded = try CVTextCodec.decode(url, forced: options.sourceEncoding.encoding)
        let delimiter: Unicode.Scalar = tsv ? "\t" : detectDelimiter(decoded.text)
        return Table(name: CVFileNaming.baseName(of: url), rows: parseDelimited(decoded.text, delimiter: delimiter))
    }

    /// CSV 有時用分號（歐洲 Excel）或 Tab 分隔：看第一行哪一種最多。
    static func detectDelimiter(_ text: String) -> Unicode.Scalar {
        var counts: [Unicode.Scalar: Int] = [",": 0, ";": 0, "\t": 0]
        var inQuotes = false
        for s in text.unicodeScalars {
            if s == "\"" { inQuotes.toggle() }
            if !inQuotes && (s == "\n" || s == "\r") { break }
            if !inQuotes, counts[s] != nil { counts[s, default: 0] += 1 }
        }
        let best = counts.max { a, b in a.value < b.value || (a.value == b.value && a.key != ",") }
        return (best?.value ?? 0) > 0 ? best!.key : ","
    }

    /// RFC 4180 解析：支援引號、欄位內換行、"" 跳脫。
    static func parseDelimited(_ text: String, delimiter: Unicode.Scalar) -> [[String]] {
        var rows: [[String]] = []
        var row: [String] = []
        var field = String.UnicodeScalarView()
        var inQuotes = false
        var quotedField = false
        let scalars = Array(text.unicodeScalars)
        var i = 0
        func endField() {
            row.append(String(field))
            field = String.UnicodeScalarView()
            quotedField = false
        }
        while i < scalars.count {
            let c = scalars[i]
            if inQuotes {
                if c == "\"" {
                    if i + 1 < scalars.count, scalars[i + 1] == "\"" { field.append("\""); i += 2; continue }
                    inQuotes = false
                } else {
                    field.append(c)
                }
                i += 1
                continue
            }
            if c == "\"" && field.isEmpty && !quotedField {
                inQuotes = true
                quotedField = true
            } else if c == delimiter {
                endField()
            } else if c == "\r" || c == "\n" {
                endField()
                rows.append(row)
                row = []
                if c == "\r", i + 1 < scalars.count, scalars[i + 1] == "\n" { i += 1 }
            } else if c == "\u{FEFF}" && rows.isEmpty && row.isEmpty && field.isEmpty {
                // 略過 BOM
            } else {
                field.append(c)
            }
            i += 1
        }
        if !field.isEmpty || !row.isEmpty || quotedField { endField(); rows.append(row) }
        // 移除尾端的完全空白列
        while let last = rows.last, last.allSatisfy({ $0.isEmpty }) { rows.removeLast() }
        return rows
    }

    // MARK: - 表格 ↔ JSON

    static func table(from json: CVJSONValue, name: String) throws -> Table {
        guard case .array(let items) = json else {
            if case .object(let members) = json {
                // 物件的每個鍵若都是陣列（例如多個工作表），取第一個
                if let first = members.first(where: { if case .array = $0.1 { return true }; return false }) {
                    return try table(from: first.1, name: first.0)
                }
            }
            throw ConversionError.invalidData("JSON 的最外層必須是陣列（例如 [{…}, {…}]）才能轉成表格。")
        }
        if items.isEmpty { return Table(name: name, rows: []) }
        if items.allSatisfy({ if case .object = $0 { return true }; return false }) {
            var headers: [String] = []
            var seen = Set<String>()
            for item in items {
                if case .object(let m) = item {
                    for (k, _) in m where !seen.contains(k) { seen.insert(k); headers.append(k) }
                }
            }
            var rows = [headers]
            for item in items {
                guard case .object(let m) = item else { continue }
                var dict: [String: CVJSONValue] = [:]
                for (k, v) in m { dict[k] = v }
                rows.append(headers.map { dict[$0]?.cellText ?? "" })
            }
            return Table(name: name, rows: rows)
        }
        if items.allSatisfy({ if case .array = $0 { return true }; return false }) {
            return Table(name: name, rows: items.map { item in
                if case .array(let cells) = item { return cells.map(\.cellText) }
                return []
            })
        }
        return Table(name: name, rows: [["value"]] + items.map { [$0.cellText] })
    }

    /// 第一列當欄位名稱；看起來像數字／布林的值自動轉型（保留前導 0 的字串，如電話號碼）。
    static func json(from table: Table) -> CVJSONValue {
        guard let header = table.rows.first else { return .array([]) }
        var keys: [String] = []
        var used: [String: Int] = [:]
        for (i, raw) in header.enumerated() {
            var key = raw.trimmingCharacters(in: .whitespaces)
            if key.isEmpty { key = "column\(i + 1)" }
            if let n = used[key] { used[key] = n + 1; key += "_\(n + 1)" } else { used[key] = 1 }
            keys.append(key)
        }
        let objects: [CVJSONValue] = table.rows.dropFirst().map { row in
            var members: [(String, CVJSONValue)] = []
            for (i, key) in keys.enumerated() {
                members.append((key, inferValue(i < row.count ? row[i] : "")))
            }
            if row.count > keys.count {
                for extra in keys.count..<row.count { members.append(("column\(extra + 1)", inferValue(row[extra]))) }
            }
            return .object(members)
        }
        return .array(objects)
    }

    static func inferValue(_ s: String) -> CVJSONValue {
        if s == "true" || s == "TRUE" { return .bool(true) }
        if s == "false" || s == "FALSE" { return .bool(false) }
        if looksNumeric(s) { return .number(s) }
        return .string(s)
    }

    /// 符合 JSON 數字語法、且不是前導 0 的整數（避免把 0912… 這類編號轉成數字）。
    static func looksNumeric(_ s: String) -> Bool {
        guard !s.isEmpty, s.count < 20,
              s.range(of: #"^-?(0|[1-9]\d*)(\.\d+)?([eE][+-]?\d+)?$"#, options: .regularExpression) != nil else { return false }
        return true
    }

    // MARK: - 表格輸出

    static func delimited(_ table: Table, delimiter: Character) -> String {
        var out = ""
        for row in table.rows {
            out += row.map { field -> String in
                let needs = field.contains(delimiter) || field.contains("\"") || field.contains("\n") || field.contains("\r")
                    || field.hasPrefix(" ") || field.hasSuffix(" ")
                return needs ? "\"" + field.replacingOccurrences(of: "\"", with: "\"\"") + "\"" : field
            }.joined(separator: String(delimiter))
            out += "\n"
        }
        return out
    }

    static func htmlTables(_ tables: [Table], title: String) -> String {
        var body = ""
        for table in tables {
            if tables.count > 1 { body += "<h2>\(CVXML.escape(table.name))</h2>\n" }
            body += "<div class=\"wrap\"><table>\n"
            for (i, row) in table.rows.enumerated() {
                let tag = i == 0 ? "th" : "td"
                if i == 0 { body += "<thead>" } else if i == 1 { body += "<tbody>" }
                body += "<tr>" + row.map { cell in
                    let cls = i > 0 && looksNumeric(cell) ? " class=\"num\"" : ""
                    return "<\(tag)\(cls)>\(CVXML.escape(cell).replacingOccurrences(of: "\n", with: "<br>"))</\(tag)>"
                }.joined() + "</tr>\n"
                if i == 0 { body += "</thead>\n" }
            }
            if table.rows.count > 1 { body += "</tbody>" }
            body += "</table></div>\n"
        }
        return """
        <!DOCTYPE html>
        <html lang="zh-Hant">
        <head>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <title>\(CVXML.escape(title))</title>
        <style>
        :root { color-scheme: light dark; }
        body { font: 14px/1.5 -apple-system, "PingFang TC", "Helvetica Neue", sans-serif; margin: 24px; color: #1f2328; background: #fff; }
        h1 { font-size: 20px; margin: 0 0 16px; } h2 { font-size: 16px; margin: 28px 0 10px; }
        .wrap { overflow: auto; border: 1px solid #d0d7de; border-radius: 10px; }
        table { border-collapse: collapse; min-width: 100%; }
        th, td { padding: 7px 12px; border-bottom: 1px solid #e5e8eb; text-align: left; white-space: nowrap; }
        th { position: sticky; top: 0; background: #f6f8fa; font-weight: 600; }
        tbody tr:nth-child(2n) { background: #f9fafb; }
        tbody tr:hover { background: #eef4ff; }
        td.num { text-align: right; font-variant-numeric: tabular-nums; }
        @media (prefers-color-scheme: dark) {
          body { background: #0d1117; color: #e6edf3; }
          .wrap { border-color: #30363d; } th, td { border-color: #21262d; }
          th { background: #161b22; } tbody tr:nth-child(2n) { background: #11161d; } tbody tr:hover { background: #1c2533; }
        }
        </style>
        </head>
        <body>
        <h1>\(CVXML.escape(title))</h1>
        \(body)
        </body>
        </html>
        """
    }

    // MARK: - XLSX 寫入

    private static func columnName(_ index: Int) -> String {
        var n = index + 1
        var name = ""
        while n > 0 {
            let r = (n - 1) % 26
            name = String(UnicodeScalar(UInt8(65 + r))) + name
            n = (n - 1) / 26
        }
        return name
    }

    private static func displayWidth(_ s: String) -> Int {
        var w = 0
        for scalar in s.unicodeScalars { w += scalar.value > 0x2E80 ? 2 : 1 }
        return w
    }

    /// 產生 XLSX（第一列粗體並凍結、欄寬自動、數字存成數值）。
    static func writeXLSX(_ tables: [Table], to out: URL) async throws {
        let scratch = try CVFileNaming.makeScratchDirectory()
        defer { try? FileManager.default.removeItem(at: scratch) }
        let root = scratch.appendingPathComponent("xlsx", isDirectory: true)
        let fm = FileManager.default
        for dir in ["_rels", "docProps", "xl/_rels", "xl/worksheets"] {
            try fm.createDirectory(at: root.appendingPathComponent(dir), withIntermediateDirectories: true)
        }
        func put(_ path: String, _ content: String) throws {
            try Data(content.utf8).write(to: root.appendingPathComponent(path))
        }
        let sheets = tables.isEmpty ? [Table(name: "工作表1", rows: [])] : tables
        var usedNames = Set<String>()
        let sheetNames: [String] = sheets.enumerated().map { i, t in
            var name = t.name.replacingOccurrences(of: #"[\\/\?\*\[\]:]"#, with: "_", options: .regularExpression)
            if name.isEmpty { name = "工作表\(i + 1)" }
            name = String(name.prefix(31))
            var candidate = name
            var n = 2
            while usedNames.contains(candidate.lowercased()) { candidate = String(name.prefix(28)) + " (\(n))"; n += 1 }
            usedNames.insert(candidate.lowercased())
            return candidate
        }

        var overrides = ""
        for i in sheets.indices {
            overrides += "<Override PartName=\"/xl/worksheets/sheet\(i + 1).xml\" ContentType=\"application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml\"/>"
        }
        try put("[Content_Types].xml", """
        <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
        <Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/><Default Extension="xml" ContentType="application/xml"/><Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>\(overrides)<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/><Override PartName="/xl/sharedStrings.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml"/><Override PartName="/docProps/core.xml" ContentType="application/vnd.openxmlformats-package.core-properties+xml"/><Override PartName="/docProps/app.xml" ContentType="application/vnd.openxmlformats-officedocument.extended-properties+xml"/></Types>
        """)
        try put("_rels/.rels", """
        <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
        <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/><Relationship Id="rId2" Type="http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties" Target="docProps/core.xml"/><Relationship Id="rId3" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/extended-properties" Target="docProps/app.xml"/></Relationships>
        """)
        let now = ISO8601DateFormatter().string(from: Date())
        try put("docProps/core.xml", """
        <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
        <cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><dc:creator>QUICK_SEEMD</dc:creator><dcterms:created xsi:type="dcterms:W3CDTF">\(now)</dcterms:created></cp:coreProperties>
        """)
        try put("docProps/app.xml", """
        <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
        <Properties xmlns="http://schemas.openxmlformats.org/officeDocument/2006/extended-properties"><Application>QUICK_SEEMD</Application></Properties>
        """)
        var sheetEntries = ""
        var rels = ""
        for (i, name) in sheetNames.enumerated() {
            sheetEntries += "<sheet name=\"\(CVXML.escape(name))\" sheetId=\"\(i + 1)\" r:id=\"rId\(i + 1)\"/>"
            rels += "<Relationship Id=\"rId\(i + 1)\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet\" Target=\"worksheets/sheet\(i + 1).xml\"/>"
        }
        rels += "<Relationship Id=\"rId\(sheets.count + 1)\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles\" Target=\"styles.xml\"/>"
        rels += "<Relationship Id=\"rId\(sheets.count + 2)\" Type=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships/sharedStrings\" Target=\"sharedStrings.xml\"/>"
        try put("xl/workbook.xml", """
        <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
        <workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><bookViews><workbookView/></bookViews><sheets>\(sheetEntries)</sheets></workbook>
        """)
        try put("xl/_rels/workbook.xml.rels", """
        <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
        <Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">\(rels)</Relationships>
        """)
        try put("xl/styles.xml", """
        <?xml version="1.0" encoding="UTF-8" standalone="yes"?>
        <styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><fonts count="2"><font><sz val="12"/><name val="Calibri"/><family val="2"/></font><font><b/><sz val="12"/><name val="Calibri"/><family val="2"/></font></fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FFEFF2F6"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="2"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>
        """)

        // 共用字串表（Excel、Numbers、系統預覽 都支援）
        var sharedIndex: [String: Int] = [:]
        var sharedList: [String] = []
        var sharedRefs = 0
        func sharedID(_ text: String) -> Int {
            sharedRefs += 1
            if let i = sharedIndex[text] { return i }
            sharedIndex[text] = sharedList.count
            sharedList.append(text)
            return sharedList.count - 1
        }
        for (s, table) in sheets.enumerated() {
            try Task.checkCancellation()
            let columnCount = table.rows.map(\.count).max() ?? 0
            var widths = [Int](repeating: 8, count: columnCount)
            for row in table.rows.prefix(2000) {
                for (c, cell) in row.enumerated() {
                    let longestLine = cell.split(separator: "\n").map { displayWidth(String($0)) }.max() ?? 0
                    widths[c] = min(60, max(widths[c], longestLine + 2))
                }
            }
            var xml = "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n"
            xml += "<worksheet xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\" xmlns:r=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships\">"
            if table.rows.count > 1 {
                xml += "<sheetViews><sheetView workbookViewId=\"0\"><pane ySplit=\"1\" topLeftCell=\"A2\" activePane=\"bottomLeft\" state=\"frozen\"/></sheetView></sheetViews>"
            } else {
                xml += "<sheetViews><sheetView workbookViewId=\"0\"/></sheetViews>"
            }
            if columnCount > 0 {
                xml += "<cols>" + widths.enumerated().map { "<col min=\"\($0.offset + 1)\" max=\"\($0.offset + 1)\" width=\"\($0.element)\" customWidth=\"1\"/>" }.joined() + "</cols>"
            }
            xml += "<sheetData>"
            var chunk = ""
            for (r, row) in table.rows.enumerated() {
                chunk += "<row r=\"\(r + 1)\">"
                for (c, cell) in row.enumerated() where !cell.isEmpty {
                    let ref = "\(columnName(c))\(r + 1)"
                    let style = r == 0 && table.rows.count > 1 ? " s=\"1\"" : ""
                    if r > 0, looksNumeric(cell), Double(cell)?.isFinite == true {
                        chunk += "<c r=\"\(ref)\"\(style)><v>\(cell)</v></c>"
                    } else {
                        chunk += "<c r=\"\(ref)\"\(style) t=\"s\"><v>\(sharedID(cell))</v></c>"
                    }
                }
                chunk += "</row>"
                if chunk.utf8.count > 1_000_000 { xml += chunk; chunk = "" }
            }
            xml += chunk + "</sheetData></worksheet>"
            try put("xl/worksheets/sheet\(s + 1).xml", xml)
        }
        var sst = "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n"
        sst += "<sst xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\" count=\"\(sharedRefs)\" uniqueCount=\"\(sharedList.count)\">"
        for text in sharedList { sst += "<si><t xml:space=\"preserve\">\(CVXML.escape(text))</t></si>" }
        sst += "</sst>"
        try put("xl/sharedStrings.xml", sst)
        try await CVProcess.runChecked("/usr/bin/zip", ["-X", "-r", "-q", out.path, "[Content_Types].xml", "_rels", "docProps", "xl"],
                                       toolName: "zip", currentDirectory: root)
    }

    // MARK: - XLSX 讀取

    static func readXLSX(_ url: URL) async throws -> [Table] {
        let scratch = try CVFileNaming.makeScratchDirectory()
        defer { try? FileManager.default.removeItem(at: scratch) }
        let dir = scratch.appendingPathComponent("x", isDirectory: true)
        try await CVProcess.runChecked("/usr/bin/ditto", ["-x", "-k", url.path, dir.path], toolName: "ditto")
        func load(_ path: String) -> CVXMLNode? {
            guard let data = try? Data(contentsOf: dir.appendingPathComponent(path)) else { return nil }
            return CVXML.parse(data)
        }
        guard let workbook = load("xl/workbook.xml") else { throw ConversionError.unreadable("找不到活頁簿內容，可能不是正確的 XLSX 檔案。") }

        // 關聯：rId → 工作表路徑
        var targets: [String: String] = [:]
        if let rels = load("xl/_rels/workbook.xml.rels") {
            var list: [CVXMLNode] = []
            rels.descendants(named: "Relationship", into: &list)
            for r in list {
                guard let id = r.attribute("Id"), var target = r.attribute("Target") else { continue }
                if target.hasPrefix("/") { target.removeFirst() } else { target = "xl/" + target }
                targets[id] = target
            }
        }
        // 共用字串
        var shared: [String] = []
        if let sst = load("xl/sharedStrings.xml") {
            var items: [CVXMLNode] = []
            sst.descendants(named: "si", into: &items)
            shared = items.map(textOfStringItem)
        }
        // 日期格式
        var dateStyles = Set<Int>()
        if let styles = load("xl/styles.xml") {
            var custom: [Int: String] = [:]
            var fmts: [CVXMLNode] = []
            styles.descendants(named: "numFmt", into: &fmts)
            for f in fmts { if let id = f.attribute("numFmtId").flatMap(Int.init), let code = f.attribute("formatCode") { custom[id] = code } }
            if let cellXfs = styles.children.first?.firstChild(named: "cellXfs") {
                for (i, xf) in cellXfs.children(named: "xf").enumerated() {
                    let id = xf.attribute("numFmtId").flatMap(Int.init) ?? 0
                    if isDateFormat(id: id, code: custom[id]) { dateStyles.insert(i) }
                }
            }
        }
        var date1904 = false
        var pr: [CVXMLNode] = []
        workbook.descendants(named: "workbookPr", into: &pr)
        if let v = pr.first?.attribute("date1904") { date1904 = v == "1" || v == "true" }

        var sheetNodes: [CVXMLNode] = []
        workbook.descendants(named: "sheet", into: &sheetNodes)
        var tables: [Table] = []
        for (i, sheet) in sheetNodes.enumerated() {
            try Task.checkCancellation()
            let name = sheet.attribute("name") ?? "工作表\(i + 1)"
            let path = sheet.attribute("id").flatMap { targets[$0] } ?? "xl/worksheets/sheet\(i + 1).xml"
            guard let doc = load(path) else { continue }
            var rowsNodes: [CVXMLNode] = []
            doc.descendants(named: "row", into: &rowsNodes)
            var grid: [Int: [Int: String]] = [:]
            var maxRow = -1, maxCol = -1
            var nextRow = 0
            for rowNode in rowsNodes {
                let r = (rowNode.attribute("r").flatMap(Int.init) ?? (nextRow + 1)) - 1
                nextRow = r + 1
                var nextCol = 0
                for c in rowNode.children(named: "c") {
                    let col = c.attribute("r").map(columnIndex) ?? nextCol
                    nextCol = col + 1
                    let type = c.attribute("t") ?? "n"
                    var value = ""
                    switch type {
                    case "s":
                        if let idx = c.firstChild(named: "v").flatMap({ Int($0.text.trimmingCharacters(in: .whitespaces)) }), idx < shared.count {
                            value = shared[idx]
                        }
                    case "inlineStr":
                        value = c.firstChild(named: "is").map(textOfStringItem) ?? ""
                    case "b":
                        value = c.firstChild(named: "v")?.text == "1" ? "TRUE" : "FALSE"
                    default:
                        value = c.firstChild(named: "v")?.text ?? ""
                        if type == "n", let s = c.attribute("s").flatMap(Int.init), dateStyles.contains(s), let serial = Double(value) {
                            value = formatSerialDate(serial, date1904: date1904)
                        }
                    }
                    if value.isEmpty { continue }
                    grid[r, default: [:]][col] = value
                    maxRow = max(maxRow, r)
                    maxCol = max(maxCol, col)
                }
            }
            var rows: [[String]] = []
            if maxRow >= 0 {
                for r in 0...maxRow {
                    let cells = grid[r] ?? [:]
                    rows.append((0...max(maxCol, 0)).map { cells[$0] ?? "" })
                }
            }
            // 去掉每列尾端多餘的空白欄
            let usedCols = rows.map { row in (row.lastIndex { !$0.isEmpty } ?? -1) + 1 }.max() ?? 0
            rows = rows.map { Array($0.prefix(usedCols)) }
            tables.append(Table(name: name, rows: rows))
        }
        if tables.isEmpty { throw ConversionError.emptyContent("活頁簿中沒有工作表。") }
        return tables
    }

    private static func textOfStringItem(_ node: CVXMLNode) -> String {
        // <si><t>…</t></si> 或 <si><r><t>…</t></r>…</si>；略過注音／振假名 <rPh>
        var out = ""
        for child in node.children {
            switch child.localName {
            case "t": out += child.text
            case "r": out += child.children(named: "t").map(\.text).joined()
            default: break
            }
        }
        return out
    }

    private static func columnIndex(_ ref: String) -> Int {
        var n = 0
        for scalar in ref.unicodeScalars {
            guard scalar.value >= 65 && scalar.value <= 90 else { break }
            n = n * 26 + Int(scalar.value - 64)
        }
        return max(0, n - 1)
    }

    private static func isDateFormat(id: Int, code: String?) -> Bool {
        if (14...22).contains(id) || (27...36).contains(id) || (45...47).contains(id) || (50...58).contains(id) { return true }
        guard let code else { return false }
        // 去掉引號字串與 [顏色]，再看有沒有 y、m、d、h、s
        let stripped = code.replacingOccurrences(of: #""[^"]*""#, with: "", options: .regularExpression)
            .replacingOccurrences(of: #"\[[^\]]*\]"#, with: "", options: .regularExpression).lowercased()
        return stripped.range(of: "[ymdhs]", options: .regularExpression) != nil && !stripped.contains("0.0")
    }

    private static func formatSerialDate(_ serial: Double, date1904: Bool) -> String {
        let base = date1904 ? DateComponents(year: 1904, month: 1, day: 1) : DateComponents(year: 1899, month: 12, day: 30)
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = TimeZone(identifier: "UTC")!
        guard let start = cal.date(from: base) else { return String(serial) }
        let date = start.addingTimeInterval((serial * 86_400).rounded())
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = cal.timeZone
        let hasTime = serial != serial.rounded(.down)
        if serial < 1 && hasTime { f.dateFormat = "HH:mm:ss" }
        else { f.dateFormat = hasTime ? "yyyy-MM-dd HH:mm:ss" : "yyyy-MM-dd" }
        return f.string(from: date)
    }

    // MARK: - 入口

    static func convert(_ input: URL, profile: CVInputProfile, to format: ConversionFormat, options: ConversionOptions,
                        workDir: URL) async throws -> [URL] {
        guard let kind = profile.dataKind else { throw ConversionError.unsupportedTarget(source: input.lastPathComponent, target: format.displayName) }
        let base = CVFileNaming.baseName(of: input)
        let out = workDir.appendingPathComponent(CVFileNaming.fileName(base, format.fileExtension))
        func save(_ text: String, to url: URL = out) throws { try Data(text.utf8).write(to: url) }

        switch kind {
        case .json, .plist:
            let value = kind == .json ? try readJSON(input, options: options) : try readPlist(input)
            switch format.id {
            case "json", "json-pretty": try save(CVJSONWriter.write(value, pretty: true) + "\n")
            case "json-min": try save(CVJSONWriter.write(value, pretty: false))
            case "plist": try save(CVPlistBridge.xmlPlist(value))
            case "bplist":
                if case .null = value { throw ConversionError.invalidData("plist 不支援 null 作為根節點。") }
                let data = try PropertyListSerialization.data(fromPropertyList: CVPlistBridge.foundationObject(value), format: .binary, options: 0)
                try data.write(to: out)
            case "yaml": try save(CVYAMLWriter.write(value))
            case "csv": try save(delimited(try table(from: value, name: base), delimiter: ","))
            case "tsv": try save(delimited(try table(from: value, name: base), delimiter: "\t"))
            case "xlsx": try await writeXLSX([try table(from: value, name: base)], to: out)
            case "html-table": try save(htmlTables([try table(from: value, name: base)], title: base))
            default: throw ConversionError.unsupportedTarget(source: input.lastPathComponent, target: format.displayName)
            }
            return [out]

        case .csv, .tsv:
            let table = try readDelimited(input, tsv: kind == .tsv, options: options)
            switch format.id {
            case "json", "json-pretty": try save(CVJSONWriter.write(json(from: table), pretty: true) + "\n")
            case "json-min": try save(CVJSONWriter.write(json(from: table), pretty: false))
            case "yaml": try save(CVYAMLWriter.write(json(from: table)))
            case "csv": try save(delimited(table, delimiter: ","))
            case "tsv": try save(delimited(table, delimiter: "\t"))
            case "xlsx": try await writeXLSX([table], to: out)
            case "html-table": try save(htmlTables([table], title: base))
            default: throw ConversionError.unsupportedTarget(source: input.lastPathComponent, target: format.displayName)
            }
            return [out]

        case .xlsx:
            let tables = try await readXLSX(input)
            switch format.id {
            case "csv", "tsv":
                let delimiter: Character = format.id == "csv" ? "," : "\t"
                if tables.count == 1 {
                    try save(delimited(tables[0], delimiter: delimiter))
                    return [out]
                }
                let folder = workDir.appendingPathComponent("\(base)-\(format.id.uppercased())", isDirectory: true)
                try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
                for t in tables {
                    let file = CVFileNaming.uniqueURL(in: folder, fileName: CVFileNaming.fileName(CVFileNaming.sanitize(t.name), format.id))
                    try save(delimited(t, delimiter: delimiter), to: file)
                }
                return [folder]
            case "json", "json-pretty", "json-min", "yaml":
                let value: CVJSONValue = tables.count == 1 ? json(from: tables[0]) : .object(tables.map { ($0.name, json(from: $0)) })
                switch format.id {
                case "yaml": try save(CVYAMLWriter.write(value))
                case "json-min": try save(CVJSONWriter.write(value, pretty: false))
                default: try save(CVJSONWriter.write(value, pretty: true) + "\n")
                }
                return [out]
            case "html-table":
                try save(htmlTables(tables, title: base))
                return [out]
            default:
                throw ConversionError.unsupportedTarget(source: input.lastPathComponent, target: format.displayName)
            }

        case .yaml:
            throw ConversionError.unsupportedTarget(source: input.lastPathComponent, target: format.displayName)
        }
    }
}
