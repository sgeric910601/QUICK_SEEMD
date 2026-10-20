import AVFoundation
import CoreImage
import Foundation
import ImageIO
import UniformTypeIdentifiers

// 影片 → GIF 動畫、影片 → 靜態畫面。

enum MTGifColor: String, CaseIterable, Sendable {
    case high, standard, compact

    var title: String {
        switch self {
        case .high: return "高畫質"
        case .standard: return "標準"
        case .compact: return "精簡"
        }
    }

    var subtitle: String {
        switch self {
        case .high: return "色彩最豐富、漸層最平滑，檔案較大"
        case .standard: return "減少雜訊與色階，畫質與大小平衡"
        case .compact: return "大幅減少色彩，檔案最小，適合圖示與螢幕錄影"
        }
    }

    /// 色階數（nil 代表不處理）
    var posterizeLevels: Double? {
        switch self {
        case .high: return nil
        case .standard: return 24
        case .compact: return 8
        }
    }
}

struct MTGifOptions: Sendable {
    var fps: Int = 10
    /// 0 代表原始寬度
    var width: Int = 480
    var start: Double = 0
    var duration: Double = 5
    var loop = true
    var color: MTGifColor = .standard

    /// 預估影格數
    func frameCount(videoDuration: Double?) -> Int {
        var length = duration
        if let d = videoDuration { length = min(length, max(0, d - min(start, d))) }
        return max(1, Int((length * Double(fps)).rounded(.down)))
    }
}

enum MTFrameMode: String, CaseIterable, Sendable {
    case times, interval, count

    var title: String {
        switch self {
        case .times: return "指定時間點"
        case .interval: return "每隔 N 秒"
        case .count: return "平均擷取 N 張"
        }
    }

    var subtitle: String {
        switch self {
        case .times: return "輸入一或多個時間，例如 0:05, 1:30"
        case .interval: return "從開頭起固定間隔擷取"
        case .count: return "平均分布在整段影片"
        }
    }

    var symbol: String {
        switch self {
        case .times: return "clock"
        case .interval: return "metronome"
        case .count: return "square.grid.3x3"
        }
    }
}

enum MTImageFormat: String, CaseIterable, Sendable {
    case jpg, png

    var title: String { rawValue.uppercased() }
    var utType: UTType { self == .jpg ? .jpeg : .png }
}

struct MTFrameOptions: Sendable {
    var mode: MTFrameMode = .count
    var timesText: String = "0:01"
    var interval: Double = 5
    var count: Int = 10
    var format: MTImageFormat = .jpg
    /// 最長邊上限；0 代表原始大小
    var maxSide: Int = 0

    static let maxFrames = 600

    /// 解析「0:05, 1:30 2:00」；格式錯誤時回傳 nil。
    static func parseTimes(_ text: String) -> [Double]? {
        let separators = CharacterSet(charactersIn: ",，、;；\n ")
        let parts = text.components(separatedBy: separators).filter { !$0.isEmpty }
        guard !parts.isEmpty else { return nil }
        var result: [Double] = []
        for p in parts {
            guard let t = MTTime.parse(p) else { return nil }
            result.append(t)
        }
        return result
    }

    /// 依影片長度算出要擷取的時間點。
    func times(duration: Double) -> [Double]? {
        let last = max(0, duration - 0.05)
        switch mode {
        case .times:
            guard let list = Self.parseTimes(timesText) else { return nil }
            var seen = Set<Int>()
            return list.map { min($0, last) }.filter { seen.insert(Int(($0 * 100).rounded())).inserted }
        case .interval:
            guard interval > 0 else { return nil }
            return Array(stride(from: 0, through: last, by: interval).prefix(Self.maxFrames))
        case .count:
            let n = max(1, min(count, Self.maxFrames))
            return (0..<n).map { duration * (Double($0) + 0.5) / Double(n) }
        }
    }
}

enum MTFrameOps {
    // MARK: GIF

    static func gif(_ file: URL, options o: MTGifOptions, context: ToolContext) async throws -> [URL] {
        let info = try await MTMediaProbe.info(file)
        try info.requireVideo()
        let start = min(max(0, o.start), max(0, info.duration - 0.1))
        let length = min(o.duration, info.duration - start)
        guard length >= 0.1 else { throw MTMediaError.tooShort(file.lastPathComponent) }
        let fps = Double(max(1, min(30, o.fps)))
        let count = max(1, min(1_800, Int((length * fps).rounded(.down))))

        let asset = AVURLAsset(url: file)
        let generator = AVAssetImageGenerator(asset: asset)
        generator.appliesPreferredTrackTransform = true
        if o.width > 0 { generator.maximumSize = CGSize(width: o.width, height: o.width * 4) }
        let tolerance = CMTime(seconds: 0.5 / fps, preferredTimescale: 600)
        generator.requestedTimeToleranceBefore = tolerance
        generator.requestedTimeToleranceAfter = tolerance
        let ci = CIContext(options: [.cacheIntermediates: false])
        let posterize = o.color.posterizeLevels.flatMap { levels -> CIFilter? in
            let f = CIFilter(name: "CIColorPosterize")
            f?.setValue(levels, forKey: "inputLevels")
            return f
        }

        return try await MTWork.withWorkDirectory(context) { dir in
            let out = dir.appendingPathComponent("animation.gif")
            guard let dest = CGImageDestinationCreateWithURL(out as CFURL, UTType.gif.identifier as CFString, count, nil) else {
                throw ConversionError.writeFailed("無法建立 GIF。")
            }
            if o.loop {
                CGImageDestinationSetProperties(dest, [kCGImagePropertyGIFDictionary: [kCGImagePropertyGIFLoopCount: 0]] as CFDictionary)
            }
            let frameProps = [kCGImagePropertyGIFDictionary: [kCGImagePropertyGIFDelayTime: 1 / fps,
                                                              kCGImagePropertyGIFUnclampedDelayTime: 1 / fps]] as CFDictionary
            var added = 0
            for i in 0..<count {
                try context.checkCancellation()
                let t = start + Double(i) / fps
                var image: CGImage
                do { image = try await generator.image(at: MTTime.cm(t)).image } catch {
                    if i == 0 { throw ConversionError.unreadable("無法擷取影片畫面（\(error.localizedDescription)）。") }
                    break
                }
                if let posterize {
                    posterize.setValue(CIImage(cgImage: image), forKey: kCIInputImageKey)
                    if let output = posterize.outputImage, let rendered = ci.createCGImage(output, from: output.extent) { image = rendered }
                }
                CGImageDestinationAddImage(dest, image, frameProps)
                added += 1
                context.progress(Double(i + 1) / Double(count) * 0.9, "製作 GIF：第 \(i + 1)／\(count) 格")
            }
            context.progress(0.92, "壓縮 GIF…")
            guard added > 0, CGImageDestinationFinalize(dest) else { throw ConversionError.writeFailed("GIF 編碼失敗。") }
            let summary = "GIF 動畫：\(added) 格、\(Int(fps)) fps、長 \(String(format: "%.1f", Double(added) / fps)) 秒" + (o.loop ? "、循環播放" : "")
            return [try MTWork.deliver(context, out, as: context.outputName(for: file, ext: "gif"), for: file, summary: summary)]
        }
    }

    // MARK: 擷取畫面

    static func frames(_ file: URL, options o: MTFrameOptions, context: ToolContext) async throws -> [URL] {
        let info = try await MTMediaProbe.info(file)
        try info.requireVideo()
        guard let times = o.times(duration: info.duration), !times.isEmpty else {
            throw ConversionError.other("時間點格式不正確，請用逗號分隔，例如 0:05, 1:30。")
        }
        let asset = AVURLAsset(url: file)
        let generator = AVAssetImageGenerator(asset: asset)
        generator.appliesPreferredTrackTransform = true
        generator.requestedTimeToleranceBefore = .zero
        generator.requestedTimeToleranceAfter = .zero
        if o.maxSide > 0 { generator.maximumSize = CGSize(width: o.maxSide, height: o.maxSide) }
        let base = CVFileNaming.sanitize(CVFileNaming.baseName(of: file))

        return try await MTWork.withWorkDirectory(context) { dir in
            let folder = dir.appendingPathComponent("frames", isDirectory: true)
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            var written: [URL] = []
            for (i, t) in times.enumerated() {
                try context.checkCancellation()
                context.progress(Double(i) / Double(times.count) * 0.95, "擷取畫面：\(i + 1)／\(times.count)（\(MTTime.format(t))）")
                let image: CGImage
                do { image = try await generator.image(at: MTTime.cm(t)).image } catch {
                    throw ConversionError.unreadable("無法擷取 \(MTTime.format(t)) 的畫面（\(error.localizedDescription)）。")
                }
                let url = folder.appendingPathComponent("\(base)-\(MTTime.fileLabel(t)).\(o.format.rawValue)")
                try write(image, to: url, format: o.format)
                written.append(url)
            }
            if written.count == 1 {
                return [try MTWork.deliver(context, written[0], as: written[0].lastPathComponent, for: file,
                                           summary: "已擷取 \(MTTime.format(times[0])) 的畫面")]
            }
            return [try MTWork.deliver(context, folder, as: "\(base)-畫面", for: file,
                                       summary: "已擷取 \(written.count) 張畫面，存在「\(base)-畫面」資料夾")]
        }
    }

    static func write(_ image: CGImage, to url: URL, format: MTImageFormat) throws {
        guard let dest = CGImageDestinationCreateWithURL(url as CFURL, format.utType.identifier as CFString, 1, nil) else {
            throw ConversionError.writeFailed(url.lastPathComponent)
        }
        let props: [CFString: Any] = format == .jpg ? [kCGImageDestinationLossyCompressionQuality: 0.92] : [:]
        CGImageDestinationAddImage(dest, image, props as CFDictionary)
        guard CGImageDestinationFinalize(dest) else { throw ConversionError.writeFailed(url.lastPathComponent) }
    }
}
