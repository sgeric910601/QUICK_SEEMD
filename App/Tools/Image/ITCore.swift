import AppKit
import CoreImage
import ImageIO
import UniformTypeIdentifiers

// 圖片工具共用的核心：格式、讀取（套用 EXIF 方向、保留色彩描述檔）、算圖、寫出（保留／移除相片資訊）。

// MARK: - 顏色

/// 可跨執行緒傳遞的 sRGB 顏色。
struct ITRGBA: Sendable, Hashable {
    var red: Double
    var green: Double
    var blue: Double
    var alpha: Double = 1

    static let white = ITRGBA(red: 1, green: 1, blue: 1)
    static let black = ITRGBA(red: 0, green: 0, blue: 0)
    static let clear = ITRGBA(red: 0, green: 0, blue: 0, alpha: 0)

    var isOpaque: Bool { alpha >= 0.999 }
    var cgColor: CGColor { CGColor(srgbRed: red, green: green, blue: blue, alpha: alpha) }
    var ciColor: CIColor { CIColor(cgColor: cgColor) }

    func opacity(_ value: Double) -> ITRGBA { ITRGBA(red: red, green: green, blue: blue, alpha: alpha * value) }
}

// MARK: - 格式

enum ITFormat: String, CaseIterable, Sendable, Hashable {
    case jpeg, png, heic, avif, tiff, gif, bmp, webp, ico, icns, pdf

    var title: String {
        switch self {
        case .jpeg: return "JPG"
        case .png: return "PNG"
        case .heic: return "HEIC"
        case .avif: return "AVIF"
        case .tiff: return "TIFF"
        case .gif: return "GIF"
        case .bmp: return "BMP"
        case .webp: return "WebP"
        case .ico: return "ICO"
        case .icns: return "ICNS"
        case .pdf: return "PDF"
        }
    }

    var fileExtension: String {
        switch self {
        case .jpeg: return "jpg"
        default: return rawValue
        }
    }

    var typeIdentifier: String {
        switch self {
        case .jpeg: return "public.jpeg"
        case .png: return "public.png"
        case .heic: return "public.heic"
        case .avif: return "public.avif"
        case .tiff: return "public.tiff"
        case .gif: return "com.compuserve.gif"
        case .bmp: return "com.microsoft.bmp"
        case .webp: return "org.webmproject.webp"
        case .ico: return "com.microsoft.ico"
        case .icns: return "com.apple.icns"
        case .pdf: return "com.adobe.pdf"
        }
    }

    /// 有損格式（有品質滑桿）。
    var isLossy: Bool { [.jpeg, .heic, .avif, .webp].contains(self) }

    /// 能保存透明背景。
    var supportsAlpha: Bool { ![.jpeg, .bmp].contains(self) }

    /// 能保存 EXIF／XMP。
    var supportsMetadata: Bool { [.jpeg, .png, .heic, .avif, .tiff, .webp].contains(self) }

    /// 一般單張點陣圖格式（不是圖示或 PDF）。
    var isBitmap: Bool { ![.ico, .icns, .pdf].contains(self) }

    private static let writableTypes: Set<String> = Set((CGImageDestinationCopyTypeIdentifiers() as? [String]) ?? [])

    /// 這台 Mac 的 ImageIO 能否寫出（PDF 用 Core Graphics 產生）。
    var isWritable: Bool { self == .pdf || Self.writableTypes.contains(typeIdentifier) }

    /// 依檔案類型判斷格式。
    static func of(typeIdentifier: String) -> ITFormat? {
        guard let type = UTType(typeIdentifier) else { return nil }
        if type.conforms(to: .jpeg) { return .jpeg }
        if type.conforms(to: .png) { return .png }
        if type.conforms(to: .heic) || type.conforms(to: .heif) { return .heic }
        if type.identifier == "public.avif" { return .avif }
        if type.conforms(to: .tiff) && !type.conforms(to: .rawImage) { return .tiff }
        if type.conforms(to: .gif) { return .gif }
        if type.conforms(to: .bmp) { return .bmp }
        if type.conforms(to: .webP) { return .webp }
        if type.conforms(to: .ico) { return .ico }
        if type.conforms(to: .icns) { return .icns }
        return nil
    }
}

// MARK: - 讀取

/// 一格畫面（動畫 GIF 會有多格）。
struct ITFrame: @unchecked Sendable {
    var image: CGImage
    var delay: Double
}

/// 讀進來的圖片：像素已依 EXIF 方向轉正，保留原本的色彩空間與相片資訊。
struct ITSource: @unchecked Sendable {
    let url: URL
    /// 至少一格；`allFrames` 讀取動畫時才會有多格
    var frames: [ITFrame]
    let properties: [CFString: Any]
    let metadata: CGImageMetadata?
    let typeIdentifier: String
    let format: ITFormat?
    let hasAlpha: Bool
    let orientation: Int
    let loopCount: Int
    let fileSize: Int64

    var image: CGImage { frames[0].image }
    var width: Int { image.width }
    var height: Int { image.height }
    var isAnimated: Bool { frames.count > 1 }

    var dpi: Double {
        let d = (properties[kCGImagePropertyDPIWidth] as? Double) ?? 72
        return d > 1 ? d : 72
    }

    /// 「維持原格式」時實際要寫出的格式（無法寫回的格式改用 PNG 或 JPG）。
    var preferredOutputFormat: ITFormat {
        if let f = format, f.isWritable, f.isBitmap { return f }
        return hasAlpha ? .png : .jpeg
    }

    /// 輸出檔副檔名：同格式時沿用原檔的寫法（.jpeg 不會變成 .jpg）。
    func outputExtension(for f: ITFormat) -> String {
        let ext = url.pathExtension
        if f == format, !ext.isEmpty, f.isBitmap { return ext.lowercased() }
        return f.fileExtension
    }
}

enum ITLoader {
    static func load(_ url: URL, allFrames: Bool = false) throws -> ITSource {
        guard FileManager.default.fileExists(atPath: url.path) else { throw ConversionError.fileNotFound(url.lastPathComponent) }
        let size = ITText.fileSize(url)
        guard let src = CGImageSourceCreateWithURL(url as CFURL, [kCGImageSourceShouldCache: false] as CFDictionary),
              CGImageSourceGetCount(src) > 0, let type = CGImageSourceGetType(src) as String? else {
            // SVG 等 ImageIO 讀不懂、但 NSImage 可以的格式
            if NSImage(contentsOf: url) != nil {
                let image = try CVImageConverter.rasterizeWithNSImage(url, maxPixelSize: nil)
                return ITSource(url: url, frames: [ITFrame(image: image, delay: 0)], properties: [:], metadata: nil,
                                typeIdentifier: ToolFiles.contentType(of: url).identifier, format: nil, hasAlpha: true,
                                orientation: 1, loopCount: 0, fileSize: size)
            }
            throw ConversionError.unreadable("「\(url.lastPathComponent)」不是可辨識的圖片。")
        }
        let index = CGImageSourceGetPrimaryImageIndex(src)
        let props = (CGImageSourceCopyPropertiesAtIndex(src, index, nil) as? [CFString: Any]) ?? [:]
        let orientation = (props[kCGImagePropertyOrientation] as? Int) ?? 1
        guard let raw = CGImageSourceCreateImageAtIndex(src, index, [kCGImageSourceShouldCacheImmediately: true] as CFDictionary) else {
            throw ConversionError.unreadable("無法解碼「\(url.lastPathComponent)」。")
        }
        let first = try oriented(raw, orientation: orientation)
        var frames = [ITFrame(image: first, delay: CVImageConverter.frameDelay(props) ?? 0.1)]
        let count = CGImageSourceGetCount(src)
        if allFrames, count > 1, index == 0 {
            for i in 1..<count {
                let p = (CGImageSourceCopyPropertiesAtIndex(src, i, nil) as? [CFString: Any]) ?? [:]
                guard let img = CGImageSourceCreateImageAtIndex(src, i, nil) else { continue }
                frames.append(ITFrame(image: try oriented(img, orientation: (p[kCGImagePropertyOrientation] as? Int) ?? 1),
                                      delay: CVImageConverter.frameDelay(p) ?? 0.1))
            }
        }
        let fileProps = (CGImageSourceCopyProperties(src, nil) as? [CFString: Any]) ?? [:]
        let loop = ((fileProps[kCGImagePropertyGIFDictionary] as? [CFString: Any])?[kCGImagePropertyGIFLoopCount] as? Int) ?? 0
        let hasAlpha = (props[kCGImagePropertyHasAlpha] as? Bool) ?? CVImageConverter.hasAlpha(raw)
        return ITSource(url: url, frames: frames, properties: props, metadata: CGImageSourceCopyMetadataAtIndex(src, index, nil),
                        typeIdentifier: type, format: ITFormat.of(typeIdentifier: type), hasAlpha: hasAlpha,
                        orientation: orientation, loopCount: loop, fileSize: size)
    }

    /// 依 EXIF 方向把像素轉正（無損：90° 倍數的搬移，不重新取樣、不轉色彩空間）。
    static func oriented(_ image: CGImage, orientation: Int) throws -> CGImage {
        guard orientation != 1, let o = CGImagePropertyOrientation(rawValue: UInt32(orientation)) else { return image }
        let ci = CIImage(cgImage: image).oriented(o)
        return try ITRender.render(ci, rect: ci.extent, like: image, exact: true)
    }

    /// 轉正後的像素尺寸（給選項面板用，不解碼整張圖）。
    static func pixelSize(of url: URL) -> CGSize? {
        guard let src = CGImageSourceCreateWithURL(url as CFURL, nil), CGImageSourceGetCount(src) > 0 else {
            if let image = NSImage(contentsOf: url), image.size.width > 0 { return image.size }
            return nil
        }
        let props = (CGImageSourceCopyPropertiesAtIndex(src, CGImageSourceGetPrimaryImageIndex(src), nil) as? [CFString: Any]) ?? [:]
        guard let w = props[kCGImagePropertyPixelWidth] as? Int, let h = props[kCGImagePropertyPixelHeight] as? Int else { return nil }
        let o = (props[kCGImagePropertyOrientation] as? Int) ?? 1
        return o >= 5 ? CGSize(width: h, height: w) : CGSize(width: w, height: h)
    }

    /// 轉正後的預覽縮圖。
    static func preview(of url: URL, maxPixelSize: Int) -> CGImage? {
        if let src = CGImageSourceCreateWithURL(url as CFURL, nil), CGImageSourceGetCount(src) > 0 {
            let opts: [CFString: Any] = [
                kCGImageSourceCreateThumbnailFromImageAlways: true,
                kCGImageSourceCreateThumbnailWithTransform: true,
                kCGImageSourceThumbnailMaxPixelSize: maxPixelSize,
            ]
            return CGImageSourceCreateThumbnailAtIndex(src, CGImageSourceGetPrimaryImageIndex(src), opts as CFDictionary)
        }
        return try? CVImageConverter.rasterizeWithNSImage(url, maxPixelSize: maxPixelSize)
    }
}

// MARK: - 算圖

enum ITRender {
    /// 有色彩管理（模糊、縮放等運算在線性空間進行）。
    static let managed = CIContext(options: [.cacheIntermediates: false, .name: "QuickSeemd.ImageTools"])
    /// 不做色彩轉換（純幾何搬移，數值完全不變）。
    static let exact = CIContext(options: [.workingColorSpace: NSNull(), .cacheIntermediates: false])

    static let sRGB = CGColorSpace(name: CGColorSpace.sRGB)!

    /// 輸出時沿用原圖的 RGB 色彩空間（例如 Display P3），其他（灰階、CMYK）轉成 sRGB。
    static func colorSpace(like image: CGImage?) -> CGColorSpace {
        if let cs = image?.colorSpace, cs.model == .rgb { return cs }
        return sRGB
    }

    static func render(_ ci: CIImage, rect: CGRect, like source: CGImage, exact: Bool = false) throws -> CGImage {
        let useExact = exact && source.colorSpace?.model == .rgb
        let format: CIFormat = source.bitsPerComponent > 8 ? .RGBA16 : .RGBA8
        let r = rect.integral
        guard r.width >= 1, r.height >= 1,
              let out = (useExact ? Self.exact : managed).createCGImage(ci, from: r, format: format, colorSpace: colorSpace(like: source)) else {
            throw ConversionError.other("影像處理失敗（圖片可能太大或記憶體不足）。")
        }
        return out
    }

    /// 與原圖相同色彩空間、位元深度的繪圖畫布。
    static func canvas(width: Int, height: Int, like source: CGImage?, opaque: Bool = false) throws -> CGContext {
        let deep = (source?.bitsPerComponent ?? 8) > 8
        let alpha: CGImageAlphaInfo = opaque ? .noneSkipLast : .premultipliedLast
        guard width > 0, height > 0, width <= 32_768, height <= 32_768,
              let ctx = CGContext(data: nil, width: width, height: height, bitsPerComponent: deep ? 16 : 8, bytesPerRow: 0,
                                  space: colorSpace(like: source), bitmapInfo: alpha.rawValue) else {
            throw ConversionError.other("無法建立 \(width) × \(height) 的畫布（尺寸太大或記憶體不足）。")
        }
        ctx.interpolationQuality = .high
        return ctx
    }

    /// 疊在背景色上，去掉透明度。
    static func flatten(_ image: CGImage, on color: ITRGBA) throws -> CGImage {
        let ctx = try canvas(width: image.width, height: image.height, like: image, opaque: true)
        let rect = CGRect(x: 0, y: 0, width: image.width, height: image.height)
        ctx.setFillColor(color.isOpaque ? color.cgColor : ITRGBA.white.cgColor)
        ctx.fill(rect)
        ctx.draw(image, in: rect)
        guard let out = ctx.makeImage() else { throw ConversionError.other("無法合成背景。") }
        return out
    }
}

// MARK: - 重新取樣

enum ITScale {
    /// 縮放到指定像素：平滑＝Lanczos（高品質），否則最近鄰（像素風、不模糊）。
    static func resample(_ image: CGImage, width: Int, height: Int, smooth: Bool = true) throws -> CGImage {
        if width == image.width && height == image.height { return image }
        guard width > 0, height > 0 else { throw ConversionError.other("輸出尺寸不正確。") }
        guard width <= 32_768, height <= 32_768 else {
            throw ConversionError.other("輸出尺寸 \(width) × \(height) 太大（上限 32768 px）。")
        }
        if !smooth {
            let ctx = try ITRender.canvas(width: width, height: height, like: image, opaque: !CVImageConverter.hasAlpha(image))
            ctx.interpolationQuality = .none
            ctx.draw(image, in: CGRect(x: 0, y: 0, width: width, height: height))
            guard let out = ctx.makeImage() else { throw ConversionError.other("縮放失敗。") }
            return out
        }
        let sy = Double(height) / Double(image.height)
        let sx = Double(width) / Double(image.width)
        let scaled = CIImage(cgImage: image).clampedToExtent().applyingFilter("CILanczosScaleTransform", parameters: [
            kCIInputScaleKey: sy,
            kCIInputAspectRatioKey: sx / sy,
        ])
        let rect = CGRect(x: 0, y: 0, width: width, height: height)
        return try ITRender.render(scaled.cropped(to: rect), rect: rect, like: image)
    }
}

// MARK: - 相片資訊

enum ITMetadataMode: String, Sendable {
    case keep, removeLocation, removeAll
}

enum ITMetadata {
    private static let locationNames: Set<String> = [
        "City", "State", "Country", "CountryCode", "Location", "Sublocation", "LocationCreated", "LocationShown",
    ]

    /// 標籤是否屬於位置資訊（EXIF GPS、IPTC 地點）。
    static func isLocation(prefix: String, name: String) -> Bool {
        if name.hasPrefix("GPS") { return true }
        return ["photoshop", "Iptc4xmpCore", "Iptc4xmpExt"].contains(prefix) && locationNames.contains(name)
    }

    /// 最上層的標籤路徑與（前綴、名稱）。
    static func tags(_ meta: CGImageMetadata) -> [(path: String, prefix: String, name: String)] {
        var list: [(String, String, String)] = []
        CGImageMetadataEnumerateTagsUsingBlock(meta, nil, nil) { path, tag in
            let prefix = (CGImageMetadataTagCopyPrefix(tag) as String?) ?? ""
            let name = (CGImageMetadataTagCopyName(tag) as String?) ?? ""
            list.append((path as String, prefix, name))
            return true
        }
        return list
    }

    static func locationTagCount(_ meta: CGImageMetadata?) -> Int {
        guard let meta else { return 0 }
        return tags(meta).filter { isLocation(prefix: $0.prefix, name: $0.name) }.count
    }

    /// 移除位置資訊後的副本。
    static func removingLocation(_ meta: CGImageMetadata) -> CGMutableImageMetadata {
        let m = CGImageMetadataCreateMutableCopy(meta) ?? CGImageMetadataCreateMutable()
        for t in tags(meta) where isLocation(prefix: t.prefix, name: t.name) {
            CGImageMetadataRemoveTagWithPath(m, nil, t.path as CFString)
        }
        return m
    }

    /// 寫出新像素時使用的相片資訊：方向設為正、尺寸更新，依模式移除位置。
    static func prepared(_ meta: CGImageMetadata, mode: ITMetadataMode, width: Int, height: Int) -> CGImageMetadata {
        let m = mode == .removeLocation ? removingLocation(meta) : (CGImageMetadataCreateMutableCopy(meta) ?? CGImageMetadataCreateMutable())
        CGImageMetadataSetValueMatchingImageProperty(m, kCGImagePropertyTIFFDictionary, kCGImagePropertyTIFFOrientation, 1 as CFNumber)
        let names = Set(tags(meta).map(\.name))
        if names.contains("PixelXDimension") {
            CGImageMetadataSetValueMatchingImageProperty(m, kCGImagePropertyExifDictionary, kCGImagePropertyExifPixelXDimension, width as CFNumber)
            CGImageMetadataSetValueMatchingImageProperty(m, kCGImagePropertyExifDictionary, kCGImagePropertyExifPixelYDimension, height as CFNumber)
        }
        return m
    }
}

// MARK: - 寫出

/// 寫出設定。
struct ITOutput: Sendable {
    var format: ITFormat
    /// 有損格式的品質 0…1
    var quality: Double = 0.92
    var metadata: ITMetadataMode = .keep
    /// 不支援透明的格式（JPG、BMP）墊在底下的顏色
    var background: ITRGBA = .white
    /// false：結果一定不透明（例如原圖本來就沒有透明度），寫出時不帶 alpha 色版，檔案較小
    var keepsAlpha: Bool = true
}

enum ITWriter {
    static func write(_ image: CGImage, source: ITSource?, to url: URL, output: ITOutput) throws {
        try write([ITFrame(image: image, delay: 0)], source: source, to: url, output: output)
    }

    static func write(_ frames: [ITFrame], source: ITSource?, to url: URL, output: ITOutput) throws {
        guard let first = frames.first else { throw ConversionError.emptyContent("沒有可寫出的影像。") }
        let format = output.format
        guard format.isWritable else { throw ConversionError.writeFailed("這台 Mac 無法寫入 \(format.title) 格式。") }
        switch format {
        case .pdf:
            try writePDF(prepare(first.image, output: output), dpi: source?.dpi ?? 72, to: url)
            return
        case .ico:
            try writeIcon(first.image, sides: [16, 32, 48, 64, 128, 256], uti: format.typeIdentifier, to: url)
            return
        case .icns:
            try writeIcon(first.image, sides: [16, 32, 64, 128, 256, 512, 1024], uti: format.typeIdentifier, to: url)
            return
        default:
            break
        }
        let list = format == .gif ? frames : [first]
        guard let dest = CGImageDestinationCreateWithURL(url as CFURL, format.typeIdentifier as CFString, list.count, nil) else {
            throw ConversionError.writeFailed("這台 Mac 無法寫入 \(format.title) 格式。")
        }
        if list.count > 1 {
            let file: [CFString: Any] = [kCGImagePropertyGIFDictionary: [kCGImagePropertyGIFLoopCount: source?.loopCount ?? 0]]
            CGImageDestinationSetProperties(dest, file as CFDictionary)
        }
        for frame in list {
            let image = try prepare(frame.image, output: output)
            var opts: [CFString: Any] = [:]
            if format.isLossy { opts[kCGImageDestinationLossyCompressionQuality] = max(0.05, min(1, output.quality)) }
            if list.count > 1 { opts[kCGImagePropertyGIFDictionary] = [kCGImagePropertyGIFDelayTime: frame.delay] }
            if format == .tiff { opts[kCGImagePropertyTIFFDictionary] = [kCGImagePropertyTIFFCompression: 5] } // LZW
            if output.metadata != .removeAll, format.supportsMetadata, let meta = source?.metadata {
                let m = ITMetadata.prepared(meta, mode: output.metadata, width: image.width, height: image.height)
                CGImageDestinationAddImageAndMetadata(dest, image, m, opts as CFDictionary)
            } else {
                if let dpi = source?.dpi, dpi != 72 {
                    opts[kCGImagePropertyDPIWidth] = dpi
                    opts[kCGImagePropertyDPIHeight] = dpi
                }
                CGImageDestinationAddImage(dest, image, opts as CFDictionary)
            }
        }
        guard CGImageDestinationFinalize(dest) else {
            throw ConversionError.writeFailed("無法完成 \(format.title) 編碼（圖片尺寸或色彩格式可能不受支援）。")
        }
    }

    /// 依格式處理透明度。
    private static func prepare(_ image: CGImage, output: ITOutput) throws -> CGImage {
        guard CVImageConverter.hasAlpha(image) else { return image }
        if !output.format.supportsAlpha || !output.keepsAlpha {
            return try ITRender.flatten(image, on: output.background)
        }
        return image
    }

    private static func writePDF(_ image: CGImage, dpi: Double, to url: URL) throws {
        let scale = 72 / dpi
        var media = CGRect(x: 0, y: 0, width: Double(image.width) * scale, height: Double(image.height) * scale)
        guard let ctx = CGContext(url as CFURL, mediaBox: &media, [kCGPDFContextCreator: "QUICK_SEEMD"] as CFDictionary) else {
            throw ConversionError.writeFailed("無法建立 PDF。")
        }
        ctx.beginPage(mediaBox: &media)
        ctx.interpolationQuality = .high
        ctx.draw(image, in: media)
        ctx.endPage()
        ctx.closePDF()
    }

    /// ICO／ICNS：多種尺寸的正方形圖示（非正方形的圖會置中）。
    static func writeIcon(_ image: CGImage, sides: [Int], uti: String, to url: URL) throws {
        guard let dest = CGImageDestinationCreateWithURL(url as CFURL, uti as CFString, sides.count, nil) else {
            throw ConversionError.writeFailed("這台 Mac 無法寫入圖示格式。")
        }
        for side in sides {
            CGImageDestinationAddImage(dest, try squareIcon(image, side: side), nil)
        }
        guard CGImageDestinationFinalize(dest) else { throw ConversionError.writeFailed("圖示編碼失敗。") }
    }

    static func squareIcon(_ image: CGImage, side: Int) throws -> CGImage {
        let ctx = try ITRender.canvas(width: side, height: side, like: nil)
        let scale = CGFloat(side) / CGFloat(max(image.width, image.height))
        let w = CGFloat(image.width) * scale, h = CGFloat(image.height) * scale
        ctx.draw(image, in: CGRect(x: (CGFloat(side) - w) / 2, y: (CGFloat(side) - h) / 2, width: w, height: h))
        guard let out = ctx.makeImage() else { throw ConversionError.other("無法產生 \(side)×\(side) 圖示。") }
        return out
    }
}

// MARK: - 流程

enum ITPipeline {
    /// 逐檔：讀取 → 處理（動畫 GIF 每一格都處理）→ 寫出 → 交付到輸出位置。
    static func run(_ file: URL, context: ToolContext, tag: String?, animated: Bool = true,
                    output: (ITSource) -> ITOutput,
                    transform: (CGImage, ITSource) throws -> CGImage) throws -> URL {
        context.progress(0.02, "讀取：\(file.lastPathComponent)")
        let source = try ITLoader.load(file, allFrames: animated && ToolFiles.contentType(of: file).conforms(to: .gif))
        try context.checkCancellation()
        let out = output(source)
        let list = out.format == .gif ? source.frames : [source.frames[0]]
        var frames: [ITFrame] = []
        for (i, f) in list.enumerated() {
            try context.checkCancellation()
            frames.append(ITFrame(image: try transform(f.image, source), delay: f.delay))
            context.progress(0.1 + 0.7 * Double(i + 1) / Double(list.count), "處理中：\(file.lastPathComponent)")
        }
        try context.checkCancellation()
        return try deliver(frames, source: source, output: out, context: context, tag: tag)
    }

    /// 寫到暫存區後交付（同名自動加編號，絕不覆蓋）。
    static func deliver(_ frames: [ITFrame], source: ITSource, output: ITOutput, context: ToolContext, tag: String?) throws -> URL {
        let work = try context.makeWorkDirectory()
        defer { try? FileManager.default.removeItem(at: work) }
        let ext = source.outputExtension(for: output.format)
        let tmp = work.appendingPathComponent("output.\(ext)")
        context.progress(0.85, "寫入 \(output.format.title)…")
        try ITWriter.write(frames, source: source, to: tmp, output: output)
        let url = try context.deliver(tmp, as: context.outputName(for: source.url, tag: tag, ext: ext), for: source.url)
        context.progress(1, "已儲存：\(url.lastPathComponent)")
        return url
    }

    /// 把暫存檔交付（給自己產生檔案的工具用）。
    static func deliver(file tmp: URL, for input: URL, context: ToolContext, tag: String?, ext: String) throws -> URL {
        try context.deliver(tmp, as: context.outputName(for: input, tag: tag, ext: ext), for: input)
    }

    /// 只處理單一檔案時直接拋出錯誤；多個檔案時略過問題檔、最後再一起說明。
    static func eachFile(_ files: [URL], context: ToolContext,
                         _ body: (URL, ToolContext) async throws -> [URL]) async throws -> (outputs: [URL], skipped: [String]) {
        var outputs: [URL] = []
        var skipped: [String] = []
        for (i, file) in files.enumerated() {
            try context.checkCancellation()
            let sub = context.segment(i, of: files.count)
            sub.progress(0, "處理中：\(file.lastPathComponent)")
            do {
                outputs += try await body(file, sub)
            } catch let e as ITSkip where files.count > 1 {
                skipped.append("\(file.lastPathComponent)：\(e.reason)")
            }
            sub.progress(1)
        }
        return (outputs, skipped)
    }
}

/// 「這個檔案不需要／無法處理」：單檔時當成錯誤顯示，多檔時略過。
struct ITSkip: LocalizedError {
    let reason: String
    var errorDescription: String? { reason }
}

// MARK: - 小工具

enum ITText {
    static func size(_ bytes: Int64) -> String { ToolFiles.formattedSize(bytes) }

    /// 檔案目前的大小（不使用 URL 的快取；同一路徑重寫後也正確）。
    static func fileSize(_ url: URL) -> Int64 {
        ((try? FileManager.default.attributesOfItem(atPath: url.path))?[.size] as? NSNumber)?.int64Value ?? 0
    }

    static func percent(_ value: Double) -> String { "\(Int((value * 100).rounded()))%" }

    static func pixels(_ w: Int, _ h: Int) -> String { "\(w) × \(h) px" }
}
