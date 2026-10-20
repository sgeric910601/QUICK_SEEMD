import AVFoundation
import AVKit
import SwiftUI

// 影音工具共用的介面元件：來源資訊、時間輸入、時間軸（雙把手範圍）、播放器。

// MARK: - 來源資訊

/// 選項面板上方的來源摘要（解析度、長度、編碼、大小）。
struct MTSourceSummary: View {
    let infos: [MTMediaInfo]
    var fileCount: Int
    var loading: Bool = false

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: icon).foregroundStyle(.secondary).frame(width: 16)
            Text(text).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            Spacer(minLength: 0)
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 8).fill(Color.primary.opacity(0.05)))
    }

    private var icon: String { infos.first?.hasVideo == false ? "waveform" : "film" }

    private var text: String {
        if fileCount == 0 { return "尚未加入檔案" }
        if infos.isEmpty { return loading ? "正在讀取影片資訊…" : "\(fileCount) 個檔案" }
        if fileCount == 1, let i = infos.first { return i.summary }
        let total = infos.reduce(0) { $0 + $1.duration }
        let size = infos.reduce(Int64(0)) { $0 + $1.fileSize }
        return "\(fileCount) 個檔案 · 共 \(MTTime.short(total)) · \(ToolFiles.formattedSize(size))"
    }
}

/// 非同步讀取多個檔案的媒體資訊（選項面板用）。
@MainActor
final class MTInfoLoader: ObservableObject {
    @Published private(set) var infos: [URL: MTMediaInfo] = [:]
    @Published private(set) var loading = false
    private var files: [URL] = []
    private var task: Task<Void, Never>?

    func load(_ files: [URL]) {
        guard files != self.files else { return }
        self.files = files
        task?.cancel()
        let known = infos
        loading = !files.isEmpty
        task = Task { [weak self] in
            var result: [URL: MTMediaInfo] = [:]
            for url in files {
                if let k = known[url] { result[url] = k; continue }
                if let info = try? await MTMediaProbe.info(url) { result[url] = info }
                if Task.isCancelled { return }
            }
            self?.infos = result
            self?.loading = false
        }
    }

    func ordered(_ files: [URL]) -> [MTMediaInfo] { files.compactMap { infos[$0] } }
    var current: [MTMediaInfo] { ordered(files) }
    var fileCount: Int { files.count }
}

// MARK: - 時間輸入

/// 時間欄位：可輸入「1:05.5」「65」等格式；空白代表 nil（例如「到結尾」）。
struct MTTimeField: View {
    let title: String
    @Binding var seconds: Double?
    var placeholder: String = "0:00.0"
    var limit: Double? = nil
    @State private var text = ""
    @State private var invalid = false
    @FocusState private var focused: Bool

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            if !title.isEmpty { Text(title).font(.callout) }
            TextField(placeholder, text: $text)
                .textFieldStyle(.roundedBorder)
                .font(.body.monospacedDigit())
                .focused($focused)
                .onSubmit(commit)
                .onChange(of: focused) { _, now in if !now { commit() } }
                .onChange(of: seconds) { _, _ in if !focused { sync() } }
                .onAppear(perform: sync)
                .overlay(alignment: .trailing) {
                    if invalid {
                        Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.orange).padding(.trailing, 6)
                            .help("時間格式不正確，例如 1:05.5 或 65")
                    }
                }
        }
    }

    private func sync() {
        text = seconds.map { MTTime.format($0, decimals: 2) } ?? ""
        invalid = false
    }

    private func commit() {
        let trimmed = text.trimmingCharacters(in: .whitespaces)
        if trimmed.isEmpty { seconds = nil; invalid = false; return }
        guard var v = MTTime.parse(trimmed) else { invalid = true; return }
        if let limit { v = min(v, limit) }
        invalid = false
        seconds = v
        text = MTTime.format(v, decimals: 2)
    }
}

enum MTBindings {
    /// 給 MTTimeField 用的非 Optional 包裝。
    static func optional(_ b: Binding<Double>) -> Binding<Double?> {
        Binding<Double?>(get: { b.wrappedValue }, set: { b.wrappedValue = $0 ?? 0 })
    }
}

// MARK: - 時間軸（雙把手）

/// 剪輯用時間軸：背景是影片縮圖或音訊波形，左右把手調整範圍，點擊或拖曳其他位置移動播放頭。
struct MTRangeBar: View {
    @Binding var start: Double
    @Binding var end: Double
    let duration: Double
    var playhead: Double? = nil
    var thumbnails: [CGImage] = []
    var peaks: [Float] = []
    var height: CGFloat = 64
    var onScrub: (Double) -> Void = { _ in }
    var onHandle: (Double) -> Void = { _ in }
    var onDragEnded: () -> Void = {}
    @Environment(\.toolTint) private var tint
    @State private var target: Target?

    private enum Target { case start, end, playhead }
    private let handle: CGFloat = 14
    private let minGap = 0.1

    var body: some View {
        GeometryReader { geo in
            let track = max(1, geo.size.width - handle * 2)
            let x: (Double) -> CGFloat = { t in handle + CGFloat(duration > 0 ? min(1, max(0, t / duration)) : 0) * track }
            let xs = x(start), xe = x(end)
            ZStack(alignment: .topLeading) {
                background(width: track)
                    .frame(width: track, height: height)
                    .clipShape(RoundedRectangle(cornerRadius: 6, style: .continuous))
                    .offset(x: handle)
                // 選取範圍外變暗
                Rectangle().fill(Color.black.opacity(0.5))
                    .frame(width: max(0, xs - handle), height: height).offset(x: handle)
                Rectangle().fill(Color.black.opacity(0.5))
                    .frame(width: max(0, handle + track - xe), height: height).offset(x: xe)
                // 選取框與把手
                RoundedRectangle(cornerRadius: 4, style: .continuous)
                    .strokeBorder(tint, lineWidth: 3)
                    .frame(width: max(6, xe - xs + handle * 2), height: height)
                    .offset(x: xs - handle)
                grip(leading: true).offset(x: xs - handle)
                grip(leading: false).offset(x: xe)
                if let p = playhead, duration > 0 {
                    Capsule().fill(Color.white)
                        .frame(width: 3, height: height + 10)
                        .shadow(color: .black.opacity(0.5), radius: 1.5)
                        .offset(x: x(p) - 1.5, y: -5)
                }
            }
            .frame(width: geo.size.width, height: height, alignment: .topLeading)
            .contentShape(Rectangle())
            .gesture(
                DragGesture(minimumDistance: 0)
                    .onChanged { value in
                        guard duration > 0 else { return }
                        if target == nil { target = pick(value.startLocation.x, xs: xs, xe: xe) }
                        let t = Double((value.location.x - handle) / track) * duration
                        switch target {
                        case .start:
                            start = min(max(0, t), max(0, end - minGap))
                            onHandle(start)
                        case .end:
                            end = max(min(duration, t), min(duration, start + minGap))
                            onHandle(end)
                        default:
                            onScrub(min(duration, max(0, t)))
                        }
                    }
                    .onEnded { _ in
                        target = nil
                        onDragEnded()
                    }
            )
        }
        .frame(height: height)
        .accessibilityElement()
        .accessibilityLabel("剪輯範圍 \(MTTime.format(start)) 到 \(MTTime.format(end))")
    }

    private func pick(_ x: CGFloat, xs: CGFloat, xe: CGFloat) -> Target {
        let ds = abs(x - (xs - handle / 2)), de = abs(x - (xe + handle / 2))
        if min(ds, de) <= handle + 4 { return ds <= de ? .start : .end }
        return .playhead
    }

    private func grip(leading: Bool) -> some View {
        UnevenRoundedRectangle(topLeadingRadius: leading ? 5 : 0, bottomLeadingRadius: leading ? 5 : 0,
                               bottomTrailingRadius: leading ? 0 : 5, topTrailingRadius: leading ? 0 : 5, style: .continuous)
            .fill(tint)
            .frame(width: handle, height: height)
            .overlay(Capsule().fill(Color.white.opacity(0.9)).frame(width: 2.5, height: height * 0.36))
    }

    @ViewBuilder
    private func background(width: CGFloat) -> some View {
        if !thumbnails.isEmpty {
            HStack(spacing: 0) {
                ForEach(thumbnails.indices, id: \.self) { i in
                    Image(decorative: thumbnails[i], scale: 1)
                        .resizable()
                        .aspectRatio(contentMode: .fill)
                        .frame(width: width / CGFloat(thumbnails.count), height: height)
                        .clipped()
                }
            }
        } else if !peaks.isEmpty {
            Canvas { ctx, size in
                let bar = size.width / CGFloat(peaks.count)
                for (i, p) in peaks.enumerated() {
                    let h = max(1.5, CGFloat(p) * (size.height - 8))
                    let rect = CGRect(x: CGFloat(i) * bar, y: (size.height - h) / 2, width: max(1, bar - 1), height: h)
                    ctx.fill(Path(roundedRect: rect, cornerRadius: 1), with: .color(tint.opacity(0.85)))
                }
            }
            .background(tint.opacity(0.10))
        } else {
            Rectangle().fill(Color.primary.opacity(0.08))
        }
    }
}

// MARK: - 播放器

@MainActor
final class MTPlayerModel: ObservableObject {
    let url: URL
    let player: AVPlayer
    @Published private(set) var time: Double = 0
    @Published private(set) var isPlaying = false
    private var stopAt: Double?
    private var observer: Any?

    init(url: URL) {
        self.url = url
        player = AVPlayer(url: url)
        player.actionAtItemEnd = .pause
        observer = player.addPeriodicTimeObserver(forInterval: CMTime(value: 1, timescale: 30), queue: .main) { [weak self] t in
            MainActor.assumeIsolated { self?.tick(t.seconds) }
        }
    }

    private func tick(_ t: Double) {
        guard t.isFinite else { return }
        time = t
        let playing = player.rate != 0
        if isPlaying != playing { isPlaying = playing }
        if let stop = stopAt, t >= stop - 0.01 {
            player.pause()
            stopAt = nil
            isPlaying = false
        }
    }

    func togglePlay() {
        if player.rate != 0 { pause() } else { stopAt = nil; player.play(); isPlaying = true }
    }

    func pause() {
        player.pause()
        isPlaying = false
        stopAt = nil
    }

    func seek(_ t: Double) {
        time = t
        player.seek(to: MTTime.cm(t), toleranceBefore: .zero, toleranceAfter: .zero)
    }

    func play(from: Double, to: Double) {
        seek(from)
        stopAt = to
        player.play()
        isPlaying = true
    }

    func stop() {
        pause()
        if let observer { player.removeTimeObserver(observer) }
        observer = nil
    }
}

/// AVPlayerView（不顯示內建控制列；由剪輯介面提供播放與拖曳）。
struct MTPlayerSurface: NSViewRepresentable {
    let player: AVPlayer

    func makeNSView(context: Context) -> AVPlayerView {
        let view = AVPlayerView()
        view.player = player
        view.controlsStyle = .none
        view.videoGravity = .resizeAspect
        view.showsFullScreenToggleButton = false
        return view
    }

    func updateNSView(_ view: AVPlayerView, context: Context) {
        if view.player !== player { view.player = player }
    }
}

// MARK: - 縮圖

enum MTThumbnailer {
    /// 時間軸用的一排縮圖。
    static func strip(_ url: URL, count: Int, height: CGFloat) async -> [CGImage] {
        let asset = AVURLAsset(url: url)
        guard let duration = try? await asset.load(.duration).seconds, duration > 0 else { return [] }
        let generator = AVAssetImageGenerator(asset: asset)
        generator.appliesPreferredTrackTransform = true
        generator.maximumSize = CGSize(width: height * 4, height: height * 2)
        let tolerance = CMTime(seconds: duration / Double(count) / 2, preferredTimescale: 600)
        generator.requestedTimeToleranceBefore = tolerance
        generator.requestedTimeToleranceAfter = tolerance
        var images: [CGImage] = []
        for i in 0..<count {
            if Task.isCancelled { return [] }
            let t = duration * (Double(i) + 0.5) / Double(count)
            if let image = try? await generator.image(at: MTTime.cm(t)).image { images.append(image) }
        }
        return images
    }
}
