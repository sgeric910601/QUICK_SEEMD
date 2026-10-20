import Foundation
import SQLite3

/// SQLite 資料庫：列出資料表、欄位結構與前 100 筆資料（唯讀開啟，不會改動檔案）。
enum QSSQLite {
    static let rowLimit = 100
    static let tableLimit = 40

    static func isSQLite(_ head: Data) -> Bool {
        head.count >= 16 && head.prefix(16) == Data("SQLite format 3\0".utf8)
    }

    static func render(file: QSFile) -> QSPage? {
        var db: OpaquePointer?
        let uri = file.url.absoluteString + "?mode=ro&immutable=1"
        guard sqlite3_open_v2(uri, &db, SQLITE_OPEN_READONLY | SQLITE_OPEN_URI, nil) == SQLITE_OK, let db else {
            sqlite3_close(db)
            return nil
        }
        defer { sqlite3_close(db) }

        func query(_ sql: String, _ row: (OpaquePointer) -> Void) -> Bool {
            var stmt: OpaquePointer?
            guard sqlite3_prepare_v2(db, sql, -1, &stmt, nil) == SQLITE_OK, let stmt else { return false }
            defer { sqlite3_finalize(stmt) }
            while sqlite3_step(stmt) == SQLITE_ROW { row(stmt) }
            return true
        }
        func text(_ s: OpaquePointer, _ i: Int32) -> String {
            guard let c = sqlite3_column_text(s, i) else { return "" }
            return String(cString: c)
        }
        func quoteIdent(_ s: String) -> String { "\"" + s.replacingOccurrences(of: "\"", with: "\"\"") + "\"" }

        var objects: [(type: String, name: String, sql: String)] = []
        let ok = query("SELECT type, name, IFNULL(sql,'') FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' ORDER BY type, name") { s in
            objects.append((text(s, 0), text(s, 1), text(s, 2)))
        }
        guard ok else { return nil }
        var indexCount = 0
        _ = query("SELECT COUNT(*) FROM sqlite_master WHERE type='index'") { s in indexCount = Int(sqlite3_column_int64(s, 0)) }

        var page = QSPage(kind: "SQLite 資料庫")
        page.size = CGSize(width: 1000, height: 720)
        let tables = objects.filter { $0.type == "table" }.count
        let views = objects.count - tables
        page.chips = [QSChip("\(tables) 個資料表"), QSChip("\(views) 個檢視"), QSChip("\(indexCount) 個索引")]

        if objects.isEmpty {
            page.body = "<div class=\"empty\">資料庫裡沒有資料表</div>"
            return page
        }

        var html = "<nav class=\"toc\">"
        for (i, o) in objects.enumerated() { html += "<a href=\"#t\(i)\">\(o.type == "view" ? "👁 " : "")\(QSHTML.escape(o.name))</a>" }
        html += "</nav>"

        for (i, o) in objects.prefix(tableLimit).enumerated() {
            let q = quoteIdent(o.name)
            var count: Int64 = -1
            _ = query("SELECT COUNT(*) FROM \(q)") { s in count = sqlite3_column_int64(s, 0) }
            var columns: [(name: String, type: String, pk: Bool)] = []
            _ = query("PRAGMA table_info(\(q))") { s in columns.append((text(s, 1), text(s, 2), sqlite3_column_int(s, 5) > 0)) }

            html += "<div class=\"tbl-title\" id=\"t\(i)\"><h2>\(QSHTML.escape(o.name))</h2><span class=\"sub\">\(o.type == "view" ? "檢視" : "資料表") · \(count >= 0 ? QSFormat.number(Int(count)) + " 筆" : "?") · \(columns.count) 欄</span></div>"
            html += "<div class=\"scroll-x\"><table class=\"grid\"><thead><tr><th class=\"rn\"></th>"
            for c in columns {
                html += "<th>\(c.pk ? "🔑 " : "")\(QSHTML.escape(c.name))<span class=\"ctype\">\(QSHTML.escape(c.type.isEmpty ? "ANY" : c.type))</span></th>"
            }
            html += "</tr></thead><tbody>"
            var n = 0
            _ = query("SELECT * FROM \(q) LIMIT \(rowLimit)") { s in
                n += 1
                html += "<tr><td class=\"rn\">\(n)</td>"
                for ci in 0..<sqlite3_column_count(s) {
                    switch sqlite3_column_type(s, ci) {
                    case SQLITE_NULL: html += "<td class=\"null\">NULL</td>"
                    case SQLITE_INTEGER: html += "<td class=\"num\">\(sqlite3_column_int64(s, ci))</td>"
                    case SQLITE_FLOAT: html += "<td class=\"num\">\(sqlite3_column_double(s, ci))</td>"
                    case SQLITE_BLOB: html += "<td class=\"null\">BLOB · \(QSFormat.bytes(Int64(sqlite3_column_bytes(s, ci))))</td>"
                    default:
                        let v = text(s, ci)
                        html += "<td>\(QSHTML.escape(v.count > 300 ? String(v.prefix(300)) + "…" : v))</td>"
                    }
                }
                html += "</tr>"
            }
            if n == 0 { html += "<tr><td class=\"rn\"></td><td class=\"null\" colspan=\"\(max(1, columns.count))\">（沒有資料）</td></tr>" }
            html += "</tbody></table></div>"
            if count > Int64(rowLimit) { html += "<div class=\"schema\" style=\"margin-top:6px\">只顯示前 \(rowLimit) 筆</div>" }
            if !o.sql.isEmpty { html += "<details class=\"schema\"><summary>結構定義（SQL）</summary>\(QSHighlighter.inline(o.sql, language: QSLanguages.byID["sql"]!))</details>" }
        }
        if objects.count > tableLimit {
            page.notices.append(QSNotice("資料表太多，只顯示前 \(tableLimit) 個的內容"))
        }
        page.body = html
        return page
    }
}
