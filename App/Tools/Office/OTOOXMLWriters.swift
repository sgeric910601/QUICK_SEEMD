import AppKit
import Foundation

// 自己產生的 Office Open XML 檔案（.docx／.xlsx／.pptx），只用到最基本、Word／Excel／PowerPoint／
// Pages／Numbers／Keynote／系統預覽 都能開的結構。

enum OTXML {
    static func esc(_ s: String) -> String {
        var out = ""
        out.reserveCapacity(s.utf8.count)
        for scalar in s.unicodeScalars {
            switch scalar {
            case "&": out += "&amp;"
            case "<": out += "&lt;"
            case ">": out += "&gt;"
            case "\"": out += "&quot;"
            default:
                // XML 1.0 不允許的控制字元
                if scalar.value < 0x20 && scalar != "\t" && scalar != "\n" && scalar != "\r" { continue }
                if (0xFFFE...0xFFFF).contains(scalar.value) || (0xD800...0xDFFF).contains(scalar.value) { continue }
                out.unicodeScalars.append(scalar)
            }
        }
        return out
    }

    static let header = "<?xml version=\"1.0\" encoding=\"UTF-8\" standalone=\"yes\"?>\n"
    static let relNS = "http://schemas.openxmlformats.org/officeDocument/2006/relationships"

    static func rels(_ items: [(id: String, type: String, target: String)]) -> String {
        header + "<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">"
            + items.map { "<Relationship Id=\"\($0.id)\" Type=\"\(relNS)/\($0.type)\" Target=\"\(esc($0.target))\"/>" }.joined()
            + "</Relationships>"
    }

    static func coreProps(title: String) -> String {
        let now = ISO8601DateFormatter().string(from: Date())
        return header + """
        <cp:coreProperties xmlns:cp="http://schemas.openxmlformats.org/package/2006/metadata/core-properties" xmlns:dc="http://purl.org/dc/elements/1.1/" xmlns:dcterms="http://purl.org/dc/terms/" xmlns:dcmitype="http://purl.org/dc/dcmitype/" xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"><dc:title>\(esc(title))</dc:title><dc:creator>QUICK_SEEMD</dc:creator><dcterms:created xsi:type="dcterms:W3CDTF">\(now)</dcterms:created><dcterms:modified xsi:type="dcterms:W3CDTF">\(now)</dcterms:modified></cp:coreProperties>
        """
    }

    static func appProps(_ application: String = "QUICK_SEEMD") -> String {
        header + "<Properties xmlns=\"http://schemas.openxmlformats.org/officeDocument/2006/extended-properties\"><Application>\(application)</Application></Properties>"
    }

    /// 寫出套件（路徑 → 內容）並壓縮。
    static func writePackage(_ parts: [String: Data], to out: URL) async throws {
        let root = try CVFileNaming.makeScratchDirectory().appendingPathComponent("pkg", isDirectory: true)
        defer { try? FileManager.default.removeItem(at: root.deletingLastPathComponent()) }
        for (path, data) in parts {
            let url = root.appendingPathComponent(path)
            try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
            try data.write(to: url)
        }
        try await OTWork.zipDirectory(root, to: out)
    }
}

// MARK: - Word (.docx)

/// 逐段組出 Word 文件。座標單位：點（1 點 = 20 twips = 12700 EMU）。
final class OTDocxBuilder {
    struct Section {
        var size: CGSize
        var margins: NSEdgeInsets
    }

    private var blocks: [String] = []
    private var media: [(path: String, data: Data)] = []
    private var relationships: [(id: String, type: String, target: String)] = []
    private var imageCount = 0
    private static let sectMark = "<!--QSSECT-->"

    static func twips(_ pt: CGFloat) -> Int { Int((pt * 20).rounded()) }
    static func emu(_ pt: CGFloat) -> Int { Int((pt * 12700).rounded()) }

    /// macOS 字型 → Word／Windows 上也有的字型。
    static func officeFont(_ family: String, cjk: Bool) -> String {
        let f = family.lowercased()
        if f.contains("songti") || f.contains("lisong") || f.contains("lisung") || f.contains("mingliu") || f.contains("ming") && cjk { return "PMingLiU" }
        if f.contains("pingfang") || f.contains("heiti") || f.contains("hiragino sans gb") || f.contains("ligothic") || f.contains("jhenghei") { return "Microsoft JhengHei" }
        if f.contains("kaiti") || f.contains("biaukai") || f.contains("kai") && cjk { return "DFKai-SB" }
        if f == "helvetica" || f == "helvetica neue" || f.hasPrefix("helvetica") || f == "arial" || f == ".sf ns" || f.hasPrefix("sf pro") { return "Arial" }
        if f == "times" || f == "times new roman" || f == "times-roman" { return "Times New Roman" }
        if f == "courier" || f == "menlo" || f == "monaco" { return "Courier New" }
        return family
    }

    private func runXML(_ run: OTTextRun) -> String {
        let cjk = run.text.contains { OTLayout.isCJK($0) }
        let font = OTDocxBuilder.officeFont(run.fontFamily, cjk: cjk)
        var rpr = cjk ? "<w:rFonts w:hint=\"eastAsia\" w:eastAsia=\"\(OTXML.esc(font))\"/>"
            : "<w:rFonts w:ascii=\"\(OTXML.esc(font))\" w:hAnsi=\"\(OTXML.esc(font))\" w:cs=\"\(OTXML.esc(font))\"/>"
        if run.bold { rpr += "<w:b/><w:bCs/>" }
        if run.italic { rpr += "<w:i/><w:iCs/>" }
        if let color = run.color { rpr += "<w:color w:val=\"\(color)\"/>" }
        let half = max(2, Int((run.size * 2).rounded()))
        rpr += "<w:sz w:val=\"\(half)\"/><w:szCs w:val=\"\(half)\"/>"
        var xml = ""
        let parts = run.text.components(separatedBy: "\t")
        for (i, part) in parts.enumerated() {
            if i > 0 { xml += "<w:r><w:rPr>\(rpr)</w:rPr><w:tab/></w:r>" }
            if !part.isEmpty { xml += "<w:r><w:rPr>\(rpr)</w:rPr><w:t xml:space=\"preserve\">\(OTXML.esc(part))</w:t></w:r>" }
        }
        return xml
    }

    func paragraph(_ p: OTParagraph, pageBreakBefore: Bool = false) {
        var ppr = ""
        if p.headingLevel > 0 { ppr += "<w:pStyle w:val=\"Heading\(p.headingLevel)\"/>" }
        if pageBreakBefore { ppr += "<w:pageBreakBefore/>" }
        ppr += "<w:spacing w:before=\"\(Self.twips(p.spaceBefore))\" w:after=\"0\" w:line=\"240\" w:lineRule=\"auto\"/>"
        let left = Self.twips(p.leftIndent), first = Self.twips(p.firstLineIndent)
        if left != 0 || first != 0 {
            ppr += first >= 0 ? "<w:ind w:left=\"\(left)\" w:firstLine=\"\(first)\"/>" : "<w:ind w:left=\"\(left)\" w:hanging=\"\(-first)\"/>"
        }
        if p.alignment != "left" { ppr += "<w:jc w:val=\"\(p.alignment == "center" ? "center" : "right")\"/>" }
        blocks.append("<w:p><w:pPr>\(ppr)\(Self.sectMark)</w:pPr>\(p.runs.map(runXML).joined())</w:p>")
    }

    /// 換頁用的極小段落（表格前面不能直接加「段落前分頁」）。
    private func pageBreakParagraph() {
        blocks.append("<w:p><w:pPr><w:pageBreakBefore/><w:spacing w:before=\"0\" w:after=\"0\" w:line=\"20\" w:lineRule=\"exact\"/>\(Self.sectMark)</w:pPr></w:p>")
    }

    func table(_ t: OTTable, maxWidth: CGFloat, pageBreakBefore: Bool = false) {
        let padded = t.columnWidths.map { $0 + 10 }
        let total = padded.reduce(0, +)
        let scale = total > maxWidth ? maxWidth / total : 1
        let widths = padded.map { Self.twips($0 * scale) }
        var xml = ""
        if pageBreakBefore { pageBreakParagraph() }
        if t.spaceBefore > 2 && !pageBreakBefore {
            xml += "<w:p><w:pPr><w:spacing w:before=\"0\" w:after=\"0\" w:line=\"\(Self.twips(t.spaceBefore))\" w:lineRule=\"exact\"/></w:pPr></w:p>"
        }
        xml += "<w:tbl><w:tblPr><w:tblW w:w=\"\(widths.reduce(0, +))\" w:type=\"dxa\"/><w:tblBorders>"
        for side in ["top", "left", "bottom", "right", "insideH", "insideV"] {
            xml += "<w:\(side) w:val=\"single\" w:sz=\"4\" w:space=\"0\" w:color=\"BFBFBF\"/>"
        }
        xml += "</w:tblBorders><w:tblLayout w:type=\"fixed\"/><w:tblCellMar><w:left w:w=\"80\" w:type=\"dxa\"/><w:right w:w=\"80\" w:type=\"dxa\"/></w:tblCellMar></w:tblPr><w:tblGrid>"
        xml += widths.map { "<w:gridCol w:w=\"\($0)\"/>" }.joined() + "</w:tblGrid>"
        for (r, row) in t.cells.enumerated() {
            let header = r == 0 && row.allSatisfy { $0.isEmpty || $0.allSatisfy(\.isBold) } && row.contains { !$0.isEmpty }
            xml += "<w:tr>" + (header ? "<w:trPr><w:tblHeader/></w:trPr>" : "")
            for (c, cell) in row.enumerated() {
                xml += "<w:tc><w:tcPr><w:tcW w:w=\"\(widths[min(c, widths.count - 1)])\" w:type=\"dxa\"/></w:tcPr>"
                if cell.isEmpty {
                    xml += "<w:p><w:pPr><w:spacing w:before=\"0\" w:after=\"0\"/></w:pPr></w:p>"
                } else {
                    let para = OTParagraph(lines: cell.sorted { $0.frame.minY < $1.frame.minY })
                    let numeric = para.lines.count == 1 && para.lines[0].text.range(of: #"^[-+(]?[$€¥£]?\s?[\d,]+(\.\d+)?%?\)?$"#, options: .regularExpression) != nil
                    xml += "<w:p><w:pPr><w:spacing w:before=\"0\" w:after=\"0\"/>\(numeric ? "<w:jc w:val=\"right\"/>" : "")</w:pPr>\(para.runs.map(runXML).joined())</w:p>"
                }
                xml += "</w:tc>"
            }
            xml += "</w:tr>"
        }
        xml += "</w:tbl>"
        blocks.append(xml)
    }

    /// 一張內嵌圖片（獨立段落）。
    func image(_ data: Data, ext: String, size: CGSize, alignment: String = "center", spaceBefore: CGFloat = 0, pageBreakBefore: Bool = false) {
        imageCount += 1
        let name = "image\(imageCount).\(ext)"
        media.append(("word/media/\(name)", data))
        let rid = "rIdImg\(imageCount)"
        relationships.append((rid, "image", "media/\(name)"))
        let cx = Self.emu(size.width), cy = Self.emu(size.height)
        let drawing = """
        <w:drawing><wp:inline distT="0" distB="0" distL="0" distR="0"><wp:extent cx="\(cx)" cy="\(cy)"/><wp:docPr id="\(imageCount)" name="圖片 \(imageCount)"/><wp:cNvGraphicFramePr><a:graphicFrameLocks noChangeAspect="1"/></wp:cNvGraphicFramePr><a:graphic><a:graphicData uri="http://schemas.openxmlformats.org/drawingml/2006/picture"><pic:pic><pic:nvPicPr><pic:cNvPr id="\(imageCount)" name="\(name)"/><pic:cNvPicPr/></pic:nvPicPr><pic:blipFill><a:blip r:embed="\(rid)"/><a:stretch><a:fillRect/></a:stretch></pic:blipFill><pic:spPr><a:xfrm><a:off x="0" y="0"/><a:ext cx="\(cx)" cy="\(cy)"/></a:xfrm><a:prstGeom prst="rect"><a:avLst/></a:prstGeom></pic:spPr></pic:pic></a:graphicData></a:graphic></wp:inline></w:drawing>
        """
        let jc = alignment == "left" ? "" : "<w:jc w:val=\"\(alignment)\"/>"
        let br = pageBreakBefore ? "<w:pageBreakBefore/>" : ""
        blocks.append("<w:p><w:pPr>\(br)<w:spacing w:before=\"\(Self.twips(spaceBefore))\" w:after=\"0\" w:line=\"240\" w:lineRule=\"auto\"/>\(jc)\(Self.sectMark)</w:pPr><w:r>\(drawing)</w:r></w:p>")
    }

    static func sectPr(_ s: Section) -> String {
        let w = twips(s.size.width), h = twips(s.size.height)
        let orient = s.size.width > s.size.height ? " w:orient=\"landscape\"" : ""
        return "<w:sectPr><w:type w:val=\"nextPage\"/><w:pgSz w:w=\"\(w)\" w:h=\"\(h)\"\(orient)/><w:pgMar w:top=\"\(twips(s.margins.top))\" w:right=\"\(twips(s.margins.right))\" w:bottom=\"\(twips(s.margins.bottom))\" w:left=\"\(twips(s.margins.left))\" w:header=\"0\" w:footer=\"0\" w:gutter=\"0\"/></w:sectPr>"
    }

    /// 結束一個節（一頁 PDF）：版面設定放進最後一段；最後是表格時補一個極小的段落。
    func endSection(_ s: Section) {
        let sect = Self.sectPr(s)
        if let last = blocks.last, last.hasPrefix("<w:p>"), let range = last.range(of: Self.sectMark, options: .backwards) {
            blocks[blocks.count - 1] = last.replacingCharacters(in: range, with: sect)
        } else {
            blocks.append("<w:p><w:pPr><w:spacing w:before=\"0\" w:after=\"0\" w:line=\"20\" w:lineRule=\"exact\"/>\(sect)</w:pPr></w:p>")
        }
    }

    var isEmpty: Bool { blocks.isEmpty }

    func write(to out: URL, lastSection: Section, title: String) async throws {
        // 最後一節的設定放在 body 結尾
        let body = blocks.joined().replacingOccurrences(of: Self.sectMark, with: "")
        let lastSect = Self.sectPr(lastSection).replacingOccurrences(of: "<w:type w:val=\"nextPage\"/>", with: "")
        let document = OTXML.header + """
        <w:document xmlns:wpc="http://schemas.microsoft.com/office/word/2010/wordprocessingCanvas" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships" xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main" xmlns:wp="http://schemas.openxmlformats.org/drawingml/2006/wordprocessingDrawing" xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" xmlns:pic="http://schemas.openxmlformats.org/drawingml/2006/picture"><w:body>\(body)\(lastSect)</w:body></w:document>
        """
        let styles = OTXML.header + """
        <w:styles xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:docDefaults><w:rPrDefault><w:rPr><w:rFonts w:ascii="Arial" w:hAnsi="Arial" w:eastAsia="Microsoft JhengHei" w:cs="Arial"/><w:sz w:val="22"/><w:szCs w:val="22"/><w:lang w:val="en-US" w:eastAsia="zh-TW"/></w:rPr></w:rPrDefault><w:pPrDefault><w:pPr><w:spacing w:after="0" w:line="240" w:lineRule="auto"/></w:pPr></w:pPrDefault></w:docDefaults><w:style w:type="paragraph" w:default="1" w:styleId="Normal"><w:name w:val="Normal"/><w:qFormat/></w:style><w:style w:type="paragraph" w:styleId="Heading1"><w:name w:val="heading 1"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:outlineLvl w:val="0"/></w:pPr><w:rPr><w:b/></w:rPr></w:style><w:style w:type="paragraph" w:styleId="Heading2"><w:name w:val="heading 2"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:outlineLvl w:val="1"/></w:pPr><w:rPr><w:b/></w:rPr></w:style><w:style w:type="paragraph" w:styleId="Heading3"><w:name w:val="heading 3"/><w:basedOn w:val="Normal"/><w:next w:val="Normal"/><w:qFormat/><w:pPr><w:keepNext/><w:outlineLvl w:val="2"/></w:pPr></w:style><w:style w:type="table" w:default="1" w:styleId="TableNormal"><w:name w:val="Normal Table"/><w:tblPr><w:tblInd w:w="0" w:type="dxa"/><w:tblCellMar><w:top w:w="0" w:type="dxa"/><w:left w:w="108" w:type="dxa"/><w:bottom w:w="0" w:type="dxa"/><w:right w:w="108" w:type="dxa"/></w:tblCellMar></w:tblPr></w:style></w:styles>
        """
        let settings = OTXML.header + "<w:settings xmlns:w=\"http://schemas.openxmlformats.org/wordprocessingml/2006/main\"><w:defaultTabStop w:val=\"720\"/><w:characterSpacingControl w:val=\"compressPunctuation\"/><w:compat><w:compatSetting w:name=\"compatibilityMode\" w:uri=\"http://schemas.microsoft.com/office/word\" w:val=\"15\"/></w:compat></w:settings>"
        var docRels: [(id: String, type: String, target: String)] = [("rIdStyles", "styles", "styles.xml"), ("rIdSettings", "settings", "settings.xml")]
        docRels += relationships
        var defaults = "<Default Extension=\"rels\" ContentType=\"application/vnd.openxmlformats-package.relationships+xml\"/><Default Extension=\"xml\" ContentType=\"application/xml\"/>"
        let exts = Set(media.map { ($0.path as NSString).pathExtension.lowercased() })
        if exts.contains("png") { defaults += "<Default Extension=\"png\" ContentType=\"image/png\"/>" }
        if exts.contains("jpg") { defaults += "<Default Extension=\"jpg\" ContentType=\"image/jpeg\"/>" }
        let types = OTXML.header + "<Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\">\(defaults)<Override PartName=\"/word/document.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml\"/><Override PartName=\"/word/styles.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.wordprocessingml.styles+xml\"/><Override PartName=\"/word/settings.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.wordprocessingml.settings+xml\"/><Override PartName=\"/docProps/core.xml\" ContentType=\"application/vnd.openxmlformats-package.core-properties+xml\"/><Override PartName=\"/docProps/app.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.extended-properties+xml\"/></Types>"
        var parts: [String: Data] = [
            "[Content_Types].xml": Data(types.utf8),
            "_rels/.rels": Data(OTXML.rels([("rId1", "officeDocument", "word/document.xml"),
                                            ("rId2", "metadata/core-properties", "docProps/core.xml"),
                                            ("rId3", "extended-properties", "docProps/app.xml")]).replacingOccurrences(of: "\(OTXML.relNS)/metadata/core-properties", with: "http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties").utf8),
            "word/document.xml": Data(document.utf8),
            "word/styles.xml": Data(styles.utf8),
            "word/settings.xml": Data(settings.utf8),
            "word/_rels/document.xml.rels": Data(OTXML.rels(docRels).utf8),
            "docProps/core.xml": Data(OTXML.coreProps(title: title).utf8),
            "docProps/app.xml": Data(OTXML.appProps().utf8),
        ]
        for m in media { parts[m.path] = m.data }
        try await OTXML.writePackage(parts, to: out)
    }
}

// MARK: - Excel (.xlsx)

/// 有型別的儲存格（數字會以數值存，Excel 才能計算）。
enum OTCell: Sendable, Equatable {
    case text(String)
    case number(Double, style: Int)
    case empty

    // 樣式編號（對應 styles.xml 的 cellXfs）
    static let styleDefault = 0, styleHeader = 1, styleInteger = 2, styleDecimal = 3, stylePercent0 = 4,
               stylePercent2 = 5, stylePercent1 = 6, styleWrap = 7, stylePlainNumber = 8

    /// 把文字判讀成數字（12,400 → 12400、23.5% → 0.235、(1,200) → -1200）。保留 0912 這類編號為文字。
    static func parse(_ raw: String, convertNumbers: Bool) -> OTCell {
        let s = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        if s.isEmpty { return .empty }
        guard convertNumbers else { return .text(s) }
        var t = s.replacingOccurrences(of: " ", with: "").replacingOccurrences(of: "\u{00A0}", with: "")
        var negative = false
        if t.hasPrefix("(") && t.hasSuffix(")") && t.count > 2 { negative = true; t = String(t.dropFirst().dropLast()) }
        if t.hasPrefix("−") { t = "-" + t.dropFirst() }
        var percent = false
        if t.hasSuffix("%") { percent = true; t.removeLast() }
        guard t.range(of: #"^[-+]?(\d{1,3}(,\d{3})+|\d+)(\.\d+)?$"#, options: .regularExpression) != nil else { return .text(s) }
        let digitsOnly = t.replacingOccurrences(of: ",", with: "")
        // 0 開頭的整數（電話、編號）保留文字
        if digitsOnly.range(of: #"^[-+]?0\d"#, options: .regularExpression) != nil { return .text(s) }
        if digitsOnly.replacingOccurrences(of: "-", with: "").count > 15 { return .text(s) }
        guard var value = Double(digitsOnly), value.isFinite else { return .text(s) }
        if negative { value = -value }
        let decimals = digitsOnly.split(separator: ".").dropFirst().first?.count ?? 0
        if percent {
            value /= 100
            return .number(value, style: decimals == 0 ? stylePercent0 : (decimals == 1 ? stylePercent1 : stylePercent2))
        }
        let grouped = t.contains(",")
        if grouped { return .number(value, style: decimals == 0 ? styleInteger : styleDecimal) }
        return .number(value, style: stylePlainNumber)
    }
}

struct OTSheet: Sendable {
    var name: String
    var rows: [[OTCell]]
    /// 表頭列（粗體、凍結窗格）
    var headerRow: Int? = nil
}

enum OTXLSXWriter {
    static func columnName(_ index: Int) -> String {
        var n = index + 1
        var name = ""
        while n > 0 {
            let r = (n - 1) % 26
            name = String(UnicodeScalar(UInt8(65 + r))) + name
            n = (n - 1) / 26
        }
        return name
    }

    static func displayWidth(_ s: String) -> Int {
        s.unicodeScalars.reduce(0) { $0 + ($1.value > 0x2E80 ? 2 : 1) }
    }

    static func sheetNames(_ sheets: [OTSheet]) -> [String] {
        var used = Set<String>()
        return sheets.enumerated().map { i, s in
            var name = s.name.replacingOccurrences(of: #"[\\/\?\*\[\]:]"#, with: "_", options: .regularExpression)
            if name.isEmpty { name = "工作表\(i + 1)" }
            name = String(name.prefix(31))
            var candidate = name
            var n = 2
            while used.contains(candidate.lowercased()) { candidate = String(name.prefix(27)) + " (\(n))"; n += 1 }
            used.insert(candidate.lowercased())
            return candidate
        }
    }

    static func write(_ sheetsIn: [OTSheet], to out: URL, title: String) async throws {
        let sheets = sheetsIn.isEmpty ? [OTSheet(name: "工作表1", rows: [])] : sheetsIn
        let names = sheetNames(sheets)
        var shared: [String] = []
        var sharedIndex: [String: Int] = [:]
        var refs = 0
        func sid(_ s: String) -> Int {
            refs += 1
            if let i = sharedIndex[s] { return i }
            sharedIndex[s] = shared.count
            shared.append(s)
            return shared.count - 1
        }
        var parts: [String: Data] = [:]
        for (index, sheet) in sheets.enumerated() {
            try Task.checkCancellation()
            let columns = sheet.rows.map(\.count).max() ?? 0
            var widths = [Int](repeating: 6, count: columns)
            // 欄寬只看有兩格以上的列（橫跨的標題文字可以延伸到旁邊的欄）
            let multi = sheet.rows.filter { $0.filter { $0 != .empty }.count >= 2 }
            for row in (multi.isEmpty ? sheet.rows : multi).prefix(3000) {
                for (c, cell) in row.enumerated() {
                    let w: Int
                    switch cell {
                    case .text(let s): w = (s.split(separator: "\n").map { displayWidth(String($0)) }.max() ?? 0) + 2
                    case .number(let v, _): w = String(format: "%.2f", v).count + 3
                    case .empty: w = 0
                    }
                    widths[c] = min(60, max(widths[c], w))
                }
            }
            var xml = OTXML.header + "<worksheet xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\" xmlns:r=\"\(OTXML.relNS)\">"
            if let h = sheet.headerRow, sheet.rows.count > h + 1 {
                xml += "<sheetViews><sheetView workbookViewId=\"0\"\(index == 0 ? " tabSelected=\"1\"" : "")><pane ySplit=\"\(h + 1)\" topLeftCell=\"A\(h + 2)\" activePane=\"bottomLeft\" state=\"frozen\"/></sheetView></sheetViews>"
            } else {
                xml += "<sheetViews><sheetView workbookViewId=\"0\"\(index == 0 ? " tabSelected=\"1\"" : "")/></sheetViews>"
            }
            xml += "<sheetFormatPr defaultRowHeight=\"16\"/>"
            if columns > 0 {
                xml += "<cols>" + widths.enumerated().map { "<col min=\"\($0.offset + 1)\" max=\"\($0.offset + 1)\" width=\"\($0.element)\" customWidth=\"1\"/>" }.joined() + "</cols>"
            }
            xml += "<sheetData>"
            var chunk = ""
            for (r, row) in sheet.rows.enumerated() {
                chunk += "<row r=\"\(r + 1)\">"
                for (c, cell) in row.enumerated() {
                    let ref = "\(columnName(c))\(r + 1)"
                    let header = sheet.headerRow == r
                    switch cell {
                    case .empty:
                        continue
                    case .text(let s):
                        let style = header ? OTCell.styleHeader : (s.contains("\n") ? OTCell.styleWrap : OTCell.styleDefault)
                        chunk += "<c r=\"\(ref)\"\(style == 0 ? "" : " s=\"\(style)\"") t=\"s\"><v>\(sid(s))</v></c>"
                    case .number(let v, let style):
                        let text = v == v.rounded() && abs(v) < 1e15 ? String(Int64(v)) : String(v)
                        let st = header ? OTCell.styleHeader : style
                        chunk += "<c r=\"\(ref)\"\(st == 0 ? "" : " s=\"\(st)\"")><v>\(text)</v></c>"
                    }
                }
                chunk += "</row>"
                if chunk.utf8.count > 1_000_000 { xml += chunk; chunk = "" }
            }
            xml += chunk + "</sheetData><pageMargins left=\"0.5\" right=\"0.5\" top=\"0.6\" bottom=\"0.6\" header=\"0.3\" footer=\"0.3\"/></worksheet>"
            parts["xl/worksheets/sheet\(index + 1).xml"] = Data(xml.utf8)
        }
        var sst = OTXML.header + "<sst xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\" count=\"\(refs)\" uniqueCount=\"\(shared.count)\">"
        for s in shared { sst += "<si><t xml:space=\"preserve\">\(OTXML.esc(s))</t></si>" }
        sst += "</sst>"
        parts["xl/sharedStrings.xml"] = Data(sst.utf8)
        parts["xl/styles.xml"] = Data((OTXML.header + """
        <styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><numFmts count="1"><numFmt numFmtId="164" formatCode="0.0%"/></numFmts><fonts count="2"><font><sz val="12"/><name val="Calibri"/><family val="2"/></font><font><b/><sz val="12"/><name val="Calibri"/><family val="2"/></font></fonts><fills count="3"><fill><patternFill patternType="none"/></fill><fill><patternFill patternType="gray125"/></fill><fill><patternFill patternType="solid"><fgColor rgb="FFEFF2F6"/><bgColor indexed="64"/></patternFill></fill></fills><borders count="1"><border><left/><right/><top/><bottom/><diagonal/></border></borders><cellStyleXfs count="1"><xf numFmtId="0" fontId="0" fillId="0" borderId="0"/></cellStyleXfs><cellXfs count="9"><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/><xf numFmtId="0" fontId="1" fillId="2" borderId="0" xfId="0" applyFont="1" applyFill="1"/><xf numFmtId="3" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/><xf numFmtId="4" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/><xf numFmtId="9" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/><xf numFmtId="10" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/><xf numFmtId="164" fontId="0" fillId="0" borderId="0" xfId="0" applyNumberFormat="1"/><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0" applyAlignment="1"><alignment wrapText="1" vertical="top"/></xf><xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/></cellXfs><cellStyles count="1"><cellStyle name="Normal" xfId="0" builtinId="0"/></cellStyles></styleSheet>
        """).utf8)
        var sheetEntries = "", rels = "", overrides = ""
        for (i, name) in names.enumerated() {
            sheetEntries += "<sheet name=\"\(OTXML.esc(name))\" sheetId=\"\(i + 1)\" r:id=\"rId\(i + 1)\"/>"
            rels += "<Relationship Id=\"rId\(i + 1)\" Type=\"\(OTXML.relNS)/worksheet\" Target=\"worksheets/sheet\(i + 1).xml\"/>"
            overrides += "<Override PartName=\"/xl/worksheets/sheet\(i + 1).xml\" ContentType=\"application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml\"/>"
        }
        rels += "<Relationship Id=\"rId\(sheets.count + 1)\" Type=\"\(OTXML.relNS)/styles\" Target=\"styles.xml\"/>"
        rels += "<Relationship Id=\"rId\(sheets.count + 2)\" Type=\"\(OTXML.relNS)/sharedStrings\" Target=\"sharedStrings.xml\"/>"
        parts["xl/workbook.xml"] = Data((OTXML.header + "<workbook xmlns=\"http://schemas.openxmlformats.org/spreadsheetml/2006/main\" xmlns:r=\"\(OTXML.relNS)\"><bookViews><workbookView activeTab=\"0\"/></bookViews><sheets>\(sheetEntries)</sheets></workbook>").utf8)
        parts["xl/_rels/workbook.xml.rels"] = Data((OTXML.header + "<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\">\(rels)</Relationships>").utf8)
        parts["[Content_Types].xml"] = Data((OTXML.header + "<Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\"><Default Extension=\"rels\" ContentType=\"application/vnd.openxmlformats-package.relationships+xml\"/><Default Extension=\"xml\" ContentType=\"application/xml\"/><Override PartName=\"/xl/workbook.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml\"/>\(overrides)<Override PartName=\"/xl/styles.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml\"/><Override PartName=\"/xl/sharedStrings.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.spreadsheetml.sharedStrings+xml\"/><Override PartName=\"/docProps/core.xml\" ContentType=\"application/vnd.openxmlformats-package.core-properties+xml\"/><Override PartName=\"/docProps/app.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.extended-properties+xml\"/></Types>").utf8)
        parts["_rels/.rels"] = Data((OTXML.header + "<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\"><Relationship Id=\"rId1\" Type=\"\(OTXML.relNS)/officeDocument\" Target=\"xl/workbook.xml\"/><Relationship Id=\"rId2\" Type=\"http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties\" Target=\"docProps/core.xml\"/><Relationship Id=\"rId3\" Type=\"\(OTXML.relNS)/extended-properties\" Target=\"docProps/app.xml\"/></Relationships>").utf8)
        parts["docProps/core.xml"] = Data(OTXML.coreProps(title: title).utf8)
        parts["docProps/app.xml"] = Data(OTXML.appProps().utf8)
        try await OTXML.writePackage(parts, to: out)
    }
}

// MARK: - PowerPoint (.pptx)

/// 每張投影片放一張圖（可加備忘稿）。
struct OTSlideImage: Sendable {
    var data: Data
    var ext: String          // jpg / png
    var pixelSize: CGSize
    var notes: String?
}

enum OTPPTXWriter {
    static let ns = "xmlns:a=\"http://schemas.openxmlformats.org/drawingml/2006/main\" xmlns:r=\"http://schemas.openxmlformats.org/officeDocument/2006/relationships\" xmlns:p=\"http://schemas.openxmlformats.org/presentationml/2006/main\""

    static func write(_ slides: [OTSlideImage], slideSize: CGSize, to out: URL, title: String) async throws {
        let cx = Int((slideSize.width * 12700).rounded()), cy = Int((slideSize.height * 12700).rounded())
        let hasNotes = slides.contains { !($0.notes ?? "").trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
        var parts: [String: Data] = [:]
        func put(_ path: String, _ s: String) { parts[path] = Data(s.utf8) }

        put("ppt/theme/theme1.xml", theme)
        put("ppt/slideMasters/slideMaster1.xml", OTXML.header + "<p:sldMaster \(ns)><p:cSld><p:bg><p:bgRef idx=\"1001\"><a:schemeClr val=\"bg1\"/></p:bgRef></p:bg><p:spTree><p:nvGrpSpPr><p:cNvPr id=\"1\" name=\"\"/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x=\"0\" y=\"0\"/><a:ext cx=\"0\" cy=\"0\"/><a:chOff x=\"0\" y=\"0\"/><a:chExt cx=\"0\" cy=\"0\"/></a:xfrm></p:grpSpPr></p:spTree></p:cSld><p:clrMap bg1=\"lt1\" tx1=\"dk1\" bg2=\"lt2\" tx2=\"dk2\" accent1=\"accent1\" accent2=\"accent2\" accent3=\"accent3\" accent4=\"accent4\" accent5=\"accent5\" accent6=\"accent6\" hlink=\"hlink\" folHlink=\"folHlink\"/><p:sldLayoutIdLst><p:sldLayoutId id=\"2147483649\" r:id=\"rId1\"/></p:sldLayoutIdLst><p:txStyles><p:titleStyle><a:lvl1pPr><a:defRPr sz=\"4400\"/></a:lvl1pPr></p:titleStyle><p:bodyStyle><a:lvl1pPr><a:defRPr sz=\"2000\"/></a:lvl1pPr></p:bodyStyle><p:otherStyle><a:lvl1pPr><a:defRPr sz=\"1800\"/></a:lvl1pPr></p:otherStyle></p:txStyles></p:sldMaster>")
        put("ppt/slideMasters/_rels/slideMaster1.xml.rels", OTXML.rels([("rId1", "slideLayout", "../slideLayouts/slideLayout1.xml"), ("rId2", "theme", "../theme/theme1.xml")]))
        put("ppt/slideLayouts/slideLayout1.xml", OTXML.header + "<p:sldLayout \(ns) type=\"blank\" preserve=\"1\"><p:cSld name=\"Blank\"><p:spTree><p:nvGrpSpPr><p:cNvPr id=\"1\" name=\"\"/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x=\"0\" y=\"0\"/><a:ext cx=\"0\" cy=\"0\"/><a:chOff x=\"0\" y=\"0\"/><a:chExt cx=\"0\" cy=\"0\"/></a:xfrm></p:grpSpPr></p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sldLayout>")
        put("ppt/slideLayouts/_rels/slideLayout1.xml.rels", OTXML.rels([("rId1", "slideMaster", "../slideMasters/slideMaster1.xml")]))
        if hasNotes {
            put("ppt/theme/theme2.xml", theme)
            put("ppt/notesMasters/notesMaster1.xml", OTXML.header + "<p:notesMaster \(ns)><p:cSld><p:bg><p:bgRef idx=\"1001\"><a:schemeClr val=\"bg1\"/></p:bgRef></p:bg><p:spTree><p:nvGrpSpPr><p:cNvPr id=\"1\" name=\"\"/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/><p:sp><p:nvSpPr><p:cNvPr id=\"2\" name=\"Slide Image Placeholder 1\"/><p:cNvSpPr><a:spLocks noGrp=\"1\" noRot=\"1\" noChangeAspect=\"1\"/></p:cNvSpPr><p:nvPr><p:ph type=\"sldImg\" idx=\"2\"/></p:nvPr></p:nvSpPr><p:spPr><a:xfrm><a:off x=\"685800\" y=\"1143000\"/><a:ext cx=\"5486400\" cy=\"3086100\"/></a:xfrm><a:prstGeom prst=\"rect\"><a:avLst/></a:prstGeom><a:noFill/></p:spPr></p:sp><p:sp><p:nvSpPr><p:cNvPr id=\"3\" name=\"Notes Placeholder 2\"/><p:cNvSpPr><a:spLocks noGrp=\"1\"/></p:cNvSpPr><p:nvPr><p:ph type=\"body\" sz=\"quarter\" idx=\"3\"/></p:nvPr></p:nvSpPr><p:spPr><a:xfrm><a:off x=\"685800\" y=\"4400550\"/><a:ext cx=\"5486400\" cy=\"3600450\"/></a:xfrm><a:prstGeom prst=\"rect\"><a:avLst/></a:prstGeom></p:spPr><p:txBody><a:bodyPr/><a:lstStyle/><a:p><a:endParaRPr lang=\"zh-TW\"/></a:p></p:txBody></p:sp></p:spTree></p:cSld><p:clrMap bg1=\"lt1\" tx1=\"dk1\" bg2=\"lt2\" tx2=\"dk2\" accent1=\"accent1\" accent2=\"accent2\" accent3=\"accent3\" accent4=\"accent4\" accent5=\"accent5\" accent6=\"accent6\" hlink=\"hlink\" folHlink=\"folHlink\"/><p:notesStyle><a:lvl1pPr marL=\"0\" algn=\"l\"><a:defRPr sz=\"1200\"><a:solidFill><a:schemeClr val=\"tx1\"/></a:solidFill><a:latin typeface=\"+mn-lt\"/><a:ea typeface=\"+mn-ea\"/><a:cs typeface=\"+mn-cs\"/></a:defRPr></a:lvl1pPr></p:notesStyle></p:notesMaster>")
            put("ppt/notesMasters/_rels/notesMaster1.xml.rels", OTXML.rels([("rId1", "theme", "../theme/theme2.xml")]))
        }

        var presRels: [(id: String, type: String, target: String)] = [("rId1", "slideMaster", "slideMasters/slideMaster1.xml"), ("rId2", "theme", "theme/theme1.xml")]
        var sldIds = ""
        var overrides = ""
        for (i, slide) in slides.enumerated() {
            let n = i + 1
            let media = "image\(n).\(slide.ext)"
            parts["ppt/media/\(media)"] = slide.data
            // 圖片等比例放進投影片並置中
            let aspect = slide.pixelSize.width / max(slide.pixelSize.height, 1)
            var w = CGFloat(cx), h = w / aspect
            if h > CGFloat(cy) { h = CGFloat(cy); w = h * aspect }
            let x = Int((CGFloat(cx) - w) / 2), y = Int((CGFloat(cy) - h) / 2)
            put("ppt/slides/slide\(n).xml", OTXML.header + "<p:sld \(ns)><p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id=\"1\" name=\"\"/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr><a:xfrm><a:off x=\"0\" y=\"0\"/><a:ext cx=\"0\" cy=\"0\"/><a:chOff x=\"0\" y=\"0\"/><a:chExt cx=\"0\" cy=\"0\"/></a:xfrm></p:grpSpPr><p:pic><p:nvPicPr><p:cNvPr id=\"2\" name=\"第 \(n) 頁\"/><p:cNvPicPr><a:picLocks noChangeAspect=\"1\"/></p:cNvPicPr><p:nvPr/></p:nvPicPr><p:blipFill><a:blip r:embed=\"rId2\"/><a:stretch><a:fillRect/></a:stretch></p:blipFill><p:spPr><a:xfrm><a:off x=\"\(x)\" y=\"\(y)\"/><a:ext cx=\"\(Int(w))\" cy=\"\(Int(h))\"/></a:xfrm><a:prstGeom prst=\"rect\"><a:avLst/></a:prstGeom></p:spPr></p:pic></p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:sld>")
            var slideRels: [(id: String, type: String, target: String)] = [("rId1", "slideLayout", "../slideLayouts/slideLayout1.xml"), ("rId2", "image", "../media/\(media)")]
            if hasNotes {
                slideRels.append(("rId3", "notesSlide", "../notesSlides/notesSlide\(n).xml"))
                let paragraphs = (slide.notes ?? "").components(separatedBy: "\n").map { line -> String in
                    line.isEmpty ? "<a:p><a:endParaRPr lang=\"zh-TW\"/></a:p>" : "<a:p><a:r><a:rPr lang=\"zh-TW\" dirty=\"0\"/><a:t>\(OTXML.esc(line))</a:t></a:r></a:p>"
                }.joined()
                put("ppt/notesSlides/notesSlide\(n).xml", OTXML.header + "<p:notes \(ns)><p:cSld><p:spTree><p:nvGrpSpPr><p:cNvPr id=\"1\" name=\"\"/><p:cNvGrpSpPr/><p:nvPr/></p:nvGrpSpPr><p:grpSpPr/><p:sp><p:nvSpPr><p:cNvPr id=\"2\" name=\"Slide Image Placeholder 1\"/><p:cNvSpPr><a:spLocks noGrp=\"1\" noRot=\"1\" noChangeAspect=\"1\"/></p:cNvSpPr><p:nvPr><p:ph type=\"sldImg\"/></p:nvPr></p:nvSpPr><p:spPr/></p:sp><p:sp><p:nvSpPr><p:cNvPr id=\"3\" name=\"Notes Placeholder 2\"/><p:cNvSpPr><a:spLocks noGrp=\"1\"/></p:cNvSpPr><p:nvPr><p:ph type=\"body\" idx=\"1\"/></p:nvPr></p:nvSpPr><p:spPr/><p:txBody><a:bodyPr/><a:lstStyle/>\(paragraphs.isEmpty ? "<a:p/>" : paragraphs)</p:txBody></p:sp></p:spTree></p:cSld><p:clrMapOvr><a:masterClrMapping/></p:clrMapOvr></p:notes>")
                put("ppt/notesSlides/_rels/notesSlide\(n).xml.rels", OTXML.rels([("rId1", "notesMaster", "../notesMasters/notesMaster1.xml"), ("rId2", "slide", "../slides/slide\(n).xml")]))
                overrides += "<Override PartName=\"/ppt/notesSlides/notesSlide\(n).xml\" ContentType=\"application/vnd.openxmlformats-officedocument.presentationml.notesSlide+xml\"/>"
            }
            put("ppt/slides/_rels/slide\(n).xml.rels", OTXML.rels(slideRels))
            presRels.append(("rId\(n + 10)", "slide", "slides/slide\(n).xml"))
            sldIds += "<p:sldId id=\"\(255 + n)\" r:id=\"rId\(n + 10)\"/>"
            overrides += "<Override PartName=\"/ppt/slides/slide\(n).xml\" ContentType=\"application/vnd.openxmlformats-officedocument.presentationml.slide+xml\"/>"
        }
        var notesList = ""
        if hasNotes {
            presRels.append(("rId3", "notesMaster", "notesMasters/notesMaster1.xml"))
            notesList = "<p:notesMasterIdLst><p:notesMasterId r:id=\"rId3\"/></p:notesMasterIdLst>"
            overrides += "<Override PartName=\"/ppt/notesMasters/notesMaster1.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.presentationml.notesMaster+xml\"/><Override PartName=\"/ppt/theme/theme2.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.theme+xml\"/>"
        }
        presRels.append(("rId4", "presProps", "presProps.xml"))
        presRels.append(("rId5", "viewProps", "viewProps.xml"))
        presRels.append(("rId6", "tableStyles", "tableStyles.xml"))
        put("ppt/presProps.xml", OTXML.header + "<p:presentationPr \(ns)/>")
        put("ppt/viewProps.xml", OTXML.header + "<p:viewPr \(ns)><p:normalViewPr><p:restoredLeft sz=\"15620\"/><p:restoredTop sz=\"94660\"/></p:normalViewPr><p:gridSpacing cx=\"76200\" cy=\"76200\"/></p:viewPr>")
        put("ppt/tableStyles.xml", OTXML.header + "<a:tblStyleLst xmlns:a=\"http://schemas.openxmlformats.org/drawingml/2006/main\" def=\"{5C22544A-7EE6-4342-B048-85BDC9FD1C3A}\"/>")
        put("ppt/presentation.xml", OTXML.header + "<p:presentation \(ns) saveSubsetFonts=\"1\"><p:sldMasterIdLst><p:sldMasterId id=\"2147483648\" r:id=\"rId1\"/></p:sldMasterIdLst>\(notesList)<p:sldIdLst>\(sldIds)</p:sldIdLst><p:sldSz cx=\"\(cx)\" cy=\"\(cy)\"/><p:notesSz cx=\"6858000\" cy=\"9144000\"/><p:defaultTextStyle><a:lvl1pPr><a:defRPr lang=\"zh-TW\"/></a:lvl1pPr></p:defaultTextStyle></p:presentation>")
        put("ppt/_rels/presentation.xml.rels", OTXML.rels(presRels))
        let exts = Set(slides.map(\.ext))
        var defaults = "<Default Extension=\"rels\" ContentType=\"application/vnd.openxmlformats-package.relationships+xml\"/><Default Extension=\"xml\" ContentType=\"application/xml\"/>"
        if exts.contains("png") { defaults += "<Default Extension=\"png\" ContentType=\"image/png\"/>" }
        if exts.contains("jpg") { defaults += "<Default Extension=\"jpg\" ContentType=\"image/jpeg\"/>" }
        put("[Content_Types].xml", OTXML.header + "<Types xmlns=\"http://schemas.openxmlformats.org/package/2006/content-types\">\(defaults)<Override PartName=\"/ppt/presentation.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.presentationml.presentation.main+xml\"/><Override PartName=\"/ppt/slideMasters/slideMaster1.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.presentationml.slideMaster+xml\"/><Override PartName=\"/ppt/slideLayouts/slideLayout1.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.presentationml.slideLayout+xml\"/><Override PartName=\"/ppt/theme/theme1.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.theme+xml\"/><Override PartName=\"/ppt/presProps.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.presentationml.presProps+xml\"/><Override PartName=\"/ppt/viewProps.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.presentationml.viewProps+xml\"/><Override PartName=\"/ppt/tableStyles.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.presentationml.tableStyles+xml\"/>\(overrides)<Override PartName=\"/docProps/core.xml\" ContentType=\"application/vnd.openxmlformats-package.core-properties+xml\"/><Override PartName=\"/docProps/app.xml\" ContentType=\"application/vnd.openxmlformats-officedocument.extended-properties+xml\"/></Types>")
        put("_rels/.rels", OTXML.header + "<Relationships xmlns=\"http://schemas.openxmlformats.org/package/2006/relationships\"><Relationship Id=\"rId1\" Type=\"\(OTXML.relNS)/officeDocument\" Target=\"ppt/presentation.xml\"/><Relationship Id=\"rId2\" Type=\"http://schemas.openxmlformats.org/package/2006/relationships/metadata/core-properties\" Target=\"docProps/core.xml\"/><Relationship Id=\"rId3\" Type=\"\(OTXML.relNS)/extended-properties\" Target=\"docProps/app.xml\"/></Relationships>")
        put("docProps/core.xml", OTXML.coreProps(title: title))
        put("docProps/app.xml", OTXML.appProps())
        try await OTXML.writePackage(parts, to: out)
    }

    static let theme = OTXML.header + """
    <a:theme xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main" name="Office Theme"><a:themeElements><a:clrScheme name="Office"><a:dk1><a:sysClr val="windowText" lastClr="000000"/></a:dk1><a:lt1><a:sysClr val="window" lastClr="FFFFFF"/></a:lt1><a:dk2><a:srgbClr val="44546A"/></a:dk2><a:lt2><a:srgbClr val="E7E6E6"/></a:lt2><a:accent1><a:srgbClr val="4472C4"/></a:accent1><a:accent2><a:srgbClr val="ED7D31"/></a:accent2><a:accent3><a:srgbClr val="A5A5A5"/></a:accent3><a:accent4><a:srgbClr val="FFC000"/></a:accent4><a:accent5><a:srgbClr val="5B9BD5"/></a:accent5><a:accent6><a:srgbClr val="70AD47"/></a:accent6><a:hlink><a:srgbClr val="0563C1"/></a:hlink><a:folHlink><a:srgbClr val="954F72"/></a:folHlink></a:clrScheme><a:fontScheme name="Office"><a:majorFont><a:latin typeface="Calibri Light"/><a:ea typeface=""/><a:cs typeface=""/><a:font script="Hant" typeface="微軟正黑體"/></a:majorFont><a:minorFont><a:latin typeface="Calibri"/><a:ea typeface=""/><a:cs typeface=""/><a:font script="Hant" typeface="微軟正黑體"/></a:minorFont></a:fontScheme><a:fmtScheme name="Office"><a:fillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:fillStyleLst><a:lnStyleLst><a:ln w="6350"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln><a:ln w="12700"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln><a:ln w="19050"><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:ln></a:lnStyleLst><a:effectStyleLst><a:effectStyle><a:effectLst/></a:effectStyle><a:effectStyle><a:effectLst/></a:effectStyle><a:effectStyle><a:effectLst/></a:effectStyle></a:effectStyleLst><a:bgFillStyleLst><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill><a:solidFill><a:schemeClr val="phClr"/></a:solidFill></a:bgFillStyleLst></a:fmtScheme></a:themeElements></a:theme>
    """
}
