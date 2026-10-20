import AudioToolbox
import AVFoundation
import CoreImage
import CoreMedia
import CoreVideo
import Foundation
import ImageIO
import UniformTypeIdentifiers

/// 影音轉換：
/// - 音訊 ↔ 音訊：/usr/bin/afconvert（AAC、ALAC、FLAC、WAV、AIFF、CAF、ADTS）
/// - 影片 → 音訊：AVAssetReader 解碼成 PCM，再交給 afconvert
/// - 影片 → 影片：AVAssetExportSession（H.264、HEVC、ProRes；可縮小解析度）
/// - 影片 → GIF 動畫／單張畫面：AVAssetImageGenerator + ImageIO
/// - GIF／動畫圖片 → 影片：AVAssetWriter
enum CVMediaConverter {
    static let afconvertPath = "/usr/bin/afconvert"

    // MARK: - 執行時能力偵測

    private static let encoderIDs: Set<UInt32> = {
        var size: UInt32 = 0
        guard AudioFormatGetPropertyInfo(kAudioFormatProperty_EncodeFormatIDs, 0, nil, &size) == noErr, size > 0 else { return [] }
        var ids = [UInt32](repeating: 0, count: Int(size) / MemoryLayout<UInt32>.size)
        guard AudioFormatGetProperty(kAudioFormatProperty_EncodeFormatIDs, 0, nil, &size, &ids) == noErr else { return [] }
        return Set(ids)
    }()

    private static let writableFileTypes: Set<UInt32> = {
        var size: UInt32 = 0
        guard AudioFileGetGlobalInfoSize(kAudioFileGlobalInfo_WritableTypes, 0, nil, &size) == noErr, size > 0 else { return [] }
        var ids = [UInt32](repeating: 0, count: Int(size) / MemoryLayout<UInt32>.size)
        guard AudioFileGetGlobalInfo(kAudioFileGlobalInfo_WritableTypes, 0, nil, &size, &ids) == noErr else { return [] }
        return Set(ids)
    }()

    /// 本機可輸出的音訊格式（依 AudioToolbox 回報的編碼器與檔案類型）。
    static let nativeAudioFormats: [ConversionFormat] = {
        guard FileManager.default.isExecutableFile(atPath: afconvertPath) else { return [] }
        var list: [ConversionFormat] = []
        let lpcm = kAudioFormatLinearPCM
        func ok(_ file: AudioFileTypeID, _ codec: AudioFormatID) -> Bool {
            writableFileTypes.contains(file) && (codec == lpcm || encoderIDs.contains(codec))
        }
        if ok(kAudioFileM4AType, kAudioFormatMPEG4AAC) { list.append(ConversionFormats.m4a) }
        if ok(kAudioFileM4AType, kAudioFormatAppleLossless) { list.append(ConversionFormats.alac) }
        if ok(kAudioFileAAC_ADTSType, kAudioFormatMPEG4AAC) { list.append(ConversionFormats.aac) }
        if ok(kAudioFileWAVEType, lpcm) { list.append(ConversionFormats.wav) }
        if ok(kAudioFileAIFFType, lpcm) { list.append(ConversionFormats.aiff) }
        if ok(kAudioFileCAFType, lpcm) { list.append(ConversionFormats.caf) }
        if ok(kAudioFileFLACType, kAudioFormatFLAC) { list.append(ConversionFormats.flac) }
        return list
    }()

    static let nativeVideoFormats: [ConversionFormat] = {
        let presets = Set(AVAssetExportSession.allExportPresets())
        var list: [ConversionFormat] = []
        if presets.contains(AVAssetExportPresetHighestQuality) {
            list += [ConversionFormats.mp4, ConversionFormats.mov, ConversionFormats.m4v]
        }
        if presets.contains(AVAssetExportPresetHEVCHighestQuality) {
            list.insert(ConversionFormats.mp4HEVC, at: min(1, list.count))
            list.append(ConversionFormats.movHEVC)
        }
        if presets.contains(AVAssetExportPresetAppleProRes422LPCM) { list.append(ConversionFormats.prores) }
        return list
    }()

    // MARK: - 音訊

    /// 取得來源的位元深度（> 16 位元或浮點時，無損輸出改用 24 位元）。
    private static func sourceBitDepth(_ url: URL) -> Int {
        var fileID: AudioFileID?
        guard AudioFileOpenURL(url as CFURL, .readPermission, 0, &fileID) == noErr, let fileID else { return 16 }
        defer { AudioFileClose(fileID) }
        var asbd = AudioStreamBasicDescription()
        var size = UInt32(MemoryLayout<AudioStreamBasicDescription>.size)
        if AudioFileGetProperty(fileID, kAudioFilePropertyDataFormat, &size, &asbd) == noErr, asbd.mFormatID == kAudioFormatLinearPCM {
            if asbd.mFormatFlags & kAudioFormatFlagIsFloat != 0 { return 32 }
            return Int(asbd.mBitsPerChannel)
        }
        var depth: Int32 = 0
        size = UInt32(MemoryLayout<Int32>.size)
        if AudioFileGetProperty(fileID, kAudioFilePropertySourceBitDepth, &size, &depth) == noErr, depth != 0 {
            return depth < 0 ? 32 : Int(depth)
        }
        return 16
    }

    private static func canAudioFileOpen(_ url: URL) -> Bool {
        var fileID: AudioFileID?
        guard AudioFileOpenURL(url as CFURL, .readPermission, 0, &fileID) == noErr, let fileID else { return false }
        AudioFileClose(fileID)
        return true
    }

    static func afconvertArguments(formatID: String, bitDepth: Int, bitrate: Int?) -> [String] {
        let hi = bitDepth > 16
        switch formatID {
        case "m4a": return ["-f", "m4af", "-d", "aac"] + (bitrate.map { ["-b", String($0)] } ?? []) + ["-q", "127"]
        case "aac": return ["-f", "adts", "-d", "aac"] + (bitrate.map { ["-b", String($0)] } ?? []) + ["-q", "127"]
        case "alac": return ["-f", "m4af", "-d", "alac"]
        case "wav": return ["-f", "WAVE", "-d", hi ? "LEI24" : "LEI16"]
        case "aiff": return ["-f", "AIFF", "-d", hi ? "BEI24" : "BEI16"]
        case "caf": return ["-f", "caff", "-d", hi ? "LEI24" : "LEI16"]
        case "flac": return ["-f", "flac", "-d", "flac"]
        default: return []
        }
    }

    /// 音訊（或影片中的音軌）→ 音訊。
    static func convertAudio(_ input: URL, profile: CVInputProfile, to format: ConversionFormat, options: ConversionOptions,
                             workDir: URL, progress: @escaping @Sendable (Double) -> Void) async throws -> [URL] {
        let out = workDir.appendingPathComponent(CVFileNaming.fileName(CVFileNaming.baseName(of: input), format.fileExtension))
        var source = input
        var cleanup: URL?
        defer { if let cleanup { try? FileManager.default.removeItem(at: cleanup) } }

        if profile.isVideo || !canAudioFileOpen(input) {
            // 先用 AVFoundation 解碼成 PCM
            let scratch = try CVFileNaming.makeScratchDirectory()
            cleanup = scratch
            let pcm = scratch.appendingPathComponent("audio.caf")
            try await decodeAudioToPCM(input, to: pcm) { progress($0 * 0.7) }
            source = pcm
        } else {
            progress(0.05)
        }
        let depth = sourceBitDepth(source)
        var args = afconvertArguments(formatID: format.id, bitDepth: depth, bitrate: options.audioBitrate)
        guard !args.isEmpty else { throw ConversionError.unsupportedTarget(source: input.lastPathComponent, target: format.displayName) }
        let first = try await CVProcess.run(afconvertPath, args + [source.path, out.path])
        if !first.succeeded {
            // 位元率或位元深度不被編碼器接受時，退回預設值再試一次
            try? FileManager.default.removeItem(at: out)
            args = afconvertArguments(formatID: format.id, bitDepth: 16, bitrate: nil)
            try await CVProcess.runChecked(afconvertPath, args + [source.path, out.path], toolName: "afconvert")
        }
        progress(1)
        return [out]
    }

    /// 以 AVAssetReader 解碼第一條音軌（最多雙聲道）並寫成 16 位元 PCM CAF。
    static func decodeAudioToPCM(_ input: URL, to out: URL, progress: @escaping @Sendable (Double) -> Void) async throws {
        let asset = AVURLAsset(url: input)
        let tracks = try await asset.loadTracks(withMediaType: .audio)
        guard let track = tracks.first else { throw ConversionError.noAudioTrack }
        let duration = try await asset.load(.duration).seconds
        var sampleRate = 44_100.0
        var channels = 2
        if let desc = try await track.load(.formatDescriptions).first,
           let asbd = CMAudioFormatDescriptionGetStreamBasicDescription(desc)?.pointee {
            if asbd.mSampleRate > 0 { sampleRate = asbd.mSampleRate }
            channels = max(1, min(2, Int(asbd.mChannelsPerFrame)))
        }
        let pcmSettings: [String: Any] = [
            AVFormatIDKey: kAudioFormatLinearPCM,
            AVSampleRateKey: sampleRate,
            AVNumberOfChannelsKey: channels,
            AVLinearPCMBitDepthKey: 16,
            AVLinearPCMIsFloatKey: false,
            AVLinearPCMIsBigEndianKey: false,
            AVLinearPCMIsNonInterleaved: false,
        ]
        let reader: AVAssetReader
        do { reader = try AVAssetReader(asset: asset) } catch { throw ConversionError.unreadable(error.localizedDescription) }
        let output = AVAssetReaderAudioMixOutput(audioTracks: [track], audioSettings: pcmSettings)
        output.alwaysCopiesSampleData = false
        guard reader.canAdd(output) else { throw ConversionError.unreadable("無法解碼這個音軌。") }
        reader.add(output)
        let writer: AVAssetWriter
        do { writer = try AVAssetWriter(outputURL: out, fileType: .caf) } catch { throw ConversionError.writeFailed(error.localizedDescription) }
        let input = AVAssetWriterInput(mediaType: .audio, outputSettings: pcmSettings)
        input.expectsMediaDataInRealTime = false
        guard writer.canAdd(input) else { throw ConversionError.writeFailed("無法建立 PCM 暫存檔。") }
        writer.add(input)
        guard reader.startReading() else { throw ConversionError.unreadable(reader.error?.localizedDescription ?? "解碼失敗") }
        guard writer.startWriting() else { throw ConversionError.writeFailed(writer.error?.localizedDescription ?? "寫入失敗") }
        writer.startSession(atSourceTime: .zero)
        try await pump(reader: reader, output: output, writer: writer, input: input, duration: duration, progress: progress)
    }

    /// 把 reader 的 sample buffer 依序送進 writer。
    private static func pump(reader: AVAssetReader, output: AVAssetReaderOutput, writer: AVAssetWriter, input: AVAssetWriterInput,
                             duration: Double, progress: @escaping @Sendable (Double) -> Void) async throws {
        let queue = DispatchQueue(label: "converter.media.pump")
        let state = CVPumpState()
        let av = CVPumpObjects(reader: reader, output: output, input: input)
        await withTaskCancellationHandler {
            await withCheckedContinuation { (cont: CheckedContinuation<Void, Never>) in
                av.input.requestMediaDataWhenReady(on: queue) {
                    let reader = av.reader, output = av.output, input = av.input
                    while input.isReadyForMoreMediaData {
                        if state.isCancelled {
                            reader.cancelReading()
                            input.markAsFinished()
                            state.finish { cont.resume() }
                            return
                        }
                        guard let buffer = output.copyNextSampleBuffer() else {
                            input.markAsFinished()
                            state.finish { cont.resume() }
                            return
                        }
                        if !input.append(buffer) {
                            reader.cancelReading()
                            input.markAsFinished()
                            state.finish { cont.resume() }
                            return
                        }
                        if duration > 0 {
                            let t = CMSampleBufferGetPresentationTimeStamp(buffer).seconds
                            if t.isFinite { progress(min(1, max(0, t / duration))) }
                        }
                    }
                }
            }
        } onCancel: {
            state.cancel()
        }
        if state.isCancelled {
            writer.cancelWriting()
            throw ConversionError.cancelled
        }
        await writer.finishWriting()
        if reader.status == .failed { throw ConversionError.unreadable(reader.error?.localizedDescription ?? "解碼失敗") }
        if writer.status != .completed { throw ConversionError.writeFailed(writer.error?.localizedDescription ?? "寫入失敗") }
    }

    // MARK: - 影片 → 影片

    static func convertVideo(_ input: URL, to format: ConversionFormat, options: ConversionOptions, workDir: URL,
                             progress: @escaping @Sendable (Double) -> Void) async throws -> [URL] {
        let asset = AVURLAsset(url: input)
        let videoTracks = try await asset.loadTracks(withMediaType: .video)
        guard let videoTrack = videoTracks.first else { throw ConversionError.noVideoTrack }
        let out = workDir.appendingPathComponent(CVFileNaming.fileName(CVFileNaming.baseName(of: input), format.fileExtension))

        let preset: String
        let fileType: AVFileType
        switch format.id {
        case "mp4": preset = AVAssetExportPresetHighestQuality; fileType = .mp4
        case "mov": preset = AVAssetExportPresetHighestQuality; fileType = .mov
        case "m4v": preset = AVAssetExportPresetHighestQuality; fileType = .m4v
        case "mp4-hevc": preset = AVAssetExportPresetHEVCHighestQuality; fileType = .mp4
        case "mov-hevc": preset = AVAssetExportPresetHEVCHighestQuality; fileType = .mov
        case "prores": preset = AVAssetExportPresetAppleProRes422LPCM; fileType = .mov
        default: throw ConversionError.unsupportedTarget(source: input.lastPathComponent, target: format.displayName)
        }
        guard await AVAssetExportSession.compatibility(ofExportPreset: preset, with: asset, outputFileType: fileType) else {
            throw ConversionError.other("系統的影片編碼器不支援把這個檔案轉成 \(format.displayName)。")
        }
        guard let session = AVAssetExportSession(asset: asset, presetName: preset) else {
            throw ConversionError.other("無法建立影片轉檔工作。")
        }
        session.shouldOptimizeForNetworkUse = fileType != .mov

        // 縮小解析度（以短邊為準，不放大）
        if options.videoSize != .original {
            let natural = try await videoTrack.load(.naturalSize)
            let transform = try await videoTrack.load(.preferredTransform)
            let oriented = natural.applying(transform)
            let size = CGSize(width: abs(oriented.width), height: abs(oriented.height))
            let shortSide = min(size.width, size.height)
            let cap = CGFloat(options.videoSize.rawValue)
            if shortSide > cap, shortSide > 0 {
                let scale = cap / shortSide
                let target = CGSize(width: evenFloor(size.width * scale), height: evenFloor(size.height * scale))
                let composition = try await AVMutableVideoComposition.videoComposition(with: asset) { request in
                    let sx = target.width / request.sourceImage.extent.width
                    let sy = target.height / request.sourceImage.extent.height
                    let scaled = request.sourceImage.transformed(by: CGAffineTransform(scaleX: sx, y: sy))
                    request.finish(with: scaled, context: nil)
                }
                composition.renderSize = target
                session.videoComposition = composition
            }
        }
        try await runExport(session, to: out, fileType: fileType, progress: progress)
        return [out]
    }

    private static func evenFloor(_ v: CGFloat) -> CGFloat {
        let i = max(2, Int(v.rounded(.down)))
        return CGFloat(i - i % 2)
    }

    static func runExport(_ session: AVAssetExportSession, to out: URL, fileType: AVFileType,
                          progress: @escaping @Sendable (Double) -> Void) async throws {
        if #available(macOS 15.0, *) {
            let monitor = Task {
                for await state in session.states(updateInterval: 0.25) {
                    if case .exporting(let p) = state { progress(p.fractionCompleted) }
                }
            }
            defer { monitor.cancel() }
            do {
                try await session.export(to: out, as: fileType)
            } catch is CancellationError {
                throw ConversionError.cancelled
            } catch {
                if Task.isCancelled { throw ConversionError.cancelled }
                throw ConversionError.other("影片轉檔失敗：\(error.localizedDescription)")
            }
        } else {
            session.outputURL = out
            session.outputFileType = fileType
            let box = CVExportBox(session)
            let poller = Task {
                while !Task.isCancelled {
                    progress(Double(box.session.progress))
                    try? await Task.sleep(nanoseconds: 250_000_000)
                }
            }
            defer { poller.cancel() }
            await withTaskCancellationHandler {
                await withCheckedContinuation { (cont: CheckedContinuation<Void, Never>) in
                    box.session.exportAsynchronously { cont.resume() }
                }
            } onCancel: {
                box.session.cancelExport()
            }
            switch session.status {
            case .completed: break
            case .cancelled: throw ConversionError.cancelled
            default: throw ConversionError.other("影片轉檔失敗：\(session.error?.localizedDescription ?? "未知錯誤")")
            }
        }
        progress(1)
    }

    // MARK: - 影片 → GIF／畫面

    static func videoToGIF(_ input: URL, options: ConversionOptions, workDir: URL,
                           progress: @escaping @Sendable (Double) -> Void) async throws -> [URL] {
        let asset = AVURLAsset(url: input)
        guard try await !asset.loadTracks(withMediaType: .video).isEmpty else { throw ConversionError.noVideoTrack }
        let duration = try await asset.load(.duration).seconds
        let fps = Double(max(1, min(30, options.gifFramesPerSecond)))
        let length = max(0.1, min(duration.isFinite ? duration : 0, options.gifMaxDuration))
        let count = max(1, min(900, Int((length * fps).rounded(.down))))
        let generator = AVAssetImageGenerator(asset: asset)
        generator.appliesPreferredTrackTransform = true
        generator.maximumSize = CGSize(width: options.gifMaxWidth, height: options.gifMaxWidth * 4)
        let tolerance = CMTime(seconds: 0.5 / fps, preferredTimescale: 600)
        generator.requestedTimeToleranceBefore = tolerance
        generator.requestedTimeToleranceAfter = tolerance

        let out = workDir.appendingPathComponent(CVFileNaming.fileName(CVFileNaming.baseName(of: input), "gif"))
        guard let dest = CGImageDestinationCreateWithURL(out as CFURL, UTType.gif.identifier as CFString, count, nil) else {
            throw ConversionError.writeFailed("無法建立 GIF。")
        }
        CGImageDestinationSetProperties(dest, [kCGImagePropertyGIFDictionary: [kCGImagePropertyGIFLoopCount: 0]] as CFDictionary)
        let frameProps = [kCGImagePropertyGIFDictionary: [kCGImagePropertyGIFDelayTime: 1.0 / fps,
                                                          kCGImagePropertyGIFUnclampedDelayTime: 1.0 / fps]] as CFDictionary
        for i in 0..<count {
            try Task.checkCancellation()
            let time = CMTime(seconds: Double(i) / fps, preferredTimescale: 600)
            let image: CGImage
            do { image = try await generator.image(at: time).image } catch {
                if i == 0 { throw ConversionError.unreadable("無法擷取影片畫面（\(error.localizedDescription)）。") }
                break
            }
            CGImageDestinationAddImage(dest, image, frameProps)
            progress(Double(i + 1) / Double(count) * 0.95)
        }
        guard CGImageDestinationFinalize(dest) else { throw ConversionError.writeFailed("GIF 編碼失敗。") }
        progress(1)
        return [out]
    }

    static func videoFrame(_ input: URL, format: ConversionFormat, options: ConversionOptions, workDir: URL) async throws -> [URL] {
        let asset = AVURLAsset(url: input)
        guard try await !asset.loadTracks(withMediaType: .video).isEmpty else { throw ConversionError.noVideoTrack }
        let duration = try await asset.load(.duration).seconds
        let requested = options.frameTime ?? min(1.0, (duration.isFinite ? duration : 0) / 2)
        let t = max(0, min(requested, max(0, duration - 0.05)))
        let generator = AVAssetImageGenerator(asset: asset)
        generator.appliesPreferredTrackTransform = true
        generator.requestedTimeToleranceBefore = .zero
        generator.requestedTimeToleranceAfter = .zero
        if let maxSide = options.imageMaxPixelSize { generator.maximumSize = CGSize(width: maxSide, height: maxSide) }
        let image: CGImage
        do { image = try await generator.image(at: CMTime(seconds: t, preferredTimescale: 600)).image } catch {
            throw ConversionError.unreadable("無法擷取影片畫面（\(error.localizedDescription)）。")
        }
        let label = String(format: "%.1f", t).replacingOccurrences(of: ".0", with: "")
        let out = workDir.appendingPathComponent(CVFileNaming.fileName("\(CVFileNaming.baseName(of: input))-\(label)s", format.fileExtension))
        try CVImageConverter.write(image, to: out, formatID: format.id, quality: options.imageQuality)
        return [out]
    }

    // MARK: - GIF 動畫 → 影片

    static func animatedImageToVideo(_ input: URL, to format: ConversionFormat, options: ConversionOptions, workDir: URL,
                                     progress: @escaping @Sendable (Double) -> Void) async throws -> [URL] {
        guard let src = CGImageSourceCreateWithURL(input as CFURL, nil) else { throw ConversionError.unreadable("無法開啟動畫圖片。") }
        let count = CGImageSourceGetCount(src)
        guard count > 0 else { throw ConversionError.emptyContent("動畫沒有任何影格。") }
        let first = try CVImageConverter.loadImage(input, index: 0, maxPixelSize: options.imageMaxPixelSize ?? 1920)
        let width = Int(evenFloor(CGFloat(first.image.width)))
        let height = Int(evenFloor(CGFloat(first.image.height)))
        let fileType: AVFileType = format.fileExtension == "mov" ? .mov : .mp4
        let codec: AVVideoCodecType = format.id.contains("hevc") ? .hevc : .h264
        let out = workDir.appendingPathComponent(CVFileNaming.fileName(CVFileNaming.baseName(of: input), format.fileExtension))
        let writer: AVAssetWriter
        do { writer = try AVAssetWriter(outputURL: out, fileType: fileType) } catch { throw ConversionError.writeFailed(error.localizedDescription) }
        let writerInput = AVAssetWriterInput(mediaType: .video, outputSettings: [
            AVVideoCodecKey: codec, AVVideoWidthKey: width, AVVideoHeightKey: height,
        ])
        writerInput.expectsMediaDataInRealTime = false
        let adaptor = AVAssetWriterInputPixelBufferAdaptor(assetWriterInput: writerInput, sourcePixelBufferAttributes: [
            kCVPixelBufferPixelFormatTypeKey as String: kCVPixelFormatType_32BGRA,
            kCVPixelBufferWidthKey as String: width, kCVPixelBufferHeightKey as String: height,
        ])
        guard writer.canAdd(writerInput) else { throw ConversionError.writeFailed("無法建立影片寫入器。") }
        writer.add(writerInput)
        guard writer.startWriting() else { throw ConversionError.writeFailed(writer.error?.localizedDescription ?? "寫入失敗") }
        writer.startSession(atSourceTime: .zero)
        var time = 0.0
        for i in 0..<count {
            if Task.isCancelled { writer.cancelWriting(); throw ConversionError.cancelled }
            let frame = i == 0 ? first : try CVImageConverter.loadImage(input, index: i, maxPixelSize: options.imageMaxPixelSize ?? 1920)
            let delay = max(0.02, CVImageConverter.frameDelay(frame.properties) ?? 0.1)
            guard let pool = adaptor.pixelBufferPool else { throw ConversionError.writeFailed("無法配置影格緩衝區。") }
            var pb: CVPixelBuffer?
            CVPixelBufferPoolCreatePixelBuffer(nil, pool, &pb)
            guard let pixelBuffer = pb else { throw ConversionError.writeFailed("無法配置影格緩衝區。") }
            CVPixelBufferLockBaseAddress(pixelBuffer, [])
            if let ctx = CGContext(data: CVPixelBufferGetBaseAddress(pixelBuffer), width: width, height: height, bitsPerComponent: 8,
                                   bytesPerRow: CVPixelBufferGetBytesPerRow(pixelBuffer), space: CGColorSpace(name: CGColorSpace.sRGB)!,
                                   bitmapInfo: CGImageAlphaInfo.premultipliedFirst.rawValue | CGBitmapInfo.byteOrder32Little.rawValue) {
                ctx.setFillColor(CGColor(gray: 1, alpha: 1))
                ctx.fill(CGRect(x: 0, y: 0, width: width, height: height))
                ctx.interpolationQuality = .high
                ctx.draw(frame.image, in: CGRect(x: 0, y: 0, width: width, height: height))
            }
            CVPixelBufferUnlockBaseAddress(pixelBuffer, [])
            while !writerInput.isReadyForMoreMediaData {
                try await Task.sleep(nanoseconds: 2_000_000)
            }
            if !adaptor.append(pixelBuffer, withPresentationTime: CMTime(seconds: time, preferredTimescale: 6000)) {
                throw ConversionError.writeFailed(writer.error?.localizedDescription ?? "影格寫入失敗")
            }
            time += delay
            progress(Double(i + 1) / Double(count) * 0.95)
        }
        writerInput.markAsFinished()
        writer.endSession(atSourceTime: CMTime(seconds: time, preferredTimescale: 6000))
        await writer.finishWriting()
        guard writer.status == .completed else { throw ConversionError.writeFailed(writer.error?.localizedDescription ?? "影片寫入失敗") }
        progress(1)
        return [out]
    }

    /// 影片是否真的有影像軌（.mp4 也可能只有聲音）。
    static func hasVideoTrack(_ url: URL) async -> Bool {
        let asset = AVURLAsset(url: url)
        return ((try? await asset.loadTracks(withMediaType: .video)) ?? []).isEmpty == false
    }

    static func hasAudioTrack(_ url: URL) async -> Bool {
        let asset = AVURLAsset(url: url)
        return ((try? await asset.loadTracks(withMediaType: .audio)) ?? []).isEmpty == false
    }

    static func duration(_ url: URL) async -> Double? {
        let asset = AVURLAsset(url: url)
        guard let d = try? await asset.load(.duration).seconds, d.isFinite, d > 0 else { return nil }
        return d
    }
}

private final class CVPumpState: @unchecked Sendable {
    private let lock = NSLock()
    private var cancelled = false
    private var finished = false

    var isCancelled: Bool { lock.lock(); defer { lock.unlock() }; return cancelled }
    func cancel() { lock.lock(); cancelled = true; lock.unlock() }

    /// 確保 continuation 只 resume 一次。
    func finish(_ body: () -> Void) {
        lock.lock()
        let already = finished
        finished = true
        lock.unlock()
        if !already { body() }
    }
}

private final class CVPumpObjects: @unchecked Sendable {
    let reader: AVAssetReader
    let output: AVAssetReaderOutput
    let input: AVAssetWriterInput
    init(reader: AVAssetReader, output: AVAssetReaderOutput, input: AVAssetWriterInput) {
        self.reader = reader
        self.output = output
        self.input = input
    }
}

private final class CVExportBox: @unchecked Sendable {
    let session: AVAssetExportSession
    init(_ session: AVAssetExportSession) { self.session = session }
}
