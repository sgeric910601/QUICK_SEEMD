import AudioToolbox
import AVFoundation
import CoreMedia
import Foundation

// 音訊輸出：AVAssetReader 解碼（可重新取樣、混成單聲道／立體聲、剪輯片段、淡入淡出）
// → AVAssetWriter 編碼成 M4A／WAV／AIFF／CAF；FLAC 交給 afconvert、MP3／OGG 交給 ffmpeg（若有安裝）。

enum MTAudioFormat: String, CaseIterable, Sendable {
    case aac, alac, wav, aiff, flac, caf, mp3, ogg

    var title: String {
        switch self {
        case .aac: return "M4A (AAC)"
        case .alac: return "M4A (ALAC 無損)"
        case .wav: return "WAV"
        case .aiff: return "AIFF"
        case .flac: return "FLAC"
        case .caf: return "CAF"
        case .mp3: return "MP3"
        case .ogg: return "OGG"
        }
    }

    var subtitle: String {
        switch self {
        case .aac: return "檔案小、幾乎所有裝置都能播放"
        case .alac: return "Apple 無損格式，音質與原始相同"
        case .wav: return "未壓縮，適合剪輯軟體"
        case .aiff: return "未壓縮，Mac 常用"
        case .flac: return "開放的無損壓縮格式"
        case .caf: return "Core Audio 格式，未壓縮"
        case .mp3: return "最通用的有損格式（使用 ffmpeg）"
        case .ogg: return "開放的有損格式（使用 ffmpeg）"
        }
    }

    var ext: String {
        switch self {
        case .aac, .alac: return "m4a"
        case .wav: return "wav"
        case .aiff: return "aiff"
        case .flac: return "flac"
        case .caf: return "caf"
        case .mp3: return "mp3"
        case .ogg: return "ogg"
        }
    }

    var needsFFmpeg: Bool { self == .mp3 || self == .ogg }
    var usesBitrate: Bool { self == .aac || self == .mp3 || self == .ogg }

    /// 目前可用的格式（MP3／OGG 需要使用者自行安裝 ffmpeg）。
    static var available: [MTAudioFormat] {
        let ffmpeg = CVExternalTools.isAvailable(.ffmpeg)
        return allCases.filter { !$0.needsFFmpeg || ffmpeg }
    }

    /// 與來源相同的格式（剪輯音訊時使用）。
    static func matching(_ info: MTMediaInfo) -> MTAudioFormat {
        switch info.audioFormat {
        case kAudioFormatAppleLossless: return .alac
        case kAudioFormatFLAC: return .flac
        case kAudioFormatLinearPCM:
            switch info.url.pathExtension.lowercased() {
            case "aif", "aiff", "aifc": return .aiff
            case "caf": return .caf
            default: return .wav
            }
        case kAudioFormatMPEGLayer3: return CVExternalTools.isAvailable(.ffmpeg) ? .mp3 : .aac
        default: return .aac
        }
    }
}

struct MTAudioOutput: Sendable {
    var format: MTAudioFormat = .aac
    /// AAC／MP3／OGG 位元率（bps）
    var bitrate: Int = 192_000
    /// nil 代表與來源相同
    var sampleRate: Double? = nil
    /// nil 代表與來源相同（最多 2 聲道）
    var channels: Int? = nil
}

enum MTAudioEngine {
    /// 輸出音訊。`segments` 為要保留的片段（秒，依序接起來）；nil 代表整段。
    static func render(_ input: URL, info: MTMediaInfo, segments: [ClosedRange<Double>]? = nil, fadeIn: Double = 0, fadeOut: Double = 0,
                       output: MTAudioOutput, to out: URL, progress report: @escaping @Sendable (Double) -> Void) async throws {
        try info.requireAudio()
        var asset = AVURLAsset(url: input)
        guard var source = try await asset.loadTracks(withMediaType: .audio).first else { throw ConversionError.noAudioTrack }
        let needsDecode = segments != nil && info.audioFormat != kAudioFormatLinearPCM
        let progress: @Sendable (Double) -> Void
        if needsDecode {
            progress = { p in report(0.4 + p * 0.6) }
        } else {
            progress = report
        }

        // 剪輯壓縮格式（AAC、FLAC、MP3…）時先完整解碼成 PCM：壓縮格式以封包為單位（FLAC 一包可達 0.2 秒），
        // 直接在原檔上切會對不準；PCM 每個取樣都能精確切開。
        var decoded: URL?
        defer { if let decoded { try? FileManager.default.removeItem(at: decoded) } }
        if needsDecode {
            let pcm = out.deletingLastPathComponent().appendingPathComponent("source-\(UUID().uuidString).caf")
            var s = MTTranscodeSpec(asset: asset, duration: info.duration)
            s.audioTracks = [source]
            s.audio = .pcm(bitDepth: 24, bigEndian: false)
            s.sampleRate = info.sampleRate
            s.channels = max(1, min(2, info.channels))
            s.fileType = .caf
            try await MTTranscoder.run(s, to: pcm) { report($0 * 0.4) }
            decoded = pcm
            asset = AVURLAsset(url: pcm)
            guard let track = try await asset.loadTracks(withMediaType: .audio).first else { throw ConversionError.noAudioTrack }
            source = track
        }

        var readAsset: AVAsset = asset
        var tracks: [AVAssetTrack] = [source]
        var total = info.duration
        var mix: AVAudioMix?
        if segments != nil || fadeIn > 0 || fadeOut > 0 {
            let ranges = (segments ?? [0...info.duration]).map { MTTime.range($0.lowerBound, $0.upperBound) }
            let composed = try await MTTranscoder.compose(asset, segments: ranges, video: false, audio: true)
            guard let track = composed.audio else { throw ConversionError.noAudioTrack }
            readAsset = composed.composition
            tracks = [track]
            total = ranges.reduce(0) { $0 + $1.duration.seconds }
            if fadeIn > 0 || fadeOut > 0 {
                let params = AVMutableAudioMixInputParameters(track: track)
                let fi = min(fadeIn, total / 2), fo = min(fadeOut, total / 2)
                if fi > 0 { params.setVolumeRamp(fromStartVolume: 0, toEndVolume: 1, timeRange: MTTime.range(0, fi)) }
                if fo > 0 { params.setVolumeRamp(fromStartVolume: 1, toEndVolume: 0, timeRange: MTTime.range(total - fo, total)) }
                let m = AVMutableAudioMix()
                m.inputParameters = [params]
                mix = m
            }
        }

        let channels = max(1, min(2, output.channels ?? info.channels))
        var rate = output.sampleRate ?? info.sampleRate
        if output.format == .aac { rate = MTBitrate.aacSampleRate(rate) }
        if output.format == .mp3 { rate = min(rate, 48_000) }
        let depth = info.bitDepth > 16 ? 24 : 16

        func spec(_ encoding: MTAudioEncoding, _ type: AVFileType) -> MTTranscodeSpec {
            var s = MTTranscodeSpec(asset: readAsset, duration: total)
            s.audioTracks = tracks
            s.audioMix = mix
            s.audio = encoding
            s.sampleRate = rate
            s.channels = channels
            s.fileType = type
            return s
        }

        switch output.format {
        case .aac:
            try await MTTranscoder.run(spec(.aac(bitrate: output.bitrate), .m4a), to: out, progress: progress)
        case .alac:
            try await MTTranscoder.run(spec(.alac(bitDepth: depth), .m4a), to: out, progress: progress)
        case .wav:
            try await MTTranscoder.run(spec(.pcm(bitDepth: depth, bigEndian: false), .wav), to: out, progress: progress)
        case .aiff:
            try await MTTranscoder.run(spec(.pcm(bitDepth: depth, bigEndian: true), .aiff), to: out, progress: progress)
        case .caf:
            try await MTTranscoder.run(spec(.pcm(bitDepth: depth, bigEndian: false), .caf), to: out, progress: progress)
        case .flac, .mp3, .ogg:
            // 先解碼成 PCM 暫存檔，再交給外部編碼器
            let pcm = out.deletingLastPathComponent().appendingPathComponent("pcm-\(UUID().uuidString).caf")
            defer { try? FileManager.default.removeItem(at: pcm) }
            try await MTTranscoder.run(spec(.pcm(bitDepth: depth, bigEndian: false), .caf), to: pcm) { progress($0 * 0.8) }
            try await encodeExternally(pcm, format: output.format, bitrate: output.bitrate, to: out)
            progress(1)
        }
    }

    private static func encodeExternally(_ pcm: URL, format: MTAudioFormat, bitrate: Int, to out: URL) async throws {
        switch format {
        case .flac:
            let result = try await CVProcess.run(CVMediaConverter.afconvertPath, ["-f", "flac", "-d", "flac", pcm.path, out.path])
            if !result.succeeded {
                try? FileManager.default.removeItem(at: out)
                throw ConversionError.toolFailed(tool: "afconvert", message: result.stderrText)
            }
        case .mp3, .ogg:
            guard let ffmpeg = CVExternalTools.executable(for: .ffmpeg) else { throw ConversionError.toolMissing("ffmpeg") }
            let kbps = "\(max(64, bitrate / 1000))k"
            let codec = format == .mp3 ? ["-c:a", "libmp3lame", "-b:a", kbps] : ["-c:a", "libvorbis", "-b:a", kbps]
            let args = ["-hide_banner", "-nostdin", "-n", "-i", pcm.path, "-vn"] + codec + [out.path]
            do {
                try await CVProcess.runChecked(ffmpeg.path, args, toolName: "ffmpeg")
            } catch {
                try? FileManager.default.removeItem(at: out)
                throw error
            }
        default:
            break
        }
    }
}

// MARK: - 波形（剪輯音訊的時間軸用）

enum MTWaveform {
    /// 回傳 count 個 0…1 的峰值。
    static func peaks(_ url: URL, count: Int) async -> [Float] {
        let asset = AVURLAsset(url: url)
        guard let track = try? await asset.loadTracks(withMediaType: .audio).first,
              let duration = try? await asset.load(.duration).seconds, duration > 0,
              let reader = try? AVAssetReader(asset: asset) else { return [] }
        // 混音輸出才支援重新取樣與降成單聲道（TrackOutput 給這些設定會丟出例外）
        let rate = 8_000.0
        let output = AVAssetReaderAudioMixOutput(audioTracks: [track], audioSettings: [
            AVFormatIDKey: kAudioFormatLinearPCM, AVSampleRateKey: rate, AVNumberOfChannelsKey: 1,
            AVLinearPCMBitDepthKey: 16, AVLinearPCMIsFloatKey: false, AVLinearPCMIsBigEndianKey: false, AVLinearPCMIsNonInterleaved: false,
        ])
        guard reader.canAdd(output) else { return [] }
        reader.add(output)
        guard reader.startReading() else { return [] }
        let totalFrames = max(1, Int(duration * rate))
        let perBucket = max(1, totalFrames / count)
        var peaks = [Float](repeating: 0, count: count)
        var frame = 0
        while let buffer = output.copyNextSampleBuffer() {
            if Task.isCancelled { reader.cancelReading(); return [] }
            guard let block = CMSampleBufferGetDataBuffer(buffer) else { continue }
            var length = 0
            var pointer: UnsafeMutablePointer<CChar>?
            guard CMBlockBufferGetDataPointer(block, atOffset: 0, lengthAtOffsetOut: &length, totalLengthOut: nil, dataPointerOut: &pointer) == noErr,
                  let pointer else { continue }
            let samples = length / 2
            pointer.withMemoryRebound(to: Int16.self, capacity: samples) { p in
                for i in 0..<samples {
                    let bucket = min(count - 1, (frame + i) / perBucket)
                    let v = Float(abs(Int32(p[i]))) / 32_768
                    if v > peaks[bucket] { peaks[bucket] = v }
                }
            }
            frame += samples
        }
        let top = max(peaks.max() ?? 1, 0.05)
        return peaks.map { min(1, $0 / top) }
    }
}
