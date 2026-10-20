import CoreGraphics
import Foundation
import ImageIO
import UniformTypeIdentifiers

// 物件層級的 PDF 複製器：直接搬移原始 PDF 物件——字型、內容串流、圖片——不經過 Quartz 重新排版。
//
// 為什麼需要：PDFKit 只要頁面結構有變（刪頁、插頁、跨文件複製）或用 CGContext 重畫頁面，Quartz 就會重新
// 子集化字型並重建 ToUnicode 對照表，常把文字對照弄錯（例如數字錯位、整頁變亂碼、「文」變成部首「⽂」），
// 造成搜尋與複製文字失敗。這個寫入器保留原始字型與內容，文字對照完全不變。

/// 寫出時的書籤節點（頁面索引指新文件）。
struct PTOutlineNode {
    var title: String
    var page: Int?
    var point: CGPoint?
    var open = false
    var children: [PTOutlineNode] = []
}

/// 壓縮 PDF 時的圖片處理設定。
struct PTImagePolicy: Sendable {
    /// JPEG 品質 0…1
    var quality: Double
    /// 依圖片在頁面上的實際大小換算的目標解析度
    var dpi: Double
    /// 圖片最長邊上限（像素）
    var maxPixels: Int
    /// 新資料要小於原本的這個比例才替換
    var minSaving: Double = 0.9
}

enum PTWriterError: Error {
    case unsupported(String)
}

final class PTPDFWriter {
    private enum Item {
        case page(CGPDFPage, rotation: Int, under: CGPDFPage?, over: CGPDFPage?)
        case blank(CGSize)
    }

    private var items: [Item] = []
    private var retained: [Any] = []
    var outline: [PTOutlineNode] = []
    /// 文件資訊：Title、Author、Subject、Keywords、Creator
    var info: [String: String] = [:]
    var imagePolicy: PTImagePolicy?
    /// 要沿用來源文件目錄（Catalog）中的哪些項目，例如標記結構、表單、圖層、頁碼標籤。
    var catalogSource: CGPDFDocument?
    var catalogKeys: [String] = []
    var pageCount: Int { items.count }

    /// 輸出包含來源的全部頁面（順序相同）時可沿用的目錄項目。
    static let wholeDocumentKeys = ["StructTreeRoot", "MarkInfo", "Lang", "AcroForm", "Names", "OCProperties", "PageLabels", "ViewerPreferences", "PageLayout"]
    /// 只取部分頁面時仍適用的目錄項目。
    static let partialDocumentKeys = ["Lang", "OCProperties", "ViewerPreferences"]
    /// 寫出時的進度（0…1）
    var progress: ((Double) -> Void)?
    var isCancelled: () -> Bool = { false }

    // 統計（壓縮用）
    private(set) var imagesRecompressed = 0

    init() {}

    /// 保留來源文件，確保寫出前不會被釋放。
    func retain(_ object: Any) { retained.append(object) }

    /// 加入一頁（來自任何已開啟的 PDF）。rotation 為絕對角度；under／over 是要疊在下方／上方的頁面（例如浮水印）。
    func addPage(_ page: CGPDFPage, rotation: Int? = nil, under: CGPDFPage? = nil, over: CGPDFPage? = nil) {
        let r = ((rotation ?? Int(page.rotationAngle)) % 360 + 360) % 360
        items.append(.page(page, rotation: r - r % 90, under: under, over: over))
    }

    func addBlank(size: CGSize) { items.append(.blank(size)) }

    // MARK: - 寫出狀態

    private struct Source {
        let catalog: CGPDFDictionaryRef?
    }

    private enum Pending {
        case dict(CGPDFDictionaryRef, Source)
        case stream(CGPDFStreamRef, Source)
    }

    private var handle: FileHandle?
    private var buffer = Data()
    private var position = 0
    private var offsets: [Int: Int] = [:]
    private var nextNumber = 1
    private var numbers: [UnsafeRawPointer: Int] = [:]
    private var queue: [(Int, Pending)] = []
    private var pageNumbers: [UnsafeRawPointer: Int] = [:]
    private var displaySizes: [UnsafeRawPointer: CGSize] = [:]
    private var softMasks = Set<UnsafeRawPointer>()

    private func reserve() -> Int {
        defer { nextNumber += 1 }
        return nextNumber
    }

    private func emit(_ number: Int, _ body: Data) throws {
        offsets[number] = position
        var chunk = Data("\(number) 0 obj\n".utf8)
        chunk.append(body)
        chunk.append(Data("\nendobj\n".utf8))
        try write(chunk)
    }

    private func emit(_ number: Int, _ body: String) throws { try emit(number, Data(body.utf8)) }

    private func write(_ data: Data) throws {
        buffer.append(data)
        position += data.count
        if buffer.count > 1 << 20 { try flush() }
    }

    private func flush() throws {
        guard !buffer.isEmpty else { return }
        try handle?.write(contentsOf: buffer)
        buffer.removeAll(keepingCapacity: true)
    }

    // MARK: - 寫出

    func write(to url: URL) throws {
        FileManager.default.createFile(atPath: url.path, contents: nil)
        guard let h = try? FileHandle(forWritingTo: url) else { throw ConversionError.writeFailed("無法建立輸出檔。") }
        handle = h
        defer { try? h.close() }
        try write(Data("%PDF-1.7\n%\u{E2}\u{E3}\u{CF}\u{D3}\n".utf8))

        let catalogNumber = reserve()
        let pagesNumber = reserve()
        // 先為每一頁保留物件編號，頁面之間的連結才對得上
        var newPages: [Int] = []
        for item in items {
            let n = reserve()
            newPages.append(n)
            if case .page(let page, _, _, _) = item, let dict = page.dictionary {
                let key = UnsafeRawPointer(dict.rawValue)
                if pageNumbers[key] == nil { pageNumbers[key] = n }
            }
        }
        if imagePolicy != nil {
            for item in items { if case .page(let page, _, _, _) = item { PTImageScanner.scan(page, sizes: &displaySizes, masks: &softMasks) } }
        }
        for (i, item) in items.enumerated() {
            if isCancelled() { throw ConversionError.cancelled }
            try autoreleasepool {
                switch item {
                case .page(let page, let rotation, let under, let over):
                    try emitPage(page, number: newPages[i], parent: pagesNumber, rotation: rotation, under: under, over: over)
                case .blank(let size):
                    let content = reserve()
                    try emit(content, "<< /Length 0 >>\nstream\n\nendstream")
                    try emit(newPages[i], "<< /Type /Page /Parent \(pagesNumber) 0 R /MediaBox [0 0 \(num(size.width)) \(num(size.height))] /Resources << >> /Contents \(content) 0 R >>")
                }
                try drain()
            }
            progress?(Double(i + 1) / Double(max(items.count, 1)))
        }
        try emit(pagesNumber, "<< /Type /Pages /Kids [\(newPages.map { "\($0) 0 R" }.joined(separator: " "))] /Count \(newPages.count) >>")

        var catalog = "<< /Type /Catalog /Pages \(pagesNumber) 0 R"
        if let source = catalogSource, let sourceCatalog = source.catalog {
            let src = Source(catalog: sourceCatalog)
            for key in catalogKeys {
                var object: CGPDFObjectRef?
                if CGPDFDictionaryGetObject(sourceCatalog, key, &object), let object, let v = try value(object, src) {
                    catalog += " /\(key) \(v)"
                }
            }
            try drain()
        }
        if !outline.isEmpty {
            let root = try emitOutline(pages: newPages)
            catalog += " /Outlines \(root) 0 R /PageMode /UseOutlines"
        }
        catalog += " >>"
        try emit(catalogNumber, catalog)

        let infoNumber = reserve()
        var infoBody = "<<"
        for key in ["Title", "Author", "Subject", "Keywords", "Creator"] {
            if let v = info[key], !v.isEmpty { infoBody += " /\(key) \(textString(v))" }
        }
        let date = Self.pdfDate(Date())
        infoBody += " /Producer \(textString("QUICK_SEEMD")) /CreationDate (\(date)) /ModDate (\(date)) >>"
        try emit(infoNumber, infoBody)

        let xrefStart = position
        var xref = "xref\n0 \(nextNumber)\n0000000000 65535 f \n"
        for n in 1..<nextNumber {
            if let o = offsets[n] { xref += String(format: "%010d 00000 n \n", o) } else { xref += "0000000000 65535 f \n" }
        }
        let id = UUID().uuidString.replacingOccurrences(of: "-", with: "")
        xref += "trailer\n<< /Size \(nextNumber) /Root \(catalogNumber) 0 R /Info \(infoNumber) 0 R /ID [<\(id)> <\(id)>] >>\nstartxref\n\(xrefStart)\n%%EOF\n"
        try write(Data(xref.utf8))
        try flush()
    }

    private func drain() throws {
        var index = 0
        while index < queue.count {
            let (n, item) = queue[index]
            index += 1
            try autoreleasepool {
                switch item {
                case .dict(let d, let src): try emit(n, dictBody(d, src))
                case .stream(let s, let src): try emitStream(n, s, src)
                }
            }
            if index % 256 == 0 && isCancelled() { throw ConversionError.cancelled }
        }
        queue.removeAll(keepingCapacity: true)
    }

    // MARK: - 頁面

    private func emitPage(_ page: CGPDFPage, number: Int, parent: Int, rotation: Int, under: CGPDFPage?, over: CGPDFPage?) throws {
        guard let dict = page.dictionary else { throw PTWriterError.unsupported("page") }
        let src = Source(catalog: page.document?.catalog)
        let media = page.getBoxRect(.mediaBox)
        let crop = page.getBoxRect(.cropBox)
        var body = "<< /Type /Page /Parent \(parent) 0 R /MediaBox \(rect(media))"
        if crop != media { body += " /CropBox \(rect(crop))" }
        if rotation != 0 { body += " /Rotate \(rotation)" }

        let skip: Set<String> = ["Type", "Parent", "MediaBox", "CropBox", "Rotate", "Resources", "Contents", "Annots", "B", "Thumb", "PieceInfo"]
        for (key, object) in entries(dict) where !skip.contains(key) {
            if let v = try value(object, src) { body += " /\(name(key)) \(v)" }
        }

        // 資源（可能繼承自上層 Pages）
        let resources = inherited(dict, "Resources")
        var forms: [(String, Int)] = []
        if let under { forms.append(("PTUnderlay", try emitForm(under))) }
        if let over { forms.append(("PTOverlay", try emitForm(over))) }
        if forms.isEmpty {
            if let resources { body += " /Resources \(try dictRef(resources, src))" } else { body += " /Resources << >>" }
        } else {
            body += " /Resources \(try mergedResources(resources, adding: forms, src)) 0 R"
        }

        // 內容
        var contents: [String] = []
        var contentObject: CGPDFObjectRef?
        if CGPDFDictionaryGetObject(dict, "Contents", &contentObject), let contentObject {
            var stream: CGPDFStreamRef?
            var array: CGPDFArrayRef?
            if CGPDFObjectGetValue(contentObject, .stream, &stream), let stream {
                contents.append(try streamRef(stream, src))
            } else if CGPDFObjectGetValue(contentObject, .array, &array), let array {
                for i in 0..<CGPDFArrayGetCount(array) {
                    var s: CGPDFStreamRef?
                    if CGPDFArrayGetStream(array, i, &s), let s { contents.append(try streamRef(s, src)) }
                }
            }
        }
        if !forms.isEmpty {
            let open = reserve(), close = reserve()
            try emit(open, "<< /Length 2 >>\nstream\nq\n\nendstream")
            try emit(close, "<< /Length 3 >>\nstream\n\nQ\n\nendstream")
            var list: [String] = []
            if under != nil { list.append("\(try invoke("PTUnderlay")) 0 R") }
            list.append("\(open) 0 R")
            list += contents
            list.append("\(close) 0 R")
            if over != nil { list.append("\(try invoke("PTOverlay")) 0 R") }
            contents = list
        }
        body += contents.isEmpty ? "" : " /Contents [\(contents.joined(separator: " "))]"

        // 註解：指向不在新文件中的頁面的連結會被移除
        var annots: CGPDFArrayRef?
        if CGPDFDictionaryGetArray(dict, "Annots", &annots), let annots {
            var kept: [String] = []
            for i in 0..<CGPDFArrayGetCount(annots) {
                var a: CGPDFDictionaryRef?
                guard CGPDFArrayGetDictionary(annots, i, &a), let a else { continue }
                if linkPointsOutside(a, src) { continue }
                kept.append(try dictRef(a, src))
            }
            if !kept.isEmpty { body += " /Annots [\(kept.joined(separator: " "))]" }
        }
        body += " >>"
        try emit(number, body)
    }

    private func invoke(_ name: String) throws -> Int {
        let n = reserve()
        let content = "q /\(name) Do Q\n"
        try emit(n, "<< /Length \(content.utf8.count) >>\nstream\n\(content)\nendstream")
        return n
    }

    /// 把另一份 PDF 的一頁包成 Form XObject（浮水印、頁碼、OCR 文字層）。
    private func emitForm(_ page: CGPDFPage) throws -> Int {
        guard let dict = page.dictionary else { throw PTWriterError.unsupported("overlay") }
        let src = Source(catalog: nil)
        var data = Data()
        var contentObject: CGPDFObjectRef?
        if CGPDFDictionaryGetObject(dict, "Contents", &contentObject), let contentObject {
            var streams: [CGPDFStreamRef] = []
            var stream: CGPDFStreamRef?
            var array: CGPDFArrayRef?
            if CGPDFObjectGetValue(contentObject, .stream, &stream), let stream { streams = [stream] }
            else if CGPDFObjectGetValue(contentObject, .array, &array), let array {
                for i in 0..<CGPDFArrayGetCount(array) { var s: CGPDFStreamRef?; if CGPDFArrayGetStream(array, i, &s), let s { streams.append(s) } }
            }
            for s in streams {
                var format = CGPDFDataFormat.raw
                guard let d = CGPDFStreamCopyData(s, &format), format == .raw else { throw PTWriterError.unsupported("overlay content") }
                data.append(d as Data)
                data.append(0x0A)
            }
        }
        var resources = "<< >>"
        if let r = inherited(dict, "Resources") { resources = try dictRef(r, src) }
        let n = reserve()
        let packed = PTFlate.compress(data)
        var body = Data("<< /Type /XObject /Subtype /Form /BBox \(rect(page.getBoxRect(.mediaBox))) /Resources \(resources) /Filter /FlateDecode /Length \(packed.count) >>\nstream\n".utf8)
        body.append(packed)
        body.append(Data("\nendstream".utf8))
        try emit(n, body)
        return n
    }

    private func mergedResources(_ resources: CGPDFDictionaryRef?, adding forms: [(String, Int)], _ src: Source) throws -> Int {
        var body = "<<"
        var xobjects = forms.map { "/\($0.0) \($0.1) 0 R" }
        if let resources {
            for (key, object) in entries(resources) {
                if key == "XObject" {
                    var x: CGPDFDictionaryRef?
                    if CGPDFObjectGetValue(object, .dictionary, &x), let x {
                        for (k, o) in entries(x) { if let v = try value(o, src) { xobjects.append("/\(name(k)) \(v)") } }
                    }
                    continue
                }
                if let v = try value(object, src) { body += " /\(name(key)) \(v)" }
            }
        }
        body += " /XObject << \(xobjects.joined(separator: " ")) >> >>"
        let n = reserve()
        try emit(n, body)
        return n
    }

    private func inherited(_ page: CGPDFDictionaryRef, _ key: String) -> CGPDFDictionaryRef? {
        var current: CGPDFDictionaryRef? = page
        var depth = 0
        while let d = current, depth < 32 {
            var r: CGPDFDictionaryRef?
            if CGPDFDictionaryGetDictionary(d, key, &r), let r { return r }
            var parent: CGPDFDictionaryRef?
            current = CGPDFDictionaryGetDictionary(d, "Parent", &parent) ? parent : nil
            depth += 1
        }
        return nil
    }

    // MARK: - 物件序列化

    private func entries(_ dict: CGPDFDictionaryRef) -> [(String, CGPDFObjectRef)] {
        var out: [(String, CGPDFObjectRef)] = []
        CGPDFDictionaryApplyBlock(dict, { key, object, _ in
            out.append((String(cString: key), object))
            return true
        }, nil)
        return out.sorted { $0.0 < $1.0 }
    }

    private func typeName(_ dict: CGPDFDictionaryRef) -> String? {
        var t: UnsafePointer<CChar>?
        guard CGPDFDictionaryGetName(dict, "Type", &t), let t else { return nil }
        return String(cString: t)
    }

    /// 物件的文字表示；nil 代表省略。
    private func value(_ object: CGPDFObjectRef, _ src: Source) throws -> String? {
        switch CGPDFObjectGetType(object) {
        case .null:
            return "null"
        case .boolean:
            var b: CGPDFBoolean = 0
            CGPDFObjectGetValue(object, .boolean, &b)
            return b != 0 ? "true" : "false"
        case .integer:
            var i: CGPDFInteger = 0
            CGPDFObjectGetValue(object, .integer, &i)
            return "\(i)"
        case .real:
            var r: CGPDFReal = 0
            CGPDFObjectGetValue(object, .real, &r)
            return num(r)
        case .name:
            var n: UnsafePointer<CChar>?
            CGPDFObjectGetValue(object, .name, &n)
            return n.map { "/" + name(String(cString: $0)) } ?? "null"
        case .string:
            var s: CGPDFStringRef?
            CGPDFObjectGetValue(object, .string, &s)
            return s.map(hexString) ?? "()"
        case .array:
            var a: CGPDFArrayRef?
            CGPDFObjectGetValue(object, .array, &a)
            guard let a else { return "[]" }
            return try arrayString(a, src)
        case .dictionary:
            var d: CGPDFDictionaryRef?
            CGPDFObjectGetValue(object, .dictionary, &d)
            guard let d else { return "null" }
            return try dictRef(d, src)
        case .stream:
            var s: CGPDFStreamRef?
            CGPDFObjectGetValue(object, .stream, &s)
            guard let s else { return "null" }
            return try streamRef(s, src)
        @unknown default:
            return "null"
        }
    }

    private func arrayString(_ a: CGPDFArrayRef, _ src: Source) throws -> String {
        var parts: [String] = []
        for i in 0..<CGPDFArrayGetCount(a) {
            var o: CGPDFObjectRef?
            if CGPDFArrayGetObject(a, i, &o), let o { parts.append(try value(o, src) ?? "null") }
        }
        return "[" + parts.joined(separator: " ") + "]"
    }

    private func dictRef(_ d: CGPDFDictionaryRef, _ src: Source) throws -> String {
        let key = UnsafeRawPointer(d.rawValue)
        switch typeName(d) {
        case "Page": return pageNumbers[key].map { "\($0) 0 R" } ?? "null"
        case "Pages", "Catalog": return "null"
        default: break
        }
        if let n = numbers[key] { return "\(n) 0 R" }
        let n = reserve()
        numbers[key] = n
        queue.append((n, .dict(d, src)))
        return "\(n) 0 R"
    }

    private func streamRef(_ s: CGPDFStreamRef, _ src: Source) throws -> String {
        let key = UnsafeRawPointer(s.rawValue)
        if let n = numbers[key] { return "\(n) 0 R" }
        let n = reserve()
        numbers[key] = n
        queue.append((n, .stream(s, src)))
        return "\(n) 0 R"
    }

    private func dictBody(_ d: CGPDFDictionaryRef, _ src: Source) throws -> String {
        let isGoTo = actionType(d) == "GoTo"
        var body = "<<"
        for (key, object) in entries(d) {
            // 具名目的地：解析成實際頁面（新文件沒有原本的名稱表）
            if (key == "Dest" || (key == "D" && isGoTo)), isNamed(object), let resolved = resolveNamed(object, src) {
                body += " /\(key) \(try arrayString(resolved, src))"
                continue
            }
            if let v = try value(object, src) { body += " /\(name(key)) \(v)" }
        }
        return body + " >>"
    }

    private func emitStream(_ number: Int, _ stream: CGPDFStreamRef, _ src: Source) throws {
        guard let dict = CGPDFStreamGetDictionary(stream) else { throw PTWriterError.unsupported("stream") }
        var format = CGPDFDataFormat.raw
        guard let cf = CGPDFStreamCopyData(stream, &format) else { throw PTWriterError.unsupported("stream data") }
        var data = cf as Data
        let skip: Set<String> = ["Length", "Filter", "DecodeParms", "DL", "F", "FFilter", "FDecodeParms"]
        var overrides: [String: String] = [:]
        var dropped = Set<String>()
        var filter: String
        switch format {
        case .raw: filter = "/FlateDecode"
        case .jpegEncoded: filter = "/DCTDecode"
        case .JPEG2000: filter = "/JPXDecode"
        @unknown default: throw PTWriterError.unsupported("format")
        }
        var decodeParms: String?
        if format == .jpegEncoded { decodeParms = try dctParms(dict, src) }
        // 安全檢查：解碼後的圖片資料量不足（例如無法解碼的濾鏡）時改用備援方式，避免寫出壞掉的圖片
        if format == .raw, isImage(dict), let expected = minimumImageBytes(dict), data.count < expected {
            throw PTWriterError.unsupported("undecodable image")
        }

        if let policy = imagePolicy, isImage(dict),
           let replacement = PTImageRecompressor.recompress(stream: stream, dict: dict, data: data, format: format, policy: policy,
                                                             displaySize: displaySizes[UnsafeRawPointer(stream.rawValue)],
                                                             isSoftMask: softMasks.contains(UnsafeRawPointer(stream.rawValue))) {
            data = replacement.data
            overrides = replacement.entries
            dropped = replacement.drop
            filter = replacement.filter
            decodeParms = nil
            imagesRecompressed += 1
        }

        if filter == "/FlateDecode" { data = PTFlate.compress(data) }
        var body = "<<"
        for (key, object) in entries(dict) where !skip.contains(key) && overrides[key] == nil && !dropped.contains(key) {
            if let v = try value(object, src) { body += " /\(name(key)) \(v)" }
        }
        for (key, v) in overrides.sorted(by: { $0.key < $1.key }) { body += " /\(key) \(v)" }
        body += " /Filter \(filter)"
        if let decodeParms { body += " /DecodeParms \(decodeParms)" }
        body += " /Length \(data.count) >>\nstream\n"
        var out = Data(body.utf8)
        out.append(data)
        out.append(Data("\nendstream".utf8))
        try emit(number, out)
    }

    /// 1 個色版時圖片解碼後至少應有的位元組數（多色版的圖片只會更大）。
    private func minimumImageBytes(_ dict: CGPDFDictionaryRef) -> Int? {
        var w: CGPDFInteger = 0, h: CGPDFInteger = 0, bpc: CGPDFInteger = 1
        guard CGPDFDictionaryGetInteger(dict, "Width", &w), CGPDFDictionaryGetInteger(dict, "Height", &h), w > 0, h > 0 else { return nil }
        var isMask: CGPDFBoolean = 0
        CGPDFDictionaryGetBoolean(dict, "ImageMask", &isMask)
        if isMask == 0 { CGPDFDictionaryGetInteger(dict, "BitsPerComponent", &bpc) }
        return (Int(w) * Int(max(bpc, 1)) + 7) / 8 * Int(h)
    }

    private func isImage(_ dict: CGPDFDictionaryRef) -> Bool {
        var n: UnsafePointer<CChar>?
        return CGPDFDictionaryGetName(dict, "Subtype", &n) && n.map { String(cString: $0) } == "Image"
    }

    /// JPEG 的 DecodeParms（例如 ColorTransform）在原本只有 DCTDecode 時保留。
    private func dctParms(_ dict: CGPDFDictionaryRef, _ src: Source) throws -> String? {
        var parms: CGPDFObjectRef?
        guard CGPDFDictionaryGetObject(dict, "DecodeParms", &parms), let parms else { return nil }
        var filterName: UnsafePointer<CChar>?
        if CGPDFDictionaryGetName(dict, "Filter", &filterName) {
            return CGPDFObjectGetType(parms) == .dictionary ? try value(parms, src) : nil
        }
        var filters: CGPDFArrayRef?
        var parmsArray: CGPDFArrayRef?
        if CGPDFDictionaryGetArray(dict, "Filter", &filters), let filters, CGPDFObjectGetValue(parms, .array, &parmsArray), let parmsArray {
            let count = CGPDFArrayGetCount(filters)
            var last: UnsafePointer<CChar>?
            if count > 0, CGPDFArrayGetName(filters, count - 1, &last), let last, String(cString: last) == "DCTDecode",
               CGPDFArrayGetCount(parmsArray) == count {
                var d: CGPDFDictionaryRef?
                if CGPDFArrayGetDictionary(parmsArray, count - 1, &d), let d { return try dictRef(d, src) }
            }
        }
        return nil
    }

    // MARK: - 連結與具名目的地

    private func actionType(_ d: CGPDFDictionaryRef) -> String? {
        var s: UnsafePointer<CChar>?
        guard CGPDFDictionaryGetName(d, "S", &s), let s else { return nil }
        return String(cString: s)
    }

    private func isNamed(_ object: CGPDFObjectRef) -> Bool {
        let t = CGPDFObjectGetType(object)
        return t == .name || t == .string
    }

    /// 連結指向的頁面不在新文件中 → true（要移除）。
    private func linkPointsOutside(_ annot: CGPDFDictionaryRef, _ src: Source) -> Bool {
        var subtype: UnsafePointer<CChar>?
        guard CGPDFDictionaryGetName(annot, "Subtype", &subtype), let subtype, String(cString: subtype) == "Link" else { return false }
        var target: CGPDFObjectRef?
        if !CGPDFDictionaryGetObject(annot, "Dest", &target) {
            var action: CGPDFDictionaryRef?
            if CGPDFDictionaryGetDictionary(annot, "A", &action), let action, actionType(action) == "GoTo" {
                _ = CGPDFDictionaryGetObject(action, "D", &target)
            }
        }
        guard let target else { return false }
        var array: CGPDFArrayRef?
        if isNamed(target) { array = resolveNamed(target, src) } else { CGPDFObjectGetValue(target, .array, &array) }
        guard let array else { return true }
        var page: CGPDFDictionaryRef?
        guard CGPDFArrayGetDictionary(array, 0, &page), let page else { return false }
        return pageNumbers[UnsafeRawPointer(page.rawValue)] == nil
    }

    private func resolveNamed(_ object: CGPDFObjectRef, _ src: Source) -> CGPDFArrayRef? {
        guard let catalog = src.catalog else { return nil }
        var key: [UInt8]?
        var n: UnsafePointer<CChar>?
        var s: CGPDFStringRef?
        if CGPDFObjectGetValue(object, .name, &n), let n {
            key = Array(String(cString: n).utf8)
            var dests: CGPDFDictionaryRef?
            if CGPDFDictionaryGetDictionary(catalog, "Dests", &dests), let dests, let found = destination(in: dests, key: String(cString: n)) { return found }
        } else if CGPDFObjectGetValue(object, .string, &s), let s, let bytes = CGPDFStringGetBytePtr(s) {
            key = Array(UnsafeBufferPointer(start: bytes, count: CGPDFStringGetLength(s)))
        }
        guard let key else { return nil }
        var names: CGPDFDictionaryRef?
        var tree: CGPDFDictionaryRef?
        guard CGPDFDictionaryGetDictionary(catalog, "Names", &names), let names,
              CGPDFDictionaryGetDictionary(names, "Dests", &tree), let tree else { return nil }
        return lookup(tree, key: key, depth: 0)
    }

    private func destination(in dests: CGPDFDictionaryRef, key: String) -> CGPDFArrayRef? {
        var a: CGPDFArrayRef?
        if CGPDFDictionaryGetArray(dests, key, &a), let a { return a }
        var d: CGPDFDictionaryRef?
        if CGPDFDictionaryGetDictionary(dests, key, &d), let d, CGPDFDictionaryGetArray(d, "D", &a), let a { return a }
        return nil
    }

    private func lookup(_ node: CGPDFDictionaryRef, key: [UInt8], depth: Int) -> CGPDFArrayRef? {
        guard depth < 32 else { return nil }
        var names: CGPDFArrayRef?
        if CGPDFDictionaryGetArray(node, "Names", &names), let names {
            var i = 0
            while i + 1 < CGPDFArrayGetCount(names) {
                var s: CGPDFStringRef?
                if CGPDFArrayGetString(names, i, &s), let s, let p = CGPDFStringGetBytePtr(s),
                   Array(UnsafeBufferPointer(start: p, count: CGPDFStringGetLength(s))) == key {
                    var a: CGPDFArrayRef?
                    if CGPDFArrayGetArray(names, i + 1, &a), let a { return a }
                    var d: CGPDFDictionaryRef?
                    if CGPDFArrayGetDictionary(names, i + 1, &d), let d, CGPDFDictionaryGetArray(d, "D", &a), let a { return a }
                    return nil
                }
                i += 2
            }
        }
        var kids: CGPDFArrayRef?
        if CGPDFDictionaryGetArray(node, "Kids", &kids), let kids {
            for i in 0..<CGPDFArrayGetCount(kids) {
                var kid: CGPDFDictionaryRef?
                if CGPDFArrayGetDictionary(kids, i, &kid), let kid, let found = lookup(kid, key: key, depth: depth + 1) { return found }
            }
        }
        return nil
    }

    // MARK: - 書籤

    private func emitOutline(pages: [Int]) throws -> Int {
        let root = reserve()
        func emitLevel(_ nodes: [PTOutlineNode], parent: Int) throws -> (first: Int, last: Int, visible: Int)? {
            guard !nodes.isEmpty else { return nil }
            let numbers = nodes.map { _ in reserve() }
            var visible = 0
            for (i, node) in nodes.enumerated() {
                var body = "<< /Title \(textString(node.title)) /Parent \(parent) 0 R"
                if i > 0 { body += " /Prev \(numbers[i - 1]) 0 R" }
                if i < nodes.count - 1 { body += " /Next \(numbers[i + 1]) 0 R" }
                if let p = node.page, p >= 0, p < pages.count {
                    if let pt = node.point { body += " /Dest [\(pages[p]) 0 R /XYZ \(num(pt.x)) \(num(pt.y)) null]" }
                    else { body += " /Dest [\(pages[p]) 0 R /Fit]" }
                }
                if let child = try emitLevel(node.children, parent: numbers[i]) {
                    body += " /First \(child.first) 0 R /Last \(child.last) 0 R /Count \(node.open ? child.visible : -node.children.count)"
                    if node.open { visible += child.visible }
                }
                body += " >>"
                try emit(numbers[i], body)
                visible += 1
            }
            return (numbers.first!, numbers.last!, visible)
        }
        let top = try emitLevel(outline, parent: root)
        var body = "<< /Type /Outlines"
        if let top { body += " /First \(top.first) 0 R /Last \(top.last) 0 R /Count \(top.visible)" }
        try emit(root, body + " >>")
        return root
    }

    // MARK: - 格式小工具

    private func num(_ v: CGFloat) -> String { num(Double(v)) }

    private func num(_ v: Double) -> String {
        if v.isNaN || v.isInfinite { return "0" }
        if v == v.rounded() && abs(v) < 1e15 { return String(Int64(v)) }
        var s = String(format: "%.6f", v)
        while s.hasSuffix("0") { s.removeLast() }
        if s.hasSuffix(".") { s.removeLast() }
        return s == "-0" ? "0" : s
    }

    private func rect(_ r: CGRect) -> String { "[\(num(r.minX)) \(num(r.minY)) \(num(r.maxX)) \(num(r.maxY))]" }

    private func name(_ s: String) -> String {
        var out = ""
        for b in s.utf8 {
            if b < 0x21 || b > 0x7E || "#()<>[]{}/%".utf8.contains(b) { out += String(format: "#%02X", b) } else { out.append(Character(UnicodeScalar(b))) }
        }
        return out
    }

    private func hexString(_ s: CGPDFStringRef) -> String {
        guard let p = CGPDFStringGetBytePtr(s) else { return "()" }
        let bytes = UnsafeBufferPointer(start: p, count: CGPDFStringGetLength(s))
        return "<" + bytes.map { String(format: "%02X", $0) }.joined() + ">"
    }

    /// 文字字串：純 ASCII 用 PDFDocEncoding，其他用 UTF-16BE（含 BOM）。
    private func textString(_ s: String) -> String {
        if s.unicodeScalars.allSatisfy({ $0.value >= 0x20 && $0.value < 0x7F }) {
            let escaped = s.replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "(", with: "\\(").replacingOccurrences(of: ")", with: "\\)")
            return "(\(escaped))"
        }
        var hex = "<FEFF"
        for unit in s.utf16 { hex += String(format: "%04X", unit) }
        return hex + ">"
    }

    static func pdfDate(_ date: Date) -> String {
        let f = DateFormatter()
        f.locale = Locale(identifier: "en_US_POSIX")
        f.timeZone = TimeZone(identifier: "UTC")
        f.dateFormat = "yyyyMMddHHmmss"
        return "D:\(f.string(from: date))Z"
    }
}

// MARK: - Flate（zlib 格式）

enum PTFlate {
    /// PDF 的 FlateDecode 需要 zlib 格式（2 bytes 標頭＋DEFLATE＋Adler-32）。
    static func compress(_ data: Data) -> Data {
        guard !data.isEmpty, let deflated = try? (data as NSData).compressed(using: .zlib) as Data else {
            return stored(data)
        }
        var out = Data([0x78, 0x9C])
        out.append(deflated)
        var a = adler32(data).bigEndian
        out.append(Data(bytes: &a, count: 4))
        return out
    }

    /// 無壓縮的 zlib 區塊（備援）。
    private static func stored(_ data: Data) -> Data {
        var out = Data([0x78, 0x01])
        var offset = 0
        repeat {
            let len = min(65535, data.count - offset)
            let final: UInt8 = offset + len >= data.count ? 1 : 0
            out.append(final)
            out.append(UInt8(len & 0xFF)); out.append(UInt8(len >> 8))
            out.append(UInt8(~len & 0xFF)); out.append(UInt8((~len >> 8) & 0xFF))
            out.append(data.subdata(in: offset..<offset + len))
            offset += len
        } while offset < data.count
        var a = adler32(data).bigEndian
        out.append(Data(bytes: &a, count: 4))
        return out
    }

    static func adler32(_ data: Data) -> UInt32 {
        var a: UInt32 = 1, b: UInt32 = 0
        data.withUnsafeBytes { (buf: UnsafeRawBufferPointer) in
            let bytes = buf.bindMemory(to: UInt8.self)
            var i = 0
            let n = bytes.count
            while i < n {
                let end = min(i + 5552, n)
                while i < end {
                    a += UInt32(bytes[i])
                    b += a
                    i += 1
                }
                a %= 65521
                b %= 65521
            }
        }
        return (b << 16) | a
    }
}

// MARK: - 圖片實際顯示大小（壓縮時決定縮小多少）

private final class PTScanState {
    var stack: [CGAffineTransform] = [.identity]
    var sizes: [UnsafeRawPointer: CGSize] = [:]
    var masks = Set<UnsafeRawPointer>()
    var depth = 0
    var table: CGPDFOperatorTableRef?
}

enum PTImageScanner {
    /// 掃描頁面內容，記錄每張圖片在頁面上最大的顯示尺寸（pt）。
    static func scan(_ page: CGPDFPage, sizes: inout [UnsafeRawPointer: CGSize], masks: inout Set<UnsafeRawPointer>) {
        let state = PTScanState()
        state.sizes = sizes
        state.masks = masks
        guard let table = CGPDFOperatorTableCreate() else { return }
        state.table = table
        CGPDFOperatorTableSetCallback(table, "q") { _, info in
            guard let info else { return }
            let s = Unmanaged<PTScanState>.fromOpaque(info).takeUnretainedValue()
            s.stack.append(s.stack.last ?? .identity)
        }
        CGPDFOperatorTableSetCallback(table, "Q") { _, info in
            guard let info else { return }
            let s = Unmanaged<PTScanState>.fromOpaque(info).takeUnretainedValue()
            if s.stack.count > 1 { s.stack.removeLast() }
        }
        CGPDFOperatorTableSetCallback(table, "cm") { scanner, info in
            guard let info else { return }
            let s = Unmanaged<PTScanState>.fromOpaque(info).takeUnretainedValue()
            var v = [CGPDFReal](repeating: 0, count: 6)
            for i in (0..<6).reversed() { guard CGPDFScannerPopNumber(scanner, &v[i]) else { return } }
            let m = CGAffineTransform(a: v[0], b: v[1], c: v[2], d: v[3], tx: v[4], ty: v[5])
            s.stack[s.stack.count - 1] = m.concatenating(s.stack.last ?? .identity)
        }
        CGPDFOperatorTableSetCallback(table, "Do") { scanner, info in
            guard let info else { return }
            let s = Unmanaged<PTScanState>.fromOpaque(info).takeUnretainedValue()
            var namePtr: UnsafePointer<CChar>?
            guard CGPDFScannerPopName(scanner, &namePtr), let namePtr else { return }
            let cs = CGPDFScannerGetContentStream(scanner)
            guard let object = CGPDFContentStreamGetResource(cs, "XObject", namePtr) else { return }
            var stream: CGPDFStreamRef?
            guard CGPDFObjectGetValue(object, .stream, &stream), let stream, let dict = CGPDFStreamGetDictionary(stream) else { return }
            var subtype: UnsafePointer<CChar>?
            guard CGPDFDictionaryGetName(dict, "Subtype", &subtype), let subtype else { return }
            let ctm = s.stack.last ?? .identity
            switch String(cString: subtype) {
            case "Image":
                let size = CGSize(width: hypot(ctm.a, ctm.b), height: hypot(ctm.c, ctm.d))
                PTImageScanner.record(stream, size: size, state: s)
                var mask: CGPDFStreamRef?
                if CGPDFDictionaryGetStream(dict, "SMask", &mask), let mask {
                    s.masks.insert(UnsafeRawPointer(mask.rawValue))
                    PTImageScanner.record(mask, size: size, state: s)
                }
            case "Form":
                guard s.depth < 12, let table = s.table else { return }
                var matrix = CGAffineTransform.identity
                var array: CGPDFArrayRef?
                if CGPDFDictionaryGetArray(dict, "Matrix", &array), let array, CGPDFArrayGetCount(array) == 6 {
                    var v = [CGPDFReal](repeating: 0, count: 6)
                    for i in 0..<6 { CGPDFArrayGetNumber(array, i, &v[i]) }
                    matrix = CGAffineTransform(a: v[0], b: v[1], c: v[2], d: v[3], tx: v[4], ty: v[5])
                }
                var resources: CGPDFDictionaryRef?
                guard CGPDFDictionaryGetDictionary(dict, "Resources", &resources), let resources else { return }
                let formStream = CGPDFContentStreamCreateWithStream(stream, resources, cs)
                s.stack.append(matrix.concatenating(ctm))
                s.depth += 1
                let sub = CGPDFScannerCreate(formStream, table, info)
                CGPDFScannerScan(sub)
                CGPDFScannerRelease(sub)
                CGPDFContentStreamRelease(formStream)
                s.depth -= 1
                s.stack.removeLast()
            default:
                break
            }
        }
        let content = CGPDFContentStreamCreateWithPage(page)
        let scanner = CGPDFScannerCreate(content, table, Unmanaged.passUnretained(state).toOpaque())
        CGPDFScannerScan(scanner)
        CGPDFScannerRelease(scanner)
        CGPDFContentStreamRelease(content)
        CGPDFOperatorTableRelease(table)
        sizes = state.sizes
        masks = state.masks
    }

    fileprivate static func record(_ stream: CGPDFStreamRef, size: CGSize, state: PTScanState) {
        let key = UnsafeRawPointer(stream.rawValue)
        let old = state.sizes[key] ?? .zero
        state.sizes[key] = CGSize(width: max(old.width, size.width), height: max(old.height, size.height))
    }
}

// MARK: - 圖片重新壓縮

enum PTImageRecompressor {
    struct Result {
        var data: Data
        var entries: [String: String]
        var drop: Set<String>
        var filter: String
    }

    static func recompress(stream: CGPDFStreamRef, dict: CGPDFDictionaryRef, data: Data, format: CGPDFDataFormat,
                           policy: PTImagePolicy, displaySize: CGSize?, isSoftMask: Bool) -> Result? {
        var w: CGPDFInteger = 0, h: CGPDFInteger = 0, bpc: CGPDFInteger = 8
        guard CGPDFDictionaryGetInteger(dict, "Width", &w), CGPDFDictionaryGetInteger(dict, "Height", &h), w > 16, h > 16 else { return nil }
        CGPDFDictionaryGetInteger(dict, "BitsPerComponent", &bpc)
        var isMask: CGPDFBoolean = 0
        CGPDFDictionaryGetBoolean(dict, "ImageMask", &isMask)
        var maskObject: CGPDFObjectRef?
        if isMask != 0 { return nil }
        // 色鍵遮罩（Mask 陣列）需要精確色彩，不能用 JPEG
        if CGPDFDictionaryGetObject(dict, "Mask", &maskObject), let maskObject, CGPDFObjectGetType(maskObject) == .array { return nil }
        var decodeArray: CGPDFArrayRef?
        if CGPDFDictionaryGetArray(dict, "Decode", &decodeArray) { return nil }
        var originalLength: CGPDFInteger = 0
        CGPDFDictionaryGetInteger(dict, "Length", &originalLength)
        let original = originalLength > 0 ? Int(originalLength) : data.count

        // 目標尺寸
        var scale = 1.0
        if let d = displaySize, d.width > 1, d.height > 1 {
            let needW = Double(d.width) / 72 * policy.dpi
            let needH = Double(d.height) / 72 * policy.dpi
            scale = min(1, max(needW / Double(w), needH / Double(h)))
        }
        scale = min(scale, Double(policy.maxPixels) / Double(max(w, h)))
        let tw = max(1, Int((Double(w) * scale).rounded()))
        let th = max(1, Int((Double(h) * scale).rounded()))

        guard let image = decode(data: data, format: format, dict: dict, width: Int(w), height: Int(h), bpc: Int(bpc)) else { return nil }
        let gray = image.colorSpace?.model == .monochrome || isSoftMask
        // 已經是 JPEG 而且不需要縮小：重新壓縮通常得不償失
        if format == .jpegEncoded && scale > 0.92 && policy.quality >= 0.75 { return nil }

        let space = gray ? CGColorSpaceCreateDeviceGray() : CGColorSpace(name: CGColorSpace.sRGB)!
        let info = gray ? CGImageAlphaInfo.none.rawValue : CGImageAlphaInfo.noneSkipLast.rawValue
        guard let ctx = CGContext(data: nil, width: tw, height: th, bitsPerComponent: 8, bytesPerRow: 0, space: space, bitmapInfo: info) else { return nil }
        ctx.interpolationQuality = .high
        ctx.setFillColor(gray ? CGColor(gray: 1, alpha: 1) : CGColor(srgbRed: 1, green: 1, blue: 1, alpha: 1))
        ctx.fill(CGRect(x: 0, y: 0, width: tw, height: th))
        ctx.draw(image, in: CGRect(x: 0, y: 0, width: tw, height: th))
        guard let scaled = ctx.makeImage() else { return nil }

        var entries: [String: String] = ["Width": "\(tw)", "Height": "\(th)", "BitsPerComponent": "8"]
        if !isSoftMask { entries["ColorSpace"] = gray ? "/DeviceGray" : "/DeviceRGB" }
        let drop: Set<String> = ["Decode", "Intent"]
        if isSoftMask {
            // 透明遮罩保持無損（Flate）
            guard let raw = grayBytes(scaled) else { return nil }
            let packedSize = PTFlate.compress(raw).count
            guard Double(packedSize) < Double(original) * policy.minSaving else { return nil }
            return Result(data: raw, entries: entries, drop: drop, filter: "/FlateDecode")
        }
        let out = NSMutableData()
        guard let dest = CGImageDestinationCreateWithData(out, UTType.jpeg.identifier as CFString, 1, nil) else { return nil }
        CGImageDestinationAddImage(dest, scaled, [kCGImageDestinationLossyCompressionQuality: policy.quality] as CFDictionary)
        guard CGImageDestinationFinalize(dest), Double(out.length) < Double(original) * policy.minSaving else { return nil }
        return Result(data: out as Data, entries: entries, drop: drop, filter: "/DCTDecode")
    }

    private static func grayBytes(_ image: CGImage) -> Data? {
        let w = image.width, h = image.height
        guard let ctx = CGContext(data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w, space: CGColorSpaceCreateDeviceGray(),
                                  bitmapInfo: CGImageAlphaInfo.none.rawValue), let p = ctx.data else { return nil }
        ctx.draw(image, in: CGRect(x: 0, y: 0, width: w, height: h))
        return Data(bytes: p, count: w * h)
    }

    private static func decode(data: Data, format: CGPDFDataFormat, dict: CGPDFDictionaryRef, width: Int, height: Int, bpc: Int) -> CGImage? {
        switch format {
        case .jpegEncoded, .JPEG2000:
            guard let src = CGImageSourceCreateWithData(data as CFData, nil), let img = CGImageSourceCreateImageAtIndex(src, 0, nil) else { return nil }
            // CMYK JPEG（常見的反相格式）不處理
            if img.colorSpace?.model == .cmyk { return nil }
            return img
        case .raw:
            return PTImageExtractor.decodeRaw(data, dict: dict, width: width, height: height)
        @unknown default:
            return nil
        }
    }
}
