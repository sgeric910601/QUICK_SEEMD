import AppKit
import PDFKit
import WebKit

// macOS 內建引擎：Word／Excel／PowerPoint → PDF（不需要安裝 Office）。
// - Word：系統預覽 產生的 HTML 交給 WebKit 依紙張分頁列印；RTF／ODT 等改用系統文字引擎（NSAttributedString）。
// - Excel：每個工作表各自載入，依欄列邊界切頁（可縮放成一頁寬），向量輸出。
// - PowerPoint：每張投影片擷取成一頁，頁面比例與投影片相同。

enum OTBuiltIn {
    struct SheetOptions: Sendable, Equatable {
        var orientation: String = "auto"   // auto / portrait / landscape
        var fitWidth = true
        var firstSheetOnly = false
        var gridlines = false
    }

    static let systemPreviewWordExtensions: Set<String> = ["doc", "docx", "docm", "dot", "dotx", "dotm"]
    static let systemPreviewSheetExtensions: Set<String> = ["xls", "xlsx", "xlsm", "xltx"]
    static let systemPreviewSlideExtensions: Set<String> = ["ppt", "pptx", "pptm", "pps", "ppsx", "potx"]

    // MARK: - Word → PDF

    static func wordToPDF(_ input: URL, context: ToolContext) async throws -> URL {
        let work = try CVFileNaming.makeScratchDirectory()
        let base = CVFileNaming.sanitize(CVFileNaming.baseName(of: input))
        let out = work.appendingPathComponent(CVFileNaming.fileName(base, "pdf"))
        if systemPreviewWordExtensions.contains(OTKind.ext(input)) {
            context.progress(0.1, "正在解析文件…")
            if let preview = try await OTSystemPreview.preview(input, work: work.appendingPathComponent("q", isDirectory: true)) {
                try context.checkCancellation()
                context.progress(0.45, "正在排版…")
                do {
                    try await renderDocument(preview, to: out)
                    if let doc = PDFDocument(url: out), doc.pageCount > 0 { return out }
                } catch {
                    try? FileManager.default.removeItem(at: out)
                }
            }
        }
        // 後援：系統文字引擎（RTF、ODT 以及 系統預覽 不支援的檔案）
        context.progress(0.4, "正在排版…")
        let alt = work.appendingPathComponent("alt", isDirectory: true)
        try FileManager.default.createDirectory(at: alt, withIntermediateDirectories: true)
        let produced = try await ConversionEngine.convert(input, toFormatID: "pdf", outputDirectory: alt)
        guard let pdf = produced.first else { throw ConversionError.writeFailed("沒有產生 PDF。") }
        try FileManager.default.moveItem(at: pdf, to: out)
        return out
    }

    @MainActor
    static func renderDocument(_ dir: URL, to out: URL) async throws {
        let source = dir.appendingPathComponent("Preview.html")
        let html = try String(contentsOf: source, encoding: .utf8)
        let printable = dir.appendingPathComponent("QUICK_SEEMD-Print.html")
        try Data(OTFontFallback.patch(html).utf8).write(to: printable)

        let renderer = await OTWebRenderer(width: 640, height: 900)
        defer { renderer.close() }
        try await renderer.load(file: printable, readAccess: dir)
        // 頁面容器（系統預覽 以 padding 表示邊界、min-height 表示紙張高度）
        let geometry = await renderer.json("""
        (function(){var d=document.body.firstElementChild;while(d&&d.tagName!=='DIV'){d=d.nextElementSibling}
        if(!d){return JSON.stringify({})}var c=getComputedStyle(d);
        return JSON.stringify({w:parseFloat(c.width)||0,pl:parseFloat(c.paddingLeft)||0,pr:parseFloat(c.paddingRight)||0,
        pt:parseFloat(c.paddingTop)||0,pb:parseFloat(c.paddingBottom)||0,mh:parseFloat(c.minHeight)||0})})()
        """) as? [String: Double] ?? [:]
        var width = CGFloat(geometry["w"] ?? 0)
        var left = CGFloat(geometry["pl"] ?? 72), right = CGFloat(geometry["pr"] ?? 72)
        var top = CGFloat(geometry["pt"] ?? 72), bottom = CGFloat(geometry["pb"] ?? 72)
        func sane(_ v: inout CGFloat) { if v < 14 || v > 200 { v = 54 } }
        sane(&left); sane(&right); sane(&top); sane(&bottom)
        if width < 200 || width > 1600 { width = OTPaper.preferred.width - left - right }
        let paperWidth = width + left + right
        var paperHeight = CGFloat(geometry["mh"] ?? 0)
        if paperHeight < paperWidth * 0.5 || paperHeight > paperWidth * 2.2 {
            paperHeight = paperWidth * OTPaper.a4.height / OTPaper.a4.width
        }
        let paper = OTPaper.snap(CGSize(width: paperWidth, height: paperHeight))
        let contentWidth = paper.width - left - right

        await renderer.addStyle("""
        html,body{margin:0!important;padding:0!important;background:#fff!important}
        body{font-family:\(OTFontFallback.defaultStack)}
        body>div{padding:0!important;min-height:0!important;width:auto!important;max-width:none!important;margin:0!important;overflow:visible!important;box-shadow:none!important}
        *{-webkit-print-color-adjust:exact!important;print-color-adjust:exact!important}
        """)
        renderer.resize(width: contentWidth, height: paper.height)
        try? await Task.sleep(nanoseconds: 100_000_000)
        // 自己分頁：在文字行、圖片、表格列之間切開，再排到紙張上（向量）
        let printableHeight = paper.height - top - bottom
        let breaks = await renderer.pageBreaks(pageHeight: printableHeight)
        guard !breaks.isEmpty else { throw ConversionError.emptyContent("文件沒有內容。") }
        var pages: [(paper: CGSize, items: [OTPageComposer.Placement])] = []
        for (y0, y1) in breaks {
            let rect = CGRect(x: 0, y: y0, width: contentWidth, height: max(1, y1 - y0))
            let page = try await renderer.capture(rect)
            pages.append((paper, [OTPageComposer.Placement(page: page, frame: CGRect(x: left, y: top, width: rect.width, height: rect.height))]))
        }
        try OTPageComposer.write(pages: pages, to: out)
    }

    // MARK: - Excel → PDF

    static func excelToPDF(_ input: URL, options: SheetOptions, context: ToolContext) async throws -> URL {
        let work = try CVFileNaming.makeScratchDirectory()
        let base = CVFileNaming.sanitize(CVFileNaming.baseName(of: input))
        let out = work.appendingPathComponent(CVFileNaming.fileName(base, "pdf"))
        let ext = OTKind.ext(input)
        var sheets: [OTSheetSource] = []
        context.progress(0.05, "正在讀取試算表…")
        if ext == "csv" || ext == "tsv" {
            let table = try CVDataConverter.readDelimited(input, tsv: ext == "tsv", options: ConversionOptions())
            sheets = [OTSheetSource(name: base, file: nil, html: OTSheetHTML.document([table], gridlines: options.gridlines), baseURL: nil)]
        } else if systemPreviewSheetExtensions.contains(ext), let preview = try await OTSystemPreview.preview(input, work: work.appendingPathComponent("q", isDirectory: true)) {
            sheets = OTSheetHTML.sheets(in: preview)
        } else if ext == "xlsx" || ext == "xlsm" {
            // 系統預覽 失敗時：只取儲存格內容
            let tables = try await OTSpreadsheet.readXLSX(input)
            sheets = tables.map { OTSheetSource(name: $0.name, file: nil, html: OTSheetHTML.document([$0], gridlines: options.gridlines), baseURL: nil) }
        } else {
            throw ConversionError.unreadable("macOS 內建引擎無法讀取 .\(ext) 試算表，請改用 Microsoft Excel 或 LibreOffice。")
        }
        if options.firstSheetOnly { sheets = Array(sheets.prefix(1)) }
        var pages: [(paper: CGSize, items: [OTPageComposer.Placement])] = []
        for (i, sheet) in sheets.enumerated() {
            try context.checkCancellation()
            context.progress(0.15 + 0.75 * Double(i) / Double(max(sheets.count, 1)), "正在排版工作表「\(sheet.name)」…")
            pages += try await renderSheet(sheet, options: options)
        }
        guard !pages.isEmpty else { throw ConversionError.emptyContent("所有工作表都是空的。") }
        try OTPageComposer.write(pages: pages, to: out)
        return out
    }

    @MainActor
    static func renderSheet(_ sheet: OTSheetSource, options: SheetOptions) async throws -> [(paper: CGSize, items: [OTPageComposer.Placement])] {
        let renderer = await OTWebRenderer(width: 1400, height: 900)
        defer { renderer.close() }
        if let file = sheet.file {
            var html = try String(contentsOf: file, encoding: .utf8)
            html = OTFontFallback.patch(html)
            let printable = file.deletingLastPathComponent().appendingPathComponent("QUICK_SEEMD-" + file.lastPathComponent)
            try Data(html.utf8).write(to: printable)
            try await renderer.load(file: printable, readAccess: file.deletingLastPathComponent())
        } else {
            try await renderer.load(html: sheet.html ?? "", baseURL: sheet.baseURL)
        }
        var css = """
        html,body{margin:0!important;padding:0!important;background:#fff!important}
        body{font-family:\(OTFontFallback.defaultStack)}
        body>div{position:static!important}
        *{-webkit-print-color-adjust:exact!important;print-color-adjust:exact!important}
        """
        // 系統預覽 只在螢幕上畫格線；這裡依選項決定要不要印
        css += options.gridlines
            ? "\ntable.worksheet td,table.worksheet th{border:thin solid #c9ccd6}"
            : "\ntable.worksheet.showGridlines td,table.worksheet.showGridlines th,table.worksheet.showGridlines{border-color:transparent}"
        await renderer.addStyle(css)
        let info = await renderer.json("""
        (function(){var t=document.querySelector('table');var sx=window.scrollX,sy=window.scrollY;
        var text=(document.body.innerText||'').replace(/\\s+/g,'');var hasImg=!!document.querySelector('img,svg,canvas');
        var rows=[],cuts=[];if(t){var trs=t.rows;for(var i=0;i<trs.length;i++){var b=trs[i].getBoundingClientRect();rows.push(b.bottom+sy);
        if(i<300){var cs=trs[i].cells;for(var j=0;j<cs.length;j++){cuts.push(cs[j].getBoundingClientRect().right+sx)}}}}
        var w=0,h=0;var els=document.querySelectorAll('table,img,svg,canvas');
        for(var k=0;k<els.length;k++){var r=els[k].getBoundingClientRect();if(r.width>0&&r.height>0){w=Math.max(w,r.right+sx);h=Math.max(h,r.bottom+sy)}}
        if(w===0){w=document.body.scrollWidth;h=document.body.scrollHeight}
        return JSON.stringify({w:w,h:h,rows:rows,cuts:cuts,empty:(text.length===0&&!hasImg)})})()
        """) as? [String: Any] ?? [:]
        if (info["empty"] as? Bool) == true { return [] }
        let contentW = CGFloat((info["w"] as? Double) ?? 0)
        let contentH = CGFloat((info["h"] as? Double) ?? 0)
        guard contentW > 1, contentH > 1 else { return [] }
        let rowCuts = ((info["rows"] as? [Double]) ?? []).map { CGFloat($0) }.sorted()
        let colCuts = Array(Set(((info["cuts"] as? [Double]) ?? []).map { CGFloat($0.rounded()) })).sorted()

        let margin: CGFloat = 36
        var paper = OTPaper.portrait(OTPaper.preferred)
        switch options.orientation {
        case "landscape": paper = OTPaper.landscape(paper)
        case "portrait": break
        default:
            if contentW > paper.width - 2 * margin { paper = OTPaper.landscape(paper) }
        }
        let printableW = paper.width - 2 * margin
        let printableH = paper.height - 2 * margin
        let scale = options.fitWidth ? min(1, printableW / contentW) : 1
        let bandW = printableW / scale
        let bandH = printableH / scale

        // 依邊界切成區塊：盡量在欄／列交界處分頁
        func bands(total: CGFloat, size: CGFloat, cuts: [CGFloat]) -> [(CGFloat, CGFloat)] {
            var result: [(CGFloat, CGFloat)] = []
            var start: CGFloat = 0
            while start < total - 0.5 {
                var end = min(total, start + size)
                if end < total {
                    if let cut = cuts.last(where: { $0 > start + size * 0.25 && $0 <= start + size + 0.5 }) { end = cut }
                }
                result.append((start, end))
                start = end
            }
            return result
        }
        let columns = bands(total: contentW, size: bandW, cuts: colCuts)
        let rows = bands(total: contentH, size: bandH, cuts: rowCuts)
        var pages: [(paper: CGSize, items: [OTPageComposer.Placement])] = []
        renderer.resize(width: max(contentW, 200), height: 900)
        // 先往下、再往右（與 Excel 預設的列印順序相同）
        for column in columns {
            for row in rows {
                let rect = CGRect(x: column.0, y: row.0, width: column.1 - column.0, height: row.1 - row.0)
                let page = try await renderer.capture(rect)
                let frame = CGRect(x: margin, y: margin, width: rect.width * scale, height: rect.height * scale)
                pages.append((paper, [OTPageComposer.Placement(page: page, frame: frame)]))
            }
        }
        return pages
    }

    // MARK: - PowerPoint → PDF

    static func powerpointToPDF(_ input: URL, context: ToolContext) async throws -> URL {
        let work = try CVFileNaming.makeScratchDirectory()
        let base = CVFileNaming.sanitize(CVFileNaming.baseName(of: input))
        let out = work.appendingPathComponent(CVFileNaming.fileName(base, "pdf"))
        guard systemPreviewSlideExtensions.contains(OTKind.ext(input)) else {
            throw ConversionError.unreadable("macOS 內建引擎無法讀取 .\(OTKind.ext(input)) 簡報，請改用 Microsoft PowerPoint 或 LibreOffice。")
        }
        context.progress(0.1, "正在解析簡報…")
        guard let preview = try await OTSystemPreview.preview(input, work: work.appendingPathComponent("q", isDirectory: true)) else {
            throw ConversionError.unreadable("系統無法解析這份簡報，檔案可能已損毀或受密碼保護。")
        }
        try context.checkCancellation()
        context.progress(0.4, "正在輸出投影片…")
        try await renderSlides(preview, to: out)
        return out
    }

    @MainActor
    static func renderSlides(_ dir: URL, to out: URL) async throws {
        let source = dir.appendingPathComponent("Preview.html")
        let html = try String(contentsOf: source, encoding: .utf8)
        let printable = dir.appendingPathComponent("QUICK_SEEMD-Print.html")
        try Data(OTFontFallback.patch(html).utf8).write(to: printable)
        let renderer = await OTWebRenderer(width: 1000, height: 700)
        defer { renderer.close() }
        try await renderer.load(file: printable, readAccess: dir)
        // 大型簡報的投影片可能是陸續載入的
        for _ in 0..<100 {
            let pending = (try? await renderer.evaluate("document.querySelectorAll('div.loading-slide').length")) as? Int ?? 0
            if pending == 0 { break }
            try await Task.sleep(nanoseconds: 100_000_000)
        }
        await renderer.addStyle("""
        html,body{margin:0!important;padding:0!important;background:#fff!important}
        div.slide{margin:0!important;-webkit-box-shadow:none!important;box-shadow:none!important}
        div.loading-slide{display:none!important}
        *{-webkit-print-color-adjust:exact!important;print-color-adjust:exact!important}
        """)
        let rects = (await renderer.json("""
        JSON.stringify(Array.prototype.map.call(document.querySelectorAll('div.slide'),function(e){var r=e.getBoundingClientRect();
        return [r.left+window.scrollX,r.top+window.scrollY,r.width,r.height]}))
        """) as? [[Double]]) ?? []
        guard !rects.isEmpty else { throw ConversionError.emptyContent("簡報中沒有投影片。") }
        let maxW = rects.map { $0[2] }.max() ?? 960
        renderer.resize(width: CGFloat(maxW), height: 800)
        // 每張投影片擷取成一頁；以物件層級組合（不重畫，文字層完整保留）
        var slides: [PDFDocument] = []
        for r in rects where r[2] > 1 && r[3] > 1 {
            slides.append(try await renderer.captureDocument(CGRect(x: r[0], y: r[1], width: r[2], height: r[3])))
        }
        try PTAssemble.write(slides.map { PTPageEntry(doc: $0, index: 0) }, outline: [], info: ["Creator": "QUICK_SEEMD"], to: out)
    }
}

// MARK: - 工作表 HTML

struct OTSheetSource: Sendable {
    let name: String
    let file: URL?
    let html: String?
    let baseURL: URL?
}

enum OTSheetHTML {
    /// 系統預覽 的試算表預覽：多個工作表時，主頁是分頁標籤，每個工作表是一個附件 HTML。
    static func sheets(in dir: URL) -> [OTSheetSource] {
        let main = dir.appendingPathComponent("Preview.html")
        guard let html = try? String(contentsOf: main, encoding: .utf8) else { return [] }
        let pattern = #"<div class="TabHeader">(.*?)</div>\s*<a href="([^"]+)""#
        var result: [OTSheetSource] = []
        if let regex = try? NSRegularExpression(pattern: pattern, options: [.dotMatchesLineSeparators]) {
            let ns = html as NSString
            for m in regex.matches(in: html, range: NSRange(location: 0, length: ns.length)) {
                let name = decodeEntities(stripTags(ns.substring(with: m.range(at: 1))))
                let href = ns.substring(with: m.range(at: 2))
                result.append(OTSheetSource(name: name, file: dir.appendingPathComponent(href), html: nil, baseURL: nil))
            }
        }
        if result.isEmpty {
            let title = matchFirst(#"<title>(.*?)</title>"#, in: html).map { decodeEntities($0) } ?? "工作表1"
            result = [OTSheetSource(name: (title as NSString).deletingPathExtension, file: main, html: nil, baseURL: nil)]
        }
        return result
    }

    /// 從 系統預覽 的工作表 HTML 取出儲存格文字（給 .xls 這類系統沒有直接讀取器的格式用）。
    static func tables(in dir: URL) -> [CVDataConverter.Table] {
        sheets(in: dir).compactMap { sheet in
            guard let file = sheet.file, let html = try? String(contentsOf: file, encoding: .utf8) else { return nil }
            return CVDataConverter.Table(name: sheet.name, rows: rows(fromHTML: html))
        }
    }

    static func rows(fromHTML html: String) -> [[String]] {
        guard let rowRegex = try? NSRegularExpression(pattern: #"<tr[^>]*>(.*?)</tr>"#, options: [.dotMatchesLineSeparators, .caseInsensitive]),
              let cellRegex = try? NSRegularExpression(pattern: #"<t([dh])([^>]*)>(.*?)</t[dh]>"#, options: [.dotMatchesLineSeparators, .caseInsensitive])
        else { return [] }
        let ns = html as NSString
        var rows: [[String]] = []
        for rm in rowRegex.matches(in: html, range: NSRange(location: 0, length: ns.length)) {
            let rowHTML = ns.substring(with: rm.range(at: 1))
            let rns = rowHTML as NSString
            var cells: [String] = []
            for cm in cellRegex.matches(in: rowHTML, range: NSRange(location: 0, length: rns.length)) {
                let attrs = rns.substring(with: cm.range(at: 2))
                var text = rns.substring(with: cm.range(at: 3))
                text = text.replacingOccurrences(of: #"<br\s*/?>"#, with: "\n", options: [.regularExpression, .caseInsensitive])
                cells.append(decodeEntities(stripTags(text)).trimmingCharacters(in: .whitespaces))
                if let span = matchFirst(#"colspan\s*=\s*"?(\d+)"#, in: attrs).flatMap(Int.init), span > 1 {
                    cells += Array(repeating: "", count: min(span - 1, 500))
                }
            }
            rows.append(cells)
        }
        // 去掉尾端空白列與空白欄
        while let last = rows.last, last.allSatisfy({ $0.isEmpty }) { rows.removeLast() }
        let used = rows.map { ($0.lastIndex { !$0.isEmpty } ?? -1) + 1 }.max() ?? 0
        return rows.map { row in
            var r = Array(row.prefix(used))
            if r.count < used { r += Array(repeating: "", count: used - r.count) }
            return r
        }
    }

    /// 給 CSV／純資料用的列印版 HTML（細框線、標題列粗體、數字靠右）。
    static func document(_ tables: [CVDataConverter.Table], gridlines: Bool) -> String {
        var body = ""
        for table in tables {
            body += "<table class=\"worksheet\">"
            for (i, row) in table.rows.enumerated() {
                body += "<tr>" + row.map { cell -> String in
                    let num = i > 0 && CVDataConverter.looksNumeric(cell.replacingOccurrences(of: ",", with: ""))
                    let cls = i == 0 ? " class=\"h\"" : (num ? " class=\"n\"" : "")
                    return "<td\(cls)>\(CVXML.escape(cell).replacingOccurrences(of: "\n", with: "<br>"))</td>"
                }.joined() + "</tr>"
            }
            body += "</table>"
        }
        let border = gridlines ? "border:0.5pt solid #b8bcc6;" : "border-bottom:0.5pt solid #e1e3e8;"
        return """
        <!DOCTYPE html><html><head><meta charset="utf-8"><style>
        body{margin:0;font:10pt \(OTFontFallback.defaultStack);color:#111}
        table{border-collapse:collapse}
        td{\(border)padding:2pt 5pt;white-space:pre;vertical-align:top}
        td.h{font-weight:600;background:#eef1f5}
        td.n{text-align:right;font-variant-numeric:tabular-nums}
        </style></head><body>\(body)</body></html>
        """
    }

    static func stripTags(_ s: String) -> String {
        s.replacingOccurrences(of: #"<[^>]+>"#, with: "", options: .regularExpression)
    }

    static func decodeEntities(_ s: String) -> String {
        guard s.contains("&") else { return s }
        var out = s.replacingOccurrences(of: "&nbsp;", with: " ")
            .replacingOccurrences(of: "&lt;", with: "<").replacingOccurrences(of: "&gt;", with: ">")
            .replacingOccurrences(of: "&quot;", with: "\"").replacingOccurrences(of: "&#39;", with: "'")
            .replacingOccurrences(of: "&apos;", with: "'")
        if let regex = try? NSRegularExpression(pattern: #"&#(x?)([0-9A-Fa-f]+);"#) {
            let ns = out as NSString
            let mutable = NSMutableString(string: out)
            for m in regex.matches(in: out, range: NSRange(location: 0, length: ns.length)).reversed() {
                let hex = ns.substring(with: m.range(at: 1)) == "x"
                let digits = ns.substring(with: m.range(at: 2))
                if let v = UInt32(digits, radix: hex ? 16 : 10), let scalar = Unicode.Scalar(v) {
                    mutable.replaceCharacters(in: m.range, with: String(Character(scalar)))
                }
            }
            out = mutable as String
        }
        return out.replacingOccurrences(of: "&amp;", with: "&")
    }

    static func matchFirst(_ pattern: String, in text: String) -> String? {
        guard let regex = try? NSRegularExpression(pattern: pattern, options: [.dotMatchesLineSeparators, .caseInsensitive]),
              let m = regex.firstMatch(in: text, range: NSRange(location: 0, length: (text as NSString).length)),
              m.numberOfRanges > 1 else { return nil }
        return (text as NSString).substring(with: m.range(at: 1))
    }
}
