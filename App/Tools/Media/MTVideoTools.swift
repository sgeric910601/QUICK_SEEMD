import SwiftUI

// 影片工具的工作階段（選項）與右側選項面板。

// MARK: - 轉換影片格式

final class MTConvertSession: ToolSession {
    @Published var format = MTVideoFormat.mp4
    @Published var resolution = MTResolution.original
    @Published var keepAudio = true
    @Published var allowRemux = true
    let loader = MTInfoLoader()

    override func filesDidChange(_ files: [URL]) { loader.load(files) }
    override func optionsView() -> AnyView { AnyView(MTConvertOptionsView(session: self, loader: loader)) }
    override var actionTitleOverride: String? { "轉成 \(format.title)" }
    override func makeJob() -> ToolJob {
        Self.job(format: format, resolution: resolution, keepAudio: keepAudio, allowRemux: allowRemux)
    }

    nonisolated static func job(format: MTVideoFormat, resolution: MTResolution = .original, keepAudio: Bool = true,
                                allowRemux: Bool = true) -> ToolJob {
        MTWork.perFile { file, context in
            try await MTVideoOps.convert(file, format: format, resolution: resolution, keepAudio: keepAudio,
                                         allowRemux: allowRemux, context: context)
        }
    }
}

private struct MTConvertOptionsView: View {
    @ObservedObject var session: MTConvertSession
    @ObservedObject var loader: MTInfoLoader

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            MTSourceSummary(infos: loader.current, fileCount: loader.fileCount, loading: loader.loading)
            OptionGroup(title: "輸出格式", systemImage: "film") {
                ChoiceChips(selection: $session.format, choices: MTVideoFormat.allCases.map {
                    OptionChoice(value: $0, title: $0.title, subtitle: $0.subtitle)
                })
                Text(session.format.subtitle).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            OptionGroup(title: "解析度", systemImage: "aspectratio") {
                ChoiceChips(selection: $session.resolution, choices: MTResolution.allCases.map { OptionChoice(value: $0, title: $0.title) })
                Text("以短邊計算，只會縮小、不會放大；直式影片會保持直式。").font(.caption).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            OptionGroup(title: "聲音與速度", systemImage: "speaker.wave.2") {
                OptionToggle(title: "保留聲音", subtitle: "關閉時輸出無聲影片", isOn: $session.keepAudio)
                OptionToggle(title: "可行時不重新編碼", subtitle: "編碼相同時直接轉封裝，速度快、畫質無損",
                             isOn: $session.allowRemux)
            }
        }
    }
}

// MARK: - 壓縮影片

final class MTCompressSession: ToolSession {
    @Published var level = MTCompressLevel.balanced
    @Published var codec = MTVideoCodec.h264
    @Published var useTarget = false
    @Published var targetText = "25"
    let loader = MTInfoLoader()

    var targetMB: Double? {
        guard useTarget else { return nil }
        return Double(targetText.trimmingCharacters(in: .whitespaces).replacingOccurrences(of: ",", with: "."))
    }

    var options: MTCompressOptions { MTCompressOptions(level: level, codec: codec, targetMB: targetMB) }

    override func filesDidChange(_ files: [URL]) { loader.load(files) }
    override func optionsView() -> AnyView { AnyView(MTCompressOptionsView(session: self, loader: loader)) }

    override func validate(_ files: [URL]) -> String? {
        guard useTarget else { return nil }
        guard let mb = targetMB, mb > 0 else { return "請輸入目標大小（MB）" }
        if files.count == 1, let info = loader.current.first {
            let plan = MTVideoOps.compressPlan(info, options)
            if plan.estimatedBytes == 0, let problem = plan.problem { return problem }
        }
        return nil
    }

    override func makeJob() -> ToolJob { Self.job(options) }

    nonisolated static func job(_ options: MTCompressOptions) -> ToolJob {
        ToolJob { files, context in
            var outputs: [URL] = []
            var kept: [String] = []
            var totalIn: Int64 = 0, totalOut: Int64 = 0
            for (i, file) in files.enumerated() {
                try context.checkCancellation()
                let sub = context.segment(i, of: files.count)
                sub.progress(0, "壓縮中：\(file.lastPathComponent)")
                if let out = try await MTVideoOps.compress(file, options: options, context: sub) {
                    outputs.append(out)
                    totalIn += ToolFiles.size(of: file)
                    totalOut += ToolFiles.size(of: out)
                } else {
                    kept.append(file.lastPathComponent)
                }
                sub.progress(1)
            }
            if outputs.isEmpty, !kept.isEmpty {
                let names = kept.count == 1 ? "「\(kept[0])」" : "這 \(kept.count) 個影片"
                if let mb = options.targetMB {
                    throw ConversionError.other("\(names)已經小於目標大小（\(String(format: "%g", mb)) MB）或無法再明顯變小，因此保留原檔、沒有產生新檔案。")
                }
                throw ConversionError.other("\(names)已經很精簡，壓縮後沒有明顯變小（不到 5%），因此保留原檔、沒有產生新檔案。可以試試「高度壓縮」、HEVC 或指定目標大小。")
            }
            let saved = totalIn > 0 ? Int((1 - Double(totalOut) / Double(totalIn)) * 100) : 0
            var summary = (outputs.count > 1 ? "\(outputs.count) 個影片" : "") +
                "從 \(ToolFiles.formattedSize(totalIn)) 縮小到 \(ToolFiles.formattedSize(totalOut))，省下 \(saved)%"
            if !kept.isEmpty { summary += "；\(kept.count) 個影片沒有明顯變小，已保留原檔" }
            context.progress(1, summary)
            return outputs
        }
    }
}

private struct MTCompressOptionsView: View {
    @ObservedObject var session: MTCompressSession
    @ObservedObject var loader: MTInfoLoader
    @Environment(\.toolTint) private var tint

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            MTSourceSummary(infos: loader.current, fileCount: loader.fileCount, loading: loader.loading)
            OptionGroup(title: "壓縮程度", systemImage: "gauge.with.dots.needle.50percent") {
                ChoiceCards(selection: $session.level, choices: MTCompressLevel.allCases.map {
                    OptionChoice(value: $0, title: $0.title, subtitle: $0.subtitle, systemImage: $0.symbol,
                                 badge: $0 == .balanced ? "推薦" : nil)
                })
                .disabled(session.useTarget)
                .opacity(session.useTarget ? 0.5 : 1)
            }
            estimate
            OptionGroup(title: "影片編碼", systemImage: "cpu") {
                SegmentedOption(selection: $session.codec, choices: [
                    OptionChoice(value: MTVideoCodec.h264, title: "H.264（相容性最佳）"),
                    OptionChoice(value: MTVideoCodec.hevc, title: "HEVC（更小）"),
                ])
                Text(session.codec == .hevc ? "HEVC 檔案約再小 35%，但較舊的裝置或 Windows 可能無法播放。"
                     : "幾乎所有裝置、瀏覽器與通訊軟體都能播放。")
                    .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            OptionGroup(title: "目標大小", systemImage: "scalemass") {
                OptionToggle(title: "指定目標大小", subtitle: "自動計算位元率，必要時降低解析度", isOn: $session.useTarget)
                if session.useTarget {
                    HStack(spacing: 8) {
                        TextField("25", text: $session.targetText)
                            .textFieldStyle(.roundedBorder)
                            .frame(width: 90)
                        Text("MB").foregroundStyle(.secondary)
                        Spacer()
                        ForEach([10, 25, 50], id: \.self) { mb in
                            Button("\(mb)") { session.targetText = "\(mb)" }
                                .buttonStyle(.bordered)
                                .controlSize(.small)
                                .help("常見上限：Email 約 25 MB、Discord 免費版 10 MB")
                        }
                    }
                }
            }
        }
    }

    @ViewBuilder
    private var estimate: some View {
        let infos = loader.current
        if !infos.isEmpty {
            let plans = infos.map { MTVideoOps.compressPlan($0, session.options) }
            let input = infos.reduce(Int64(0)) { $0 + $1.fileSize }
            let output = plans.reduce(Int64(0)) { $0 + $1.estimatedBytes }
            let problem = plans.compactMap(\.problem).first
            VStack(alignment: .leading, spacing: 6) {
                HStack(alignment: .firstTextBaseline) {
                    Text("預估輸出").font(.callout).foregroundStyle(.secondary)
                    Spacer()
                    if output > 0 {
                        Text("約 \(ToolFiles.formattedSize(output))").font(.title3.weight(.bold).monospacedDigit()).foregroundStyle(tint)
                    }
                }
                if output > 0, input > 0, output < input {
                    let saving = Int((1 - Double(output) / Double(input)) * 100)
                    Text("原始 \(ToolFiles.formattedSize(input))，約可節省 \(saving)%").font(.caption).foregroundStyle(.secondary)
                }
                if infos.count == 1, let plan = plans.first, output > 0 {
                    Text(verbatim: "\(Int(plan.size.width))×\(Int(plan.size.height)) · 影像 \(Self.kbps(plan.videoBitrate))"
                         + (plan.audioBitrate > 0 ? " · 聲音 \(plan.audioBitrate / 1000) kbps" : ""))
                        .font(.caption).foregroundStyle(.secondary)
                }
                if let problem {
                    Label(problem, systemImage: "exclamationmark.triangle.fill").font(.caption).foregroundStyle(.orange)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
            .padding(12)
            .background(RoundedRectangle(cornerRadius: 10, style: .continuous).fill(tint.opacity(0.08)))
        }
    }

    static func kbps(_ bps: Int) -> String {
        bps >= 1_000_000 ? String(format: "%.1f Mbps", Double(bps) / 1_000_000) : "\(bps / 1000) kbps"
    }
}

// MARK: - 移除聲音

final class MTMuteSession: ToolSession {
    override func optionsView() -> AnyView {
        AnyView(VStack(alignment: .leading, spacing: 12) {
            OptionNote(text: "直接移除所有音軌，影像不重新編碼：畫質完全不變、速度很快。輸出檔名會加上「無聲」。",
                       systemImage: "speaker.slash")
        })
    }

    override func makeJob() -> ToolJob { Self.job() }

    nonisolated static func job() -> ToolJob {
        MTWork.perFile { file, context in try await MTVideoOps.mute(file, context: context) }
    }
}

// MARK: - 旋轉／翻轉

final class MTRotateSession: ToolSession {
    @Published var op = MTRotateOp.right
    @Published var method = MTRotateMethod.fast

    override func optionsView() -> AnyView { AnyView(MTRotateOptionsView(session: self)) }
    override var actionTitleOverride: String? { op.isFlip ? "翻轉影片" : "旋轉影片" }
    override func makeJob() -> ToolJob { Self.job(op: op, method: method) }

    nonisolated static func job(op: MTRotateOp, method: MTRotateMethod = .fast) -> ToolJob {
        MTWork.perFile { file, context in try await MTVideoOps.rotate(file, op: op, method: method, context: context) }
    }
}

private struct MTRotateOptionsView: View {
    @ObservedObject var session: MTRotateSession

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            OptionGroup(title: "方向", systemImage: "rotate.right") {
                ChoiceCards(selection: $session.op, choices: MTRotateOp.allCases.map {
                    OptionChoice(value: $0, title: $0.title, subtitle: $0.subtitle, systemImage: $0.symbol)
                })
            }
            OptionGroup(title: "處理方式", systemImage: "gearshape") {
                if session.op.isFlip {
                    OptionNote(text: "翻轉需要重新編碼影像，會花一點時間；畫質會盡量維持與原檔相同。")
                } else {
                    ChoiceCards(selection: $session.method, choices: [
                        OptionChoice(value: MTRotateMethod.fast, title: "快速（不重新編碼）",
                                     subtitle: "只改方向標記，幾秒完成、畫質無損", systemImage: "bolt"),
                        OptionChoice(value: MTRotateMethod.reencode, title: "重新編碼",
                                     subtitle: "所有播放器與網站都一定正確顯示", systemImage: "arrow.triangle.2.circlepath"),
                    ])
                }
            }
        }
    }
}

// MARK: - 調整速度

final class MTSpeedSession: ToolSession {
    static let rates: [Double] = [0.25, 0.5, 0.75, 1.25, 1.5, 2, 4]
    @Published var rate = 2.0
    @Published var keepPitch = true
    @Published var keepAudio = true
    let loader = MTInfoLoader()

    override func filesDidChange(_ files: [URL]) { loader.load(files) }
    override func optionsView() -> AnyView { AnyView(MTSpeedOptionsView(session: self, loader: loader)) }
    override func makeJob() -> ToolJob { Self.job(rate: rate, keepPitch: keepPitch, keepAudio: keepAudio) }

    nonisolated static func job(rate: Double, keepPitch: Bool = true, keepAudio: Bool = true) -> ToolJob {
        MTWork.perFile { file, context in
            try await MTVideoOps.speed(file, rate: rate, keepPitch: keepPitch, keepAudio: keepAudio, context: context)
        }
    }
}

private struct MTSpeedOptionsView: View {
    @ObservedObject var session: MTSpeedSession
    @ObservedObject var loader: MTInfoLoader

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            MTSourceSummary(infos: loader.current, fileCount: loader.fileCount, loading: loader.loading)
            OptionGroup(title: "播放速度", systemImage: "hare") {
                ChoiceChips(selection: $session.rate, choices: MTSpeedSession.rates.map {
                    OptionChoice(value: $0, title: String(format: "%g×", $0), subtitle: $0 < 1 ? "慢動作" : "快轉")
                })
                if loader.current.count == 1, let d = loader.current.first?.duration {
                    Text("長度 \(MTTime.short(d)) → \(MTTime.short(d / session.rate))").font(.caption).foregroundStyle(.secondary)
                }
            }
            OptionGroup(title: "聲音", systemImage: "speaker.wave.2") {
                OptionToggle(title: "保留聲音", isOn: $session.keepAudio)
                if session.keepAudio {
                    OptionToggle(title: "保持原本音高", subtitle: session.keepPitch ? "人聲不會變尖或變低沉" : "像錄音帶一樣，速度與音高一起改變",
                                 isOn: $session.keepPitch)
                }
            }
        }
    }
}

// MARK: - 調整尺寸

final class MTResizeSession: ToolSession {
    @Published var aspect = MTAspect.original
    @Published var resolution = MTResolution.hd
    @Published var fit = MTFitMode.letterbox
    let loader = MTInfoLoader()

    override func filesDidChange(_ files: [URL]) { loader.load(files) }
    override func optionsView() -> AnyView { AnyView(MTResizeOptionsView(session: self, loader: loader)) }
    override func validate(_ files: [URL]) -> String? {
        aspect == .original && resolution == .original ? "請選擇新的比例或解析度" : nil
    }
    override func makeJob() -> ToolJob { Self.job(aspect: aspect, resolution: resolution, fit: fit) }

    nonisolated static func job(aspect: MTAspect, resolution: MTResolution, fit: MTFitMode) -> ToolJob {
        MTWork.perFile { file, context in
            try await MTVideoOps.resize(file, aspect: aspect, resolution: resolution, fit: fit, context: context)
        }
    }
}

private struct MTResizeOptionsView: View {
    @ObservedObject var session: MTResizeSession
    @ObservedObject var loader: MTInfoLoader

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            MTSourceSummary(infos: loader.current, fileCount: loader.fileCount, loading: loader.loading)
            OptionGroup(title: "畫面比例", systemImage: "aspectratio") {
                ChoiceChips(selection: $session.aspect, choices: MTAspect.allCases.map {
                    OptionChoice(value: $0, title: $0.title, subtitle: $0.subtitle)
                })
                Text(session.aspect.subtitle).font(.caption).foregroundStyle(.secondary)
            }
            OptionGroup(title: "解析度（短邊）", systemImage: "rectangle.expand.vertical") {
                ChoiceChips(selection: $session.resolution, choices: MTResolution.allCases.map { OptionChoice(value: $0, title: $0.title) })
                if loader.current.count == 1, let info = loader.current.first {
                    let target = MTVideoOps.resizeTarget(display: info.displaySize, aspect: session.aspect, resolution: session.resolution)
                    Text(verbatim: "\(info.resolutionText) → \(Int(target.width))×\(Int(target.height))").font(.caption.monospacedDigit())
                        .foregroundStyle(.secondary)
                }
            }
            if session.aspect != .original {
                OptionGroup(title: "比例不同時", systemImage: "rectangle.center.inset.filled") {
                    ChoiceCards(selection: $session.fit, choices: [
                        OptionChoice(value: MTFitMode.letterbox, title: "完整顯示（補黑邊）", subtitle: "畫面不會被裁掉",
                                     systemImage: "rectangle.inset.filled"),
                        OptionChoice(value: MTFitMode.crop, title: "裁切填滿", subtitle: "放大到填滿畫面，邊緣會被裁掉",
                                     systemImage: "crop"),
                    ])
                }
            }
        }
    }
}

// MARK: - 合併影片

final class MTMergeSession: ToolSession {
    @Published var size = MTMergeSize.first
    @Published var codec = MTVideoCodec.h264
    let loader = MTInfoLoader()

    override func filesDidChange(_ files: [URL]) { loader.load(files) }
    override func optionsView() -> AnyView { AnyView(MTMergeOptionsView(session: self, loader: loader)) }
    override func validate(_ files: [URL]) -> String? { files.count < 2 ? "至少需要 2 個影片" : nil }
    override func makeJob() -> ToolJob { Self.job(size: size, codec: codec) }

    nonisolated static func job(size: MTMergeSize, codec: MTVideoCodec = .h264) -> ToolJob {
        ToolJob { files, context in try await MTVideoOps.merge(files, size: size, codec: codec, context: context) }
    }
}

private struct MTMergeOptionsView: View {
    @ObservedObject var session: MTMergeSession
    @ObservedObject var loader: MTInfoLoader

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            MTSourceSummary(infos: loader.current, fileCount: loader.fileCount, loading: loader.loading)
            OptionGroup(title: "輸出解析度", systemImage: "aspectratio") {
                ChoiceCards(selection: $session.size, choices: MTMergeSize.allCases.map {
                    OptionChoice(value: $0, title: $0.title, subtitle: $0.subtitle)
                })
                if let first = loader.current.first {
                    let r = MTVideoOps.mergeRenderSize(first: first.displaySize, option: session.size)
                    Text(verbatim: "輸出 \(Int(r.width))×\(Int(r.height))（MP4）").font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                }
            }
            OptionGroup(title: "影片編碼", systemImage: "cpu") {
                SegmentedOption(selection: $session.codec, choices: [
                    OptionChoice(value: MTVideoCodec.h264, title: "H.264"),
                    OptionChoice(value: MTVideoCodec.hevc, title: "HEVC"),
                ])
            }
            OptionNote(text: "依左側順序接成一個 MP4（拖曳卡片可調整順序）。尺寸或方向不同的影片會等比例縮放並補黑邊；沒有聲音的片段保持靜音。")
        }
    }
}
