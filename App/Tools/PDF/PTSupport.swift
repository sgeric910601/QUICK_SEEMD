import AppKit
import CoreText
import PDFKit
import SwiftUI

// PDF 工具共用的開檔、頁碼、重繪、書籤與文字繪製小工具。

// MARK: - 開檔與寫檔

enum PTPDF {
    /// 開啟 PDF；有開啟密碼時先試空白密碼，再試使用者提供的密碼。
    static func open(_ url: URL, password: String? = nil) throws -> PDFDocument {
        guard FileManager.default.fileExists(atPath: url.path) else { throw ConversionError.fileNotFound(url.lastPathComponent) }
        guard let doc = PDFDocument(url: url) else {
            throw ConversionError.unreadable("「\(url.lastPathComponent)」不是有效的 PDF，或檔案已損毀。")
        }
        if doc.isLocked {
            if let password, !password.isEmpty, doc.unlock(withPassword: password) {
                // 已解鎖
            } else if !doc.unlock(withPassword: "") {
                throw lockedError(url)
            }
        }
        if doc.pageCount == 0 { throw ConversionError.emptyContent("「\(url.lastPathComponent)」沒有任何頁面。") }
        // 設有權限限制（沒有開啟密碼）的檔案：尊重原作者的限制
        if doc.isEncrypted && doc.permissionsStatus != .owner && doc.accessPermissions.rawValue & 0xFF != 0xFF {
            throw ConversionError.other("「\(url.lastPathComponent)」設有權限限制（禁止修改或複製），請先用「解鎖 PDF」輸入權限密碼。")
        }
        return doc
    }

    /// 文件資訊（寫出用）。
    static func info(of doc: PDFDocument) -> [String: String] {
        let a = doc.documentAttributes ?? [:]
        var out: [String: String] = [:]
        let keys: [(PDFDocumentAttribute, String)] = [(.titleAttribute, "Title"), (.authorAttribute, "Author"), (.subjectAttribute, "Subject"), (.creatorAttribute, "Creator")]
        for (k, name) in keys { if let v = a[k] as? String, !v.isEmpty { out[name] = v } }
        if let k = a[PDFDocumentAttribute.keywordsAttribute] as? [String], !k.isEmpty { out["Keywords"] = k.joined(separator: ", ") }
        else if let k = a[PDFDocumentAttribute.keywordsAttribute] as? String, !k.isEmpty { out["Keywords"] = k }
        return out
    }

    static func attributes(from info: [String: String]) -> [AnyHashable: Any] {
        var out: [AnyHashable: Any] = [:]
        let keys: [(String, PDFDocumentAttribute)] = [("Title", .titleAttribute), ("Author", .authorAttribute), ("Subject", .subjectAttribute), ("Creator", .creatorAttribute)]
        for (name, k) in keys { if let v = info[name] { out[k] = v } }
        if let k = info["Keywords"] { out[PDFDocumentAttribute.keywordsAttribute] = k.components(separatedBy: ", ") }
        return out
    }

    static func lockedError(_ url: URL) -> ConversionError {
        .other("「\(url.lastPathComponent)」受密碼保護，請先用「解鎖 PDF」工具移除密碼後再試。")
    }

    /// 選項面板用：讀頁數（失敗或上鎖回傳 nil）。
    static func pageCount(of url: URL) -> Int? {
        guard let doc = PDFDocument(url: url), !doc.isLocked else { return nil }
        return doc.pageCount
    }

    static func isLocked(_ url: URL) -> Bool {
        guard let doc = PDFDocument(url: url) else { return false }
        return doc.isLocked && !doc.unlock(withPassword: "")
    }

    /// 解析頁碼範圍（空白＝全部），錯誤時丟出清楚的中文訊息。
    static func pages(_ text: String, count: Int, file: URL) throws -> [Int] {
        guard let list = PageRange.parse(text, pageCount: count) else {
            throw ConversionError.other("頁碼範圍「\(text)」不正確：「\(file.lastPathComponent)」共 \(count) 頁。")
        }
        return list
    }

    /// 以逗號分組的範圍：「1-3, 4-6, 8」→ [[1,2,3],[4,5,6],[8]]。
    static func rangeGroups(_ text: String, count: Int) -> [[Int]]? {
        let parts = text.replacingOccurrences(of: "，", with: ",").replacingOccurrences(of: "、", with: ",")
            .split(separator: ",").map { $0.trimmingCharacters(in: .whitespaces) }.filter { !$0.isEmpty }
        if parts.isEmpty { return nil }
        var groups: [[Int]] = []
        for p in parts {
            guard let g = PageRange.parse(p, pageCount: count) else { return nil }
            groups.append(g)
        }
        return groups
    }

    static func write(_ doc: PDFDocument, to url: URL, options: [PDFDocumentWriteOption: Any] = [:]) throws {
        let ok = options.isEmpty ? doc.write(to: url) : doc.write(to: url, withOptions: options)
        guard ok, FileManager.default.fileExists(atPath: url.path) else {
            throw ConversionError.writeFailed("PDFKit 無法寫出 PDF。")
        }
    }

    /// 複製一頁（含註解），供另一份文件使用。
    static func copy(_ page: PDFPage) -> PDFPage {
        (page.copy() as? PDFPage) ?? page
    }

    /// 與指定大小相同的空白頁。
    static func blankPage(size: CGSize) -> PDFPage {
        let page = PDFPage()
        page.setBounds(CGRect(origin: .zero, size: size), for: .mediaBox)
        return page
    }

    /// 頁面「看起來」的大小（已套用旋轉）。
    static func visualSize(_ page: PDFPage) -> CGSize {
        let box = page.bounds(for: .cropBox)
        return page.rotation % 180 == 0 ? box.size : CGSize(width: box.height, height: box.width)
    }

    static func size(of url: URL) -> Int64 { ToolFiles.size(of: url) }

    /// 檔名中的頁碼補零：7 頁 → "07"。
    static func padded(_ n: Int, total: Int) -> String {
        let digits = max(2, String(total).count)
        let s = String(n)
        return String(repeating: "0", count: max(0, digits - s.count)) + s
    }

    /// 把一群檔案放進暫存資料夾裡的新資料夾。
    static func makeFolder(in work: URL, named name: String) throws -> URL {
        let folder = work.appendingPathComponent(CVFileNaming.sanitize(name), isDirectory: true)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        return folder
    }
}

// MARK: - 書籤

enum PTOutline {
    /// 把 PDFKit 書籤轉成寫出用的節點；map：來源頁索引 → 新頁索引（nil＝不在新文件中，該書籤略過）。
    static func nodes(from doc: PDFDocument, map: (Int) -> Int?) -> [PTOutlineNode] {
        guard let root = doc.outlineRoot else { return [] }
        return children(of: root, doc: doc, map: map, depth: 0)
    }

    private static func children(of item: PDFOutline, doc: PDFDocument, map: (Int) -> Int?, depth: Int) -> [PTOutlineNode] {
        guard depth < 16 else { return [] }
        var out: [PTOutlineNode] = []
        for i in 0..<item.numberOfChildren {
            guard let child = item.child(at: i) else { continue }
            let kids = children(of: child, doc: doc, map: map, depth: depth + 1)
            var node = PTOutlineNode(title: child.label ?? "", open: child.isOpen, children: kids)
            if let dest = child.destination ?? (child.action as? PDFActionGoTo)?.destination, let page = dest.page {
                let index = doc.index(for: page)
                if index != NSNotFound, let n = map(index) {
                    node.page = n
                    let box = page.bounds(for: .cropBox)
                    let unspecified = CGFloat(kPDFDestinationUnspecifiedValue)
                    let x = dest.point.x >= unspecified * 0.5 ? box.minX : dest.point.x
                    let y = dest.point.y >= unspecified * 0.5 ? box.maxY : dest.point.y
                    node.point = CGPoint(x: x, y: y)
                }
            }
            if node.page == nil && kids.isEmpty { continue }
            out.append(node)
        }
        return out
    }

    /// 指向某頁頂端的書籤。
    static func entry(_ title: String, page: Int, doc: PDFDocument, sourceIndex: Int, children: [PTOutlineNode] = []) -> PTOutlineNode {
        let box = doc.page(at: sourceIndex)?.bounds(for: .cropBox) ?? .zero
        return PTOutlineNode(title: title, page: page, point: CGPoint(x: box.minX, y: box.maxY), open: false, children: children)
    }

    /// 備援用：節點 → PDFKit 書籤。
    static func pdfOutline(_ nodes: [PTOutlineNode], in doc: PDFDocument) -> PDFOutline {
        let root = PDFOutline()
        func add(_ nodes: [PTOutlineNode], to parent: PDFOutline) {
            for node in nodes {
                let item = PDFOutline()
                item.label = node.title
                if let p = node.page, let page = doc.page(at: p) {
                    item.destination = PDFDestination(page: page, at: node.point ?? .zero)
                }
                parent.insertChild(item, at: parent.numberOfChildren)
                add(node.children, to: item)
            }
        }
        add(nodes, to: root)
        return root
    }
}

// MARK: - 組合頁面（合併、拆分、刪頁、整理、解鎖）

/// 新文件中的一頁：來源文件的第幾頁（nil＝空白頁）。
struct PTPageEntry {
    let doc: PDFDocument
    let index: Int?
    /// 絕對旋轉角度；nil＝沿用原頁
    var rotation: Int?
    var blankSize: CGSize?

    init(doc: PDFDocument, index: Int?, rotation: Int? = nil, blankSize: CGSize? = nil) {
        self.doc = doc
        self.index = index
        self.rotation = rotation
        self.blankSize = blankSize
    }
}

enum PTAssemble {
    /// 以物件層級複製頁面寫出新 PDF（字型與文字對照完全保留）；遇到無法處理的特殊 PDF 時改用 PDFKit。
    static func write(_ entries: [PTPageEntry], outline: [PTOutlineNode], info: [String: String], to url: URL,
                      context: ToolContext? = nil, span: ClosedRange<Double> = 0.3...0.95) throws {
        let writer = PTPDFWriter()
        writer.outline = outline
        writer.info = info
        writer.isCancelled = { Task.isCancelled }
        writer.progress = { f in context?.progress(span.lowerBound + (span.upperBound - span.lowerBound) * f) }
        // 全部來自同一份文件時，沿用它的標記結構（閱讀順序）、表單、圖層等
        if let first = entries.first?.doc, entries.allSatisfy({ $0.doc === first }), let source = first.documentRef {
            writer.catalogSource = source
            let whole = entries.count == first.pageCount && entries.enumerated().allSatisfy { $0.element.index == $0.offset }
            writer.catalogKeys = whole ? PTPDFWriter.wholeDocumentKeys : PTPDFWriter.partialDocumentKeys
        }
        var usable = true
        for entry in entries {
            writer.retain(entry.doc)
            if let i = entry.index {
                guard let ref = entry.doc.page(at: i)?.pageRef else { usable = false; break }
                writer.addPage(ref, rotation: entry.rotation)
            } else {
                writer.addBlank(size: entry.blankSize ?? CGSize(width: 595.28, height: 841.89))
            }
        }
        do {
            guard usable else { throw PTWriterError.unsupported("page") }
            try writer.write(to: url)
            guard let check = PDFDocument(url: url), check.pageCount == entries.count else { throw PTWriterError.unsupported("verify") }
        } catch let error as ConversionError {
            throw error
        } catch {
            try? FileManager.default.removeItem(at: url)
            try fallback(entries, outline: outline, info: info, to: url)
        }
    }

    private static func fallback(_ entries: [PTPageEntry], outline: [PTOutlineNode], info: [String: String], to url: URL) throws {
        let result = PDFDocument()
        for entry in entries {
            if let i = entry.index, let page = entry.doc.page(at: i) {
                let copy = PTPDF.copy(page)
                if let r = entry.rotation { copy.rotation = r }
                result.insert(copy, at: result.pageCount)
            } else {
                result.insert(PTPDF.blankPage(size: entry.blankSize ?? CGSize(width: 595.28, height: 841.89)), at: result.pageCount)
            }
        }
        if !outline.isEmpty { result.outlineRoot = PTOutline.pdfOutline(outline, in: result) }
        result.documentAttributes = PTPDF.attributes(from: info)
        try PTPDF.write(result, to: url)
    }
}

// MARK: - 疊加內容（浮水印、頁碼、OCR 文字層）

/// 疊加時一頁的資訊。座標系統是「看起來的頁面」：左下角為原點，已套用旋轉與裁切框。
struct PTCanvas {
    let index: Int
    let count: Int
    let size: CGSize
    let page: CGPDFPage
}

enum PTLayerPlacement: Sendable {
    case over, under
}

enum PTRedraw {
    /// 在每一頁的上方（或下方）疊加新內容。原頁面的物件原封不動（文字、字型、連結、書籤、表單外觀都保留），
    /// 新內容另外畫成一層 Form XObject 加進頁面。`draw` 回傳 false 代表這頁不加東西。
    static func run(_ doc: PDFDocument, to out: URL, context: ToolContext, span: ClosedRange<Double> = 0.05...0.85,
                    placement: PTLayerPlacement = .over,
                    message: (Int, Int) -> String = { "處理第 \($0) / \($1) 頁…" },
                    draw: (CGContext, PTCanvas) throws -> Bool) throws {
        let layerURL = out.deletingLastPathComponent().appendingPathComponent("layer-\(UUID().uuidString).pdf")
        defer { try? FileManager.default.removeItem(at: layerURL) }
        guard let ctx = CGContext(layerURL as CFURL, mediaBox: nil, [kCGPDFContextCreator: "QUICK_SEEMD"] as CFDictionary) else {
            throw ConversionError.writeFailed("無法建立 PDF。")
        }
        let count = doc.pageCount
        var drew: [Bool] = []
        for i in 0..<count {
            try context.checkCancellation()
            context.progress(span.lowerBound + (span.upperBound - span.lowerBound) * Double(i) / Double(count), message(i + 1, count))
            try autoreleasepool {
                guard let page = doc.page(at: i)?.pageRef else {
                    var empty = CGRect(x: 0, y: 0, width: 10, height: 10)
                    ctx.beginPage(mediaBox: &empty); ctx.endPage()
                    drew.append(false)
                    return
                }
                var media = page.getBoxRect(.mediaBox)
                ctx.beginPage(mediaBox: &media)
                let crop = page.getBoxRect(.cropBox)
                let rotated = abs(page.rotationAngle) % 180 == 90
                let size = rotated ? CGSize(width: crop.height, height: crop.width) : crop.size
                let toUser = page.getDrawingTransform(.cropBox, rect: CGRect(origin: .zero, size: size), rotate: 0, preserveAspectRatio: true).inverted()
                ctx.saveGState()
                ctx.concatenate(toUser)
                let did = try draw(ctx, PTCanvas(index: i, count: count, size: size, page: page))
                ctx.restoreGState()
                ctx.endPage()
                drew.append(did)
            }
        }
        ctx.closePDF()
        guard let layer = CGPDFDocument(layerURL as CFURL), layer.numberOfPages == count else {
            throw ConversionError.writeFailed("無法建立疊加內容。")
        }
        context.progress(span.upperBound, "寫出 PDF…")
        let writer = PTPDFWriter()
        writer.retain(doc)
        writer.retain(layer)
        writer.isCancelled = { Task.isCancelled }
        for i in 0..<count {
            guard let source = doc.page(at: i)?.pageRef else { continue }
            let extra = drew[i] ? layer.page(at: i + 1) : nil
            writer.addPage(source, under: placement == .under ? extra : nil, over: placement == .over ? extra : nil)
        }
        writer.outline = PTOutline.nodes(from: doc, map: { $0 })
        writer.info = PTPDF.info(of: doc)
        writer.catalogSource = doc.documentRef
        writer.catalogKeys = PTPDFWriter.wholeDocumentKeys
        do {
            guard writer.pageCount == count else { throw PTWriterError.unsupported("pages") }
            try writer.write(to: out)
            guard PDFDocument(url: out)?.pageCount == count else { throw PTWriterError.unsupported("verify") }
        } catch let error as ConversionError {
            throw error
        } catch {
            try? FileManager.default.removeItem(at: out)
            try fallback(doc, layer: layer, drew: drew, placement: placement, to: out)
        }
    }

    /// 備援：以 Quartz 重畫每一頁再疊上新內容（外觀相同，但文字對照可能受影響）。
    private static func fallback(_ doc: PDFDocument, layer: CGPDFDocument, drew: [Bool], placement: PTLayerPlacement, to out: URL) throws {
        guard let ctx = CGContext(out as CFURL, mediaBox: nil, [kCGPDFContextCreator: "QUICK_SEEMD"] as CFDictionary) else {
            throw ConversionError.writeFailed("無法建立 PDF。")
        }
        for i in 0..<doc.pageCount {
            guard let page = doc.page(at: i)?.pageRef else { continue }
            var media = page.getBoxRect(.mediaBox)
            let crop = page.getBoxRect(.cropBox)
            let info: [CFString: Any] = [
                kCGPDFContextMediaBox: Data(bytes: &media, count: MemoryLayout<CGRect>.size) as CFData,
                kCGPDFContextCropBox: withUnsafeBytes(of: crop) { Data($0) } as CFData,
            ]
            ctx.beginPDFPage(info as CFDictionary)
            let extra = drew[i] ? layer.page(at: i + 1) : nil
            if placement == .under, let extra { ctx.drawPDFPage(extra) }
            ctx.saveGState(); ctx.drawPDFPage(page); ctx.restoreGState()
            if placement == .over, let extra { ctx.drawPDFPage(extra) }
            ctx.endPDFPage()
        }
        ctx.closePDF()
        // 套回旋轉與書籤（頁面結構不變，PDFKit 寫出不會再動到字型）
        if let result = PDFDocument(url: out) {
            for i in 0..<min(result.pageCount, doc.pageCount) { result.page(at: i)?.rotation = doc.page(at: i)?.rotation ?? 0 }
            let nodes = PTOutline.nodes(from: doc, map: { $0 })
            if !nodes.isEmpty { result.outlineRoot = PTOutline.pdfOutline(nodes, in: result) }
            result.documentAttributes = PTPDF.attributes(from: PTPDF.info(of: doc))
            result.write(to: out)
        }
    }
}

// MARK: - 文字繪製

enum PTFontStyle: String, Sendable, CaseIterable {
    case sans, serif

    func name(bold: Bool) -> String {
        switch self {
        case .sans: return bold ? "PingFangTC-Semibold" : "PingFangTC-Regular"
        case .serif: return bold ? "STSongti-TC-Bold" : "STSongti-TC-Regular"
        }
    }
}

/// 一行文字（CoreText），顏色取自繪圖環境的填色。
struct PTTextLine {
    let line: CTLine
    let width: CGFloat
    let ascent: CGFloat
    let descent: CGFloat
    var height: CGFloat { ascent + descent }

    init(_ text: String, fontName: String, size: CGFloat) {
        let font = CTFontCreateWithName(fontName as CFString, size, nil)
        let attrs: [NSAttributedString.Key: Any] = [
            NSAttributedString.Key(kCTFontAttributeName as String): font,
            NSAttributedString.Key(kCTForegroundColorFromContextAttributeName as String): true,
        ]
        line = CTLineCreateWithAttributedString(NSAttributedString(string: text, attributes: attrs))
        var a: CGFloat = 0, d: CGFloat = 0, l: CGFloat = 0
        width = CGFloat(CTLineGetTypographicBounds(line, &a, &d, &l))
        ascent = a
        descent = d
    }

    /// 以 (x, y) 為文字左側基線位置畫出。
    func draw(in ctx: CGContext, x: CGFloat, baseline y: CGFloat) {
        ctx.textMatrix = .identity
        ctx.textPosition = CGPoint(x: x, y: y)
        CTLineDraw(line, ctx)
    }

    /// 以 (0,0) 為中心畫出（配合旋轉使用）。
    func drawCentered(in ctx: CGContext) {
        draw(in: ctx, x: -width / 2, baseline: -height / 2 + descent)
    }
}

/// 常用顏色（浮水印、頁碼）。
enum PTInk: String, Sendable, CaseIterable, Identifiable {
    case gray, red, blue, black, green

    var id: String { rawValue }

    var title: String {
        switch self {
        case .gray: return "灰色"
        case .red: return "紅色"
        case .blue: return "藍色"
        case .black: return "黑色"
        case .green: return "綠色"
        }
    }

    var rgb: (CGFloat, CGFloat, CGFloat) {
        switch self {
        case .gray: return (0.5, 0.5, 0.5)
        case .red: return (0.86, 0.16, 0.14)
        case .blue: return (0.10, 0.36, 0.80)
        case .black: return (0, 0, 0)
        case .green: return (0.11, 0.55, 0.27)
        }
    }

    var cgColor: CGColor { CGColor(srgbRed: rgb.0, green: rgb.1, blue: rgb.2, alpha: 1) }
    var color: Color { Color(.sRGB, red: Double(rgb.0), green: Double(rgb.1), blue: Double(rgb.2)) }
}

/// 九宮格位置（row 0 在上）。
enum PTGridPosition: Int, Sendable, CaseIterable, Identifiable {
    case topLeft, top, topRight, left, center, right, bottomLeft, bottom, bottomRight

    var id: Int { rawValue }
    var column: Int { rawValue % 3 }
    var row: Int { rawValue / 3 }

    var title: String {
        ["左上", "上方置中", "右上", "左側", "正中央", "右側", "左下", "下方置中", "右下"][rawValue]
    }

    /// 在頁面中放一個 size 大小的方塊時，方塊中心的位置。
    func center(of box: CGSize, in page: CGSize, margin: CGFloat) -> CGPoint {
        let x: CGFloat
        switch column {
        case 0: x = margin + box.width / 2
        case 1: x = page.width / 2
        default: x = page.width - margin - box.width / 2
        }
        let y: CGFloat
        switch row {
        case 0: y = page.height - margin - box.height / 2
        case 1: y = page.height / 2
        default: y = margin + box.height / 2
        }
        return CGPoint(x: x, y: y)
    }
}

// MARK: - 共用工作階段

/// PDF 工具的共同基底：讀頁數、偵測受密碼保護的檔案。
class PTSession: ToolSession {
    @Published var pageCount: Int?
    @Published var lockedNames: [String] = []
    /// 解鎖工具自己處理密碼，不需要在選擇檔案時擋下。
    var allowsLockedFiles: Bool { false }

    override func filesDidChange(_ files: [URL]) {
        let pdfs = files.filter { $0.pathExtension.lowercased() == "pdf" }
        pageCount = pdfs.count == 1 && files.count == 1 ? PTPDF.pageCount(of: pdfs[0]) : nil
        lockedNames = pdfs.filter { PTPDF.isLocked($0) }.map(\.lastPathComponent)
    }

    override func validate(_ files: [URL]) -> String? {
        if !allowsLockedFiles, let name = lockedNames.first {
            return "「\(name)」受密碼保護，請先用「解鎖 PDF」移除密碼"
        }
        return nil
    }
}

// MARK: - 共用選項元件

/// 顏色圓點選擇。
struct PTInkPicker: View {
    @Binding var selection: PTInk
    var choices: [PTInk] = PTInk.allCases
    @Environment(\.toolTint) private var tint

    var body: some View {
        HStack(spacing: 10) {
            ForEach(choices) { ink in
                Button { selection = ink } label: {
                    Circle().fill(ink.color)
                        .frame(width: 24, height: 24)
                        .overlay(Circle().strokeBorder(Color.primary.opacity(0.15)))
                        .padding(3)
                        .overlay(Circle().strokeBorder(selection == ink ? tint : Color.clear, lineWidth: 2))
                        .contentShape(Circle())
                }
                .buttonStyle(.plain)
                .help(ink.title)
            }
            Spacer(minLength: 0)
            Text(selection.title).font(.callout).foregroundStyle(.secondary)
        }
    }
}

/// 九宮格（或只顯示上下兩列）位置選擇。
struct PTPositionPicker: View {
    @Binding var selection: PTGridPosition
    var rows: [Int] = [0, 1, 2]
    @Environment(\.toolTint) private var tint

    var body: some View {
        HStack(alignment: .center, spacing: 14) {
            VStack(spacing: 4) {
                ForEach(rows, id: \.self) { row in
                    HStack(spacing: 4) {
                        ForEach(0..<3, id: \.self) { col in
                            let pos = PTGridPosition(rawValue: row * 3 + col)!
                            let selected = pos == selection
                            Button { selection = pos } label: {
                                RoundedRectangle(cornerRadius: 4, style: .continuous)
                                    .fill(selected ? tint : Color(nsColor: .controlBackgroundColor))
                                    .overlay(RoundedRectangle(cornerRadius: 4).strokeBorder(selected ? Color.clear : Color.primary.opacity(0.15)))
                                    .frame(width: 30, height: rows.count == 3 ? 24 : 20)
                                    .contentShape(Rectangle())
                            }
                            .buttonStyle(.plain)
                            .help(pos.title)
                        }
                    }
                    if rows.count == 2 && row == rows.first {
                        Rectangle().fill(Color.primary.opacity(0.06)).frame(width: 98, height: 26)
                            .overlay(Text("頁面內容").font(.caption2).foregroundStyle(.tertiary))
                    }
                }
            }
            .padding(6)
            .background(RoundedRectangle(cornerRadius: 8).fill(Color.primary.opacity(0.04)))
            .overlay(RoundedRectangle(cornerRadius: 8).strokeBorder(Color.primary.opacity(0.10)))
            Text(selection.title).font(.callout).foregroundStyle(.secondary)
            Spacer(minLength: 0)
        }
    }
}

/// 整數輸入＋上下按鈕。
struct PTNumberField: View {
    let title: String
    @Binding var value: Int
    var range: ClosedRange<Int> = 1...9999

    var body: some View {
        HStack {
            Text(title).font(.callout)
            Spacer()
            TextField("", value: $value, format: .number)
                .textFieldStyle(.roundedBorder)
                .multilineTextAlignment(.trailing)
                .frame(width: 64)
                .onChange(of: value) { _, v in if !range.contains(v) { value = min(max(v, range.lowerBound), range.upperBound) } }
            Stepper("", value: $value, in: range).labelsHidden()
        }
    }
}
