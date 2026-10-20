import AVFoundation
import CoreMedia
import Foundation
import VideoToolbox

// 各影片工具的背景工作（不碰介面，可在任何執行緒執行）。

// MARK: - 選項型別

enum MTVideoFormat: String, CaseIterable, Sendable {
    case mp4, mp4hevc, mov, m4v, prores

    var title: String {
        switch self {
        case .mp4: return "MP4 (H.264)"
        case .mp4hevc: return "MP4 (HEVC)"
        case .mov: return "MOV"
        case .m4v: return "M4V"
        case .prores: return "ProRes 422"
        }
    }

    var subtitle: String {
        switch self {
        case .mp4: return "最通用：網頁、Windows、手機、社群平台都能播放。"
        case .mp4hevc: return "畫質相同時檔案約小 40%；較舊的裝置或 Windows 可能無法播放。"
        case .mov: return "QuickTime 影片（H.264），適合 Mac 與 iPhone 剪輯。"
        case .m4v: return "Apple 裝置用的 MP4 變體（H.264），適合 Apple TV、iTunes。"
        case .prores: return "專業剪輯用的高畫質格式，檔案非常大，適合交給剪輯軟體。"
        }
    }

    var codec: MTVideoCodec {
        switch self {
        case .mp4, .mov, .m4v: return .h264
        case .mp4hevc: return .hevc
        case .prores: return .prores422
        }
    }

    var fileType: AVFileType {
        switch self {
        case .mp4, .mp4hevc: return .mp4
        case .mov, .prores: return .mov
        case .m4v: return .m4v
        }
    }

    var ext: String {
        switch self {
        case .mp4, .mp4hevc: return "mp4"
        case .mov, .prores: return "mov"
        case .m4v: return "m4v"
        }
    }
}

enum MTResolution: Int, CaseIterable, Sendable {
    case original = 0, uhd = 2160, fhd = 1080, hd = 720, sd = 480

    var title: String {
        switch self {
        case .original: return "原始"
        case .uhd: return "4K"
        case .fhd: return "1080p"
        case .hd: return "720p"
        case .sd: return "480p"
        }
    }
}

enum MTCompressLevel: String, CaseIterable, Sendable {
    case strong, balanced, quality

    var title: String {
        switch self {
        case .strong: return "高度壓縮"
        case .balanced: return "建議壓縮"
        case .quality: return "高畫質"
        }
    }

    var subtitle: String {
        switch self {
        case .strong: return "檔案最小，最高 720p，適合傳訊息或 Email"
        case .balanced: return "畫質與大小兼顧，最高 1080p"
        case .quality: return "幾乎看不出差異，保留原始解析度"
        }
    }

    var symbol: String {
        switch self {
        case .strong: return "arrow.down.right.and.arrow.up.left"
        case .balanced: return "checkmark.seal"
        case .quality: return "sparkles"
        }
    }

    var shortSide: Int {
        switch self {
        case .strong: return 720
        case .balanced: return 1080
        case .quality: return 0
        }
    }

    /// H.264 每像素每影格位元數（HEVC 另乘 0.65）
    var bpp: Double {
        switch self {
        case .strong: return 0.045
        case .balanced: return 0.07
        case .quality: return 0.10
        }
    }

    /// 與來源位元率相比的上限比例（確保真的變小）
    var sourceFactor: Double {
        switch self {
        case .strong: return 0.35
        case .balanced: return 0.55
        case .quality: return 0.75
        }
    }

    var audioBitrate: Int {
        switch self {
        case .strong: return 96_000
        case .balanced: return 128_000
        case .quality: return 160_000
        }
    }
}

struct MTCompressOptions: Sendable {
    var level: MTCompressLevel = .balanced
    var codec: MTVideoCodec = .h264
    /// 指定目標大小（MB）；nil 代表依壓縮程度
    var targetMB: Double? = nil
}

struct MTCompressPlan: Sendable {
    var size: CGSize
    var scaled: Bool
    var videoBitrate: Int
    var audioBitrate: Int
    var estimatedBytes: Int64
    var problem: String?
}

enum MTRotateOp: String, CaseIterable, Sendable {
    case right, left, upsideDown, flipH, flipV

    var title: String {
        switch self {
        case .right: return "向右轉 90°"
        case .left: return "向左轉 90°"
        case .upsideDown: return "旋轉 180°"
        case .flipH: return "水平翻轉"
        case .flipV: return "垂直翻轉"
        }
    }

    var subtitle: String {
        switch self {
        case .right: return "順時針"
        case .left: return "逆時針"
        case .upsideDown: return "上下顛倒"
        case .flipH: return "左右鏡像（例如自拍鏡頭）"
        case .flipV: return "上下鏡像"
        }
    }

    var symbol: String {
        switch self {
        case .right: return "rotate.right"
        case .left: return "rotate.left"
        case .upsideDown: return "arrow.up.arrow.down"
        case .flipH: return "arrow.left.and.right"
        case .flipV: return "arrow.up.and.down"
        }
    }

    var transform: CGAffineTransform {
        switch self {
        case .right: return MTGeometry.rotateRight
        case .left: return MTGeometry.rotateLeft
        case .upsideDown: return MTGeometry.rotate180
        case .flipH: return MTGeometry.flipHorizontal
        case .flipV: return MTGeometry.flipVertical
        }
    }

    var isFlip: Bool { self == .flipH || self == .flipV }
    var tag: String { isFlip ? "已翻轉" : "已旋轉" }
}

enum MTRotateMethod: String, CaseIterable, Sendable {
    case fast, reencode
}

enum MTAspect: String, CaseIterable, Sendable {
    case original, wide, vertical, square, portrait

    var title: String {
        switch self {
        case .original: return "原始比例"
        case .wide: return "16:9"
        case .vertical: return "9:16"
        case .square: return "1:1"
        case .portrait: return "4:5"
        }
    }

    var subtitle: String {
        switch self {
        case .original: return "只改變大小"
        case .wide: return "橫式（YouTube）"
        case .vertical: return "直式（Reels、Shorts、TikTok）"
        case .square: return "方形"
        case .portrait: return "直式貼文（Instagram）"
        }
    }

    /// 寬 / 高；nil 代表依原始比例
    var ratio: Double? {
        switch self {
        case .original: return nil
        case .wide: return 16.0 / 9
        case .vertical: return 9.0 / 16
        case .square: return 1
        case .portrait: return 4.0 / 5
        }
    }
}

enum MTFitMode: String, CaseIterable, Sendable {
    case letterbox, crop
}

enum MTMergeSize: String, CaseIterable, Sendable {
    case first, fhd, hd

    var title: String {
        switch self {
        case .first: return "依第一個影片"
        case .fhd: return "1080p"
        case .hd: return "720p"
        }
    }

    var subtitle: String {
        switch self {
        case .first: return "使用第一個影片的解析度與方向"
        case .fhd: return "短邊 1080 像素，方向依第一個影片"
        case .hd: return "短邊 720 像素，檔案較小"
        }
    }
}

// MARK: - 工作

enum MTVideoOps {
    // MARK: 共用

    /// 建立重新編碼設定。render 與 layerTransform 都是 nil 時不重新算圖（保留原本的方向標記）。
    static func spec(asset: AVAsset, info: MTMediaInfo, codec: MTVideoCodec, bitrate: Int? = nil, render: CGSize? = nil,
                     layerTransform: CGAffineTransform? = nil, fill: Bool = false, keepAudio: Bool = true,
                     audioBitrate: Int? = nil, fileType: AVFileType, frameRate: Double? = nil, forceComposition: Bool = false) async throws -> MTTranscodeSpec {
        guard let track = try await asset.loadTracks(withMediaType: .video).first else { throw ConversionError.noVideoTrack }
        let duration = try await asset.load(.duration)
        let (natural, preferred) = try await track.load(.naturalSize, .preferredTransform)
        var spec = MTTranscodeSpec(asset: asset, duration: duration.seconds)
        spec.videoTracks = [track]
        spec.codec = codec
        spec.frameRate = frameRate ?? info.frameRate
        let display = MTGeometry.displaySize(natural, preferred)
        let oddSize = Int(natural.width.rounded()) % 2 != 0 || Int(natural.height.rounded()) % 2 != 0
        if render != nil || layerTransform != nil || forceComposition || oddSize {
            let size = MTGeometry.evenSize(render ?? display)
            let t = layerTransform ?? MTGeometry.fit(natural: natural, preferred: preferred, into: size, fill: fill)
            spec.videoComposition = MTTranscoder.composition(track: track, duration: duration, renderSize: size, transform: t,
                                                             frameRate: spec.frameRate)
            spec.width = Int(size.width)
            spec.height = Int(size.height)
        } else {
            spec.width = Int(natural.width.rounded())
            spec.height = Int(natural.height.rounded())
            spec.transform = preferred
        }
        let outSize = CGSize(width: spec.width, height: spec.height)
        spec.bitrate = bitrate ?? MTBitrate.preserving(info, codec: codec, output: outSize, frameRate: spec.frameRate)
        spec.fileType = fileType
        if keepAudio, let audio = try await asset.loadTracks(withMediaType: .audio).first {
            spec.audioTracks = [audio]
            spec.channels = max(1, min(2, info.channels))
            if codec == .prores422 {
                spec.audio = .pcm(bitDepth: info.bitDepth > 16 ? 24 : 16, bigEndian: false)
                spec.sampleRate = info.sampleRate
            } else {
                spec.audio = .aac(bitrate: audioBitrate ?? MTBitrate.aacMatching(info))
                spec.sampleRate = MTBitrate.aacSampleRate(info.sampleRate)
            }
        }
        return spec
    }

    static func fullRange(_ asset: AVAsset) async throws -> CMTimeRange {
        CMTimeRange(start: .zero, duration: try await asset.load(.duration))
    }

    /// 轉封裝（不重新編碼）；不支援時回傳 false。
    static func tryPassthrough(_ asset: AVAsset, fileType: AVFileType, timeRange: CMTimeRange? = nil, metadataFrom source: AVAsset,
                               to out: URL, context: ToolContext, message: String) async throws -> Bool {
        context.progress(0, message)
        do {
            try await MTExport.passthrough(asset, fileType: fileType, timeRange: timeRange, metadata: await MTExport.metadata(of: source),
                                           to: out, progress: MTWork.progress(context, to: 0.98, message))
            return true
        } catch is MTPassthroughUnavailable {
            return false
        }
    }

    static func codecMatches(_ source: MTVideoCodecKind, _ target: MTVideoCodec) -> Bool {
        switch (source, target) {
        case (.h264, .h264), (.hevc, .hevc), (.prores, .prores422): return true
        default: return false
        }
    }

    // MARK: 轉換格式

    static func convert(_ file: URL, format: MTVideoFormat, resolution: MTResolution, keepAudio: Bool, allowRemux: Bool,
                        context: ToolContext) async throws -> [URL] {
        let info: MTMediaInfo
        do {
            info = try await MTMediaProbe.info(file)
        } catch {
            // macOS 讀不懂（MKV、WebM…）但使用者有安裝 ffmpeg 時，交給 ffmpeg
            if CVExternalTools.isAvailable(.ffmpeg) { return try await convertWithFFmpeg(file, format: format, resolution: resolution, context: context) }
            throw error
        }
        try info.requireVideo()
        return try await MTWork.withWorkDirectory(context) { dir in
            let asset = AVURLAsset(url: file)
            let out = dir.appendingPathComponent("converted.\(format.ext)")
            let scaled = MTGeometry.capped(info.displaySize, shortSide: resolution.rawValue)
            var done = false
            if allowRemux, scaled == nil, codecMatches(info.videoCodec, format.codec) {
                let src = try await MTTranscoder.compose(asset, segments: [fullRange(asset)], audio: keepAudio).composition
                done = try await tryPassthrough(src, fileType: format.fileType, metadataFrom: asset, to: out, context: context,
                                                message: "快速轉換（不重新編碼）…")
            }
            if !done {
                let message = "重新編碼成 \(format.title)…"
                context.progress(0, message)
                var spec = try await spec(asset: asset, info: info, codec: format.codec, render: scaled, keepAudio: keepAudio,
                                          fileType: format.fileType)
                spec.metadata = await MTExport.metadata(of: asset)
                try await MTTranscoder.run(spec, to: out, progress: MTWork.progress(context, to: 0.98, message))
            }
            let tag = scaled != nil ? resolution.title : (keepAudio || !info.hasAudio ? nil : "無聲")
            let dims = scaled ?? MTGeometry.evenSize(info.displaySize)
            let summary = done ? "已轉成 \(format.title)（直接轉封裝，畫質無損）"
                : "已轉成 \(format.title)，\(Int(dims.width))×\(Int(dims.height))" + (keepAudio || !info.hasAudio ? "" : "、無聲")
            return [try MTWork.deliver(context, out, as: MTWork.outputName(context, for: file, tag: tag, ext: format.ext), for: file, summary: summary)]
        }
    }

    private static func convertWithFFmpeg(_ file: URL, format: MTVideoFormat, resolution: MTResolution, context: ToolContext) async throws -> [URL] {
        guard let ffmpeg = CVExternalTools.executable(for: .ffmpeg) else { throw MTMediaError.unreadable(file.lastPathComponent) }
        return try await MTWork.withWorkDirectory(context) { dir in
            let out = dir.appendingPathComponent("converted.\(format.ext)")
            var options = ConversionOptions()
            options.videoSize = ConversionVideoSize(rawValue: resolution.rawValue) ?? .original
            let id: String
            switch format {
            case .mp4: id = "mp4"
            case .mp4hevc: id = "mp4-hevc"
            case .mov: id = "mov"
            case .m4v: id = "m4v"
            case .prores: id = "prores"
            }
            context.progress(0, "使用 ffmpeg 轉換…")
            let args = CVExternalTools.ffmpegArguments(input: file.path, output: out.path, formatID: id, options: options)
            try await CVProcess.runChecked(ffmpeg.path, args, toolName: "ffmpeg")
            return [try MTWork.deliver(context, out, as: MTWork.outputName(context, for: file, tag: nil, ext: format.ext), for: file,
                                       summary: "已用 ffmpeg 轉成 \(format.title)")]
        }
    }

    // MARK: 壓縮

    static func compressPlan(_ info: MTMediaInfo, _ options: MTCompressOptions) -> MTCompressPlan {
        let display = info.displaySize
        let duration = max(info.duration, 0.1)
        let fps = MTBitrate.effectiveFPS(info.frameRate)
        let hevc = options.codec == .hevc
        let codecFactor = hevc ? 0.65 : 1.0
        func pixels(_ s: CGSize) -> Double { Double(s.width * s.height) }

        if let mb = options.targetMB, mb > 0 {
            let total = mb * 1_000_000 * 8 * 0.97 / duration
            let audio = info.hasAudio ? (total < 600_000 ? 64_000 : total < 1_500_000 ? 96_000 : 128_000) : 0
            let video = total - Double(audio)
            let minimumVideo = 100_000.0
            if video < minimumVideo {
                let need = (minimumVideo + Double(info.hasAudio ? 64_000 : 0)) * duration / 8 / 1_000_000 / 0.97
                return MTCompressPlan(size: display, scaled: false, videoBitrate: Int(minimumVideo), audioBitrate: audio, estimatedBytes: 0,
                                      problem: String(format: "目標太小：這段影片至少需要約 %.1f MB。", need))
            }
            // 依位元率挑選能維持基本畫質的最大解析度
            let short = Int(min(display.width, display.height).rounded())
            var size = display
            var scaled = false
            for cap in [short, 1080, 720, 540, 480, 360] where cap <= short {
                let candidate = MTGeometry.capped(display, shortSide: cap) ?? display
                size = candidate
                scaled = cap < short
                if video / (pixels(candidate) * fps) >= 0.035 * codecFactor { break }
            }
            let bytes = Int64((video + Double(audio)) * duration / 8 * 1.01)
            var problem: String?
            if Double(info.fileSize) > 0 && Double(bytes) >= Double(info.fileSize) * 0.98 {
                problem = "原檔已經小於目標大小，不需要壓縮。"
            }
            return MTCompressPlan(size: size, scaled: scaled, videoBitrate: Int(video), audioBitrate: audio, estimatedBytes: bytes, problem: problem)
        }

        let level = options.level
        let capped = level.shortSide > 0 ? MTGeometry.capped(display, shortSide: level.shortSide) : nil
        let size = capped ?? display
        let px = pixels(size) * fps
        // 依畫質需求算出位元率（不低於基本畫質），再確保一定比來源低
        var video = max(px * level.bpp * codecFactor, px * 0.02 * codecFactor, 150_000)
        if info.videoBitrate > 0, pixels(display) > 0 {
            let scale = min(1, pixels(size) / pixels(display))
            video = min(video, info.videoBitrate * scale * level.sourceFactor * (hevc && info.videoCodec != .hevc ? 0.8 : 1))
        }
        video = max(video, 80_000)
        var audio = 0
        if info.hasAudio {
            audio = level.audioBitrate
            if info.audioFormat == kAudioFormatMPEG4AAC, info.audioBitrate > 0 { audio = min(audio, max(64_000, Int(info.audioBitrate))) }
        }
        let bytes = Int64((video + Double(audio)) * duration / 8 * 1.01)
        var problem: String?
        if info.fileSize > 0 && Double(bytes) >= Double(info.fileSize) * 0.95 {
            problem = "這個影片已經很精簡，可能無法再變小。"
        }
        return MTCompressPlan(size: size, scaled: capped != nil, videoBitrate: Int(video), audioBitrate: audio, estimatedBytes: bytes, problem: problem)
    }

    /// 壓縮一個影片；壓完沒有變小時回傳 nil（保留原檔）。
    static func compress(_ file: URL, options: MTCompressOptions, context: ToolContext) async throws -> URL? {
        let info = try await MTMediaProbe.info(file)
        try info.requireVideo()
        let plan = compressPlan(info, options)
        if let problem = plan.problem, plan.estimatedBytes == 0 { throw ConversionError.other("「\(file.lastPathComponent)」\(problem)") }
        if options.targetMB != nil, plan.problem != nil {
            context.progress(1, "「\(file.lastPathComponent)」已經小於目標大小，保留原檔。")
            return nil
        }
        return try await MTWork.withWorkDirectory(context) { dir in
            let asset = AVURLAsset(url: file)
            let container = MTTranscoder.container(for: file)
            let out = dir.appendingPathComponent("compressed.\(container.ext)")
            let message = "壓縮中（\(options.codec.title)，\(Int(plan.size.width))×\(Int(plan.size.height))）…"
            context.progress(0, message)
            var spec = try await spec(asset: asset, info: info, codec: options.codec, bitrate: plan.videoBitrate,
                                      render: plan.scaled ? plan.size : nil, keepAudio: plan.audioBitrate > 0,
                                      audioBitrate: plan.audioBitrate, fileType: container.type)
            spec.metadata = await MTExport.metadata(of: asset)
            // 限制每秒的最大資料量，讓複雜畫面也不會大幅超出預估（指定大小時更嚴格）
            let peak = Double(plan.videoBitrate) / 8 * (options.targetMB != nil ? 1.1 : 1.5)
            spec.compressionExtras = [kVTCompressionPropertyKey_DataRateLimits as String: [peak, 1.0]]
            try await MTTranscoder.run(spec, to: out, progress: MTWork.progress(context, to: 0.97, message))
            // 至少要小 5% 才值得（否則只是多一次有損編碼）
            let newSize = ToolFiles.size(of: out)
            guard newSize > 0, Double(newSize) < Double(info.fileSize) * 0.95 else {
                context.progress(1, "「\(file.lastPathComponent)」壓縮後沒有明顯變小，保留原檔。")
                return nil
            }
            let saved = Int((1 - Double(newSize) / Double(max(1, info.fileSize))) * 100)
            let url = try context.deliver(out, as: context.outputName(for: file, tag: "已壓縮", ext: container.ext), for: file)
            context.progress(1, "從 \(ToolFiles.formattedSize(info.fileSize)) 縮小到 \(ToolFiles.formattedSize(newSize))，省下 \(saved)%")
            return url
        }
    }

    // MARK: 剪輯

    /// keep = true：保留 start…end；false：刪除 start…end，保留前後。
    static func trim(_ file: URL, start: Double, end: Double?, keep: Bool, precise: Bool, context: ToolContext) async throws -> [URL] {
        let info = try await MTMediaProbe.info(file)
        try info.requireVideo()
        let name = file.lastPathComponent
        let s = max(0, min(start, info.duration))
        let e = min(end ?? info.duration, info.duration)
        guard e - s >= 0.1 else { throw MTMediaError.tooShort(name) }
        if !keep && s <= 0.05 && e >= info.duration - 0.05 { throw ConversionError.other("「\(name)」不能刪除整段影片。") }
        let asset = AVURLAsset(url: file)
        let container = MTTranscoder.container(for: file)
        return try await MTWork.withWorkDirectory(context) { dir in
            let out = dir.appendingPathComponent("trimmed.\(container.ext)")
            var done = false
            var actualStart = s
            if !precise {
                // 不重新編碼：起點對齊到最近的關鍵影格
                guard let track = try await asset.loadTracks(withMediaType: .video).first else { throw ConversionError.noVideoTrack }
                if keep {
                    let ks = await MTMediaProbe.keyframe(atOrBefore: s, in: track)
                    actualStart = ks
                    let src = try await MTTranscoder.compose(asset, segments: [MTTime.range(ks, e)]).composition
                    done = try await tryPassthrough(src, fileType: container.type, metadataFrom: asset, to: out, context: context,
                                                    message: "快速剪輯（不重新編碼）…")
                } else {
                    let ke = await MTMediaProbe.keyframe(atOrBefore: e, in: track)
                    var segments: [CMTimeRange] = []
                    if s > 0.05 { segments.append(MTTime.range(0, s)) }
                    if ke < info.duration - 0.05 { segments.append(MTTime.range(ke, info.duration)) }
                    let src = try await MTTranscoder.compose(asset, segments: segments).composition
                    done = try await tryPassthrough(src, fileType: container.type, metadataFrom: asset, to: out, context: context,
                                                    message: "快速剪輯（不重新編碼）…")
                }
            }
            if !done {
                var segments: [CMTimeRange] = []
                if keep { segments = [MTTime.range(s, e)] } else {
                    if s > 0.01 { segments.append(MTTime.range(0, s)) }
                    if e < info.duration - 0.01 { segments.append(MTTime.range(e, info.duration)) }
                }
                let src = try await MTTranscoder.compose(asset, segments: segments).composition
                let message = "精確剪輯（重新編碼）…"
                context.progress(0, message)
                var spec = try await spec(asset: src, info: info, codec: MTVideoCodec.matching(info.videoCodec), fileType: container.type)
                spec.metadata = await MTExport.metadata(of: asset)
                try await MTTranscoder.run(spec, to: out, progress: MTWork.progress(context, to: 0.98, message))
            }
            if !done { actualStart = s }
            let summary = keep
                ? "已剪下 \(MTTime.format(actualStart))–\(MTTime.format(e))，長度 \(MTTime.format(e - actualStart))"
                    + (actualStart < s - 0.01 ? "（起點對齊關鍵影格）" : "")
                : "已刪除 \(MTTime.format(s))–\(MTTime.format(e))，剩下約 \(MTTime.format(info.duration - (e - s)))"
            return [try MTWork.deliver(context, out, as: context.outputName(for: file, tag: "剪輯", ext: container.ext), for: file, summary: summary)]
        }
    }

    // MARK: 移除聲音

    static func mute(_ file: URL, context: ToolContext) async throws -> [URL] {
        let info = try await MTMediaProbe.info(file)
        try info.requireVideo()
        let asset = AVURLAsset(url: file)
        let container = MTTranscoder.container(for: file)
        return try await MTWork.withWorkDirectory(context) { dir in
            let out = dir.appendingPathComponent("muted.\(container.ext)")
            let src = try await MTTranscoder.compose(asset, segments: [fullRange(asset)], audio: false).composition
            let done = try await tryPassthrough(src, fileType: container.type, metadataFrom: asset, to: out, context: context,
                                                message: "移除聲音（畫質不變）…")
            let summary = done ? "已移除聲音，影像未重新編碼、畫質不變" : "已移除聲音"
            if !done {
                let message = "移除聲音（重新編碼）…"
                let spec = try await spec(asset: asset, info: info, codec: MTVideoCodec.matching(info.videoCodec), keepAudio: false,
                                          fileType: container.type)
                try await MTTranscoder.run(spec, to: out, progress: MTWork.progress(context, to: 0.98, message))
            }
            return [try MTWork.deliver(context, out, as: context.outputName(for: file, tag: "無聲", ext: container.ext), for: file, summary: summary)]
        }
    }

    // MARK: 旋轉／翻轉

    static func rotate(_ file: URL, op: MTRotateOp, method: MTRotateMethod, context: ToolContext) async throws -> [URL] {
        let info = try await MTMediaProbe.info(file)
        try info.requireVideo()
        let asset = AVURLAsset(url: file)
        let container = MTTranscoder.container(for: file)
        guard let track = try await asset.loadTracks(withMediaType: .video).first else { throw ConversionError.noVideoTrack }
        let (natural, preferred) = try await track.load(.naturalSize, .preferredTransform)
        let combined = MTGeometry.normalized(preferred.concatenating(op.transform), natural: natural)
        return try await MTWork.withWorkDirectory(context) { dir in
            let out = dir.appendingPathComponent("rotated.\(container.ext)")
            var done = false
            if method == .fast && !op.isFlip {
                let composed = try await MTTranscoder.compose(asset, segments: [fullRange(asset)])
                composed.video?.preferredTransform = combined
                done = try await tryPassthrough(composed.composition, fileType: container.type, metadataFrom: asset, to: out,
                                                context: context, message: "旋轉中（不重新編碼）…")
            }
            if !done {
                let message = op.isFlip ? "翻轉中（重新編碼）…" : "旋轉中（重新編碼）…"
                context.progress(0, message)
                let render = MTGeometry.evenSize(MTGeometry.displaySize(natural, combined))
                var spec = try await spec(asset: asset, info: info, codec: MTVideoCodec.matching(info.videoCodec), render: render,
                                          layerTransform: combined, fileType: container.type)
                spec.metadata = await MTExport.metadata(of: asset)
                try await MTTranscoder.run(spec, to: out, progress: MTWork.progress(context, to: 0.98, message))
            }
            let summary = "\(op.title)完成" + (done ? "（只改方向標記，未重新編碼）" : "")
            return [try MTWork.deliver(context, out, as: context.outputName(for: file, tag: op.tag, ext: container.ext), for: file, summary: summary)]
        }
    }

    // MARK: 速度

    static func speedTag(_ rate: Double) -> String {
        let text = rate == rate.rounded() ? String(Int(rate)) : String(format: "%g", rate)
        return "\(text)倍速"
    }

    static func speed(_ file: URL, rate: Double, keepPitch: Bool, keepAudio: Bool, context: ToolContext) async throws -> [URL] {
        let info = try await MTMediaProbe.info(file)
        try info.requireVideo()
        guard rate > 0, rate != 1 else { throw ConversionError.other("請選擇 1× 以外的速度。") }
        let asset = AVURLAsset(url: file)
        let container = MTTranscoder.container(for: file)
        return try await MTWork.withWorkDirectory(context) { dir in
            let out = dir.appendingPathComponent("speed.\(container.ext)")
            let full = try await fullRange(asset)
            let composed = try await MTTranscoder.compose(asset, segments: [full], audio: keepAudio)
            let newDuration = CMTimeMultiplyByFloat64(full.duration, multiplier: 1 / rate)
            composed.video?.scaleTimeRange(full, toDuration: newDuration)
            composed.audio?.scaleTimeRange(full, toDuration: newDuration)
            let message = "調整速度為 \(String(format: "%g", rate))×…"
            context.progress(0, message)
            var spec = try await spec(asset: composed.composition, info: info, codec: MTVideoCodec.matching(info.videoCodec),
                                      keepAudio: keepAudio, fileType: container.type, forceComposition: true)
            spec.pitchAlgorithm = keepPitch ? .spectral : .varispeed
            spec.metadata = await MTExport.metadata(of: asset)
            try await MTTranscoder.run(spec, to: out, progress: MTWork.progress(context, to: 0.98, message))
            let summary = "\(String(format: "%g", rate))× 速度：\(MTTime.short(info.duration)) → \(MTTime.short(info.duration / rate))"
            return [try MTWork.deliver(context, out, as: context.outputName(for: file, tag: speedTag(rate), ext: container.ext), for: file,
                                       summary: summary)]
        }
    }

    // MARK: 調整尺寸

    static func resizeTarget(display: CGSize, aspect: MTAspect, resolution: MTResolution) -> CGSize {
        let sourceShort = min(display.width, display.height)
        let short = resolution == .original ? sourceShort : CGFloat(resolution.rawValue)
        guard let ratio = aspect.ratio else {
            if resolution == .original { return MTGeometry.evenSize(display) }
            let scale = short / max(1, sourceShort)
            return MTGeometry.evenSize(CGSize(width: display.width * scale, height: display.height * scale))
        }
        let r = CGFloat(ratio)
        return MTGeometry.evenSize(r >= 1 ? CGSize(width: short * r, height: short) : CGSize(width: short, height: short / r))
    }

    static func resizeTag(aspect: MTAspect, size: CGSize) -> String {
        aspect == .original ? "\(Int(size.width))x\(Int(size.height))"
            : aspect.title.replacingOccurrences(of: ":", with: "x") + "-\(Int(min(size.width, size.height)))p"
    }

    static func resize(_ file: URL, aspect: MTAspect, resolution: MTResolution, fit: MTFitMode, context: ToolContext) async throws -> [URL] {
        let info = try await MTMediaProbe.info(file)
        try info.requireVideo()
        let target = resizeTarget(display: info.displaySize, aspect: aspect, resolution: resolution)
        let asset = AVURLAsset(url: file)
        let container = MTTranscoder.container(for: file)
        return try await MTWork.withWorkDirectory(context) { dir in
            let out = dir.appendingPathComponent("resized.\(container.ext)")
            let message = "調整尺寸為 \(Int(target.width))×\(Int(target.height))…"
            context.progress(0, message)
            var spec = try await spec(asset: asset, info: info, codec: MTVideoCodec.matching(info.videoCodec), render: target,
                                      fill: fit == .crop, fileType: container.type)
            spec.metadata = await MTExport.metadata(of: asset)
            try await MTTranscoder.run(spec, to: out, progress: MTWork.progress(context, to: 0.98, message))
            return [try MTWork.deliver(context, out, as: context.outputName(for: file, tag: resizeTag(aspect: aspect, size: target), ext: container.ext),
                                       for: file, summary: "已調整為 \(Int(target.width))×\(Int(target.height))")]
        }
    }

    // MARK: 合併

    static func mergeRenderSize(first: CGSize, option: MTMergeSize) -> CGSize {
        switch option {
        case .first: return MTGeometry.evenSize(first)
        case .fhd, .hd:
            let short: CGFloat = option == .fhd ? 1080 : 720
            let s = min(first.width, first.height)
            guard s > 0 else { return CGSize(width: 1920, height: 1080) }
            let scale = short / s
            return MTGeometry.evenSize(CGSize(width: first.width * scale, height: first.height * scale))
        }
    }

    static func merge(_ files: [URL], size option: MTMergeSize, codec: MTVideoCodec, context: ToolContext) async throws -> [URL] {
        guard files.count >= 2 else { throw ConversionError.other("至少需要 2 個影片才能合併。") }
        var infos: [MTMediaInfo] = []
        for (i, f) in files.enumerated() {
            context.progress(0.01 * Double(i) / Double(files.count), "讀取影片資訊…")
            let info = try await MTMediaProbe.info(f)
            do { try info.requireVideo() } catch ConversionError.noVideoTrack {
                throw ConversionError.other("「\(f.lastPathComponent)」沒有影像軌，無法合併。")
            }
            infos.append(info)
        }
        let render = mergeRenderSize(first: infos[0].displaySize, option: option)
        let fps = min(60, infos.map(\.frameRate).max() ?? 30)

        let composition = AVMutableComposition()
        let videoTracks = [kCMPersistentTrackID_Invalid, kCMPersistentTrackID_Invalid].compactMap {
            composition.addMutableTrack(withMediaType: .video, preferredTrackID: $0)
        }
        guard videoTracks.count == 2 else { throw ConversionError.other("無法建立合併用的影片軌。") }
        var audioTrack: AVMutableCompositionTrack?
        var instructions: [AVMutableVideoCompositionInstruction] = []
        var cursor = CMTime.zero
        for (i, file) in files.enumerated() {
            let asset = AVURLAsset(url: file)
            guard let src = try await asset.loadTracks(withMediaType: .video).first else { throw ConversionError.noVideoTrack }
            let (natural, preferred, srcRange) = try await src.load(.naturalSize, .preferredTransform, .timeRange)
            let duration = try await asset.load(.duration)
            let range = CMTimeRange(start: .zero, duration: duration).intersection(srcRange)
            guard range.duration > .zero else { continue }
            let target = videoTracks[i % 2]
            try target.insertTimeRange(range, of: src, at: cursor)
            if let a = try await asset.loadTracks(withMediaType: .audio).first {
                if audioTrack == nil { audioTrack = composition.addMutableTrack(withMediaType: .audio, preferredTrackID: kCMPersistentTrackID_Invalid) }
                let aRange = try await a.load(.timeRange).intersection(range)
                if aRange.duration > .zero { try audioTrack?.insertTimeRange(aRange, of: a, at: cursor + (aRange.start - range.start)) }
            }
            let instruction = AVMutableVideoCompositionInstruction()
            instruction.timeRange = CMTimeRange(start: cursor, duration: range.duration)
            instruction.backgroundColor = CGColor(gray: 0, alpha: 1)
            let layer = AVMutableVideoCompositionLayerInstruction(assetTrack: target)
            layer.setTransform(MTGeometry.fit(natural: natural, preferred: preferred, into: render), at: cursor)
            instruction.layerInstructions = [layer]
            instructions.append(instruction)
            cursor = cursor + range.duration
        }
        guard !instructions.isEmpty else { throw ConversionError.emptyContent("影片沒有任何畫面。") }
        let videoComposition = AVMutableVideoComposition()
        videoComposition.renderSize = render
        videoComposition.frameDuration = MTTranscoder.frameDuration(fps)
        videoComposition.instructions = instructions

        var spec = MTTranscodeSpec(asset: composition, duration: cursor.seconds)
        spec.videoTracks = videoTracks
        spec.videoComposition = videoComposition
        spec.codec = codec
        spec.width = Int(render.width)
        spec.height = Int(render.height)
        spec.frameRate = fps
        spec.bitrate = Int(Double(render.width * render.height) * MTBitrate.effectiveFPS(fps) * (codec == .hevc ? 0.06 : 0.09))
        if let audioTrack {
            spec.audioTracks = [audioTrack]
            spec.audio = .aac(bitrate: 192_000)
            spec.sampleRate = MTBitrate.aacSampleRate(infos.filter(\.hasAudio).map(\.sampleRate).max() ?? 44_100)
            spec.channels = 2
        }
        spec.fileType = .mp4
        let first = files[0]
        return try await MTWork.withWorkDirectory(context) { dir in
            let out = dir.appendingPathComponent("merged.mp4")
            let message = "合併 \(files.count) 個影片（\(Int(render.width))×\(Int(render.height))）…"
            context.progress(0.02, message)
            try await MTTranscoder.run(spec, to: out, progress: MTWork.progress(context, from: 0.02, to: 0.98, message))
            let summary = "已合併 \(files.count) 個影片：長度 \(MTTime.short(cursor.seconds))，\(Int(render.width))×\(Int(render.height))"
            return [try MTWork.deliver(context, out, as: context.outputName(for: first, tag: "合併", ext: "mp4"), for: first, summary: summary)]
        }
    }
}
