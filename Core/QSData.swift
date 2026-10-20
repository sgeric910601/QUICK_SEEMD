import Foundation

/// CSV / TSV 解析與表格輸出（固定表頭、固定列號、斑馬紋）。
enum QSCSV {
    static let maxRows = 2000

    static func detectDelimiter(_ b: [UInt8], ext: String) -> UInt8 {
        if ext == "tsv" || ext == "tab" { return 9 }
        let candidates: [UInt8] = [44, 9, 59, 124]
        var best: UInt8 = 44
        var bestScore = -1
        for d in candidates {
            var counts: [Int] = []
            var current = 0
            var inQuote = false
            var i = 0
            while i < b.count && counts.count < 20 {
                let c = b[i]
                if c == 34 { inQuote.toggle() }
                else if !inQuote && c == d { current += 1 }
                else if !inQuote && c == 10 { counts.append(current); current = 0 }
                i += 1
            }
            if current > 0 { counts.append(current) }
            guard let first = counts.first, first > 0 else { continue }
            let consistent = counts.filter { $0 == first }.count
            let score = consistent * 100 + first
            if score > bestScore { bestScore = score; best = d }
        }
        return best
    }

    static func parse(_ b: [UInt8], delimiter: UInt8, maxRows: Int) -> (rows: [[String]], total: Int) {
        var rows: [[String]] = []
        var row: [String] = []
        var field: [UInt8] = []
        var total = 0
        var i = 0
        var inQuote = false
        var storing = true
        func endField() { if storing { row.append(String(decoding: field, as: UTF8.self)) }; field.removeAll(keepingCapacity: true) }
        func endRow() {
            endField()
            if storing { rows.append(row) }
            row.removeAll(keepingCapacity: true)
            total += 1
            if total >= maxRows { storing = false }
        }
        while i < b.count {
            let c = b[i]
            if inQuote {
                if c == 34 {
                    if i + 1 < b.count && b[i + 1] == 34 { if storing { field.append(34) }; i += 2; continue }
                    inQuote = false
                } else if storing { field.append(c) }
            } else if c == 34 && field.isEmpty {
                inQuote = true
            } else if c == delimiter {
                endField()
            } else if c == 10 {
                endRow()
            } else if c != 13 {
                if storing { field.append(c) }
            }
            i += 1
        }
        if !field.isEmpty || !row.isEmpty { endRow() }
        return (rows, total)
    }

    static func render(text: String, file: QSFile, truncatedFile: Bool) -> QSPage {
        let bytes = Array(text.utf8)
        let delim = detectDelimiter(bytes, ext: file.ext)
        let (rows, total) = parse(bytes, delimiter: delim, maxRows: maxRows + 1)
        var page = QSPage(kind: delim == 9 ? "TSV 表格" : "CSV 表格")
        page.size = CGSize(width: 1000, height: 700)
        guard let header = rows.first else {
            page.body = "<div class=\"empty\">空白的表格</div>"
            return page
        }
        let dataRows = rows.dropFirst()
        let columns = rows.map(\.count).max() ?? 0
        let delimName = [44: "逗號", 9: "Tab", 59: "分號", 124: "直線 |"][Int(delim)] ?? "?"
        page.chips = [QSChip("\(QSFormat.number(max(0, total - 1))) 列 × \(columns) 欄"), QSChip("分隔：\(delimName)")]
        if total - 1 > maxRows || truncatedFile {
            page.notices.append(QSNotice("只顯示前 \(QSFormat.number(min(maxRows, dataRows.count))) 列\(truncatedFile ? "（檔案較大，僅讀取開頭部分）" : "")"))
        }

        // 判斷數字欄
        var numeric = [Bool](repeating: false, count: columns)
        for col in 0..<columns {
            var nums = 0, nonEmpty = 0
            for r in dataRows.prefix(200) where col < r.count {
                let v = r[col].trimmingCharacters(in: .whitespaces)
                if v.isEmpty { continue }
                nonEmpty += 1
                if Double(v.replacingOccurrences(of: ",", with: "").replacingOccurrences(of: "%", with: "")) != nil { nums += 1 }
            }
            numeric[col] = nonEmpty > 0 && nums * 10 >= nonEmpty * 8
        }

        var h = "<div class=\"grid-wrap\"><table class=\"grid\"><thead><tr><th class=\"rn\"></th>"
        for col in 0..<columns {
            let name = col < header.count ? header[col] : ""
            h += "<th>\(QSHTML.escape(name))<span class=\"ctype\">\(numeric[col] ? "123" : "abc")</span></th>"
        }
        h += "</tr></thead><tbody>"
        for (idx, r) in dataRows.prefix(maxRows).enumerated() {
            h += "<tr><td class=\"rn\">\(idx + 1)</td>"
            for col in 0..<columns {
                let v = col < r.count ? r[col] : ""
                h += numeric[col] ? "<td class=\"num\">" : "<td>"
                h += QSHTML.escape(v.count > 500 ? String(v.prefix(500)) + "…" : v)
                h += "</td>"
            }
            h += "</tr>"
        }
        h += "</tbody></table></div>"
        page.body = h
        return page
    }
}

/// JSON 工具：壓縮過的 JSON 自動排版、驗證、摘要。
enum QSJSON {
    static func looksMinified(_ text: String) -> Bool {
        let n = text.utf8.count
        guard n > 200 else { return false }
        let newlines = text.utf8.reduce(0) { $0 + ($1 == 10 ? 1 : 0) }
        return newlines < 3 || n / max(newlines, 1) > 400
    }

    static func prettyPrint(_ text: String) -> String {
        let b = Array(text.utf8)
        var out: [UInt8] = []
        out.reserveCapacity(b.count * 2)
        var indent = 0
        var inStr = false
        var esc = false
        func newline() { out.append(10); out.append(contentsOf: [UInt8](repeating: 32, count: indent * 2)) }
        var i = 0
        while i < b.count {
            let c = b[i]
            if inStr {
                out.append(c)
                if esc { esc = false } else if c == 92 { esc = true } else if c == 34 { inStr = false }
                i += 1; continue
            }
            switch c {
            case 34: inStr = true; out.append(c)
            case 123, 91:
                var j = i + 1
                while j < b.count && (b[j] == 32 || b[j] == 9 || b[j] == 10 || b[j] == 13) { j += 1 }
                if j < b.count && (b[j] == 125 || b[j] == 93) { out.append(c); out.append(b[j]); i = j + 1; continue }
                out.append(c); indent += 1; newline()
            case 125, 93: indent = max(0, indent - 1); newline(); out.append(c)
            case 44: out.append(c); newline()
            case 58: out.append(58); out.append(32)
            case 32, 9, 10, 13: break
            default: out.append(c)
            }
            i += 1
        }
        return String(decoding: out, as: UTF8.self)
    }

    /// 回傳 (摘要, 錯誤訊息)
    static func inspect(_ text: String) -> (summary: String?, error: String?) {
        guard let data = text.data(using: .utf8) else { return (nil, nil) }
        do {
            let obj = try JSONSerialization.jsonObject(with: data, options: [.fragmentsAllowed])
            if let d = obj as? [String: Any] { return ("物件 · \(QSFormat.number(d.count)) 個鍵", nil) }
            if let a = obj as? [Any] { return ("陣列 · \(QSFormat.number(a.count)) 個項目", nil) }
            return ("單一值", nil)
        } catch {
            let ns = error as NSError
            let detail = (ns.userInfo["NSDebugDescription"] as? String) ?? ns.localizedDescription
            return (nil, detail)
        }
    }
}
