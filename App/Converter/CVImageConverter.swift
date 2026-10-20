import AppKit
import CoreGraphics
import Foundation
import ImageIO
import UniformTypeIdentifiers
import Vision

/// 圖片轉換：ImageIO 讀寫、ICO／ICNS 多尺寸、SVG 點陣化、OCR、圖片轉 PDF。
enum CVImageConverter {
    private static let srgb = CGColorSpace(name: CGColorSpace.sRGB)!

    // MARK: - 讀取

    /// 開啟一張圖片並套用方向（EXIF orientation）、縮放。回傳 CGImage 與原始屬性。
    static func loadImage(_ url: URL, index: Int = 0, maxPixelSize: Int?) throws -> (image: CGImage, properties: [CFString: Any]) {
        let ext = url.pathExtension.lowercased()
        if ext == "svg" {
            return (try rasterizeWithNSImage(url, maxPixelSize: maxPixelSize), [:])
        }
        guard let src = CGImageSourceCreateWithURL(url as CFURL, [kCGImageSourceShouldCache: false] as CFDictionary),
              CGImageSourceGetCount(src) > index else {
            throw ConversionError.unreadable("ImageIO 無法開啟這張圖片。")
        }
        let props = (CGImageSourceCopyPropertiesAtIndex(src, index, nil) as? [CFString: Any]) ?? [:]
        let width = (props[kCGImagePropertyPixelWidth] as? Int) ?? 0
        let height = (props[kCGImagePropertyPixelHeight] as? Int) ?? 0
        let longSide = max(width, height)
        let target = min(maxPixelSize ?? longSide, longSide > 0 ? longSide : (maxPixelSize ?? 4096))
        let opts: [CFString: Any] = [
            kCGImageSourceCreateThumbnailFromImageAlways: true,
            kCGImageSourceCreateThumbnailWithTransform: true,
            kCGImageSourceThumbnailMaxPixelSize: max(target, 1),
            kCGImageSourceShouldCacheImmediately: true,
        ]
        if let img = CGImageSourceCreateThumbnailAtIndex(src, index, opts as CFDictionary) {
            return (img, props)
        }
        if let img = CGImageSourceCreateImageAtIndex(src, index, nil) {
            return (img, props)
        }
        throw ConversionError.unreadable("無法解碼這張圖片。")
    }

    /// SVG 等只有 NSImage 讀得懂的格式：依指定尺寸點陣化。
    static func rasterizeWithNSImage(_ url: URL, maxPixelSize: Int?) throws -> CGImage {
        guard let image = NSImage(contentsOf: url) else { throw ConversionError.unreadable("無法開啟這個向量圖。") }
        var size = image.size
        if size.width <= 0 || size.height <= 0 { size = CGSize(width: 512, height: 512) }
        let intrinsicLong = max(size.width, size.height)
        let targetLong = CGFloat(maxPixelSize ?? Int(max(intrinsicLong * 2, 1024)))
        let scale = targetLong / intrinsicLong
        let pw = max(1, Int((size.width * scale).rounded()))
        let ph = max(1, Int((size.height * scale).rounded()))
        guard let ctx = CGContext(data: nil, width: pw, height: ph, bitsPerComponent: 8, bytesPerRow: 0, space: srgb,
                                  bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else {
            throw ConversionError.other("無法建立繪圖緩衝區。")
        }
        let gc = NSGraphicsContext(cgContext: ctx, flipped: false)
        NSGraphicsContext.saveGraphicsState()
        NSGraphicsContext.current = gc
        image.draw(in: CGRect(x: 0, y: 0, width: pw, height: ph), from: .zero, operation: .copy, fraction: 1)
        NSGraphicsContext.restoreGraphicsState()
        guard let cg = ctx.makeImage() else { throw ConversionError.other("向量圖點陣化失敗。") }
        return cg
    }

    // MARK: - 寫入

    static func hasAlpha(_ image: CGImage) -> Bool {
        switch image.alphaInfo {
        case .none, .noneSkipFirst, .noneSkipLast: return false
        default: return true
        }
    }

    /// 將有透明度的圖片疊在白底上（JPEG 等不支援透明）。
    static func flatten(_ image: CGImage, background: CGColor = CGColor(gray: 1, alpha: 1)) -> CGImage {
        guard hasAlpha(image),
              let ctx = CGContext(data: nil, width: image.width, height: image.height, bitsPerComponent: 8, bytesPerRow: 0,
                                  space: srgb, bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue) else { return image }
        let rect = CGRect(x: 0, y: 0, width: image.width, height: image.height)
        ctx.setFillColor(background)
        ctx.fill(rect)
        ctx.draw(image, in: rect)
        return ctx.makeImage() ?? image
    }

    /// 把圖片置中放進正方形畫布（ICO／ICNS 需要正方形）。
    static func squareCanvas(_ image: CGImage, side: Int) -> CGImage? {
        guard let ctx = CGContext(data: nil, width: side, height: side, bitsPerComponent: 8, bytesPerRow: 0, space: srgb,
                                  bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return nil }
        ctx.interpolationQuality = .high
        let scale = CGFloat(side) / CGFloat(max(image.width, image.height))
        let w = CGFloat(image.width) * scale, h = CGFloat(image.height) * scale
        ctx.draw(image, in: CGRect(x: (CGFloat(side) - w) / 2, y: (CGFloat(side) - h) / 2, width: w, height: h))
        return ctx.makeImage()
    }

    /// 移除方向資訊（像素已經轉正）。
    private static func normalizedProperties(_ props: [CFString: Any]) -> [CFString: Any] {
        var out = props
        out[kCGImagePropertyOrientation] = 1
        if var tiff = out[kCGImagePropertyTIFFDictionary] as? [CFString: Any] {
            tiff[kCGImagePropertyTIFFOrientation] = 1
            out[kCGImagePropertyTIFFDictionary] = tiff
        }
        for key in [kCGImagePropertyPixelWidth, kCGImagePropertyPixelHeight, kCGImagePropertyDepth] {
            out.removeValue(forKey: key)
        }
        return out
    }

    /// 寫入單張 CGImage。
    static func write(_ image: CGImage, to url: URL, formatID: String, quality: Double, properties: [CFString: Any] = [:]) throws {
        guard let uti = ConversionFormats.imageUTIs[formatID] else {
            throw ConversionError.other("不支援的圖片格式：\(formatID)")
        }
        try writeFrames([(image, properties)], to: url, uti: uti, formatID: formatID, quality: quality, fileProperties: nil)
    }

    private static func writeFrames(_ frames: [(CGImage, [CFString: Any])], to url: URL, uti: String, formatID: String,
                                    quality: Double, fileProperties: [CFString: Any]?) throws {
        guard let dest = CGImageDestinationCreateWithURL(url as CFURL, uti as CFString, frames.count, nil) else {
            throw ConversionError.writeFailed("這台 Mac 無法寫入 \(formatID.uppercased()) 格式。")
        }
        if let fileProperties { CGImageDestinationSetProperties(dest, fileProperties as CFDictionary) }
        let lossy = ["jpeg", "heic", "avif", "webp", "jxl", "jp2"].contains(formatID)
        for (image, props) in frames {
            var p = normalizedProperties(props)
            if lossy { p[kCGImageDestinationLossyCompressionQuality] = max(0.05, min(1, quality)) }
            let img = formatID == "jpeg" ? flatten(image) : image
            CGImageDestinationAddImage(dest, img, p as CFDictionary)
        }
        guard CGImageDestinationFinalize(dest) else {
            throw ConversionError.writeFailed("ImageIO 無法完成 \(formatID.uppercased()) 編碼（圖片尺寸或色彩格式可能不受支援）。")
        }
    }

    // MARK: - 轉換入口

    /// 圖片 → 圖片。
    static func convert(_ input: URL, profile: CVInputProfile, to format: ConversionFormat, options: ConversionOptions,
                        workDir: URL, progress: @Sendable (Double) -> Void) throws -> [URL] {
        let out = workDir.appendingPathComponent(CVFileNaming.fileName(CVFileNaming.baseName(of: input), format.fileExtension))
        switch format.id {
        case "ico":
            try writeIcon(input, to: out, uti: "com.microsoft.ico", sides: [16, 32, 48, 64, 128, 256]) // ImageIO 的 ICO 最多 6 張
        case "icns":
            try writeIcon(input, to: out, uti: "com.apple.icns", sides: [16, 32, 64, 128, 256, 512, 1024])
        default:
            guard let uti = ConversionFormats.imageUTIs[format.id] else {
                throw ConversionError.unsupportedTarget(source: input.lastPathComponent, target: format.displayName)
            }
            let multiFrameTarget = ["gif", "tiff"].contains(format.id)
            let frameCount = (profile.isAnimatedImage || profile.imageFrameCount > 1) && multiFrameTarget ? profile.imageFrameCount : 1
            var frames: [(CGImage, [CFString: Any])] = []
            for i in 0..<max(frameCount, 1) {
                try Task.checkCancellation()
                let loaded = try loadImage(input, index: i, maxPixelSize: options.imageMaxPixelSize)
                frames.append((loaded.image, loaded.properties))
                if frameCount > 1 { progress(Double(i + 1) / Double(frameCount) * 0.9) }
            }
            var fileProps: [CFString: Any]?
            if frameCount > 1, let src = CGImageSourceCreateWithURL(input as CFURL, nil) {
                fileProps = CGImageSourceCopyProperties(src, nil) as? [CFString: Any]
                if format.id == "gif", fileProps?[kCGImagePropertyGIFDictionary] == nil {
                    fileProps?[kCGImagePropertyGIFDictionary] = [kCGImagePropertyGIFLoopCount: 0]
                }
                // 動畫 PNG／WebP → GIF 時，沿用每格延遲時間
                if format.id == "gif" {
                    frames = frames.map { image, props in
                        var p = props
                        if p[kCGImagePropertyGIFDictionary] == nil {
                            let delay = frameDelay(props) ?? 0.1
                            p[kCGImagePropertyGIFDictionary] = [kCGImagePropertyGIFDelayTime: delay]
                        }
                        return (image, p)
                    }
                }
            }
            try writeFrames(frames, to: out, uti: uti, formatID: format.id, quality: options.imageQuality, fileProperties: fileProps)
        }
        progress(1)
        return [out]
    }

    static func frameDelay(_ props: [CFString: Any]) -> Double? {
        let dicts: [(CFString, CFString, CFString)] = [
            (kCGImagePropertyGIFDictionary, kCGImagePropertyGIFUnclampedDelayTime, kCGImagePropertyGIFDelayTime),
            (kCGImagePropertyPNGDictionary, kCGImagePropertyAPNGUnclampedDelayTime, kCGImagePropertyAPNGDelayTime),
            (kCGImagePropertyWebPDictionary, kCGImagePropertyWebPUnclampedDelayTime, kCGImagePropertyWebPDelayTime),
            (kCGImagePropertyHEICSDictionary, kCGImagePropertyHEICSUnclampedDelayTime, kCGImagePropertyHEICSDelayTime),
        ]
        for (dictKey, unclamped, clamped) in dicts {
            if let d = props[dictKey] as? [CFString: Any] {
                if let v = d[unclamped] as? Double, v > 0 { return v }
                if let v = d[clamped] as? Double, v > 0 { return v }
            }
        }
        return nil
    }

    /// ICO／ICNS：產生多種尺寸的正方形圖示。
    private static func writeIcon(_ input: URL, to out: URL, uti: String, sides: [Int]) throws {
        let loaded = try loadImage(input, maxPixelSize: 1024)
        guard let dest = CGImageDestinationCreateWithURL(out as CFURL, uti as CFString, sides.count, nil) else {
            throw ConversionError.writeFailed("這台 Mac 無法寫入圖示格式。")
        }
        for side in sides {
            guard let square = squareCanvas(loaded.image, side: side) else { continue }
            CGImageDestinationAddImage(dest, square, nil)
        }
        guard CGImageDestinationFinalize(dest) else { throw ConversionError.writeFailed("圖示編碼失敗。") }
    }

    // MARK: - 圖片 → PDF

    /// 將多張圖片（與 PDF）依序合併成一份 PDF；每張圖片一頁，頁面大小依 DPI 計算。
    static func writePDF(from inputs: [URL], to out: URL, options: ConversionOptions, progress: @Sendable (Double) -> Void) throws {
        guard let ctx = CGContext(out as CFURL, mediaBox: nil, [kCGPDFContextCreator: "QUICK_SEEMD"] as CFDictionary) else {
            throw ConversionError.writeFailed("無法建立 PDF。")
        }
        var pageCount = 0
        for (i, input) in inputs.enumerated() {
            try Task.checkCancellation()
            if input.pathExtension.lowercased() == "pdf" {
                guard let doc = CGPDFDocument(input as CFURL) else { throw ConversionError.unreadable(input.lastPathComponent) }
                if doc.isEncrypted && !doc.unlockWithPassword("") { throw ConversionError.passwordProtected }
                for n in 0..<doc.numberOfPages {
                    guard let page = doc.page(at: n + 1) else { continue }
                    let box = page.getBoxRect(.cropBox)
                    let rotated = page.rotationAngle % 180 != 0
                    var media = CGRect(x: 0, y: 0, width: rotated ? box.height : box.width, height: rotated ? box.width : box.height)
                    ctx.beginPage(mediaBox: &media)
                    ctx.concatenate(page.getDrawingTransform(.cropBox, rect: media, rotate: 0, preserveAspectRatio: true))
                    ctx.drawPDFPage(page)
                    ctx.endPage()
                    pageCount += 1
                }
            } else {
                let loaded = try loadImage(input, maxPixelSize: options.imageMaxPixelSize)
                let dpiX = (loaded.properties[kCGImagePropertyDPIWidth] as? Double) ?? 72
                let scale = 72.0 / (dpiX > 0 ? dpiX : 72)
                var media = CGRect(x: 0, y: 0, width: Double(loaded.image.width) * scale, height: Double(loaded.image.height) * scale)
                ctx.beginPage(mediaBox: &media)
                ctx.interpolationQuality = .high
                ctx.draw(loaded.image, in: media)
                ctx.endPage()
                pageCount += 1
            }
            progress(Double(i + 1) / Double(inputs.count))
        }
        ctx.closePDF()
        if pageCount == 0 {
            try? FileManager.default.removeItem(at: out)
            throw ConversionError.emptyContent("沒有可放進 PDF 的頁面。")
        }
    }

    // MARK: - OCR

    /// 以 Vision 辨識圖片中的文字（支援繁中、簡中、英、日、韓）。
    static func recognizeText(in image: CGImage) throws -> String {
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .accurate
        request.usesLanguageCorrection = true
        let wanted = ["zh-Hant", "zh-Hans", "en-US", "ja-JP", "ko-KR"]
        if let supported = try? request.supportedRecognitionLanguages() {
            let langs = wanted.filter { supported.contains($0) }
            if !langs.isEmpty { request.recognitionLanguages = langs }
        }
        if #available(macOS 13.0, *) { request.automaticallyDetectsLanguage = false }
        let handler = VNImageRequestHandler(cgImage: image, options: [:])
        try handler.perform([request])
        let observations = request.results ?? []
        // 依位置排序：由上而下、由左而右；同一行的片段以空白相連
        let items = observations.compactMap { obs -> (CGRect, String)? in
            guard let text = obs.topCandidates(1).first?.string else { return nil }
            return (obs.boundingBox, text)
        }
        let sorted = items.sorted { a, b in
            if abs(a.0.midY - b.0.midY) > min(a.0.height, b.0.height) * 0.5 { return a.0.midY > b.0.midY }
            return a.0.minX < b.0.minX
        }
        var lines: [String] = []
        var lastMidY: CGFloat?
        var lastHeight: CGFloat = 0
        for (box, text) in sorted {
            if let y = lastMidY, abs(box.midY - y) <= min(box.height, lastHeight) * 0.5, !lines.isEmpty {
                lines[lines.count - 1] += " " + text
            } else {
                lines.append(text)
            }
            lastMidY = box.midY
            lastHeight = box.height
        }
        return lines.joined(separator: "\n")
    }

    static func ocr(_ input: URL, to out: URL) throws {
        let loaded = try loadImage(input, maxPixelSize: 4096)
        let text = try recognizeText(in: loaded.image)
        if text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            throw ConversionError.emptyContent("在圖片中找不到可辨識的文字。")
        }
        try Data((text + "\n").utf8).write(to: out)
    }
}
