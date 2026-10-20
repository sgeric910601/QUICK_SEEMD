import AVFoundation
import SwiftUI

// 剪輯影片／剪輯音訊：單一檔案時以播放器＋時間軸取代檔案縮圖區；多個檔案時在選項面板輸入時間。

enum MTTrimKeep: String, CaseIterable, Sendable {
    case selection, remove
}

final class MTTrimSession: ToolSession {
    let isAudio: Bool
    @Published var precise = false
    @Published var keep = MTTrimKeep.selection
    @Published var start: Double = 0
    @Published var end: Double?
    @Published var fadeIn = false
    @Published var fadeOut = false
    @Published private(set) var info: MTMediaInfo?
    @Published private(set) var loadError: String?
    @Published private(set) var thumbnails: [CGImage] = []
    @Published private(set) var peaks: [Float] = []
    private(set) var player: MTPlayerModel?
    private var files: [URL] = []
    private var loadTask: Task<Void, Never>?

    init(isAudio: Bool) {
        self.isAudio = isAudio
        super.init()
    }

    var tint: Color { isAudio ? ToolPalette.audio : ToolPalette.video }
    var duration: Double? { info?.duration }
    var effectiveEnd: Double { end ?? duration ?? 0 }

    override func filesDidChange(_ files: [URL]) {
        guard files != self.files else { return }
        self.files = files
        loadTask?.cancel()
        player?.stop()
        player = nil
        info = nil
        loadError = nil
        thumbnails = []
        peaks = []
        guard files.count == 1, let url = files.first else { return }
        start = 0
        end = nil
        let audio = isAudio
        loadTask = Task { [weak self] in
            do {
                let info = try await MTMediaProbe.info(url)
                guard let self, !Task.isCancelled else { return }
                self.info = info
                if self.end == nil || (self.end ?? 0) > info.duration { self.end = info.duration }
                if audio || !info.hasVideo {
                    let peaks = await MTWaveform.peaks(url, count: 280)
                    if !Task.isCancelled { self.peaks = peaks }
                } else {
                    let strip = await MTThumbnailer.strip(url, count: 12, height: 64)
                    if !Task.isCancelled { self.thumbnails = strip }
                }
            } catch {
                self?.loadError = error.localizedDescription
            }
        }
    }

    override func validate(_ files: [URL]) -> String? {
        if let e = end, e - start < 0.1 { return "結束時間必須比開始時間晚至少 0.1 秒" }
        if let d = duration, files.count == 1 {
            if start >= d { return "開始時間超過檔案長度（\(MTTime.format(d))）" }
            if keep == .remove && start <= 0.05 && effectiveEnd >= d - 0.05 { return "不能刪除整段內容" }
        }
        return nil
    }

    override var actionTitleOverride: String? { isAudio ? "剪輯音訊" : "剪輯影片" }
    override func optionsView() -> AnyView { AnyView(MTTrimOptionsView(session: self)) }

    override func customFilesView(files: Binding<[URL]>) -> AnyView? {
        guard files.wrappedValue.count == 1, let url = files.wrappedValue.first else { return nil }
        if player?.url != url {
            player?.stop()
            player = MTPlayerModel(url: url)
        }
        guard let player else { return nil }
        return AnyView(MTTrimEditor(session: self, player: player).environment(\.toolTint, tint))
    }

    override func makeJob() -> ToolJob {
        isAudio ? Self.audioJob(start: start, end: end, keep: keep, fadeIn: fadeIn ? 2 : 0, fadeOut: fadeOut ? 2 : 0)
            : Self.videoJob(start: start, end: end, keep: keep, precise: precise)
    }

    nonisolated static func videoJob(start: Double, end: Double?, keep: MTTrimKeep = .selection, precise: Bool) -> ToolJob {
        MTWork.perFile { file, context in
            try await MTVideoOps.trim(file, start: start, end: end, keep: keep == .selection, precise: precise, context: context)
        }
    }

    nonisolated static func audioJob(start: Double, end: Double?, keep: MTTrimKeep = .selection, fadeIn: Double = 0,
                                     fadeOut: Double = 0) -> ToolJob {
        MTWork.perFile { file, context in
            try await MTAudioOps.trim(file, start: start, end: end, keep: keep == .selection, fadeIn: fadeIn, fadeOut: fadeOut,
                                      context: context)
        }
    }

    // 時間軸操作

    func setStart(_ t: Double) {
        let limit = effectiveEnd - 0.1
        start = max(0, min(t, limit))
    }

    func setEnd(_ t: Double) {
        let d = duration ?? t
        end = min(d, max(t, start + 0.1))
    }
}

// MARK: - 選項面板

private struct MTTrimOptionsView: View {
    @ObservedObject var session: MTTrimSession

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            if let info = session.info {
                MTSourceSummary(infos: [info], fileCount: 1)
            }
            OptionGroup(title: "要保留的部分", systemImage: "scissors") {
                SegmentedOption(selection: $session.keep, choices: [
                    OptionChoice(value: MTTrimKeep.selection, title: "保留選取範圍"),
                    OptionChoice(value: MTTrimKeep.remove, title: "刪除選取範圍"),
                ])
                Text(session.keep == .selection ? "只留下選取的片段。" : "刪掉選取的片段，前後兩段接在一起。")
                    .font(.caption).foregroundStyle(.secondary)
            }
            if session.info != nil {
                rangeSummary
            } else {
                OptionGroup(title: "時間範圍（套用到每個檔案）", systemImage: "clock") {
                    HStack(alignment: .top, spacing: 10) {
                        MTTimeField(title: "開始", seconds: MTBindings.optional($session.start))
                        MTTimeField(title: "結束", seconds: $session.end, placeholder: "結尾")
                    }
                    Text("格式例如 1:05.5 或 65；結束留白代表到結尾。").font(.caption).foregroundStyle(.secondary)
                }
            }
            if session.isAudio {
                OptionGroup(title: "淡入淡出", systemImage: "waveform.path") {
                    OptionToggle(title: "開頭淡入", subtitle: "2 秒內由無聲漸強", isOn: $session.fadeIn)
                    OptionToggle(title: "結尾淡出", subtitle: "最後 2 秒漸弱到無聲", isOn: $session.fadeOut)
                }
                OptionNote(text: "輸出格式與原檔相同（MP3 會輸出成 M4A；若已安裝 ffmpeg 則維持 MP3）。")
            } else {
                OptionGroup(title: "剪輯方式", systemImage: "gearshape") {
                    ChoiceCards(selection: $session.precise, choices: [
                        OptionChoice(value: false, title: "快速（不重新編碼）", subtitle: "幾秒完成、畫質無損；起點會對齊到前一個關鍵影格",
                                     systemImage: "bolt"),
                        OptionChoice(value: true, title: "精確剪輯（重新編碼）", subtitle: "準確到每一格畫面，需要較長時間",
                                     systemImage: "scope"),
                    ])
                }
            }
        }
    }

    private var rangeSummary: some View {
        let length = max(0, session.effectiveEnd - session.start)
        let total = session.duration ?? 0
        let result = session.keep == .selection ? length : max(0, total - length)
        return VStack(alignment: .leading, spacing: 4) {
            Text("選取 \(MTTime.format(session.start)) – \(MTTime.format(session.effectiveEnd))")
                .font(.callout.monospacedDigit().weight(.semibold))
            Text("輸出長度約 \(MTTime.format(result))（在左側拖曳時間軸的把手調整）")
                .font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 10, style: .continuous).fill(session.tint.opacity(0.08)))
    }
}

// MARK: - 剪輯編輯器（取代檔案縮圖區）

struct MTTrimEditor: View {
    @ObservedObject var session: MTTrimSession
    @ObservedObject var player: MTPlayerModel
    @Environment(\.toolTint) private var tint

    private var duration: Double { session.duration ?? 0 }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 16) {
                header
                preview
                transport
                if duration > 0 {
                    MTRangeBar(start: Binding(get: { session.start }, set: { session.setStart($0) }),
                               end: Binding(get: { session.effectiveEnd }, set: { session.setEnd($0) }),
                               duration: duration, playhead: player.time,
                               thumbnails: session.thumbnails, peaks: session.peaks,
                               height: session.isAudio ? 84 : 64,
                               onScrub: { player.pause(); player.seek($0) },
                               onHandle: { player.pause(); player.seek($0) })
                    timeFields
                } else if let error = session.loadError {
                    Label(error, systemImage: "exclamationmark.triangle.fill").foregroundStyle(.orange)
                } else {
                    ProgressView().controlSize(.small).frame(maxWidth: .infinity)
                }
            }
            .padding(20)
            .frame(maxWidth: 900)
            .frame(maxWidth: .infinity)
        }
        .onDisappear { player.pause() }
    }

    private var header: some View {
        HStack(spacing: 10) {
            Image(systemName: session.isAudio ? "waveform" : "film").foregroundStyle(tint)
            Text(player.url.lastPathComponent).font(.headline).lineLimit(1).truncationMode(.middle)
            Spacer()
            if let info = session.info {
                Text(info.summary).font(.caption.monospacedDigit()).foregroundStyle(.secondary).lineLimit(1)
            }
        }
    }

    @ViewBuilder
    private var preview: some View {
        if session.isAudio {
            ZStack {
                RoundedRectangle(cornerRadius: 12, style: .continuous).fill(tint.opacity(0.10))
                VStack(spacing: 6) {
                    Image(systemName: player.isPlaying ? "speaker.wave.3.fill" : "music.note")
                        .font(.system(size: 34, weight: .medium)).foregroundStyle(tint)
                    Text(MTTime.format(player.time, decimals: 2)).font(.system(size: 28, weight: .bold).monospacedDigit())
                }
            }
            .frame(height: 140)
        } else {
            let size = session.info?.displaySize ?? CGSize(width: 16, height: 9)
            let ratio = size.height > 0 ? size.width / size.height : 16.0 / 9
            MTPlayerSurface(player: player.player)
                .aspectRatio(ratio, contentMode: .fit)
                .frame(maxWidth: .infinity, minHeight: 180, maxHeight: 340)
                .background(Color.black)
                .clipShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
                .onTapGesture { player.togglePlay() }
        }
    }

    private var transport: some View {
        HStack(spacing: 12) {
            Button { player.togglePlay() } label: {
                Image(systemName: player.isPlaying ? "pause.fill" : "play.fill")
                    .font(.system(size: 15, weight: .bold))
                    .foregroundStyle(.white)
                    .frame(width: 36, height: 36)
                    .background(Circle().fill(tint))
            }
            .buttonStyle(.plain)
            .keyboardShortcut(.space, modifiers: [])
            .help(player.isPlaying ? "暫停（空白鍵）" : "播放（空白鍵）")

            Button { player.play(from: session.start, to: session.effectiveEnd) } label: {
                Label("播放選取範圍", systemImage: "play.rectangle")
            }
            .buttonStyle(.bordered)

            Spacer()
            Text("\(MTTime.format(player.time, decimals: 2)) / \(MTTime.format(duration, decimals: 2))")
                .font(.callout.monospacedDigit())
                .foregroundStyle(.secondary)
        }
    }

    private var timeFields: some View {
        let length = max(0, session.effectiveEnd - session.start)
        return HStack(alignment: .bottom, spacing: 8) {
            MTTimeField(title: "開始", seconds: Binding(get: { session.start }, set: { session.setStart($0 ?? 0) }), limit: duration)
                .frame(width: 110)
            Button { session.setStart(player.time) } label: { Image(systemName: "arrow.right.to.line") }
                .help("把開始設為目前播放位置")
            Spacer(minLength: 8)
            VStack(spacing: 2) {
                Text(session.keep == .selection ? "保留長度" : "刪除長度").font(.caption).foregroundStyle(.secondary)
                Text(MTTime.format(length, decimals: 2)).font(.title3.weight(.bold).monospacedDigit()).foregroundStyle(tint)
            }
            Spacer(minLength: 8)
            Button { session.setEnd(player.time) } label: { Image(systemName: "arrow.left.to.line") }
                .help("把結束設為目前播放位置")
            MTTimeField(title: "結束", seconds: Binding(get: { session.effectiveEnd }, set: { session.setEnd($0 ?? duration) }),
                        limit: duration)
                .frame(width: 110)
        }
    }
}
