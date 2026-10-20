import ImageIO
import PDFKit
import SwiftUI
import UniformTypeIdentifiers

// PDF 轉圖片（每頁一張或擷取內嵌圖片）、圖片轉 PDF。

extension PDFToolCatalog {
    static let toImages = ToolDefinition(
        id: "pdf.to-images",
        category: .pdf,
        title: "PDF 轉圖片",
        subtitle: "把每一頁轉成 JPG、PNG 等圖片，或取出 PDF 裡的原始圖片。",
        symbol: "photo.on.rectangle.angled",
        tint: ToolPalette.image,
        badge: "JPG",
        inputTypes: [.pdf],
        inputLabel: "PDF 檔案",
        actionTitle: "轉成圖片",
        keywords: ["pdf to jpg", "pdf to png", "pdf to image", "extract images", "轉圖", "截圖", "圖片", "JPG", "PNG"],
        quickActions: [
            "pdf.to-jpg": { PTToImagesSession.job(PTToImagesOptions()) },
        ],
        makeSession: { PTToImagesSession() }
    )

    static let fromImages = ToolDefinition(
        id: "pdf.from-images",
        category: .pdf,
        title: "圖片轉 PDF",
        subtitle: "把 JPG、PNG、HEIC 等圖片排好順序，轉成一份或多份 PDF。",
        symbol: "photo.stack",
        tint: ToolPalette.image,
        badge: "PDF",
        inputTypes: [.image],
        inputLabel: "圖片",
        reorderable: true,
        actionTitle: "轉成 PDF",
        keywords: ["jpg to pdf", "image to pdf", "png to pdf", "photos to pdf", "scan", "圖片轉PDF", "照片", "掃描"],
        quickActions: [
            "pdf.images-to-pdf": { PTFromImagesSession.job(PTFromImagesOptions()) },
        ],
        makeSession: { PTFromImagesSession() }
    )
}

// MARK: - PDF 轉圖片

enum PTImageMode: String, Sendable {
    case pages, embedded
}

struct PTToImagesOptions: Sendable {
    var mode: PTImageMode = .pages
    var format = "jpeg"
    var dpi = 150
    var quality = 85.0
    var pages = ""

    var ext: String {
        switch format {
        case "jpeg": return "jpg"
        case "heic": return "heic"
        case "tiff": return "tiff"
        default: return "png"
        }
    }

    var formatName: String { format == "jpeg" ? "JPG" : format.uppercased() }
}

final class PTToImagesSession: PTSession {
    @Published var options = PTToImagesOptions()

    override func validate(_ files: [URL]) -> String? {
        if let reason = super.validate(files) { return reason }
        if let count = pageCount, PageRange.parse(options.pages, pageCount: count) == nil { return "頁碼範圍不正確" }
        return nil
    }

    override func optionsView() -> AnyView { AnyView(PTToImagesPanel(session: self)) }

    override func makeJob() -> ToolJob { Self.job(options) }

    nonisolated static func job(_ o: PTToImagesOptions) -> ToolJob {
        .perFile { file, context in
            let doc = try PTPDF.open(file)
            let list = try PTPDF.pages(o.pages, count: doc.pageCount, file: file)
            let base = CVFileNaming.baseName(of: file)
            let work = try context.makeWorkDirectory()
            if o.mode == .embedded {
                let folder = try PTPDF.makeFolder(in: work, named: "\(base)-內嵌圖片")
                let count = try PTImageExtractor.extract(doc, pages: list, into: folder, base: base, context: context)
                if count == 0 { throw ConversionError.emptyContent("「\(file.lastPathComponent)」的指定頁面中沒有內嵌圖片。") }
                context.progress(1, "已取出 \(count) 張圖片")
                return [try context.deliver(folder, as: folder.lastPathComponent, for: file)]
            }
            let single = list.count == 1
            let folder = single ? work : try PTPDF.makeFolder(in: work, named: "\(base)-\(o.formatName)")
            var produced: [URL] = []
            for (i, n) in list.enumerated() {
                try context.checkCancellation()
                context.progress(Double(i) / Double(list.count), "轉換第 \(n) 頁（\(i + 1)/\(list.count)）…")
                try autoreleasepool {
                    guard let page = doc.page(at: n - 1)?.pageRef, let image = CVPDFConverter.render(page: page, dpi: Double(o.dpi)) else {
                        throw ConversionError.other("第 \(n) 頁繪製失敗。")
                    }
                    let name = single ? "\(base)-第\(n)頁" : "\(base)-\(PTPDF.padded(n, total: doc.pageCount))"
                    let out = folder.appendingPathComponent(CVFileNaming.fileName(CVFileNaming.sanitize(name), o.ext))
                    try CVImageConverter.write(image, to: out, formatID: o.format, quality: o.quality / 100,
                                               properties: [kCGImagePropertyDPIWidth: o.dpi, kCGImagePropertyDPIHeight: o.dpi])
                    produced.append(out)
                }
            }
            if single, let item = produced.first {
                return [try context.deliver(item, as: item.lastPathComponent, for: file)]
            }
            context.progress(1, "已轉出 \(list.count) 張 \(o.formatName) 圖片")
            return [try context.deliver(folder, as: folder.lastPathComponent, for: file)]
        }
    }
}

private struct PTToImagesPanel: View {
    @ObservedObject var session: PTToImagesSession

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            OptionGroup(title: "轉換方式", systemImage: "photo.on.rectangle") {
                ChoiceCards(selection: $session.options.mode, choices: [
                    OptionChoice(value: .pages, title: "每頁轉成一張圖片", subtitle: "整頁（含文字）輸出成圖片", systemImage: "doc.richtext"),
                    OptionChoice(value: .embedded, title: "擷取內嵌圖片", subtitle: "取出 PDF 中的原始相片與圖片", systemImage: "photo.stack"),
                ])
            }
            if session.options.mode == .pages {
                OptionGroup(title: "圖片格式", systemImage: "photo") {
                    ChoiceChips(selection: $session.options.format, choices: [
                        OptionChoice(value: "jpeg", title: "JPG", subtitle: "檔案小，適合分享"),
                        OptionChoice(value: "png", title: "PNG", subtitle: "無損，文字邊緣最清楚"),
                        OptionChoice(value: "heic", title: "HEIC", subtitle: "比 JPG 更小"),
                        OptionChoice(value: "tiff", title: "TIFF", subtitle: "無損，適合印刷"),
                    ])
                }
                OptionGroup(title: "解析度", systemImage: "square.resize") {
                    VStack(alignment: .leading, spacing: 6) {
                        SegmentedOption(selection: $session.options.dpi, choices: [
                            OptionChoice(value: 72, title: "72 dpi"),
                            OptionChoice(value: 150, title: "150 dpi"),
                            OptionChoice(value: 300, title: "300 dpi"),
                        ])
                        Text(dpiHint).font(.caption).foregroundStyle(.secondary)
                    }
                }
                if session.options.format == "jpeg" || session.options.format == "heic" {
                    OptionSlider(title: "畫質", value: $session.options.quality, range: 40...100, step: 5, format: { "\(Int($0))%" })
                }
            } else {
                OptionNote(text: "JPEG 圖片會原封不動取出（不重新壓縮），其他圖片存成 PNG。")
            }
            OptionGroup(title: "頁面", systemImage: "doc.on.doc") {
                PageRangeField(title: "要轉換的頁面", text: $session.options.pages, pageCount: session.pageCount)
            }
        }
    }

    private var dpiHint: String {
        switch session.options.dpi {
        case 72: return "螢幕瀏覽、檔案最小"
        case 300: return "列印品質，檔案較大"
        default: return "清晰且大小適中"
        }
    }
}

// MARK: - 擷取內嵌圖片

enum PTImageExtractor {
    /// 取出指定頁面中的圖片（含 Form XObject 內的圖片），回傳張數。
    static func extract(_ doc: PDFDocument, pages: [Int], into folder: URL, base: String, context: ToolContext) throws -> Int {
        var seen = Set<UnsafeRawPointer>()
        var total = 0
        for (i, n) in pages.enumerated() {
            try context.checkCancellation()
            context.progress(Double(i) / Double(pages.count), "搜尋第 \(n) 頁的圖片…")
            guard let page = doc.page(at: n - 1)?.pageRef, let dict = page.dictionary else { continue }
            var streams: [CGPDFStreamRef] = []
            collect(resourcesOf: dict, into: &streams, seen: &seen, depth: 0)
            var k = 0
            for stream in streams {
                try autoreleasepool {
                    guard let (data, ext) = encode(stream) else { return }
                    k += 1
                    let name = "\(base)-第\(PTPDF.padded(n, total: doc.pageCount))頁-\(k)"
                    try data.write(to: folder.appendingPathComponent(CVFileNaming.fileName(CVFileNaming.sanitize(name), ext)))
                    total += 1
                }
            }
        }
        return total
    }

    private static func collect(resourcesOf dict: CGPDFDictionaryRef, into streams: inout [CGPDFStreamRef],
                                seen: inout Set<UnsafeRawPointer>, depth: Int) {
        guard depth < 8 else { return }
        var resources: CGPDFDictionaryRef?
        guard CGPDFDictionaryGetDictionary(dict, "Resources", &resources), let resources else { return }
        var xobjects: CGPDFDictionaryRef?
        guard CGPDFDictionaryGetDictionary(resources, "XObject", &xobjects), let xobjects else { return }
        var found: [CGPDFStreamRef] = []
        CGPDFDictionaryApplyBlock(xobjects, { _, object, _ in
            var stream: CGPDFStreamRef?
            if CGPDFObjectGetValue(object, .stream, &stream), let stream { found.append(stream) }
            return true
        }, nil)
        for stream in found {
            let key = UnsafeRawPointer(stream.rawValue)
            if seen.contains(key) { continue }
            seen.insert(key)
            guard let sd = CGPDFStreamGetDictionary(stream) else { continue }
            var subtype: UnsafePointer<CChar>?
            guard CGPDFDictionaryGetName(sd, "Subtype", &subtype), let subtype else { continue }
            switch String(cString: subtype) {
            case "Image": streams.append(stream)
            case "Form": collect(resourcesOf: sd, into: &streams, seen: &seen, depth: depth + 1)
            default: break
            }
        }
    }

    /// 圖片串流 → (檔案內容, 副檔名)。JPEG／JPEG 2000 直接取出原始資料，其他解碼後存成 PNG。
    private static func encode(_ stream: CGPDFStreamRef) -> (Data, String)? {
        guard let dict = CGPDFStreamGetDictionary(stream) else { return nil }
        var w: CGPDFInteger = 0, h: CGPDFInteger = 0
        guard CGPDFDictionaryGetInteger(dict, "Width", &w), CGPDFDictionaryGetInteger(dict, "Height", &h), w >= 8, h >= 8 else { return nil }
        var format = CGPDFDataFormat.raw
        guard let cfData = CGPDFStreamCopyData(stream, &format) else { return nil }
        let data = cfData as Data
        switch format {
        case .jpegEncoded: return (data, "jpg")
        case .JPEG2000: return (data, "jp2")
        case .raw: break
        @unknown default: return nil
        }
        guard let image = rawImage(data, dict: dict, width: Int(w), height: Int(h)) else { return nil }
        let out = NSMutableData()
        guard let dest = CGImageDestinationCreateWithData(out, UTType.png.identifier as CFString, 1, nil) else { return nil }
        CGImageDestinationAddImage(dest, image, nil)
        guard CGImageDestinationFinalize(dest) else { return nil }
        return (out as Data, "png")
    }

    /// 解出未壓縮的圖片串流，保留原本的色彩空間（灰階、RGB、CMYK、索引色、ICC）。
    static func decodeRaw(_ data: Data, dict: CGPDFDictionaryRef, width w: Int, height h: Int) -> CGImage? {
        var bpcValue: CGPDFInteger = 8
        var isMask: CGPDFBoolean = 0
        CGPDFDictionaryGetBoolean(dict, "ImageMask", &isMask)
        if isMask != 0 { bpcValue = 1 } else { CGPDFDictionaryGetInteger(dict, "BitsPerComponent", &bpcValue) }
        let bpc = Int(bpcValue)
        guard [1, 2, 4, 8, 16].contains(bpc) else { return nil }
        let space: CGColorSpace
        if isMask != 0 {
            space = CGColorSpaceCreateDeviceGray()
        } else {
            guard let s = colorSpace(dict) else { return nil }
            space = s
        }
        let comps = space.model == .indexed ? 1 : space.numberOfComponents
        let bytesPerRow = (w * comps * bpc + 7) / 8
        guard data.count >= bytesPerRow * h, let provider = CGDataProvider(data: data as CFData) else { return nil }
        var decode: [CGFloat]?
        var decodeArray: CGPDFArrayRef?
        if CGPDFDictionaryGetArray(dict, "Decode", &decodeArray), let decodeArray, comps == 1, CGPDFArrayGetCount(decodeArray) == 2 {
            var a: CGPDFReal = 0, b: CGPDFReal = 1
            CGPDFArrayGetNumber(decodeArray, 0, &a)
            CGPDFArrayGetNumber(decodeArray, 1, &b)
            if a > b { decode = space.model == .indexed ? nil : [CGFloat(a), CGFloat(b)] }
        }
        guard let raw = CGImage(width: w, height: h, bitsPerComponent: bpc, bitsPerPixel: bpc * comps, bytesPerRow: bytesPerRow,
                                space: space, bitmapInfo: CGBitmapInfo(rawValue: CGImageAlphaInfo.none.rawValue),
                                provider: provider, decode: decode, shouldInterpolate: false, intent: .defaultIntent) else { return nil }
        return raw
    }

    private static func rawImage(_ data: Data, dict: CGPDFDictionaryRef, width w: Int, height h: Int) -> CGImage? {
        guard let raw = decodeRaw(data, dict: dict, width: w, height: h) else { return nil }
        // 統一轉成 sRGB（CMYK、索引色等格式 PNG 不一定支援），並套用柔和遮罩（透明度）
        let srgb = CGColorSpace(name: CGColorSpace.sRGB)!
        guard let ctx = CGContext(data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w * 4, space: srgb,
                                  bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return raw }
        ctx.draw(raw, in: CGRect(x: 0, y: 0, width: w, height: h))
        if let alpha = softMask(dict, width: w, height: h), let buffer = ctx.data {
            let px = buffer.bindMemory(to: UInt8.self, capacity: w * h * 4)
            for i in 0..<(w * h) {
                let a = UInt16(alpha[i])
                px[i * 4] = UInt8(UInt16(px[i * 4]) * a / 255)
                px[i * 4 + 1] = UInt8(UInt16(px[i * 4 + 1]) * a / 255)
                px[i * 4 + 2] = UInt8(UInt16(px[i * 4 + 2]) * a / 255)
                px[i * 4 + 3] = UInt8(a)
            }
        }
        return ctx.makeImage() ?? raw
    }

    /// 解出 SMask（灰階透明度），縮放成與圖片相同大小。
    private static func softMask(_ dict: CGPDFDictionaryRef, width w: Int, height h: Int) -> [UInt8]? {
        var maskStream: CGPDFStreamRef?
        guard CGPDFDictionaryGetStream(dict, "SMask", &maskStream), let maskStream, let md = CGPDFStreamGetDictionary(maskStream) else { return nil }
        var mw: CGPDFInteger = 0, mh: CGPDFInteger = 0, mbpc: CGPDFInteger = 8
        guard CGPDFDictionaryGetInteger(md, "Width", &mw), CGPDFDictionaryGetInteger(md, "Height", &mh) else { return nil }
        CGPDFDictionaryGetInteger(md, "BitsPerComponent", &mbpc)
        var format = CGPDFDataFormat.raw
        guard mbpc == 8, let cf = CGPDFStreamCopyData(maskStream, &format), format == .raw else { return nil }
        let data = cf as Data
        guard data.count >= Int(mw * mh), let provider = CGDataProvider(data: cf),
              let maskImage = CGImage(width: Int(mw), height: Int(mh), bitsPerComponent: 8, bitsPerPixel: 8, bytesPerRow: Int(mw),
                                      space: CGColorSpaceCreateDeviceGray(), bitmapInfo: CGBitmapInfo(rawValue: 0),
                                      provider: provider, decode: nil, shouldInterpolate: true, intent: .defaultIntent),
              let ctx = CGContext(data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w,
                                  space: CGColorSpaceCreateDeviceGray(), bitmapInfo: CGImageAlphaInfo.none.rawValue),
              let buffer = ctx.data else { return nil }
        ctx.interpolationQuality = .high
        ctx.draw(maskImage, in: CGRect(x: 0, y: 0, width: w, height: h))
        let p = buffer.bindMemory(to: UInt8.self, capacity: w * h)
        return Array(UnsafeBufferPointer(start: p, count: w * h))
    }

    private static func colorSpace(_ dict: CGPDFDictionaryRef) -> CGColorSpace? {
        var name: UnsafePointer<CChar>?
        if CGPDFDictionaryGetName(dict, "ColorSpace", &name), let name { return named(String(cString: name)) }
        var array: CGPDFArrayRef?
        if CGPDFDictionaryGetArray(dict, "ColorSpace", &array), let array { return fromArray(array) }
        return nil
    }

    private static func named(_ name: String) -> CGColorSpace? {
        switch name {
        case "DeviceGray", "G", "CalGray": return CGColorSpaceCreateDeviceGray()
        case "DeviceRGB", "RGB", "CalRGB": return CGColorSpace(name: CGColorSpace.sRGB)
        case "DeviceCMYK", "CMYK": return CGColorSpace(name: CGColorSpace.genericCMYK)
        default: return nil
        }
    }

    private static func fromArray(_ array: CGPDFArrayRef) -> CGColorSpace? {
        var family: UnsafePointer<CChar>?
        guard CGPDFArrayGetName(array, 0, &family), let family else { return nil }
        switch String(cString: family) {
        case "ICCBased":
            var stream: CGPDFStreamRef?
            guard CGPDFArrayGetStream(array, 1, &stream), let stream, let sd = CGPDFStreamGetDictionary(stream) else { return nil }
            var format = CGPDFDataFormat.raw
            if let icc = CGPDFStreamCopyData(stream, &format), let space = CGColorSpace(iccData: icc) { return space }
            var n: CGPDFInteger = 3
            CGPDFDictionaryGetInteger(sd, "N", &n)
            return n == 1 ? named("DeviceGray") : n == 4 ? named("DeviceCMYK") : named("DeviceRGB")
        case "CalRGB": return named("DeviceRGB")
        case "CalGray": return named("DeviceGray")
        case "Indexed", "I":
            var base: CGColorSpace?
            var baseName: UnsafePointer<CChar>?
            var baseArray: CGPDFArrayRef?
            if CGPDFArrayGetName(array, 1, &baseName), let baseName { base = named(String(cString: baseName)) }
            else if CGPDFArrayGetArray(array, 1, &baseArray), let baseArray { base = fromArray(baseArray) }
            guard let base else { return nil }
            var hival: CGPDFInteger = 0
            guard CGPDFArrayGetInteger(array, 2, &hival) else { return nil }
            let needed = Int(hival + 1) * base.numberOfComponents
            var table: Data?
            var str: CGPDFStringRef?
            var lookupStream: CGPDFStreamRef?
            if CGPDFArrayGetString(array, 3, &str), let str, let bytes = CGPDFStringGetBytePtr(str) {
                table = Data(bytes: bytes, count: CGPDFStringGetLength(str))
            } else if CGPDFArrayGetStream(array, 3, &lookupStream), let lookupStream {
                var format = CGPDFDataFormat.raw
                table = CGPDFStreamCopyData(lookupStream, &format) as Data?
            }
            guard let table, table.count >= needed else { return nil }
            return table.withUnsafeBytes { buf in
                CGColorSpace(indexedBaseSpace: base, last: Int(hival), colorTable: buf.bindMemory(to: UInt8.self).baseAddress!)
            }
        default:
            return nil
        }
    }
}

// MARK: - 圖片轉 PDF

enum PTPaperSize: String, Sendable {
    case a4, letter, fit

    var portrait: CGSize? {
        switch self {
        case .a4: return CGSize(width: 595.28, height: 841.89)
        case .letter: return CGSize(width: 612, height: 792)
        case .fit: return nil
        }
    }
}

enum PTOrientation: String, Sendable {
    case auto, portrait, landscape
}

struct PTFromImagesOptions: Sendable {
    var paper: PTPaperSize = .a4
    var orientation: PTOrientation = .auto
    var margin: Double = 0
    var merge = true
}

final class PTFromImagesSession: ToolSession {
    @Published var options = PTFromImagesOptions()
    @Published var count = 0

    override func filesDidChange(_ files: [URL]) { count = files.count }

    override func optionsView() -> AnyView { AnyView(PTFromImagesPanel(session: self)) }

    override func makeJob() -> ToolJob { Self.job(options) }

    override var actionTitleOverride: String? {
        options.merge || count <= 1 ? "轉成 PDF" : "轉成 \(count) 個 PDF"
    }

    nonisolated static func job(_ o: PTFromImagesOptions) -> ToolJob {
        ToolJob { files, context in
            guard !files.isEmpty else { throw ConversionError.other("請選擇至少一張圖片。") }
            let work = try context.makeWorkDirectory()
            if o.merge {
                let tmp = work.appendingPathComponent("images.pdf")
                try PTImagePDFWriter.write(files, to: tmp, options: o, context: context)
                let name = context.outputName(for: files[0], tag: files.count > 1 ? "已合併" : nil, ext: "pdf")
                return [try context.deliver(tmp, as: name, for: files[0])]
            }
            var outputs: [URL] = []
            for (i, file) in files.enumerated() {
                try context.checkCancellation()
                let sub = context.segment(i, of: files.count)
                let tmp = work.appendingPathComponent("image-\(i).pdf")
                try PTImagePDFWriter.write([file], to: tmp, options: o, context: sub)
                outputs.append(try sub.deliver(tmp, as: sub.outputName(for: file, ext: "pdf"), for: file))
            }
            return outputs
        }
    }
}

enum PTImagePDFWriter {
    static func write(_ files: [URL], to out: URL, options o: PTFromImagesOptions, context: ToolContext) throws {
        guard let ctx = CGContext(out as CFURL, mediaBox: nil, [kCGPDFContextCreator: "QUICK_SEEMD"] as CFDictionary) else {
            throw ConversionError.writeFailed("無法建立 PDF。")
        }
        var pages = 0
        for (i, file) in files.enumerated() {
            try context.checkCancellation()
            context.progress(Double(i) / Double(files.count), "加入「\(file.lastPathComponent)」（\(i + 1)/\(files.count)）…")
            guard let src = CGImageSourceCreateWithURL(file as CFURL, nil), CGImageSourceGetCount(src) > 0 else {
                throw ConversionError.unreadable("「\(file.lastPathComponent)」不是可辨識的圖片。")
            }
            let isTIFF = (CGImageSourceGetType(src) as String?) == UTType.tiff.identifier
            let frames = isTIFF ? CGImageSourceGetCount(src) : 1
            for f in 0..<frames {
                try autoreleasepool {
                    let (image, orientation) = try load(src, index: f, file: file)
                    try place(image, orientation: orientation, dpi: dpi(src, index: f), in: ctx, options: o)
                    pages += 1
                }
            }
        }
        ctx.closePDF()
        if pages == 0 { throw ConversionError.emptyContent("沒有可放進 PDF 的圖片。") }
    }

    private static func dpi(_ src: CGImageSource, index: Int) -> Double {
        let props = CGImageSourceCopyPropertiesAtIndex(src, index, nil) as? [CFString: Any]
        let d = (props?[kCGImagePropertyDPIWidth] as? Double) ?? 72
        return d > 0 ? d : 72
    }

    /// 載入圖片；JPEG 保持原始資料（PDF 內直接沿用，不重新壓縮），HEIC 等轉成高品質 JPEG。
    private static func load(_ src: CGImageSource, index: Int, file: URL) throws -> (CGImage, Int) {
        let props = CGImageSourceCopyPropertiesAtIndex(src, index, nil) as? [CFString: Any]
        var orientation = (props?[kCGImagePropertyOrientation] as? Int) ?? 1
        let type = (CGImageSourceGetType(src) as String?) ?? ""
        if type == UTType.jpeg.identifier, let data = try? Data(contentsOf: file),
           let provider = CGDataProvider(data: data as CFData),
           let jpeg = CGImage(jpegDataProviderSource: provider, decode: nil, shouldInterpolate: true, intent: .defaultIntent) {
            if [2, 4, 5, 7].contains(orientation), let fixed = upright(src, index: index) { return (fixed, 1) }
            return (jpeg, orientation)
        }
        guard var image = CGImageSourceCreateImageAtIndex(src, index, nil) else {
            throw ConversionError.unreadable("無法解碼「\(file.lastPathComponent)」。")
        }
        if [2, 4, 5, 7].contains(orientation), let fixed = upright(src, index: index) { image = fixed; orientation = 1 }
        let lossySource = [UTType.heic.identifier, "public.heif", "public.avif", "org.webmproject.webp"].contains(type)
        if lossySource && !CVImageConverter.hasAlpha(image), let jpeg = reencodeJPEG(image) { return (jpeg, orientation) }
        return (image, orientation)
    }

    private static func upright(_ src: CGImageSource, index: Int) -> CGImage? {
        CGImageSourceCreateThumbnailAtIndex(src, index, [
            kCGImageSourceCreateThumbnailFromImageAlways: true,
            kCGImageSourceCreateThumbnailWithTransform: true,
            kCGImageSourceThumbnailMaxPixelSize: 20000,
        ] as CFDictionary)
    }

    private static func reencodeJPEG(_ image: CGImage) -> CGImage? {
        let data = NSMutableData()
        guard let dest = CGImageDestinationCreateWithData(data, UTType.jpeg.identifier as CFString, 1, nil) else { return nil }
        CGImageDestinationAddImage(dest, image, [kCGImageDestinationLossyCompressionQuality: 0.9] as CFDictionary)
        guard CGImageDestinationFinalize(dest), let provider = CGDataProvider(data: data as CFData) else { return nil }
        return CGImage(jpegDataProviderSource: provider, decode: nil, shouldInterpolate: true, intent: .defaultIntent)
    }

    private static func place(_ image: CGImage, orientation: Int, dpi: Double, in ctx: CGContext, options o: PTFromImagesOptions) throws {
        let swap = [5, 6, 7, 8].contains(orientation)
        let pxW = Double(swap ? image.height : image.width), pxH = Double(swap ? image.width : image.height)
        let natural = CGSize(width: pxW * 72 / dpi, height: pxH * 72 / dpi)
        let margin = CGFloat(o.margin)
        var page: CGSize
        if let paper = o.paper.portrait {
            let landscape: Bool
            switch o.orientation {
            case .auto: landscape = natural.width > natural.height
            case .portrait: landscape = false
            case .landscape: landscape = true
            }
            page = landscape ? CGSize(width: paper.height, height: paper.width) : paper
        } else {
            page = CGSize(width: natural.width + margin * 2, height: natural.height + margin * 2)
        }
        page.width = max(page.width, 1)
        page.height = max(page.height, 1)
        var media = CGRect(origin: .zero, size: page)
        ctx.beginPage(mediaBox: &media)
        let area = media.insetBy(dx: margin, dy: margin)
        let scale = min(area.width / natural.width, area.height / natural.height)
        let drawSize = CGSize(width: natural.width * scale, height: natural.height * scale)
        let rect = CGRect(x: area.midX - drawSize.width / 2, y: area.midY - drawSize.height / 2, width: drawSize.width, height: drawSize.height)
        ctx.saveGState()
        ctx.interpolationQuality = .high
        ctx.translateBy(x: rect.minX, y: rect.minY)
        // 依 EXIF 方向轉正（顯示寬高為 rect.size）
        let w = rect.width, h = rect.height
        switch orientation {
        case 3:
            ctx.translateBy(x: w, y: h); ctx.rotate(by: .pi)
            ctx.draw(image, in: CGRect(x: 0, y: 0, width: w, height: h))
        case 6:
            ctx.translateBy(x: 0, y: h); ctx.rotate(by: -.pi / 2)
            ctx.draw(image, in: CGRect(x: 0, y: 0, width: h, height: w))
        case 8:
            ctx.translateBy(x: w, y: 0); ctx.rotate(by: .pi / 2)
            ctx.draw(image, in: CGRect(x: 0, y: 0, width: h, height: w))
        default:
            ctx.draw(image, in: CGRect(x: 0, y: 0, width: w, height: h))
        }
        ctx.restoreGState()
        ctx.endPage()
    }
}

private struct PTFromImagesPanel: View {
    @ObservedObject var session: PTFromImagesSession

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            OptionGroup(title: "頁面大小", systemImage: "doc") {
                ChoiceChips(selection: $session.options.paper, choices: [
                    OptionChoice(value: .a4, title: "A4"),
                    OptionChoice(value: .letter, title: "Letter"),
                    OptionChoice(value: .fit, title: "符合圖片大小"),
                ])
            }
            if session.options.paper != .fit {
                OptionGroup(title: "方向", systemImage: "rectangle.portrait.rotate") {
                    SegmentedOption(selection: $session.options.orientation, choices: [
                        OptionChoice(value: .auto, title: "自動"),
                        OptionChoice(value: .portrait, title: "直向"),
                        OptionChoice(value: .landscape, title: "橫向"),
                    ])
                }
            }
            OptionGroup(title: "邊界", systemImage: "square.dashed") {
                SegmentedOption(selection: $session.options.margin, choices: [
                    OptionChoice(value: 0, title: "無"),
                    OptionChoice(value: 20, title: "小"),
                    OptionChoice(value: 50, title: "大"),
                ])
            }
            OptionGroup(title: "輸出", systemImage: "doc.on.doc") {
                ChoiceCards(selection: $session.options.merge, choices: [
                    OptionChoice(value: true, title: "合併成一個 PDF", subtitle: "依左側順序，每張圖片一頁", systemImage: "doc.richtext"),
                    OptionChoice(value: false, title: "每張圖片各一個 PDF", subtitle: "分別輸出，檔名與圖片相同", systemImage: "square.grid.2x2"),
                ])
            }
            OptionNote(text: "JPEG 照片會直接放進 PDF，不會重新壓縮而降低畫質；方向會依照片的拍攝資訊自動轉正。")
        }
    }
}
