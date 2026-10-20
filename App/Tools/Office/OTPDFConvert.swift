import AppKit
import CoreText
import ImageIO
import PDFKit
import UniformTypeIdentifiers

// macOS 內建引擎：PDF → Word／Excel／PowerPoint，以及 PDF 後處理（中文字對應修正、備忘稿頁面）。

enum OTPDFConvert {
    // MARK: - PDF → Word

    /// 可編輯文字：重建段落、標題、粗斜體、顏色、字級、表格與圖片。
    /// 每頁 PDF 從新的一頁開始（跨頁的段落會接起來）；頁面大小改變時才分節。
    static func toWordText(_ input: URL, ocr: Bool, context: ToolContext) async throws -> URL {
        let doc = try OTPDFText.open(input)
        struct PageContent {
            var size: CGSize
            var margins: NSEdgeInsets
            var blocks: [OTBlock]
        }
        var pages: [PageContent] = []
        var characters = 0
        for i in 0..<doc.pageCount {
            try context.checkCancellation()
            context.progress(Double(i) / Double(doc.pageCount) * 0.9, "正在分析第 \(i + 1)／\(doc.pageCount) 頁…")
            guard let page = doc.page(at: i) else { continue }
            let text = try OTPDFText.pageText(page, ocr: ocr)
            characters += text.lines.reduce(0) { $0 + $1.text.count }
            var blocks = OTLayout.blocks(text)
            // 圖片依垂直位置插進段落之間
            let images = autoreleasepool { OTPDFImages.extract(page) }
            for image in images {
                let index = blocks.firstIndex { $0.top >= image.frame.minY - 2 } ?? blocks.count
                blocks.insert(.image(image), at: index)
                // 圖片後面那段的間距本來包含圖片高度，改小
                if index + 1 < blocks.count, case .paragraph(var next) = blocks[index + 1] {
                    next.spaceBefore = min(next.spaceBefore, 6)
                    blocks[index + 1] = .paragraph(next)
                }
            }
            let size = text.size
            let frames = text.lines.map(\.frame) + images.map(\.frame)
            let left = clamp(frames.map(\.minX).min() ?? 72, 18, 144)
            let right = clamp(size.width - (frames.map(\.maxX).max() ?? size.width - 72), 18, 144)
            let top = clamp((frames.map(\.minY).min() ?? 72) - 2, 18, 144)
            let bottom = clamp(min(size.height - (frames.map(\.maxY).max() ?? size.height - 72), 54), 18, 72)
            pages.append(PageContent(size: size, margins: NSEdgeInsets(top: top, left: left, bottom: bottom, right: right), blocks: blocks))
        }
        guard characters > 0 || pages.contains(where: { $0.blocks.contains { if case .image = $0 { return true } else { return false } } }) else {
            throw ConversionError.emptyContent(ocr ? "PDF 中沒有文字，文字辨識也找不到內容。可以改用「保留原始版面」模式。"
                                                  : "PDF 中沒有文字層（可能是掃描檔）。請打開「辨識掃描頁面的文字」，或改用「保留原始版面」模式。")
        }

        // 跨頁的段落接起來：上一頁最後一段沒有句尾標點、下一頁第一段字級相同
        var continued = Array(repeating: false, count: pages.count)
        for i in 1..<max(pages.count, 1) {
            guard case .paragraph(var last)? = pages[i - 1].blocks.last, case .paragraph(let first)? = pages[i].blocks.first,
                  last.headingLevel == 0, first.headingLevel == 0, first.alignment == "left",
                  let lastLine = last.lines.last, let firstLine = first.lines.first,
                  abs(lastLine.size - firstLine.size) < 0.6, first.firstLineIndent < firstLine.size,
                  let end = lastLine.text.trimmingCharacters(in: .whitespaces).last, !"。！？.!?:：;；」』)）]".contains(end)
            else { continue }
            last.lines += first.lines
            pages[i - 1].blocks[pages[i - 1].blocks.count - 1] = .paragraph(last)
            pages[i].blocks.removeFirst()
            continued[i] = true
        }

        // 輸出：同樣大小的頁面放在同一節，以「段落前分頁」對應 PDF 的換頁
        let builder = OTDocxBuilder()
        func sectionMargins(_ range: [PageContent]) -> NSEdgeInsets {
            func median(_ values: [CGFloat]) -> CGFloat { OTLayout.median(values) ?? 72 }
            return NSEdgeInsets(top: median(range.map(\.margins.top)), left: median(range.map(\.margins.left)),
                                bottom: median(range.map(\.margins.bottom)), right: median(range.map(\.margins.right)))
        }
        var sectionStart = 0
        var lastSection = OTDocxBuilder.Section(size: pages.first?.size ?? OTPaper.a4, margins: NSEdgeInsets(top: 72, left: 72, bottom: 72, right: 72))
        for (i, page) in pages.enumerated() {
            let sizeChanged = i > 0 && (abs(page.size.width - pages[i - 1].size.width) > 1 || abs(page.size.height - pages[i - 1].size.height) > 1)
            if sizeChanged {
                let range = Array(pages[sectionStart..<i])
                builder.endSection(OTDocxBuilder.Section(size: range[0].size, margins: sectionMargins(range)))
                sectionStart = i
            }
            let margins = sectionMargins(Array(pages[sectionStart...min(i, pages.count - 1)]))
            let width = page.size.width - margins.left - margins.right
            for (index, block) in page.blocks.enumerated() {
                let breakBefore = index == 0 && i > 0 && !sizeChanged && !continued[i]
                switch block {
                case .paragraph(var p):
                    if index == 0 { p.spaceBefore = 0 }
                    p.leftIndent = max(0, p.leftIndent + (page.margins.left - margins.left))
                    builder.paragraph(p, pageBreakBefore: breakBefore)
                case .table(var t):
                    if index == 0 { t.spaceBefore = 0 }
                    builder.table(t, maxWidth: width, pageBreakBefore: breakBefore)
                case .image(let image):
                    var size = image.frame.size
                    if size.width > width { size = CGSize(width: width, height: size.height * width / size.width) }
                    let maxH = page.size.height - margins.top - margins.bottom - 4
                    if size.height > maxH { size = CGSize(width: size.width * maxH / size.height, height: maxH) }
                    let center = abs(image.frame.midX - page.size.width / 2) < page.size.width * 0.08
                    builder.image(image.data, ext: "jpg", size: size, alignment: center ? "center" : "left",
                                  spaceBefore: index == 0 ? 0 : 4, pageBreakBefore: breakBefore)
                }
            }
            if page.blocks.isEmpty && !continued[i] {
                builder.paragraph(OTParagraph(lines: []), pageBreakBefore: i > 0 && !sizeChanged)
            }
        }
        if !pages.isEmpty {
            let range = Array(pages[sectionStart...])
            lastSection = OTDocxBuilder.Section(size: range[0].size, margins: sectionMargins(range))
        }
        context.progress(0.92, "正在寫入 Word 檔…")
        let out = try outputURL(for: input, ext: "docx")
        try await builder.write(to: out, lastSection: lastSection, title: CVFileNaming.baseName(of: input))
        return out
    }

    /// 保留原始版面：每頁一張圖，頁面大小與 PDF 相同。
    static func toWordImages(_ input: URL, dpi: Double, context: ToolContext) async throws -> URL {
        let doc = try OTPDFText.open(input)
        let builder = OTDocxBuilder()
        var lastSection = OTDocxBuilder.Section(size: OTPaper.a4, margins: NSEdgeInsets())
        for i in 0..<doc.pageCount {
            try context.checkCancellation()
            context.progress(Double(i) / Double(doc.pageCount) * 0.9, "正在輸出第 \(i + 1)／\(doc.pageCount) 頁…")
            guard let page = doc.page(at: i), let cg = page.pageRef else { continue }
            let size = OTPDFText.displaySize(page)
            let data = try autoreleasepool { () throws -> Data in
                guard let image = CVPDFConverter.render(page: cg, dpi: dpi) else { throw ConversionError.other("第 \(i + 1) 頁繪製失敗。") }
                return try encode(image, jpegQuality: 0.9)
            }
            builder.image(data, ext: "jpg", size: CGSize(width: size.width, height: max(1, size.height - 2)), alignment: "left")
            let section = OTDocxBuilder.Section(size: size, margins: NSEdgeInsets(top: 0, left: 0, bottom: 0, right: 0))
            if i < doc.pageCount - 1 { builder.endSection(section) } else { lastSection = section }
        }
        context.progress(0.92, "正在寫入 Word 檔…")
        let out = try outputURL(for: input, ext: "docx")
        try await builder.write(to: out, lastSection: lastSection, title: CVFileNaming.baseName(of: input))
        return out
    }

    // MARK: - PDF → Excel

    struct ExcelOptions: Sendable, Equatable {
        var mergeSheets = false
        var convertNumbers = true
        var ocr = true
    }

    /// 依文字位置重建表格：同一高度的文字成一列，依各列的水平間隔切出欄。
    static func toExcel(_ input: URL, options: ExcelOptions, context: ToolContext) async throws -> URL {
        let doc = try OTPDFText.open(input)
        var sheets: [OTSheet] = []
        var merged: [[OTCell]] = []
        var lastHeader: [OTCell]?
        var anyText = false
        for i in 0..<doc.pageCount {
            try context.checkCancellation()
            context.progress(Double(i) / Double(doc.pageCount) * 0.9, "正在擷取第 \(i + 1)／\(doc.pageCount) 頁的表格…")
            guard let page = doc.page(at: i) else { continue }
            let text = try OTPDFText.pageText(page, ocr: options.ocr)
            let grid = tableGrid(text)
            if grid.isEmpty { continue }
            anyText = true
            let cells = grid.map { $0.map { OTCell.parse($0, convertNumbers: options.convertNumbers) } }
            let headerIndex = headerRow(cells)
            let header = headerIndex == 0
            if options.mergeSheets {
                var rows = cells
                // 跨頁重複的表頭只留第一次
                if header, let last = lastHeader, let first = rows.first, first == last { rows.removeFirst() }
                if !merged.isEmpty && !rows.isEmpty && !(header && lastHeader != nil && cells.first == lastHeader) { merged.append([]) }
                merged += rows
                if header { lastHeader = cells.first }
            } else {
                sheets.append(OTSheet(name: "第 \(i + 1) 頁", rows: cells, headerRow: headerIndex))
            }
        }
        guard anyText else {
            throw ConversionError.emptyContent(options.ocr ? "PDF 中找不到文字或表格。" : "PDF 中沒有文字層（可能是掃描檔），請打開「辨識掃描頁面的文字」。")
        }
        if options.mergeSheets {
            sheets = [OTSheet(name: CVFileNaming.baseName(of: input), rows: merged, headerRow: headerRow(merged))]
        }
        context.progress(0.93, "正在寫入 Excel 檔…")
        let out = try outputURL(for: input, ext: "xlsx")
        try await OTXLSXWriter.write(sheets, to: out, title: CVFileNaming.baseName(of: input))
        return out
    }

    /// 一頁 → 文字格子（列 × 欄）。
    static func tableGrid(_ page: OTPageText) -> [[String]] {
        let rows = OTLayout.rows(page.lines).map { OTLayout.cells(in: $0) }
        guard !rows.isEmpty else { return [] }
        let body = OTLayout.median(page.lines.map(\.size)) ?? 10
        let multi = rows.filter { $0.count >= 2 }
        var bands = OTLayout.columnBands(multi.isEmpty ? rows : multi, gap: body * 0.6)
        if bands.isEmpty { bands = [0...page.size.width] }
        var grid: [[String]] = []
        for row in rows {
            var cells = Array(repeating: "", count: bands.count)
            for cell in row {
                // 單獨一格、橫跨好幾欄的文字（標題、說明）放在它開始的那一欄
                let spanning = bands.filter { $0.upperBound > cell.frame.minX && $0.lowerBound < cell.frame.maxX }
                let i = row.count == 1 && spanning.count > 1
                    ? (bands.firstIndex { $0.upperBound > cell.frame.minX } ?? 0)
                    : OTLayout.bandIndex(cell.frame, bands: bands)
                cells[i] = cells[i].isEmpty ? cell.text : cells[i] + " " + cell.text
            }
            grid.append(cells.map { $0.replacingOccurrences(of: "\t", with: " ").trimmingCharacters(in: .whitespaces) })
        }
        // 去掉完全空白的欄
        let used = (0..<bands.count).filter { c in grid.contains { !$0[c].isEmpty } }
        return grid.map { row in used.map { row[$0] } }
    }

    static func looksLikeHeader(_ rows: [[OTCell]]) -> Bool { headerRow(rows) == 0 }

    /// 表頭列：前幾列中第一個「兩格以上、全是文字」而且下面幾列有數字的列。
    static func headerRow(_ rows: [[OTCell]]) -> Int? {
        for (index, row) in rows.prefix(6).enumerated() {
            let texts = row.filter { if case .text = $0 { return true } else { return false } }.count
            let nonEmpty = row.filter { $0 != .empty }.count
            guard nonEmpty >= 2 else { continue }
            guard texts == nonEmpty else { return nil }
            let below = rows.dropFirst(index + 1).prefix(5)
            return below.contains { r in r.contains { if case .number = $0 { return true } else { return false } } } ? index : nil
        }
        return nil
    }

    // MARK: - PDF → PowerPoint

    enum SlideSize: String, Sendable { case auto, wide, standard }

    struct PowerPointOptions: Sendable, Equatable {
        var slideSize: SlideSize = .auto
        var dpi: Double = 220
        var png = false
        var notesFromText = false
    }

    static func toPowerPoint(_ input: URL, options: PowerPointOptions, context: ToolContext) async throws -> URL {
        let doc = try OTPDFText.open(input)
        guard let first = doc.page(at: 0) else { throw ConversionError.emptyContent("PDF 沒有任何頁面。") }
        var slideSize: CGSize
        switch options.slideSize {
        case .wide: slideSize = CGSize(width: 960, height: 540)
        case .standard: slideSize = CGSize(width: 720, height: 540)
        case .auto:
            slideSize = OTPDFText.displaySize(first)
            // PowerPoint 的投影片大小限制：1～56 英吋
            let longest = max(slideSize.width, slideSize.height)
            if longest > 4000 { slideSize = CGSize(width: slideSize.width * 4000 / longest, height: slideSize.height * 4000 / longest) }
            let shortest = min(slideSize.width, slideSize.height)
            if shortest < 72 { slideSize = CGSize(width: slideSize.width * 72 / shortest, height: slideSize.height * 72 / shortest) }
        }
        var slides: [OTSlideImage] = []
        for i in 0..<doc.pageCount {
            try context.checkCancellation()
            context.progress(Double(i) / Double(doc.pageCount) * 0.88, "正在輸出第 \(i + 1)／\(doc.pageCount) 頁…")
            guard let page = doc.page(at: i), let cg = page.pageRef else { continue }
            // 依投影片大小決定解析度：至少讓長邊達到 dpi 的品質
            let size = OTPDFText.displaySize(page)
            let scaleToSlide = max(slideSize.width / max(size.width, 1), slideSize.height / max(size.height, 1))
            let dpi = min(600, options.dpi * max(1, scaleToSlide))
            let slide = try autoreleasepool { () throws -> OTSlideImage in
                guard let image = CVPDFConverter.render(page: cg, dpi: dpi) else { throw ConversionError.other("第 \(i + 1) 頁繪製失敗。") }
                let data = options.png ? try encodePNG(image) : try encode(image, jpegQuality: options.dpi >= 220 ? 0.92 : 0.85)
                var notes: String?
                if options.notesFromText {
                    notes = OTPDFText.normalize(page.string ?? "").trimmingCharacters(in: .whitespacesAndNewlines)
                    if let n = notes, n.count > 20_000 { notes = String(n.prefix(20_000)) }
                }
                return OTSlideImage(data: data, ext: options.png ? "png" : "jpg", pixelSize: CGSize(width: image.width, height: image.height), notes: notes)
            }
            slides.append(slide)
        }
        context.progress(0.92, "正在寫入 PowerPoint 檔…")
        let out = try outputURL(for: input, ext: "pptx")
        try await OTPPTXWriter.write(slides, slideSize: slideSize, to: out, title: CVFileNaming.baseName(of: input))
        return out
    }

    // MARK: - 共用

    static func clamp(_ v: CGFloat, _ lo: CGFloat, _ hi: CGFloat) -> CGFloat { min(max(v, lo), hi) }

    static func outputURL(for input: URL, ext: String) throws -> URL {
        try CVFileNaming.makeScratchDirectory().appendingPathComponent(CVFileNaming.fileName(CVFileNaming.sanitize(CVFileNaming.baseName(of: input)), ext))
    }

    static func encode(_ image: CGImage, jpegQuality: Double) throws -> Data {
        let data = NSMutableData()
        guard let dest = CGImageDestinationCreateWithData(data, UTType.jpeg.identifier as CFString, 1, nil) else {
            throw ConversionError.writeFailed("無法產生 JPEG。")
        }
        CGImageDestinationAddImage(dest, image, [kCGImageDestinationLossyCompressionQuality: jpegQuality] as CFDictionary)
        guard CGImageDestinationFinalize(dest) else { throw ConversionError.writeFailed("無法產生 JPEG。") }
        return data as Data
    }

    static func encodePNG(_ image: CGImage) throws -> Data {
        let data = NSMutableData()
        guard let dest = CGImageDestinationCreateWithData(data, UTType.png.identifier as CFString, 1, nil) else {
            throw ConversionError.writeFailed("無法產生 PNG。")
        }
        CGImageDestinationAddImage(dest, image, nil)
        guard CGImageDestinationFinalize(dest) else { throw ConversionError.writeFailed("無法產生 PNG。") }
        return data as Data
    }

    /// PDF → 每頁一張圖片（PNG／JPG）；寬度以像素指定。一頁時輸出單檔，多頁放進資料夾。
    static func toImages(_ pdf: URL, baseName: String, format: String, width: Int, context: ToolContext) throws -> URL {
        let doc = try OTPDFText.open(pdf)
        let work = try CVFileNaming.makeScratchDirectory()
        let ext = format == "png" ? "png" : "jpg"
        let digits = max(2, String(doc.pageCount).count)
        let folder = doc.pageCount == 1 ? work : work.appendingPathComponent("\(baseName)-投影片", isDirectory: true)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        var single: URL?
        for i in 0..<doc.pageCount {
            try context.checkCancellation()
            context.progress(Double(i) / Double(doc.pageCount), "正在輸出第 \(i + 1)／\(doc.pageCount) 張…")
            guard let page = doc.page(at: i), let cg = page.pageRef else { continue }
            let size = OTPDFText.displaySize(page)
            let dpi = Double(width) / Double(max(size.width, 1)) * 72
            try autoreleasepool {
                guard let image = CVPDFConverter.render(page: cg, dpi: dpi) else { throw ConversionError.other("第 \(i + 1) 頁繪製失敗。") }
                let data = ext == "png" ? try encodePNG(image) : try encode(image, jpegQuality: 0.9)
                let number = String(repeating: "0", count: max(0, digits - String(i + 1).count)) + String(i + 1)
                let name = doc.pageCount == 1 ? CVFileNaming.fileName(baseName, ext) : CVFileNaming.fileName("\(baseName)-\(number)", ext)
                let url = folder.appendingPathComponent(name)
                try data.write(to: url)
                single = url
            }
        }
        if doc.pageCount == 1, let single { return single }
        return folder
    }
}

// MARK: - PDF 中文字對應修正

/// macOS 輸出 PDF 時，某些漢字（文、一、日、大、用…）的 ToUnicode 對應會寫成康熙部首（⽂、⼀、⽇…），
/// 造成在 PDF 裡搜尋或複製文字時變成怪字。這裡直接修正 PDF 的 ToUnicode 對照表；遇到看不懂的結構就保持原樣。
enum OTPDFUnicodeFix {
    @discardableResult
    static func fix(_ url: URL) -> Bool {
        guard let data = try? Data(contentsOf: url), data.count < 400_000_000 else { return false }
        guard let fixed = fixed(data) else { return false }
        do {
            try fixed.write(to: url, options: .atomic)
            return PDFDocument(url: url) != nil
        } catch {
            return false
        }
    }

    static func fixed(_ data: Data) -> Data? {
        let bytes = [UInt8](data)
        // 只處理單一傳統 xref 表（macOS 產生的 PDF 都是這種）
        guard let startxrefPos = lastIndex(of: Array("startxref".utf8), in: bytes) else { return nil }
        guard let xrefOffset = readInt(bytes, from: startxrefPos + 9), xrefOffset < bytes.count,
              Array(bytes[xrefOffset..<min(xrefOffset + 4, bytes.count)]) == Array("xref".utf8) else { return nil }
        var p = xrefOffset + 4
        skipWS(bytes, &p)
        guard let firstObj = readInt(bytes, from: p, advance: &p), firstObj == 0,
              let count = readInt(bytes, from: p, advance: &p) else { return nil }
        skipWS(bytes, &p)
        var offsets: [(obj: Int, offset: Int, gen: String)] = []
        var original: [String] = []
        // 每個條目固定 20 位元組：「0000012345 00000 n \n」
        for i in 0..<count {
            let start = p + i * 20
            guard start + 18 <= bytes.count else { return nil }
            let entry = String(decoding: bytes[start..<(start + 18)], as: UTF8.self)
            let fields = entry.split(separator: " ")
            guard fields.count == 3, let off = Int(fields[0]) else { return nil }
            original.append(entry)
            if fields[2] == "n" && off > 0 { offsets.append((i, off, String(fields[1]))) }
        }
        p += count * 20
        // 後面不能有增量更新（/Prev）
        guard let trailerPos = indexOf(Array("trailer".utf8), in: bytes, from: p) else { return nil }
        let trailer = String(decoding: bytes[trailerPos..<startxrefPos], as: UTF8.self)
        if trailer.contains("/Prev") || trailer.contains("/XRefStm") { return nil }

        let sorted = offsets.sorted { $0.offset < $1.offset }
        guard let firstOffset = sorted.first?.offset, sorted.last!.offset < xrefOffset else { return nil }
        var out = Data(bytes[0..<firstOffset])
        var newOffsets: [Int: Int] = [:]
        var changed = false
        for (k, item) in sorted.enumerated() {
            let end = k + 1 < sorted.count ? sorted[k + 1].offset : xrefOffset
            guard end > item.offset else { return nil }
            var chunk = Array(bytes[item.offset..<end])
            if let replaced = fixObject(chunk) { chunk = replaced; changed = true }
            newOffsets[item.obj] = out.count
            out.append(contentsOf: chunk)
        }
        guard changed else { return nil }
        let newXref = out.count
        var xref = "xref\n0 \(count)\n"
        let gens = Dictionary(uniqueKeysWithValues: offsets.map { ($0.obj, $0.gen) })
        for i in 0..<count {
            if let off = newOffsets[i] {
                xref += String(format: "%010d %@ n", off, gens[i] ?? "00000") + " \n"
            } else {
                xref += original[i] + " \n"
            }
        }
        out.append(contentsOf: Array(xref.utf8))
        out.append(contentsOf: Array(trailer.utf8))
        out.append(contentsOf: Array("startxref\n\(newXref)\n%%EOF\n".utf8))
        return out
    }

    /// 一個物件：若是壓縮過的 ToUnicode CMap 而且含有部首對應，就改寫成未壓縮的修正版。
    static func fixObject(_ chunk: [UInt8]) -> [UInt8]? {
        guard let streamPos = indexOf(Array("stream".utf8), in: chunk, from: 0),
              let endPos = lastIndex(of: Array("endstream".utf8), in: chunk) else { return nil }
        let dict = String(decoding: chunk[0..<streamPos], as: UTF8.self)
        guard dict.contains("/FlateDecode"), !dict.contains("/DecodeParms"),
              dict.range(of: #"/Length\s+\d+\s+\d+\s+R"#, options: .regularExpression) == nil,
              !dict.contains("/Subtype"), !dict.contains("/Type /XObject") else { return nil }
        var dataStart = streamPos + 6
        if dataStart < chunk.count && chunk[dataStart] == 0x0D { dataStart += 1 }
        if dataStart < chunk.count && chunk[dataStart] == 0x0A { dataStart += 1 }
        var dataEnd = endPos
        while dataEnd > dataStart && (chunk[dataEnd - 1] == 0x0A || chunk[dataEnd - 1] == 0x0D) { dataEnd -= 1 }
        guard dataEnd - dataStart > 2, dataEnd - dataStart < 2_000_000 else { return nil }
        // zlib 標頭 2 位元組後是 raw deflate
        let compressed = Data(chunk[(dataStart + 2)..<dataEnd])
        guard let raw = try? (compressed as NSData).decompressed(using: .zlib) as Data else { return nil }
        let text = String(decoding: raw, as: UTF8.self)
        guard text.contains("begincmap"), text.contains("beginbf") else { return nil }
        // 逐行改「目的地」：bfrange 的第三個值（lo == hi 時）、bfchar 的第二個值
        var hits = 0
        var section = ""
        var lines: [String] = []
        for line in text.components(separatedBy: "\n") {
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            if trimmed.hasSuffix("beginbfrange") { section = "range" } else if trimmed.hasSuffix("beginbfchar") { section = "char" }
            else if trimmed == "endbfrange" || trimmed == "endbfchar" { section = "" }
            var newLine = line
            if !section.isEmpty, trimmed.hasPrefix("<") {
                let tokens = trimmed.components(separatedBy: "<").dropFirst().map { $0.components(separatedBy: ">").first ?? "" }
                let target = section == "range" ? (tokens.count == 3 && tokens[0].lowercased() == tokens[1].lowercased() ? 2 : -1) : (tokens.count == 2 ? 1 : -1)
                if target >= 0, let v = UInt32(tokens[target], radix: 16), let mapped = OTPDFText.radicalMap[v] {
                    var rebuilt = tokens
                    rebuilt[target] = String(format: "%04x", mapped.value)
                    newLine = rebuilt.map { "<\($0)>" }.joined(separator: section == "range" ? "" : " ")
                    hits += 1
                }
            }
            lines.append(newLine)
        }
        let mutable = lines.joined(separator: "\n")
        guard hits > 0 else { return nil }
        let newStream = Array(mutable.utf8)
        var newDict = dict.replacingOccurrences(of: #"\s*/Filter\s*/FlateDecode"#, with: "", options: .regularExpression)
        newDict = newDict.replacingOccurrences(of: #"/Length\s+\d+"#, with: "/Length \(newStream.count)", options: .regularExpression)
        var result = Array(newDict.utf8)
        result += Array("stream\n".utf8) + newStream + Array("\nendstream".utf8)
        result += Array(chunk[(endPos + 9)...])
        return result
    }

    // 位元組搜尋小工具
    static func indexOf(_ needle: [UInt8], in hay: [UInt8], from: Int) -> Int? {
        guard needle.count <= hay.count else { return nil }
        var i = from
        while i <= hay.count - needle.count {
            if hay[i] == needle[0] && Array(hay[i..<(i + needle.count)]) == needle { return i }
            i += 1
        }
        return nil
    }

    static func lastIndex(of needle: [UInt8], in hay: [UInt8]) -> Int? {
        guard needle.count <= hay.count else { return nil }
        var i = hay.count - needle.count
        while i >= 0 {
            if hay[i] == needle[0] && Array(hay[i..<(i + needle.count)]) == needle { return i }
            i -= 1
        }
        return nil
    }

    static func skipWS(_ b: [UInt8], _ p: inout Int) {
        while p < b.count && (b[p] == 0x20 || b[p] == 0x0A || b[p] == 0x0D || b[p] == 0x09) { p += 1 }
    }

    static func readInt(_ b: [UInt8], from: Int) -> Int? {
        var p = from
        return readInt(b, from: from, advance: &p)
    }

    static func readInt(_ b: [UInt8], from: Int, advance p: inout Int) -> Int? {
        p = from
        skipWS(b, &p)
        var value = 0, digits = 0
        while p < b.count && b[p] >= 0x30 && b[p] <= 0x39 {
            value = value * 10 + Int(b[p] - 0x30)
            p += 1
            digits += 1
        }
        return digits > 0 ? value : nil
    }
}

// MARK: - 簡報備忘稿

enum OTPresentationNotes {
    /// 從 .pptx 讀出每張投影片的備忘稿（依投影片順序）。
    static func read(_ pptx: URL) async throws -> [String] {
        let dir = try CVFileNaming.makeScratchDirectory()
        defer { try? FileManager.default.removeItem(at: dir) }
        let root = dir.appendingPathComponent("x", isDirectory: true)
        let unzip = try await OTProcess.run("/usr/bin/ditto", ["-x", "-k", pptx.path, root.path], timeout: 300)
        guard unzip.succeeded else { throw ConversionError.unreadable("無法解開簡報檔。") }
        func load(_ path: String) -> CVXMLNode? {
            guard let data = try? Data(contentsOf: root.appendingPathComponent(path)) else { return nil }
            return CVXML.parse(data)
        }
        func rels(_ path: String) -> [String: (type: String, target: String)] {
            guard let node = load(path) else { return [:] }
            var list: [CVXMLNode] = []
            node.descendants(named: "Relationship", into: &list)
            var map: [String: (String, String)] = [:]
            for r in list { if let id = r.attribute("Id"), let t = r.attribute("Type"), let target = r.attribute("Target") { map[id] = (t, target) } }
            return map
        }
        func resolve(_ base: String, _ target: String) -> String {
            if target.hasPrefix("/") { return String(target.dropFirst()) }
            var parts = (base as NSString).deletingLastPathComponent.split(separator: "/").map(String.init)
            for comp in target.split(separator: "/") {
                if comp == ".." { if !parts.isEmpty { parts.removeLast() } } else if comp != "." { parts.append(String(comp)) }
            }
            return parts.joined(separator: "/")
        }
        guard let pres = load("ppt/presentation.xml") else { throw ConversionError.unreadable("找不到簡報內容。") }
        let presRels = rels("ppt/_rels/presentation.xml.rels")
        var ids: [CVXMLNode] = []
        pres.descendants(named: "sldId", into: &ids)
        var notes: [String] = []
        for sld in ids {
            guard let rid = sld.attribute("id"), let rel = presRels[rid] else { notes.append(""); continue }
            let slidePath = resolve("ppt/presentation.xml", rel.target)
            let slideRels = rels((slidePath as NSString).deletingLastPathComponent + "/_rels/" + (slidePath as NSString).lastPathComponent + ".rels")
            guard let notesRel = slideRels.values.first(where: { $0.type.hasSuffix("/notesSlide") }),
                  let notesXML = load(resolve(slidePath, notesRel.target)) else { notes.append(""); continue }
            // 找 body 預留位置的文字
            var shapes: [CVXMLNode] = []
            notesXML.descendants(named: "sp", into: &shapes)
            var paragraphs: [String] = []
            for sp in shapes {
                var phs: [CVXMLNode] = []
                sp.descendants(named: "ph", into: &phs)
                guard phs.contains(where: { $0.attribute("type") == "body" }) else { continue }
                var ps: [CVXMLNode] = []
                sp.descendants(named: "p", into: &ps)
                for p in ps {
                    var ts: [CVXMLNode] = []
                    p.descendants(named: "t", into: &ts)
                    paragraphs.append(ts.map(\.text).joined())
                }
            }
            notes.append(paragraphs.joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines))
        }
        return notes
    }
}

/// 把投影片 PDF 加上備忘稿，排成「上方投影片、下方備忘稿」的 A4 直式頁面（像 PowerPoint 的備忘稿列印）。
enum OTNotesPages {
    static func compose(slides: URL, notes: [String], to out: URL) throws {
        guard let doc = PDFDocument(url: slides), doc.pageCount > 0 else { throw ConversionError.unreadable("投影片 PDF 無法讀取。") }
        let paper = OTPaper.portrait(OTPaper.preferred)
        let margin: CGFloat = 54
        var box = CGRect(origin: .zero, size: paper)
        guard let ctx = CGContext(out as CFURL, mediaBox: &box, [kCGPDFContextCreator: "QUICK_SEEMD"] as CFDictionary) else {
            throw ConversionError.writeFailed("無法建立 PDF。")
        }
        let font = NSFont(name: "Helvetica Neue", size: 11.5) ?? NSFont.systemFont(ofSize: 11.5)
        let style = NSMutableParagraphStyle()
        style.lineHeightMultiple = 1.3
        style.paragraphSpacing = 4
        let attrs: [NSAttributedString.Key: Any] = [.font: font, .foregroundColor: NSColor.black, .paragraphStyle: style]
        let numberAttrs: [NSAttributedString.Key: Any] = [.font: NSFont.systemFont(ofSize: 9), .foregroundColor: NSColor.gray]
        for i in 0..<doc.pageCount {
            guard let page = doc.page(at: i), let cg = page.pageRef else { continue }
            let src = page.bounds(for: .mediaBox)
            let maxW = paper.width - 2 * margin
            let maxH = paper.height * 0.45
            let scale = min(maxW / src.width, maxH / src.height)
            let w = src.width * scale, h = src.height * scale
            let slideRect = CGRect(x: (paper.width - w) / 2, y: paper.height - margin - h, width: w, height: h)
            ctx.beginPDFPage(nil)
            ctx.saveGState()
            ctx.translateBy(x: slideRect.minX, y: slideRect.minY)
            ctx.scaleBy(x: scale, y: scale)
            ctx.translateBy(x: -src.minX, y: -src.minY)
            ctx.drawPDFPage(cg)
            ctx.restoreGState()
            ctx.setStrokeColor(CGColor(gray: 0.75, alpha: 1))
            ctx.setLineWidth(0.5)
            ctx.stroke(slideRect)
            // 備忘稿文字
            let text = i < notes.count ? notes[i] : ""
            var remaining = NSAttributedString(string: text.isEmpty ? " " : text, attributes: attrs)
            var textTop = slideRect.minY - 24
            while true {
                let frameRect = CGRect(x: margin, y: margin, width: paper.width - 2 * margin, height: textTop - margin)
                let setter = CTFramesetterCreateWithAttributedString(remaining)
                let frame = CTFramesetterCreateFrame(setter, CFRange(location: 0, length: 0), CGPath(rect: frameRect, transform: nil), nil)
                CTFrameDraw(frame, ctx)
                drawNumber("\(i + 1)", ctx: ctx, paper: paper, attrs: numberAttrs)
                let visible = CTFrameGetVisibleStringRange(frame)
                if visible.location + visible.length >= remaining.length || visible.length == 0 { break }
                remaining = remaining.attributedSubstring(from: NSRange(location: visible.location + visible.length, length: remaining.length - visible.location - visible.length))
                ctx.endPDFPage()
                ctx.beginPDFPage(nil)
                textTop = paper.height - margin
            }
            ctx.endPDFPage()
        }
        ctx.closePDF()
    }

    private static func drawNumber(_ s: String, ctx: CGContext, paper: CGSize, attrs: [NSAttributedString.Key: Any]) {
        let line = CTLineCreateWithAttributedString(NSAttributedString(string: s, attributes: attrs))
        let width = CTLineGetTypographicBounds(line, nil, nil, nil)
        ctx.textPosition = CGPoint(x: (paper.width - width) / 2, y: 28)
        CTLineDraw(line, ctx)
    }
}
