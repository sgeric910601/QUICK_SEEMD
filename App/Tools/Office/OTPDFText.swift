import AppKit
import PDFKit
import Vision

// 從 PDF 取出「有位置的文字」：每一行的文字、字型、大小、顏色與位置（左上原點、已套用頁面旋轉）。
// PDF 轉 Word／Excel／PowerPoint 共用；沒有文字層的掃描頁改用 Vision 文字辨識。

/// 一段格式相同的文字。
struct OTTextRun: Sendable {
    var text: String
    var fontFamily: String
    var size: CGFloat
    var bold: Bool
    var italic: Bool
    /// RRGGBB；黑色為 nil
    var color: String?
}

/// 一行文字（版面座標：左上原點，單位為點）。
struct OTTextLine: Sendable {
    var frame: CGRect
    var runs: [OTTextRun]

    var text: String { runs.map(\.text).joined() }

    /// 這一行主要的字級（以字數加權）。
    var size: CGFloat {
        var weights: [CGFloat: Int] = [:]
        for r in runs { weights[(r.size * 2).rounded() / 2, default: 0] += r.text.count }
        return weights.max { $0.value < $1.value }?.key ?? frame.height * 0.75
    }

    var isBold: Bool {
        let total = runs.reduce(0) { $0 + $1.text.trimmingCharacters(in: .whitespaces).count }
        let bold = runs.filter(\.bold).reduce(0) { $0 + $1.text.trimmingCharacters(in: .whitespaces).count }
        return total > 0 && bold * 10 >= total * 8
    }
}

/// 一頁的文字內容。
struct OTPageText: Sendable {
    var size: CGSize
    var lines: [OTTextLine]
    var usedOCR = false
}

enum OTPDFText {
    // MARK: 文字正規化

    /// macOS 產生的 PDF 常把「文、一、日…」對應成康熙部首（⽂、⼀、⽇），這裡換回一般漢字。
    static func normalize(_ s: String) -> String {
        var out = String.UnicodeScalarView()
        var changed = false
        for scalar in s.unicodeScalars {
            if let mapped = radicalMap[scalar.value] {
                out.append(mapped)
                changed = true
            } else if (0xFB00...0xFB06).contains(scalar.value) {
                // 英文連字（ﬁ、ﬂ、ﬃ…）拆回一般字母
                out.append(contentsOf: String(Character(scalar)).precomposedStringWithCompatibilityMapping.unicodeScalars)
                changed = true
            } else if scalar.value == 0xFFFC || scalar.value == 0xFEFF || scalar.value == 0 {
                changed = true
            } else {
                out.append(scalar)
            }
        }
        return changed ? String(out) : s
    }

    /// 康熙部首（U+2F00–U+2FD5）→ 對應的統一漢字。
    static let radicalMap: [UInt32: Unicode.Scalar] = {
        var map: [UInt32: Unicode.Scalar] = [:]
        for v in UInt32(0x2F00)...UInt32(0x2FD5) {
            guard let scalar = Unicode.Scalar(v) else { continue }
            let folded = String(Character(scalar)).precomposedStringWithCompatibilityMapping.unicodeScalars
            if folded.count == 1, let f = folded.first, f.value != v { map[v] = f }
        }
        // CJK 部首補充中少數有對應的
        for (v, target) in [(UInt32(0x2E9F), UInt32(0x6BCD)), (0x2EF3, 0x9F9F)] {
            if let s = Unicode.Scalar(target) { map[v] = s }
        }
        return map
    }()

    // MARK: 座標

    /// 頁面實際顯示的大小（套用旋轉後）。
    static func displaySize(_ page: PDFPage) -> CGSize {
        let box = page.bounds(for: .cropBox)
        return page.rotation % 180 == 0 ? box.size : CGSize(width: box.height, height: box.width)
    }

    /// 把頁面座標（左下原點、未旋轉）換成顯示座標（左上原點、已旋轉）。
    static func toDisplay(_ rect: CGRect, page: PDFPage) -> CGRect {
        let box = page.bounds(for: .cropBox)
        let w = box.width, h = box.height
        func map(_ p: CGPoint) -> CGPoint {
            let u = p.x - box.minX, v = p.y - box.minY
            switch ((page.rotation % 360) + 360) % 360 {
            case 90: return CGPoint(x: v, y: u)
            case 180: return CGPoint(x: w - u, y: v)
            case 270: return CGPoint(x: h - v, y: w - u)
            default: return CGPoint(x: u, y: h - v)
            }
        }
        let a = map(CGPoint(x: rect.minX, y: rect.minY)), b = map(CGPoint(x: rect.maxX, y: rect.maxY))
        return CGRect(x: min(a.x, b.x), y: min(a.y, b.y), width: abs(a.x - b.x), height: abs(a.y - b.y))
    }

    // MARK: 字型

    static func family(of font: NSFont?) -> String {
        guard let font else { return "Helvetica" }
        var name = font.familyName ?? font.fontName
        if let plus = name.firstIndex(of: "+"), name.distance(from: name.startIndex, to: plus) == 6 {
            name = String(name[name.index(after: plus)...])
        }
        // PDF 內嵌字型常是 PostScript 名稱（Calibri-Bold、ArialMT…）
        for suffix in ["-BoldItalic", "-BoldOblique", "-Bold", "-Italic", "-Oblique", "-Regular", "-Roman", "-Light", "-Medium", "-Semibold", "MT", ",Bold", ",Italic", ",BoldItalic"] where name.hasSuffix(suffix) && name.count > suffix.count {
            name = String(name.dropLast(suffix.count))
        }
        switch name {
        case "STSongti-TC", "STSongti-SC", "Songti TC", "Songti SC": return name.contains("SC") ? "Songti SC" : "Songti TC"
        case "PingFangTC", "PingFang TC": return "PingFang TC"
        case "PingFangSC", "PingFang SC": return "PingFang SC"
        case "Times", "Times-Roman", "TimesNewRomanPS", "TimesNewRoman": return "Times New Roman"
        case "Arial", "ArialMT": return "Arial"
        default: return name
        }
    }

    static func traits(of font: NSFont?) -> (bold: Bool, italic: Bool) {
        guard let font else { return (false, false) }
        let t = font.fontDescriptor.symbolicTraits
        let name = font.fontName.lowercased()
        let bold = t.contains(.bold) || ["bold", "black", "heavy", "semibold", "demi"].contains { name.contains($0) }
        let italic = t.contains(.italic) || name.contains("italic") || name.contains("oblique")
        return (bold, italic)
    }

    static func hex(_ color: NSColor?) -> String? {
        guard let c = color?.usingColorSpace(.sRGB) else { return nil }
        let r = Int((c.redComponent * 255).rounded()), g = Int((c.greenComponent * 255).rounded()), b = Int((c.blueComponent * 255).rounded())
        if r < 40 && g < 40 && b < 40 { return nil }
        if r > 250 && g > 250 && b > 250 { return nil }
        return String(format: "%02X%02X%02X", r, g, b)
    }

    // MARK: 文字層

    /// 依 PDFKit 的分行取出文字行（含字型資訊）。
    static func lines(on page: PDFPage) -> [OTTextLine] {
        guard let all = page.selection(for: page.bounds(for: .cropBox)) else { return [] }
        var result: [OTTextLine] = []
        for line in all.selectionsByLine() {
            let raw = line.string ?? ""
            if raw.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { continue }
            let frame = toDisplay(line.bounds(for: page), page: page)
            if frame.width <= 0 || frame.height <= 0 { continue }
            var runs: [OTTextRun] = []
            if let attributed = line.attributedString, attributed.length > 0 {
                attributed.enumerateAttributes(in: NSRange(location: 0, length: attributed.length)) { attrs, range, _ in
                    let text = normalize(attributed.attributedSubstring(from: range).string.replacingOccurrences(of: "\n", with: " "))
                    if text.isEmpty { return }
                    let font = attrs[.font] as? NSFont
                    let (bold, italic) = traits(of: font)
                    let run = OTTextRun(text: text, fontFamily: family(of: font), size: font?.pointSize ?? frame.height * 0.75,
                                        bold: bold, italic: italic, color: hex(attrs[.foregroundColor] as? NSColor))
                    if let last = runs.last, last.fontFamily == run.fontFamily, abs(last.size - run.size) < 0.25,
                       last.bold == run.bold, last.italic == run.italic, last.color == run.color {
                        runs[runs.count - 1].text += run.text
                    } else {
                        runs.append(run)
                    }
                }
            } else {
                runs = [OTTextRun(text: normalize(raw), fontFamily: "Helvetica", size: frame.height * 0.75, bold: false, italic: false, color: nil)]
            }
            // 去掉頭尾空白
            while let first = runs.first, first.text.trimmingCharacters(in: .whitespaces).isEmpty { runs.removeFirst() }
            while let last = runs.last, last.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { runs.removeLast() }
            guard !runs.isEmpty else { continue }
            runs[0].text = String(runs[0].text.drop { $0 == " " })
            runs[runs.count - 1].text = String(runs[runs.count - 1].text.reversed().drop { $0 == " " || $0 == "\n" }.reversed())
            result.append(OTTextLine(frame: frame, runs: runs))
        }
        return result
    }

    /// 掃描頁：以 Vision 辨識文字行與位置。
    static func ocrLines(on page: PDFPage) throws -> [OTTextLine] {
        guard let cg = page.pageRef, let image = CVPDFConverter.render(page: cg, dpi: 220) else { return [] }
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.usesLanguageCorrection = true
        let wanted = ["zh-Hant", "zh-Hans", "en-US", "ja-JP", "ko-KR"]
        if let supported = try? request.supportedRecognitionLanguages() {
            let langs = wanted.filter { supported.contains($0) }
            if !langs.isEmpty { request.recognitionLanguages = langs }
        }
        try VNImageRequestHandler(cgImage: image, options: [:]).perform([request])
        let size = displaySize(page)
        var lines: [OTTextLine] = []
        for obs in request.results ?? [] {
            guard let text = obs.topCandidates(1).first?.string, !text.trimmingCharacters(in: .whitespaces).isEmpty else { continue }
            let b = obs.boundingBox
            let frame = CGRect(x: b.minX * size.width, y: (1 - b.maxY) * size.height, width: b.width * size.width, height: b.height * size.height)
            let fontSize = max(6, (frame.height * 0.78 * 2).rounded() / 2)
            lines.append(OTTextLine(frame: frame, runs: [OTTextRun(text: text, fontFamily: "PingFang TC", size: fontSize, bold: false, italic: false, color: nil)]))
        }
        return lines.sorted { abs($0.frame.midY - $1.frame.midY) > min($0.frame.height, $1.frame.height) * 0.5 ? $0.frame.midY < $1.frame.midY : $0.frame.minX < $1.frame.minX }
    }

    /// 取一頁的文字；沒有文字層而且允許 OCR 時改用辨識。
    static func pageText(_ page: PDFPage, ocr: Bool) throws -> OTPageText {
        var lines = autoreleasepool { Self.lines(on: page) }
        var used = false
        if lines.isEmpty && ocr {
            lines = try autoreleasepool { try ocrLines(on: page) }
            used = !lines.isEmpty
        }
        return OTPageText(size: displaySize(page), lines: lines, usedOCR: used)
    }

    static func open(_ url: URL) throws -> PDFDocument {
        guard let doc = PDFDocument(url: url) else { throw ConversionError.unreadable("PDF 檔案損毀或格式不正確。") }
        if doc.isLocked && !doc.unlock(withPassword: "") { throw ConversionError.passwordProtected }
        if doc.pageCount == 0 { throw ConversionError.emptyContent("PDF 沒有任何頁面。") }
        return doc
    }
}

// MARK: - 版面分析：段落與表格

/// 頁面上的一個區塊。
enum OTBlock: Sendable {
    case paragraph(OTParagraph)
    case table(OTTable)
    case image(OTImageBlock)

    var top: CGFloat {
        switch self {
        case .paragraph(let p): return p.frame.minY
        case .table(let t): return t.frame.minY
        case .image(let i): return i.frame.minY
        }
    }
}

/// 頁面上的一張圖（從頁面畫面裁下來）。
struct OTImageBlock: Sendable {
    var frame: CGRect
    var data: Data
    var spaceBefore: CGFloat = 0
}

struct OTParagraph: Sendable {
    var lines: [OTTextLine]
    var alignment: String = "left"   // left / center / right
    var headingLevel = 0             // 0：內文，1～3：標題
    var spaceBefore: CGFloat = 0
    var firstLineIndent: CGFloat = 0
    var leftIndent: CGFloat = 0

    var frame: CGRect { lines.dropFirst().reduce(lines.first?.frame ?? .zero) { $0.union($1.frame) } }

    /// 把各行接成段落文字（中文之間不加空白、英文斷字的連字號會去掉）。
    var runs: [OTTextRun] {
        var out: [OTTextRun] = []
        for (i, line) in lines.enumerated() {
            var lineRuns = line.runs
            if i > 0, let prev = out.last?.text.last, let next = lineRuns.first?.text.first {
                if prev == "-" && next.isLowercase {
                    out[out.count - 1].text.removeLast()
                } else if !(OTLayout.isCJK(prev) || OTLayout.isCJK(next)) && prev != " " {
                    lineRuns[0].text = " " + lineRuns[0].text
                }
            }
            for r in lineRuns {
                if let last = out.last, last.fontFamily == r.fontFamily, abs(last.size - r.size) < 0.25,
                   last.bold == r.bold, last.italic == r.italic, last.color == r.color {
                    out[out.count - 1].text += r.text
                } else {
                    out.append(r)
                }
            }
        }
        return out
    }
}

struct OTTable: Sendable {
    /// rows × columns，每格是若干行文字
    var cells: [[[OTTextLine]]]
    /// 欄寬（點）
    var columnWidths: [CGFloat]
    var frame: CGRect
    var spaceBefore: CGFloat = 0
}

enum OTLayout {
    static func isCJK(_ c: Character) -> Bool {
        guard let v = c.unicodeScalars.first?.value else { return false }
        return (0x2E80...0x9FFF).contains(v) || (0xF900...0xFAFF).contains(v) || (0xFF00...0xFFEF).contains(v) || (0x3000...0x303F).contains(v)
            || (0x20000...0x2FA1F).contains(v)
    }

    /// 同一列（垂直重疊一半以上）的文字行歸成一組，由上而下。
    static func rows(_ lines: [OTTextLine]) -> [[OTTextLine]] {
        let sorted = lines.sorted { $0.frame.minY < $1.frame.minY }
        var rows: [[OTTextLine]] = []
        var current: [OTTextLine] = []
        var band = CGRect.null
        for line in sorted {
            if let _ = current.first {
                let overlap = min(band.maxY, line.frame.maxY) - max(band.minY, line.frame.minY)
                if overlap >= min(band.height, line.frame.height) * 0.5 {
                    current.append(line)
                    band = band.union(line.frame)
                    continue
                }
                rows.append(current.sorted { $0.frame.minX < $1.frame.minX })
            }
            current = [line]
            band = line.frame
        }
        if !current.isEmpty { rows.append(current.sorted { $0.frame.minX < $1.frame.minX }) }
        return rows
    }

    /// 把一列中距離很近的片段合成一個儲存格（間距小於約一個字寬）。
    static func cells(in row: [OTTextLine]) -> [OTTextLine] {
        var cells: [OTTextLine] = []
        for line in row {
            if var last = cells.last {
                let gap = line.frame.minX - last.frame.maxX
                let em = max(4, min(last.size, line.size))
                // PDFKit 只在有明顯空隙時才把同一行拆開，所以只合併幾乎相連的片段
                if gap < em * 0.15 {
                    if gap > em * 0.2, let lastChar = last.runs.last?.text.last, let firstChar = line.runs.first?.text.first,
                       !(isCJK(lastChar) && isCJK(firstChar)) {
                        last.runs[last.runs.count - 1].text += " "
                    }
                    last.runs += line.runs
                    last.frame = last.frame.union(line.frame)
                    cells[cells.count - 1] = last
                    continue
                }
            }
            cells.append(line)
        }
        return cells
    }

    /// 由多格的列推出欄位邊界（把各列儲存格的水平範圍合併）。
    static func columnBands(_ rows: [[OTTextLine]], gap: CGFloat) -> [ClosedRange<CGFloat>] {
        var spans: [ClosedRange<CGFloat>] = []
        for row in rows {
            for cell in row { spans.append(cell.frame.minX...cell.frame.maxX) }
        }
        spans.sort { $0.lowerBound < $1.lowerBound }
        var merged: [ClosedRange<CGFloat>] = []
        for s in spans {
            if let last = merged.last, s.lowerBound <= last.upperBound + gap {
                merged[merged.count - 1] = last.lowerBound...max(last.upperBound, s.upperBound)
            } else {
                merged.append(s)
            }
        }
        return merged
    }

    static func bandIndex(_ frame: CGRect, bands: [ClosedRange<CGFloat>]) -> Int {
        var best = 0
        var bestOverlap: CGFloat = -.greatestFiniteMagnitude
        for (i, b) in bands.enumerated() {
            let overlap = min(b.upperBound, frame.maxX) - max(b.lowerBound, frame.minX)
            if overlap > bestOverlap { bestOverlap = overlap; best = i }
        }
        return best
    }

    /// 頁面 → 段落與表格。
    static func blocks(_ page: OTPageText, detectTables: Bool = true) -> [OTBlock] {
        let rowGroups = rows(page.lines).map { cells(in: $0) }
        guard !rowGroups.isEmpty else { return [] }
        let bodySize = median(page.lines.flatMap { line in Array(repeating: line.size, count: max(1, min(line.text.count, 200))) }) ?? 11

        // 1. 找表格：連續兩列以上、每列有兩格以上
        var blocks: [(kind: Int, rows: [[OTTextLine]])] = []   // kind 0：一般列，1：表格
        var i = 0
        while i < rowGroups.count {
            if detectTables, rowGroups[i].count >= 2 {
                var j = i
                while j < rowGroups.count && rowGroups[j].count >= 2 {
                    // 列與列之間距離太遠就不算同一個表格
                    if j > i {
                        let prevBottom = rowGroups[j - 1].map(\.frame.maxY).max() ?? 0
                        let top = rowGroups[j].map(\.frame.minY).min() ?? 0
                        if top - prevBottom > bodySize * 3.5 { break }
                    }
                    j += 1
                }
                if j - i >= 2 {
                    let bands = columnBands(Array(rowGroups[i..<j]), gap: bodySize * 0.15)
                    if bands.count >= 2 {
                        blocks.append((1, Array(rowGroups[i..<j])))
                        i = j
                        continue
                    }
                }
            }
            blocks.append((0, [rowGroups[i]]))
            i += 1
        }

        // 2. 一般列 → 段落
        var result: [OTBlock] = []
        var pending: [OTTextLine] = []
        var pendingSpace: CGFloat = 0
        var lastBottom: CGFloat?
        let textLeft = page.lines.map(\.frame.minX).min() ?? 0
        let textRight = page.lines.map(\.frame.maxX).max() ?? page.size.width

        func flushParagraph() {
            guard !pending.isEmpty else { return }
            var p = OTParagraph(lines: pending)
            p.spaceBefore = pendingSpace
            let f = p.frame
            let size = pending.map(\.size).max() ?? bodySize
            if size >= bodySize * 1.6 { p.headingLevel = 1 }
            else if size >= bodySize * 1.3 { p.headingLevel = 2 }
            else if size >= bodySize * 1.1 || (pending.count == 1 && pending[0].isBold && pending[0].text.count <= 40 && size >= bodySize * 0.95) {
                p.headingLevel = pending.count <= 2 ? 3 : 0
            }
            let center = (textLeft + textRight) / 2
            let width = textRight - textLeft
            if pending.allSatisfy({ abs($0.frame.midX - center) < width * 0.06 && $0.frame.width < width * 0.85 }) {
                p.alignment = "center"
            } else if pending.allSatisfy({ abs($0.frame.maxX - textRight) < 4 && $0.frame.minX > textLeft + width * 0.4 }) {
                p.alignment = "right"
            }
            if p.alignment == "left" {
                p.leftIndent = max(0, (pending.count > 1 ? pending[1].frame.minX : f.minX) - textLeft)
                p.firstLineIndent = pending[0].frame.minX - textLeft - p.leftIndent
            }
            result.append(.paragraph(p))
            pending = []
        }

        for block in blocks {
            if block.kind == 1 {
                flushParagraph()
                var table = makeTable(block.rows, bodySize: bodySize)
                let top = block.rows.first?.map(\.frame.minY).min() ?? 0
                table.spaceBefore = lastBottom.map { min(max(0, top - $0 - bodySize * 0.4), 36) } ?? 0
                result.append(.table(table))
                lastBottom = block.rows.last?.map(\.frame.maxY).max()
                continue
            }
            // 同一列有多段（但不成表格）：用 Tab 接起來
            var row = block.rows[0]
            var line = row.removeFirst()
            for other in row {
                line.runs[line.runs.count - 1].text += "\t"
                line.runs += other.runs
                line.frame = line.frame.union(other.frame)
            }
            if let last = pending.last {
                let gap = line.frame.minY - last.frame.maxY
                let sameSize = abs(line.size - last.size) <= max(0.6, last.size * 0.1)
                let lastEndsEarly = last.frame.maxX < textRight - max(last.size * 4, (textRight - textLeft) * 0.12)
                let startsBullet = line.text.first.map { "•●▪■◆-–*·➢✓□".contains($0) } ?? false
                    || line.text.range(of: #"^(\d{1,2}[\.、)）]|[（(]\d{1,2}[)）]|[一二三四五六七八九十]+、)"#, options: .regularExpression) != nil
                let indentJump = pending.count > 1 && abs(line.frame.minX - last.frame.minX) > last.size * 2.5
                if gap < last.size * 0.9 && gap > -last.size * 0.5 && sameSize && !lastEndsEarly && !startsBullet && !indentJump
                    && last.isBold == line.isBold {
                    pending.append(line)
                    lastBottom = line.frame.maxY
                    continue
                }
                flushParagraph()
            }
            let gap = lastBottom.map { max(0, line.frame.minY - $0) } ?? 0
            pendingSpace = gap > line.size * 0.9 ? min(gap - line.size * 0.4, 36) : 0
            pending = [line]
            lastBottom = line.frame.maxY
        }
        flushParagraph()
        return result
    }

    static func makeTable(_ rows: [[OTTextLine]], bodySize: CGFloat) -> OTTable {
        let bands = columnBands(rows, gap: bodySize * 0.15)
        var grid: [[[OTTextLine]]] = []
        for row in rows {
            var cells = Array(repeating: [OTTextLine](), count: bands.count)
            for cell in row { cells[bandIndex(cell.frame, bands: bands)].append(cell) }
            grid.append(cells)
        }
        var widths: [CGFloat] = []
        for (i, b) in bands.enumerated() {
            let next = i + 1 < bands.count ? bands[i + 1].lowerBound : b.upperBound + 8
            widths.append(max(24, next - b.lowerBound))
        }
        let frame = rows.flatMap { $0 }.dropFirst().reduce(rows.first?.first?.frame ?? .zero) { $0.union($1.frame) }
        return OTTable(cells: grid, columnWidths: widths, frame: frame)
    }

    static func median(_ values: [CGFloat]) -> CGFloat? {
        guard !values.isEmpty else { return nil }
        let s = values.sorted()
        return s[s.count / 2]
    }
}
