import SwiftUI

// 擷取音訊、轉換音訊格式、剪輯音訊的背景工作，以及前兩者的選項面板。

enum MTAudioOps {
    static func extract(_ file: URL, output: MTAudioOutput, context: ToolContext) async throws -> [URL] {
        let info = try await MTMediaProbe.info(file)
        try info.requireAudio()
        return try await MTWork.withWorkDirectory(context) { dir in
            let out = dir.appendingPathComponent("audio.\(output.format.ext)")
            let message = "擷取音訊（\(output.format.title)）…"
            context.progress(0, message)
            try await MTAudioEngine.render(file, info: info, output: output, to: out, progress: MTWork.progress(context, to: 0.98, message))
            return [try MTWork.deliver(context, out, as: MTWork.outputName(context, for: file, tag: nil, ext: output.format.ext), for: file,
                                       summary: "已擷取 \(MTTime.short(info.duration)) 的音訊，存成 \(output.format.title)")]
        }
    }

    static func convert(_ file: URL, output: MTAudioOutput, context: ToolContext) async throws -> [URL] {
        let info = try await MTMediaProbe.info(file)
        try info.requireAudio()
        return try await MTWork.withWorkDirectory(context) { dir in
            let out = dir.appendingPathComponent("audio.\(output.format.ext)")
            let message = "轉換成 \(output.format.title)…"
            context.progress(0, message)
            try await MTAudioEngine.render(file, info: info, output: output, to: out, progress: MTWork.progress(context, to: 0.98, message))
            let sizes = "\(ToolFiles.formattedSize(info.fileSize)) → \(ToolFiles.formattedSize(ToolFiles.size(of: out)))"
            return [try MTWork.deliver(context, out, as: MTWork.outputName(context, for: file, tag: nil, ext: output.format.ext), for: file,
                                       summary: "已轉成 \(output.format.title)：\(sizes)", appendSize: false)]
        }
    }

    static func trim(_ file: URL, start: Double, end: Double?, keep: Bool, fadeIn: Double, fadeOut: Double,
                     context: ToolContext) async throws -> [URL] {
        let info = try await MTMediaProbe.info(file)
        try info.requireAudio()
        let name = file.lastPathComponent
        let s = max(0, min(start, info.duration))
        let e = min(end ?? info.duration, info.duration)
        guard e - s >= 0.1 else { throw MTMediaError.tooShort(name) }
        var segments: [ClosedRange<Double>] = []
        if keep { segments = [s...e] } else {
            if s > 0.01 { segments.append(0...s) }
            if e < info.duration - 0.01 { segments.append(e...info.duration) }
        }
        guard !segments.isEmpty else { throw ConversionError.other("「\(name)」不能刪除整段內容。") }
        let format = MTAudioFormat.matching(info)
        var output = MTAudioOutput(format: format)
        output.bitrate = format == .mp3 ? max(128_000, Int(info.audioBitrate / 32_000) * 32_000) : MTBitrate.aacMatching(info, fallback: 256_000)
        return try await MTWork.withWorkDirectory(context) { dir in
            let out = dir.appendingPathComponent("trimmed.\(format.ext)")
            let message = "剪輯音訊…"
            context.progress(0, message)
            try await MTAudioEngine.render(file, info: info, segments: segments, fadeIn: fadeIn, fadeOut: fadeOut, output: output, to: out,
                                           progress: MTWork.progress(context, to: 0.98, message))
            let total = segments.reduce(0) { $0 + ($1.upperBound - $1.lowerBound) }
            let summary = "已剪輯音訊，長度 \(MTTime.format(total))" + (fadeIn > 0 || fadeOut > 0 ? "，含淡入淡出" : "")
            return [try MTWork.deliver(context, out, as: context.outputName(for: file, tag: "剪輯", ext: format.ext), for: file, summary: summary)]
        }
    }
}

/// 擷取音訊與轉換音訊共用的選項。
final class MTAudioOutputSession: ToolSession {
    enum Kind { case extract, convert }
    let kind: Kind
    @Published var format = MTAudioFormat.aac
    @Published var bitrate = 192_000
    @Published var sampleRate: Double = 0
    @Published var channels = 0
    let loader = MTInfoLoader()

    init(kind: Kind) {
        self.kind = kind
        super.init()
        if kind == .convert { bitrate = 256_000 }
    }

    var output: MTAudioOutput {
        MTAudioOutput(format: format, bitrate: bitrate, sampleRate: sampleRate > 0 ? sampleRate : nil, channels: channels > 0 ? channels : nil)
    }

    override func filesDidChange(_ files: [URL]) { loader.load(files) }
    override func optionsView() -> AnyView { AnyView(MTAudioOutputOptionsView(session: self, loader: loader)) }
    override var actionTitleOverride: String? { kind == .extract ? "擷取成 \(format.title)" : "轉成 \(format.title)" }
    override func validate(_ files: [URL]) -> String? {
        format.needsFFmpeg && !CVExternalTools.isAvailable(.ffmpeg) ? "\(format.title) 需要先安裝 ffmpeg" : nil
    }
    override func makeJob() -> ToolJob { kind == .extract ? Self.extractJob(output) : Self.convertJob(output) }

    nonisolated static func extractJob(_ output: MTAudioOutput) -> ToolJob {
        MTWork.perFile { file, context in try await MTAudioOps.extract(file, output: output, context: context) }
    }

    nonisolated static func convertJob(_ output: MTAudioOutput) -> ToolJob {
        MTWork.perFile { file, context in try await MTAudioOps.convert(file, output: output, context: context) }
    }
}

private struct MTAudioOutputOptionsView: View {
    @ObservedObject var session: MTAudioOutputSession
    @ObservedObject var loader: MTInfoLoader
    private let ffmpeg = CVExternalTools.isAvailable(.ffmpeg)

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            MTSourceSummary(infos: loader.current, fileCount: loader.fileCount, loading: loader.loading)
            OptionGroup(title: "輸出格式", systemImage: "music.note") {
                ChoiceChips(selection: $session.format, choices: MTAudioFormat.available.map {
                    OptionChoice(value: $0, title: $0.title, subtitle: $0.subtitle)
                })
                Text(session.format.subtitle).font(.caption).foregroundStyle(.secondary)
                if !ffmpeg {
                    Text("MP3、OGG 需要自行安裝 ffmpeg（brew install ffmpeg）後才會出現。").font(.caption).foregroundStyle(.secondary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            if session.format.usesBitrate {
                OptionGroup(title: "位元率（音質）", systemImage: "dial.medium") {
                    ChoiceChips(selection: $session.bitrate, choices: [96_000, 128_000, 192_000, 256_000, 320_000].map {
                        OptionChoice(value: $0, title: "\($0 / 1000) kbps", subtitle: Self.bitrateHint($0))
                    })
                    Text(Self.bitrateHint(session.bitrate)).font(.caption).foregroundStyle(.secondary)
                }
            }
            if session.kind == .convert {
                OptionGroup(title: "取樣率", systemImage: "waveform") {
                    SegmentedOption(selection: $session.sampleRate, choices: [
                        OptionChoice(value: 0.0, title: "原始"),
                        OptionChoice(value: 44_100.0, title: "44.1 kHz"),
                        OptionChoice(value: 48_000.0, title: "48 kHz"),
                    ])
                }
                OptionGroup(title: "聲道", systemImage: "hifispeaker.2") {
                    SegmentedOption(selection: $session.channels, choices: [
                        OptionChoice(value: 0, title: "原始"),
                        OptionChoice(value: 1, title: "單聲道"),
                        OptionChoice(value: 2, title: "立體聲"),
                    ])
                }
            }
            if let info = loader.current.first, loader.current.count == 1, info.duration > 0, session.format.usesBitrate {
                let bytes = Int64(Double(session.bitrate) * info.duration / 8)
                OptionNote(text: "預估約 \(ToolFiles.formattedSize(bytes))")
            }
        }
    }

    static func bitrateHint(_ bps: Int) -> String {
        switch bps {
        case ..<128_000: return "適合語音、Podcast"
        case ..<192_000: return "一般音樂"
        case ..<256_000: return "高音質（建議）"
        case ..<320_000: return "非常高音質"
        default: return "最高音質"
        }
    }
}
