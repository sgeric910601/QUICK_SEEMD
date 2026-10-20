import AudioToolbox
import AVFoundation
import CoreMedia
import Foundation
import VideoToolbox

// 影音工具的共用引擎：
// - MTMediaProbe：讀取長度、解析度、編碼、位元率等資訊
// - MTTranscoder：AVAssetReader → AVAssetWriter 重新編碼（可控制位元率、解析度、方向、速度、片段）
// - MTExport：AVAssetExportSession 直接轉封裝（不重新編碼）
// 所有輸出都先寫在暫存資料夾，取消或失敗時整個資料夾刪除，不會留下半成品。

// MARK: - 錯誤訊息

enum MTMediaError {
    static func protected(_ name: String) -> ConversionError {
        .other("「\(name)」受 DRM 版權保護（例如從 Apple TV App 購買或租借的影片），無法編輯或轉換。")
    }

    static func unreadable(_ name: String) -> ConversionError {
        .other("macOS 無法讀取「\(name)」的影音格式（例如 MKV、WebM、AVI，或檔案已損毀）。")
    }

    static func undecodable(_ name: String, codec: String) -> ConversionError {
        .other("系統無法解碼「\(name)」使用的 \(codec) 編碼，無法處理這個檔案。")
    }

    static func tooShort(_ name: String) -> ConversionError {
        .other("「\(name)」選取的範圍太短（至少需要 0.1 秒）。")
    }
}

// MARK: - 時間

enum MTTime {
    static let timescale: CMTimeScale = 60_000

    static func cm(_ seconds: Double) -> CMTime { CMTime(seconds: max(0, seconds), preferredTimescale: timescale) }

    static func range(_ start: Double, _ end: Double) -> CMTimeRange {
        CMTimeRange(start: cm(start), end: cm(end))
    }

    /// 65.25 → "1:05.3"；3725 → "1:02:05.0"
    static func format(_ seconds: Double, decimals: Int = 1) -> String {
        let s = max(0, seconds.isFinite ? seconds : 0)
        let factor = pow(10, Double(decimals))
        let rounded = (s * factor).rounded() / factor
        let whole = Int(rounded)
        let h = whole / 3600, m = (whole % 3600) / 60
        let sec = rounded - Double(h * 3600 + m * 60)
        let secText = decimals > 0 ? String(format: "%0\(decimals + 3).\(decimals)f", sec) : String(format: "%02d", Int(sec))
        return h > 0 ? String(format: "%d:%02d:", h, m) + secText : "\(m):" + secText
    }

    /// 短格式：0:15、1:02:05（不含小數）
    static func short(_ seconds: Double) -> String { format(seconds, decimals: 0) }

    /// 解析「75」「75.5」「1:15」「1:15.5」「0:01:15」；不正確時回傳 nil。
    static func parse(_ text: String) -> Double? {
        let t = text.trimmingCharacters(in: .whitespaces)
            .replacingOccurrences(of: "：", with: ":")
            .replacingOccurrences(of: "。", with: ".")
            .replacingOccurrences(of: "s", with: "")
            .replacingOccurrences(of: "秒", with: "")
        if t.isEmpty { return nil }
        let parts = t.split(separator: ":", omittingEmptySubsequences: false).map(String.init)
        guard parts.count <= 3 else { return nil }
        var total = 0.0
        for (i, p) in parts.enumerated() {
            let isLast = i == parts.count - 1
            guard !p.isEmpty, let v = Double(p), v >= 0 else { return nil }
            if !isLast && v != v.rounded() { return nil }
            if i > 0 && v >= 60 { return nil }
            total = total * 60 + v
        }
        return total
    }

    /// 檔名用：65.5 → "1m05.5s"、5 → "0m05s"
    static func fileLabel(_ seconds: Double) -> String {
        let s = max(0, seconds)
        let h = Int(s) / 3600, m = (Int(s) % 3600) / 60
        let sec = s - Double(h * 3600 + m * 60)
        let secText = String(format: "%04.1f", (sec * 10).rounded(.down) / 10)
        return (h > 0 ? "\(h)h" : "") + String(format: "%02dm", m) + secText + "s"
    }
}

// MARK: - 媒體資訊

enum MTVideoCodecKind: Sendable, Equatable {
    case h264, hevc, prores, other(String)

    var title: String {
        switch self {
        case .h264: return "H.264"
        case .hevc: return "HEVC"
        case .prores: return "ProRes"
        case .other(let s): return s
        }
    }
}

struct MTMediaInfo: Sendable {
    var url: URL
    var duration: Double = 0
    var fileSize: Int64 = 0
    var hasVideo = false
    var hasAudio = false
    var naturalSize: CGSize = .zero
    var transform: CGAffineTransform = .identity
    var frameRate: Double = 30
    var videoBitrate: Double = 0
    var videoCodec: MTVideoCodecKind = .other("")
    var videoDecodable = true
    var audioBitrate: Double = 0
    var audioFormat: AudioFormatID = 0
    var sampleRate: Double = 44_100
    var channels: Int = 2
    var bitDepth: Int = 16
    var isProtected = false

    var name: String { url.lastPathComponent }
    var displaySize: CGSize { MTGeometry.displaySize(naturalSize, transform) }
    var resolutionText: String { "\(Int(displaySize.width.rounded()))×\(Int(displaySize.height.rounded()))" }

    var audioCodecTitle: String {
        switch audioFormat {
        case kAudioFormatMPEG4AAC, kAudioFormatMPEG4AAC_HE, kAudioFormatMPEG4AAC_HE_V2: return "AAC"
        case kAudioFormatAppleLossless: return "ALAC"
        case kAudioFormatLinearPCM: return "PCM"
        case kAudioFormatFLAC: return "FLAC"
        case kAudioFormatMPEGLayer3: return "MP3"
        case kAudioFormatAC3, kAudioFormatEnhancedAC3: return "Dolby"
        case kAudioFormatOpus: return "Opus"
        default: return "音訊"
        }
    }

    /// 例如「1920×1080 · 0:15 · H.264 · 24.1 MB」
    var summary: String {
        var parts: [String] = []
        if hasVideo { parts.append(resolutionText) }
        parts.append(MTTime.short(duration))
        if hasVideo { parts.append(videoCodec.title) } else if hasAudio {
            parts.append(audioCodecTitle)
            parts.append(String(format: "%g kHz", (sampleRate / 100).rounded() / 10))
        }
        parts.append(ToolFiles.formattedSize(fileSize))
        return parts.joined(separator: " · ")
    }

    func requireVideo() throws {
        if isProtected { throw MTMediaError.protected(name) }
        guard hasVideo else { throw ConversionError.noVideoTrack }
        if !videoDecodable { throw MTMediaError.undecodable(name, codec: videoCodec.title) }
    }

    func requireAudio() throws {
        if isProtected { throw MTMediaError.protected(name) }
        guard hasAudio else { throw ConversionError.noAudioTrack }
    }
}

enum MTMediaProbe {
    static func info(_ url: URL) async throws -> MTMediaInfo {
        let name = url.lastPathComponent
        guard FileManager.default.fileExists(atPath: url.path) else { throw ConversionError.fileNotFound(name) }
        let asset = AVURLAsset(url: url)
        var info = MTMediaInfo(url: url)
        info.fileSize = ToolFiles.size(of: url)
        let duration: CMTime
        let isProtected: Bool
        let tracks: [AVAssetTrack]
        do {
            (duration, isProtected, tracks) = try await asset.load(.duration, .hasProtectedContent, .tracks)
        } catch {
            throw MTMediaError.unreadable(name)
        }
        info.isProtected = isProtected
        info.duration = duration.seconds.isFinite ? max(0, duration.seconds) : 0
        if let v = tracks.first(where: { $0.mediaType == .video }) {
            info.hasVideo = true
            if let (natural, transform, fps, rate, descs) = try? await v.load(.naturalSize, .preferredTransform, .nominalFrameRate,
                                                                                  .estimatedDataRate, .formatDescriptions) {
                info.naturalSize = natural
                info.transform = transform
                info.frameRate = fps > 1 ? Double(fps) : 30
                info.videoBitrate = Double(rate)
                if let d = descs.first { info.videoCodec = codecKind(CMFormatDescriptionGetMediaSubType(d)) }
            }
            info.videoDecodable = (try? await v.load(.isDecodable)) ?? true
        }
        if let a = tracks.first(where: { $0.mediaType == .audio }) {
            info.hasAudio = true
            if let (rate, descs) = try? await a.load(.estimatedDataRate, .formatDescriptions) {
                info.audioBitrate = Double(rate)
                if let d = descs.first, let asbd = CMAudioFormatDescriptionGetStreamBasicDescription(d)?.pointee {
                    info.audioFormat = asbd.mFormatID
                    if asbd.mSampleRate > 0 { info.sampleRate = asbd.mSampleRate }
                    info.channels = max(1, Int(asbd.mChannelsPerFrame))
                    info.bitDepth = bitDepth(asbd)
                }
            }
        }
        if !info.hasVideo && !info.hasAudio {
            throw isProtected ? MTMediaError.protected(name) : MTMediaError.unreadable(name)
        }
        return info
    }

    static func codecKind(_ code: FourCharCode) -> MTVideoCodecKind {
        switch code {
        case kCMVideoCodecType_H264: return .h264
        case kCMVideoCodecType_HEVC, fourCC("hev1"), kCMVideoCodecType_HEVCWithAlpha: return .hevc
        case kCMVideoCodecType_AppleProRes422, kCMVideoCodecType_AppleProRes422HQ, kCMVideoCodecType_AppleProRes422LT,
             kCMVideoCodecType_AppleProRes422Proxy, kCMVideoCodecType_AppleProRes4444, kCMVideoCodecType_AppleProRes4444XQ:
            return .prores
        case kCMVideoCodecType_JPEG: return .other("Motion JPEG")
        case kCMVideoCodecType_MPEG4Video: return .other("MPEG-4")
        case fourCC("av01"): return .other("AV1")
        case fourCC("vp09"): return .other("VP9")
        default:
            let bytes = [UInt8((code >> 24) & 0xFF), UInt8((code >> 16) & 0xFF), UInt8((code >> 8) & 0xFF), UInt8(code & 0xFF)]
            let s = String(decoding: bytes, as: UTF8.self).trimmingCharacters(in: .whitespaces)
            return .other(s.isEmpty ? "未知" : s.uppercased())
        }
    }

    static func fourCC(_ s: String) -> FourCharCode {
        s.utf8.reduce(0) { ($0 << 8) | FourCharCode($1) }
    }

    /// 來源位元深度（無損輸出用）：> 16 位元一律用 24 位元。
    static func bitDepth(_ asbd: AudioStreamBasicDescription) -> Int {
        switch asbd.mFormatID {
        case kAudioFormatLinearPCM:
            if asbd.mFormatFlags & kAudioFormatFlagIsFloat != 0 { return 24 }
            return asbd.mBitsPerChannel > 16 ? 24 : 16
        case kAudioFormatAppleLossless, kAudioFormatFLAC:
            return asbd.mFormatFlags >= kAppleLosslessFormatFlag_20BitSourceData ? 24 : 16
        default:
            return 16
        }
    }

    /// 某個時間點以前（含）最近的關鍵影格。無法判斷時回傳原時間。
    static func keyframe(atOrBefore seconds: Double, in track: AVAssetTrack) async -> Double {
        guard seconds > 0, (try? await track.load(.canProvideSampleCursors)) == true,
              let cursor = track.makeSampleCursor(presentationTimeStamp: MTTime.cm(seconds)) else { return seconds }
        var steps = 0
        while !cursor.currentSampleSyncInfo.sampleIsFullSync.boolValue && steps < 5000 {
            if cursor.stepInPresentationOrder(byCount: -1) == 0 { break }
            steps += 1
        }
        let t = cursor.presentationTimeStamp.seconds
        guard t.isFinite else { return seconds }
        return min(seconds, max(0, t))
    }
}

// MARK: - 幾何

enum MTGeometry {
    static func displaySize(_ natural: CGSize, _ transform: CGAffineTransform) -> CGSize {
        let r = CGRect(origin: .zero, size: natural).applying(transform)
        return CGSize(width: abs(r.width), height: abs(r.height))
    }

    /// 讓變換後的畫面落在 (0,0) 起點。
    static func normalized(_ t: CGAffineTransform, natural: CGSize) -> CGAffineTransform {
        let r = CGRect(origin: .zero, size: natural).applying(t)
        return t.concatenating(CGAffineTransform(translationX: -r.minX, y: -r.minY))
    }

    /// 把影片（含原本的方向）等比例放進 render 尺寸並置中；fill = true 時裁切填滿。
    static func fit(natural: CGSize, preferred: CGAffineTransform, into render: CGSize, fill: Bool = false) -> CGAffineTransform {
        let upright = normalized(preferred, natural: natural)
        let disp = displaySize(natural, preferred)
        guard disp.width > 0, disp.height > 0 else { return upright }
        let sx = render.width / disp.width, sy = render.height / disp.height
        let scale = fill ? max(sx, sy) : min(sx, sy)
        let w = disp.width * scale, h = disp.height * scale
        return upright
            .concatenating(CGAffineTransform(scaleX: scale, y: scale))
            .concatenating(CGAffineTransform(translationX: (render.width - w) / 2, y: (render.height - h) / 2))
    }

    static func even(_ v: CGFloat) -> Int {
        let i = max(2, Int(v.rounded()))
        return i - i % 2
    }

    static func evenSize(_ s: CGSize) -> CGSize { CGSize(width: even(s.width), height: even(s.height)) }

    /// 短邊上限（不放大）。回傳 nil 代表不需要縮小。
    static func capped(_ size: CGSize, shortSide cap: Int) -> CGSize? {
        let short = min(size.width, size.height)
        guard cap > 0, short > CGFloat(cap) + 1 else { return nil }
        let scale = CGFloat(cap) / short
        return evenSize(CGSize(width: size.width * scale, height: size.height * scale))
    }

    static let rotateRight = CGAffineTransform(a: 0, b: 1, c: -1, d: 0, tx: 0, ty: 0)
    static let rotateLeft = CGAffineTransform(a: 0, b: -1, c: 1, d: 0, tx: 0, ty: 0)
    static let rotate180 = CGAffineTransform(a: -1, b: 0, c: 0, d: -1, tx: 0, ty: 0)
    static let flipHorizontal = CGAffineTransform(a: -1, b: 0, c: 0, d: 1, tx: 0, ty: 0)
    static let flipVertical = CGAffineTransform(a: 1, b: 0, c: 0, d: -1, tx: 0, ty: 0)
}

// MARK: - 位元率

enum MTVideoCodec: String, Sendable, CaseIterable {
    case h264, hevc, prores422

    var avType: AVVideoCodecType {
        switch self {
        case .h264: return .h264
        case .hevc: return .hevc
        case .prores422: return .proRes422
        }
    }

    var title: String {
        switch self {
        case .h264: return "H.264"
        case .hevc: return "HEVC"
        case .prores422: return "ProRes 422"
        }
    }

    /// 與來源相同的編碼（H.264／HEVC 以外一律用 H.264）。
    static func matching(_ kind: MTVideoCodecKind) -> MTVideoCodec { kind == .hevc ? .hevc : .h264 }
}

enum MTBitrate {
    /// 高於 30 fps 的部分只算一半（高影格率的畫面差異較小）。
    static func effectiveFPS(_ fps: Double) -> Double {
        let f = fps > 1 ? fps : 30
        return min(f, 30) + max(0, f - 30) * 0.5
    }

    /// 「盡量保持畫質」的位元率：不超過來源（換算解析度與編碼效率後）太多，也不低於基本畫質。
    static func preserving(_ info: MTMediaInfo, codec: MTVideoCodec, output: CGSize, frameRate: Double? = nil) -> Int {
        let fps = effectiveFPS(frameRate ?? info.frameRate)
        let pixels = Double(output.width * output.height) * fps
        let hq = pixels * (codec == .hevc ? 0.065 : 0.10)
        var rate = hq
        let src = info.displaySize
        if info.videoBitrate > 0, src.width > 0, src.height > 0 {
            var equivalent = info.videoBitrate * min(1, Double(output.width * output.height) / Double(src.width * src.height))
            switch (info.videoCodec, codec) {
            case (.hevc, .h264): equivalent *= 1.4
            case (.h264, .hevc): equivalent *= 0.75
            default: break
            }
            rate = min(hq, equivalent * 1.1)
        }
        return Int(max(rate, pixels * 0.03, 300_000))
    }

    /// AAC 可用的位元率：取不超過要求值的最大可用值。
    static func aac(_ requested: Int, sampleRate: Double, channels: Int) -> Int {
        let allowed = applicableAAC(sampleRate: sampleRate, channels: channels)
        guard !allowed.isEmpty else { return requested }
        return allowed.filter { $0 <= requested }.max() ?? allowed.min() ?? requested
    }

    private static func applicableAAC(sampleRate: Double, channels: Int) -> [Int] {
        let ch = UInt32(max(1, min(2, channels)))
        var src = AudioStreamBasicDescription(mSampleRate: sampleRate, mFormatID: kAudioFormatLinearPCM,
                                              mFormatFlags: kAudioFormatFlagIsFloat | kAudioFormatFlagIsPacked,
                                              mBytesPerPacket: 4 * ch, mFramesPerPacket: 1, mBytesPerFrame: 4 * ch,
                                              mChannelsPerFrame: ch, mBitsPerChannel: 32, mReserved: 0)
        var dst = AudioStreamBasicDescription()
        dst.mFormatID = kAudioFormatMPEG4AAC
        dst.mSampleRate = sampleRate
        dst.mChannelsPerFrame = ch
        var converter: AudioConverterRef?
        guard AudioConverterNew(&src, &dst, &converter) == noErr, let converter else { return [] }
        defer { AudioConverterDispose(converter) }
        var size: UInt32 = 0
        guard AudioConverterGetPropertyInfo(converter, kAudioConverterApplicableEncodeBitRates, &size, nil) == noErr, size > 0 else { return [] }
        var ranges = [AudioValueRange](repeating: AudioValueRange(), count: Int(size) / MemoryLayout<AudioValueRange>.size)
        guard AudioConverterGetProperty(converter, kAudioConverterApplicableEncodeBitRates, &size, &ranges) == noErr else { return [] }
        return ranges.map { Int($0.mMinimum) }.filter { $0 > 0 }
    }

    /// AAC 支援的取樣率（最高 48 kHz）。
    static func aacSampleRate(_ rate: Double) -> Double {
        let valid: [Double] = [8_000, 11_025, 12_000, 16_000, 22_050, 24_000, 32_000, 44_100, 48_000]
        if rate >= 48_000 { return 48_000 }
        return valid.last { $0 <= rate + 1 } ?? 44_100
    }

    /// 常見的 AAC 位元率（依來源推估）。
    static func aacMatching(_ info: MTMediaInfo, fallback: Int = 192_000) -> Int {
        guard info.audioFormat == kAudioFormatMPEG4AAC, info.audioBitrate > 0 else { return fallback }
        let steps = [96_000, 128_000, 160_000, 192_000, 256_000, 320_000]
        return steps.first { Double($0) >= info.audioBitrate * 0.95 } ?? 320_000
    }
}

// MARK: - 重新編碼

enum MTAudioEncoding: Sendable, Equatable {
    case aac(bitrate: Int)
    case alac(bitDepth: Int)
    case pcm(bitDepth: Int, bigEndian: Bool)
}

/// 一次重新編碼的完整設定（在同一個非同步函式內建立與使用）。
struct MTTranscodeSpec {
    var asset: AVAsset
    var videoTracks: [AVAssetTrack] = []
    var audioTracks: [AVAssetTrack] = []
    var videoComposition: AVVideoComposition?
    var audioMix: AVAudioMix?
    var pitchAlgorithm: AVAudioTimePitchAlgorithm = .spectral
    var timeRange: CMTimeRange?
    /// 輸出長度（秒），用於進度。
    var duration: Double
    // 影像
    var codec: MTVideoCodec = .h264
    var bitrate: Int = 5_000_000
    /// 額外的 VideoToolbox 壓縮屬性（實驗與進階設定用）
    var compressionExtras: [String: Any] = [:]
    var width: Int = 0
    var height: Int = 0
    var transform: CGAffineTransform = .identity
    var frameRate: Double = 30
    // 聲音
    var audio: MTAudioEncoding?
    var sampleRate: Double = 44_100
    var channels: Int = 2
    var fileType: AVFileType = .mp4
    var metadata: [AVMetadataItem] = []
}

enum MTTranscoder {
    /// 依 spec 重新編碼到 `out`。取消時中止讀寫並刪除輸出檔。
    /// 若編碼器在開始時拒絕中繼資料或額外的壓縮屬性，會自動不帶這些設定再試一次。
    /// AVFoundation 偶爾會在開頭卡住（解碼器不回應、也不報錯）；看門狗偵測到後會自動重試一次。
    static func run(_ spec: MTTranscodeSpec, to out: URL, progress: @escaping @Sendable (Double) -> Void) async throws {
        do {
            try await runOnce(spec, to: out, progress: progress)
        } catch is MTPump.Stall {
            try? FileManager.default.removeItem(at: out)
            do {
                try await runOnce(spec, to: out, progress: progress)
            } catch is MTPump.Stall {
                try? FileManager.default.removeItem(at: out)
                throw ConversionError.other("系統的影音解碼器沒有回應，已停止處理。請再試一次；若持續發生，請重新啟動 Mac。")
            }
        }
    }

    private static func runOnce(_ spec: MTTranscodeSpec, to out: URL, progress: @escaping @Sendable (Double) -> Void) async throws {
        var first = spec
        first.metadata = safeMetadata(spec.metadata)
        do {
            try await attempt(first, to: out, progress: progress)
        } catch let failure as MTStartFailure {
            try? FileManager.default.removeItem(at: out)
            guard !first.metadata.isEmpty || !first.compressionExtras.isEmpty else {
                throw ConversionError.writeFailed(failure.message)
            }
            var plain = spec
            plain.metadata = []
            plain.compressionExtras = [:]
            do {
                try await attempt(plain, to: out, progress: progress)
            } catch let again as MTStartFailure {
                throw ConversionError.writeFailed(again.message)
            }
        }
    }

    /// 只保留常見、各容器都能寫入的中繼資料（拍攝日期、地點、裝置）。
    static func safeMetadata(_ items: [AVMetadataItem]) -> [AVMetadataItem] {
        let keep: Set<AVMetadataIdentifier> = [
            .commonIdentifierCreationDate, .quickTimeMetadataCreationDate, .quickTimeMetadataLocationISO6709,
            .commonIdentifierMake, .commonIdentifierModel, .quickTimeMetadataMake, .quickTimeMetadataModel,
        ]
        return items.filter { $0.identifier.map(keep.contains) ?? false }
    }

    /// 每條軌各用一個 AVAssetReader：共用一個 reader 時，若寫入端暫停接收聲音（等影像編碼器追上），
    /// reader 會因聲音緩衝區滿而停止解碼影像，兩邊互等造成卡死。
    private static func makeReader(_ spec: MTTranscodeSpec) throws -> AVAssetReader {
        let reader: AVAssetReader
        do { reader = try AVAssetReader(asset: spec.asset) } catch {
            throw ConversionError.unreadable(error.localizedDescription)
        }
        if let range = spec.timeRange { reader.timeRange = range }
        return reader
    }

    private static func attempt(_ spec: MTTranscodeSpec, to out: URL, progress: @escaping @Sendable (Double) -> Void) async throws {
        let writer: AVAssetWriter
        do { writer = try AVAssetWriter(outputURL: out, fileType: spec.fileType) } catch {
            throw ConversionError.writeFailed(error.localizedDescription)
        }
        writer.shouldOptimizeForNetworkUse = spec.fileType == .mp4 || spec.fileType == .m4v
        if !spec.metadata.isEmpty { writer.metadata = spec.metadata }

        var pairs: [MTPumpPair] = []
        if !spec.videoTracks.isEmpty {
            let pixelFormat = spec.codec == .prores422 ? kCVPixelFormatType_32BGRA : kCVPixelFormatType_420YpCbCr8BiPlanarVideoRange
            let readerSettings: [String: Any] = [kCVPixelBufferPixelFormatTypeKey as String: pixelFormat]
            let output: AVAssetReaderOutput
            if let composition = spec.videoComposition {
                let o = AVAssetReaderVideoCompositionOutput(videoTracks: spec.videoTracks, videoSettings: readerSettings)
                o.videoComposition = composition
                output = o
            } else {
                output = AVAssetReaderTrackOutput(track: spec.videoTracks[0], outputSettings: readerSettings)
            }
            output.alwaysCopiesSampleData = false
            let reader = try makeReader(spec)
            guard reader.canAdd(output) else { throw ConversionError.unreadable("無法解碼這個影片的影像。") }
            reader.add(output)
            let settings = try videoSettings(spec, writer: writer)
            let input = AVAssetWriterInput(mediaType: .video, outputSettings: settings)
            input.expectsMediaDataInRealTime = false
            input.transform = spec.transform
            guard writer.canAdd(input) else { throw ConversionError.writeFailed("無法建立影片編碼器。") }
            writer.add(input)
            pairs.append(MTPumpPair(reader: reader, output: output, input: input, reportsProgress: true, label: "video"))
        }
        if let audio = spec.audio, !spec.audioTracks.isEmpty {
            let output = AVAssetReaderAudioMixOutput(audioTracks: spec.audioTracks, audioSettings: [
                AVFormatIDKey: kAudioFormatLinearPCM,
                AVSampleRateKey: spec.sampleRate,
                AVNumberOfChannelsKey: spec.channels,
                AVLinearPCMBitDepthKey: 32,
                AVLinearPCMIsFloatKey: true,
                AVLinearPCMIsBigEndianKey: false,
                AVLinearPCMIsNonInterleaved: false,
            ])
            output.audioMix = spec.audioMix
            output.audioTimePitchAlgorithm = spec.pitchAlgorithm
            output.alwaysCopiesSampleData = false
            let reader = try makeReader(spec)
            guard reader.canAdd(output) else { throw ConversionError.unreadable("無法解碼這個檔案的聲音。") }
            reader.add(output)
            let settings = audioSettings(audio, sampleRate: spec.sampleRate, channels: spec.channels)
            guard writer.canApply(outputSettings: settings, forMediaType: .audio) else {
                throw ConversionError.writeFailed("系統的音訊編碼器不接受這組設定。")
            }
            let input = AVAssetWriterInput(mediaType: .audio, outputSettings: settings)
            input.expectsMediaDataInRealTime = false
            guard writer.canAdd(input) else { throw ConversionError.writeFailed("無法建立音訊編碼器。") }
            writer.add(input)
            pairs.append(MTPumpPair(reader: reader, output: output, input: input, reportsProgress: pairs.isEmpty, label: "audio"))
        }
        guard !pairs.isEmpty else { throw ConversionError.emptyContent("沒有可輸出的影像或聲音。") }
        let start = spec.timeRange?.start ?? .zero
        try await MTPump.run(writer: writer, pairs: pairs, start: start.seconds, duration: spec.duration,
                             sessionStart: start, out: out, progress: progress)
    }

    /// 開始寫入失敗（通常是設定不被編碼器接受）。
    struct MTStartFailure: Error {
        let message: String
    }

    static func videoSettings(_ spec: MTTranscodeSpec, writer: AVAssetWriter) throws -> [String: Any] {
        let base: [String: Any] = [AVVideoCodecKey: spec.codec.avType, AVVideoWidthKey: spec.width, AVVideoHeightKey: spec.height]
        if spec.codec == .prores422 {
            if writer.canApply(outputSettings: base, forMediaType: .video) { return base }
        } else {
            var props: [String: Any] = [
                AVVideoAverageBitRateKey: spec.bitrate,
                AVVideoExpectedSourceFrameRateKey: max(1, Int(spec.frameRate.rounded())),
                AVVideoMaxKeyFrameIntervalDurationKey: 2.0,
                AVVideoAllowFrameReorderingKey: true,
            ]
            props[AVVideoProfileLevelKey] = spec.codec == .h264 ? AVVideoProfileLevelH264HighAutoLevel
                : (kVTProfileLevel_HEVC_Main_AutoLevel as String)
            props.merge(spec.compressionExtras) { _, new in new }
            var full = base
            full[AVVideoCompressionPropertiesKey] = props
            if writer.canApply(outputSettings: full, forMediaType: .video) { return full }
            full[AVVideoCompressionPropertiesKey] = [AVVideoAverageBitRateKey: spec.bitrate]
            if writer.canApply(outputSettings: full, forMediaType: .video) { return full }
        }
        throw ConversionError.other("系統的影片編碼器不支援輸出 \(spec.width)×\(spec.height) 的 \(spec.codec.title)。")
    }

    static func audioSettings(_ encoding: MTAudioEncoding, sampleRate: Double, channels: Int) -> [String: Any] {
        switch encoding {
        case .aac(let bitrate):
            return [AVFormatIDKey: kAudioFormatMPEG4AAC, AVSampleRateKey: sampleRate, AVNumberOfChannelsKey: channels,
                    AVEncoderBitRateKey: MTBitrate.aac(bitrate, sampleRate: sampleRate, channels: channels)]
        case .alac(let depth):
            return [AVFormatIDKey: kAudioFormatAppleLossless, AVSampleRateKey: sampleRate, AVNumberOfChannelsKey: channels,
                    AVEncoderBitDepthHintKey: depth]
        case .pcm(let depth, let bigEndian):
            return [AVFormatIDKey: kAudioFormatLinearPCM, AVSampleRateKey: sampleRate, AVNumberOfChannelsKey: channels,
                    AVLinearPCMBitDepthKey: depth, AVLinearPCMIsFloatKey: false, AVLinearPCMIsBigEndianKey: bigEndian,
                    AVLinearPCMIsNonInterleaved: false]
        }
    }

    /// 建立單一影像軌的影片合成（旋轉、翻轉、縮放、補黑邊共用）。
    static func composition(track: AVAssetTrack, duration: CMTime, renderSize: CGSize, transform: CGAffineTransform,
                            frameRate: Double) -> AVMutableVideoComposition {
        let composition = AVMutableVideoComposition()
        composition.renderSize = renderSize
        composition.frameDuration = frameDuration(frameRate)
        let instruction = AVMutableVideoCompositionInstruction()
        instruction.timeRange = CMTimeRange(start: .zero, duration: duration)
        instruction.backgroundColor = CGColor(gray: 0, alpha: 1)
        let layer = AVMutableVideoCompositionLayerInstruction(assetTrack: track)
        layer.setTransform(transform, at: .zero)
        instruction.layerInstructions = [layer]
        composition.instructions = [instruction]
        return composition
    }

    static func frameDuration(_ fps: Double) -> CMTime {
        let f = fps > 1 ? min(fps, 120) : 30
        // 29.97、59.94 這類 NTSC 影格率用 1001 為分子
        let rounded = f.rounded()
        if abs(f - rounded * 1000 / 1001) < 0.01 { return CMTime(value: 1001, timescale: CMTimeScale(rounded * 1000)) }
        return CMTime(value: 1000, timescale: CMTimeScale((f * 1000).rounded()))
    }

    /// 依副檔名決定容器（mov／mp4／m4v 維持原樣，其他一律 mp4）。
    static func container(for url: URL) -> (type: AVFileType, ext: String) {
        switch url.pathExtension.lowercased() {
        case "mov", "qt": return (.mov, "mov")
        case "m4v": return (.m4v, "m4v")
        default: return (.mp4, "mp4")
        }
    }

    /// 片段組合：把第一條影像軌與第一條音訊軌的指定片段依序接起來（保留原本的方向）。
    static func compose(_ asset: AVAsset, segments: [CMTimeRange], video: Bool = true, audio: Bool = true) async throws
        -> (composition: AVMutableComposition, video: AVMutableCompositionTrack?, audio: AVMutableCompositionTrack?) {
        let composition = AVMutableComposition()
        var vTrack: AVMutableCompositionTrack?
        var aTrack: AVMutableCompositionTrack?
        if video, let src = try await asset.loadTracks(withMediaType: .video).first {
            vTrack = composition.addMutableTrack(withMediaType: .video, preferredTrackID: kCMPersistentTrackID_Invalid)
            let (transform, srcRange) = try await src.load(.preferredTransform, .timeRange)
            vTrack?.preferredTransform = transform
            var cursor = CMTime.zero
            for range in segments {
                let clipped = range.intersection(srcRange)
                if clipped.duration > .zero {
                    try vTrack?.insertTimeRange(clipped, of: src, at: cursor + (clipped.start - range.start))
                }
                cursor = cursor + range.duration
            }
        }
        if audio, let src = try await asset.loadTracks(withMediaType: .audio).first {
            aTrack = composition.addMutableTrack(withMediaType: .audio, preferredTrackID: kCMPersistentTrackID_Invalid)
            var cursor = CMTime.zero
            for range in segments {
                let srcRange = try await src.load(.timeRange)
                let clipped = range.intersection(srcRange)
                if clipped.duration > .zero {
                    try aTrack?.insertTimeRange(clipped, of: src, at: cursor + (clipped.start - range.start))
                }
                cursor = cursor + range.duration
            }
        }
        return (composition, vTrack, aTrack)
    }
}

// MARK: - 讀寫幫浦

final class MTPumpPair: @unchecked Sendable {
    let reader: AVAssetReader
    let output: AVAssetReaderOutput
    let input: AVAssetWriterInput
    let reportsProgress: Bool
    let queue: DispatchQueue
    private let lock = NSLock()
    private var done = false

    init(reader: AVAssetReader, output: AVAssetReaderOutput, input: AVAssetWriterInput, reportsProgress: Bool, label: String) {
        self.reader = reader
        self.output = output
        self.input = input
        self.reportsProgress = reportsProgress
        self.queue = DispatchQueue(label: "quickseemd.media.\(label)")
    }

    /// 第一次呼叫回傳 true。
    func markDone() -> Bool {
        lock.lock(); defer { lock.unlock() }
        if done { return false }
        done = true
        return true
    }
}

final class MTPumpState: @unchecked Sendable {
    private let lock = NSLock()
    private var remaining: Int
    private var cancelled = false
    private var failed = false
    private var resumed = false
    private var continuation: CheckedContinuation<Void, Never>?
    private var stalled = false
    private var lastActivity = CFAbsoluteTimeGetCurrent()
    private var lastReport = 0.0
    private var lastFraction = -1.0
    private let progress: @Sendable (Double) -> Void

    init(count: Int, progress: @escaping @Sendable (Double) -> Void) {
        remaining = count
        self.progress = progress
    }

    var wasStalled: Bool { lock.lock(); defer { lock.unlock() }; return stalled }
    var idleSeconds: Double { lock.lock(); defer { lock.unlock() }; return CFAbsoluteTimeGetCurrent() - lastActivity }

    /// 有資料進出時呼叫（看門狗用）。
    func touch() {
        lock.lock(); lastActivity = CFAbsoluteTimeGetCurrent(); lock.unlock()
    }

    func stall() {
        lock.lock(); stalled = true; failed = true
        let c = resumeLocked()
        lock.unlock()
        c?.resume()
    }

    var wasCancelled: Bool { lock.lock(); defer { lock.unlock() }; return cancelled }
    var isFinished: Bool { lock.lock(); defer { lock.unlock() }; return resumed }
    var didFail: Bool { lock.lock(); defer { lock.unlock() }; return failed }
    var shouldStop: Bool { lock.lock(); defer { lock.unlock() }; return cancelled || failed }

    func attach(_ c: CheckedContinuation<Void, Never>) {
        lock.lock()
        if resumed || remaining == 0 || cancelled || failed {
            resumed = true
            lock.unlock()
            c.resume()
            return
        }
        continuation = c
        lock.unlock()
    }

    private func resumeLocked() -> CheckedContinuation<Void, Never>? {
        guard !resumed, let c = continuation else { return nil }
        resumed = true
        continuation = nil
        return c
    }

    func pairFinished() {
        lock.lock()
        lastActivity = CFAbsoluteTimeGetCurrent()
        remaining -= 1
        let c = remaining <= 0 ? resumeLocked() : nil
        lock.unlock()
        c?.resume()
    }

    func fail() {
        lock.lock(); failed = true
        let c = resumeLocked()
        lock.unlock()
        c?.resume()
    }

    func cancel() {
        lock.lock(); cancelled = true
        let c = resumeLocked()
        lock.unlock()
        c?.resume()
    }

    /// 節流回報（最多每 0.1 秒一次）。
    func report(_ fraction: Double) {
        let now = CFAbsoluteTimeGetCurrent()
        lock.lock()
        let should = now - lastReport >= 0.1 && fraction > lastFraction
        if should { lastReport = now; lastFraction = fraction }
        lock.unlock()
        if should { progress(min(1, max(0, fraction))) }
    }
}

final class MTPumpWatch: @unchecked Sendable {
    let writer: AVAssetWriter
    let readers: [AVAssetReader]

    init(writer: AVAssetWriter, readers: [AVAssetReader]) {
        self.writer = writer
        self.readers = readers
    }

    var anyFailed: Bool { writer.status == .failed || readers.contains { $0.status == .failed } }
}

enum MTPump {
    /// 超過這麼久沒有任何資料進出就視為卡住。
    nonisolated(unsafe) static var stallTimeout: Double = 20
    /// 測試用：設為 true 時，下一次的影像讀取會故意停住超過 stallTimeout，用來驗證自動重試。
    nonisolated(unsafe) static var simulateStallOnce = false

    struct Stall: Error {}

    static func run(writer: AVAssetWriter, pairs: [MTPumpPair], start: Double, duration: Double,
                    sessionStart: CMTime, out: URL, progress: @escaping @Sendable (Double) -> Void) async throws {
        let readers = pairs.map(\.reader)
        func cancelReaders() { for r in readers where r.status == .reading { r.cancelReading() } }
        for reader in readers where !reader.startReading() {
            cancelReaders()
            throw ConversionError.unreadable(reader.error?.localizedDescription ?? "無法開始解碼。")
        }
        guard writer.startWriting() else {
            cancelReaders()
            throw MTTranscoder.MTStartFailure(message: writer.error?.localizedDescription ?? "無法開始寫入。")
        }
        writer.startSession(atSourceTime: sessionStart)
        let state = MTPumpState(count: pairs.count, progress: progress)
        // 看門狗：讀寫中途失敗時，AVFoundation 不一定會再呼叫要資料的區塊，這裡定期檢查避免永遠等待
        let watched = MTPumpWatch(writer: writer, readers: readers)
        let watchdog = Task.detached {
            while !Task.isCancelled && !state.isFinished {
                try? await Task.sleep(nanoseconds: 300_000_000)
                if watched.anyFailed { state.fail() }
                if state.idleSeconds > stallTimeout { state.stall() }
            }
        }
        defer { watchdog.cancel() }
        await withTaskCancellationHandler {
            await withCheckedContinuation { (c: CheckedContinuation<Void, Never>) in
                state.attach(c)
                for pair in pairs {
                    pair.input.requestMediaDataWhenReady(on: pair.queue) {
                        if pair.reportsProgress && simulateStallOnce {
                            simulateStallOnce = false
                            Thread.sleep(forTimeInterval: stallTimeout + 2)
                        }
                        while pair.input.isReadyForMoreMediaData {
                            if state.shouldStop { return }
                            guard let buffer = pair.output.copyNextSampleBuffer() else {
                                if pair.markDone() {
                                    pair.input.markAsFinished()
                                    state.pairFinished()
                                }
                                return
                            }
                            if !pair.input.append(buffer) {
                                state.fail()
                                return
                            }
                            state.touch()
                            if pair.reportsProgress, duration > 0 {
                                let t = CMSampleBufferGetPresentationTimeStamp(buffer).seconds
                                if t.isFinite { state.report((t - start) / duration) }
                            }
                        }
                    }
                }
            }
        } onCancel: {
            state.cancel()
        }
        if state.wasCancelled || Task.isCancelled {
            cancelReaders()
            writer.cancelWriting()
            try? FileManager.default.removeItem(at: out)
            throw ConversionError.cancelled
        }
        if state.wasStalled {
            cancelReaders()
            writer.cancelWriting()
            try? FileManager.default.removeItem(at: out)
            throw Stall()
        }
        let failedReader = readers.first { $0.status == .failed }
        if state.didFail || failedReader != nil || writer.status == .failed {
            let message = writer.error?.localizedDescription ?? failedReader?.error?.localizedDescription ?? "未知錯誤"
            let readFailed = failedReader != nil
            cancelReaders()
            writer.cancelWriting()
            try? FileManager.default.removeItem(at: out)
            throw readFailed ? ConversionError.unreadable(message) : ConversionError.writeFailed(message)
        }
        // 對齊到預定長度（例如變速時音訊的尾巴會稍微多出一點）
        if duration > 0 { writer.endSession(atSourceTime: sessionStart + MTTime.cm(duration)) }
        await writer.finishWriting()
        guard writer.status == .completed else {
            try? FileManager.default.removeItem(at: out)
            throw ConversionError.writeFailed(writer.error?.localizedDescription ?? "影片寫入失敗")
        }
        progress(1)
    }
}

// MARK: - 直接轉封裝（不重新編碼）

enum MTExport {
    /// 以 Passthrough 輸出；不相容時丟出錯誤，呼叫端可改用重新編碼。
    static func passthrough(_ asset: AVAsset, fileType: AVFileType, timeRange: CMTimeRange? = nil, metadata: [AVMetadataItem] = [],
                            to out: URL, progress: @escaping @Sendable (Double) -> Void) async throws {
        guard await AVAssetExportSession.compatibility(ofExportPreset: AVAssetExportPresetPassthrough, with: asset, outputFileType: fileType),
              let session = AVAssetExportSession(asset: asset, presetName: AVAssetExportPresetPassthrough) else {
            throw MTPassthroughUnavailable()
        }
        if let timeRange { session.timeRange = timeRange }
        if !metadata.isEmpty { session.metadata = metadata }
        session.shouldOptimizeForNetworkUse = fileType != .mov
        do {
            try await CVMediaConverter.runExport(session, to: out, fileType: fileType, progress: progress)
        } catch ConversionError.cancelled {
            try? FileManager.default.removeItem(at: out)
            throw ConversionError.cancelled
        } catch {
            try? FileManager.default.removeItem(at: out)
            if Task.isCancelled { throw ConversionError.cancelled }
            throw MTPassthroughUnavailable()
        }
    }

    /// 來源的中繼資料（拍攝日期、地點…），供轉封裝時保留。
    static func metadata(of asset: AVAsset) async -> [AVMetadataItem] {
        (try? await asset.load(.metadata)) ?? []
    }
}

struct MTPassthroughUnavailable: Error {}

// MARK: - 共用的工作步驟

enum MTWork {
    /// 建立暫存資料夾並在結束時刪除（不論成功、失敗或取消）。
    static func withWorkDirectory<T>(_ context: ToolContext, _ body: (URL) async throws -> T) async throws -> T {
        let dir = try context.makeWorkDirectory()
        defer { try? FileManager.default.removeItem(at: dir) }
        return try await body(dir)
    }

    /// 進度回呼（把 0…1 對應到 context 的某一段）。
    static func progress(_ context: ToolContext, from: Double = 0, to: Double = 1, _ message: String? = nil) -> @Sendable (Double) -> Void {
        { p in context.progress(from + (to - from) * p, message) }
    }

    /// 交付輸出檔，並把結果摘要（附檔案大小）當作最後的進度訊息（結果頁會顯示）。
    static func deliver(_ context: ToolContext, _ temp: URL, as name: String, for input: URL, summary: String,
                        appendSize: Bool = true) throws -> URL {
        let size = !appendSize || CVFileNaming.isDirectory(temp) ? 0 : ToolFiles.size(of: temp)
        let url = try context.deliver(temp, as: name, for: input)
        context.progress(1, size > 0 ? "\(summary)（\(ToolFiles.formattedSize(size))）" : summary)
        return url
    }

    /// 逐檔處理；多個檔案時最後回報整批摘要。
    static func perFile(_ body: @escaping @Sendable (URL, ToolContext) async throws -> [URL]) -> ToolJob {
        ToolJob { files, context in
            var outputs: [URL] = []
            for (i, file) in files.enumerated() {
                try context.checkCancellation()
                let sub = context.segment(i, of: files.count)
                sub.progress(0, "處理中：\(file.lastPathComponent)")
                outputs += try await body(file, sub)
            }
            if files.count > 1 {
                let size = outputs.reduce(Int64(0)) { $0 + ToolFiles.size(of: $1) }
                context.progress(1, "已完成 \(files.count) 個檔案" + (size > 0 ? "，共 \(ToolFiles.formattedSize(size))" : ""))
            }
            return outputs
        }
    }

    /// 同副檔名時加上標籤，避免與原檔混淆。
    static func outputName(_ context: ToolContext, for input: URL, tag: String?, ext: String) -> String {
        let same = input.pathExtension.lowercased() == ext.lowercased()
        return context.outputName(for: input, tag: tag ?? (same ? "已轉換" : nil), ext: ext)
    }
}
