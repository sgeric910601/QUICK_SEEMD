import SwiftUI

// 影片轉 GIF、擷取畫面的工作階段與選項面板。

// MARK: - 影片轉 GIF

final class MTGifSession: ToolSession {
    @Published var fps = 10
    @Published var width = 480
    @Published var start: Double = 0
    @Published var length: Double = 5
    @Published var loop = true
    @Published var color = MTGifColor.standard
    let loader = MTInfoLoader()

    var options: MTGifOptions {
        MTGifOptions(fps: fps, width: width, start: start, duration: length, loop: loop, color: color)
    }

    override func filesDidChange(_ files: [URL]) { loader.load(files) }
    override func optionsView() -> AnyView { AnyView(MTGifOptionsView(session: self, loader: loader)) }
    override func validate(_ files: [URL]) -> String? {
        if files.count == 1, let d = loader.current.first?.duration, start >= d - 0.1 { return "開始時間超過影片長度" }
        return length < 0.2 ? "長度至少 0.2 秒" : nil
    }
    override func makeJob() -> ToolJob { Self.job(options) }

    nonisolated static func job(_ options: MTGifOptions) -> ToolJob {
        MTWork.perFile { file, context in try await MTFrameOps.gif(file, options: options, context: context) }
    }
}

private struct MTGifOptionsView: View {
    @ObservedObject var session: MTGifSession
    @ObservedObject var loader: MTInfoLoader

    private var single: MTMediaInfo? { loader.current.count == 1 ? loader.current.first : nil }

    var body: some View {
        let duration = single?.duration
        let maxStart = max(0.1, (duration ?? 60) - 0.2)
        let maxLength = max(1, min(30, (duration ?? 30) - min(session.start, maxStart)))
        VStack(alignment: .leading, spacing: 20) {
            MTSourceSummary(infos: loader.current, fileCount: loader.fileCount, loading: loader.loading)
            OptionGroup(title: "片段", systemImage: "timeline.selection") {
                OptionSlider(title: "開始", value: $session.start, range: 0...maxStart, step: 0.1, format: { MTTime.format($0) })
                OptionSlider(title: "長度", value: Binding(get: { min(session.length, maxLength) }, set: { session.length = $0 }),
                             range: 0.5...maxLength, step: 0.5, format: { String(format: "%.1f 秒", $0) })
            }
            OptionGroup(title: "每秒影格（流暢度）", systemImage: "film.stack") {
                ChoiceChips(selection: $session.fps, choices: [5, 10, 15, 20].map {
                    OptionChoice(value: $0, title: "\($0) fps", subtitle: $0 >= 15 ? "較流暢、檔案較大" : "檔案較小")
                })
            }
            OptionGroup(title: "寬度", systemImage: "arrow.left.and.right") {
                ChoiceChips(selection: $session.width, choices: [320, 480, 640, 0].map {
                    OptionChoice(value: $0, title: $0 == 0 ? "原始" : "\($0) px")
                })
            }
            OptionGroup(title: "色彩", systemImage: "paintpalette") {
                ChoiceCards(selection: $session.color, choices: MTGifColor.allCases.map {
                    OptionChoice(value: $0, title: $0.title, subtitle: $0.subtitle)
                })
            }
            OptionToggle(title: "循環播放", subtitle: session.loop ? "播完自動重頭開始" : "只播放一次", isOn: $session.loop)
            OptionNote(text: summary)
        }
    }

    private var summary: String {
        let count = session.options.frameCount(videoDuration: single?.duration)
        var text = "共 \(count) 格"
        if let info = single, info.displaySize.width > 0 {
            let w = session.width == 0 ? info.displaySize.width : min(CGFloat(session.width), info.displaySize.width)
            let h = (info.displaySize.height * w / info.displaySize.width).rounded()
            text += "，輸出 \(Int(w))×\(Int(h))"
        }
        return text + "。GIF 最多 256 色，長片段或大尺寸的檔案會很大。"
    }
}

// MARK: - 擷取畫面

final class MTFramesSession: ToolSession {
    @Published var mode = MTFrameMode.count
    @Published var timesText = "0:01"
    @Published var interval: Double = 5
    @Published var count: Double = 10
    @Published var format = MTImageFormat.jpg
    @Published var maxSide = 0
    let loader = MTInfoLoader()

    var options: MTFrameOptions {
        MTFrameOptions(mode: mode, timesText: timesText, interval: interval, count: Int(count), format: format, maxSide: maxSide)
    }

    override func filesDidChange(_ files: [URL]) { loader.load(files) }
    override func optionsView() -> AnyView { AnyView(MTFramesOptionsView(session: self, loader: loader)) }
    override func validate(_ files: [URL]) -> String? {
        if mode == .times && MTFrameOptions.parseTimes(timesText) == nil { return "時間點格式不正確，例如 0:05, 1:30" }
        return nil
    }
    override func makeJob() -> ToolJob { Self.job(options) }

    nonisolated static func job(_ options: MTFrameOptions) -> ToolJob {
        MTWork.perFile { file, context in try await MTFrameOps.frames(file, options: options, context: context) }
    }
}

private struct MTFramesOptionsView: View {
    @ObservedObject var session: MTFramesSession
    @ObservedObject var loader: MTInfoLoader

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            MTSourceSummary(infos: loader.current, fileCount: loader.fileCount, loading: loader.loading)
            OptionGroup(title: "擷取方式", systemImage: "camera.viewfinder") {
                ChoiceCards(selection: $session.mode, choices: MTFrameMode.allCases.map {
                    OptionChoice(value: $0, title: $0.title, subtitle: $0.subtitle, systemImage: $0.symbol)
                })
                switch session.mode {
                case .times:
                    OptionTextField(title: "時間點", text: $session.timesText, placeholder: "0:05, 1:30, 2:15.5")
                    if MTFrameOptions.parseTimes(session.timesText) == nil {
                        Label("格式不正確，請用逗號分隔，例如 0:05, 1:30", systemImage: "exclamationmark.triangle.fill")
                            .font(.caption).foregroundStyle(.orange)
                    }
                case .interval:
                    ChoiceChips(selection: $session.interval, choices: [1.0, 2, 5, 10, 30, 60].map {
                        OptionChoice(value: $0, title: "\(Int($0)) 秒")
                    })
                case .count:
                    OptionSlider(title: "張數", value: $session.count, range: 2...60, step: 1, format: { "\(Int($0)) 張" })
                }
            }
            OptionGroup(title: "圖片格式", systemImage: "photo") {
                SegmentedOption(selection: $session.format, choices: [
                    OptionChoice(value: MTImageFormat.jpg, title: "JPG（檔案小）"),
                    OptionChoice(value: MTImageFormat.png, title: "PNG（無損）"),
                ])
            }
            OptionGroup(title: "圖片大小（最長邊）", systemImage: "arrow.up.left.and.arrow.down.right") {
                ChoiceChips(selection: $session.maxSide, choices: [0, 1920, 1280, 640].map {
                    OptionChoice(value: $0, title: $0 == 0 ? "原始" : "\($0) px")
                })
            }
            OptionNote(text: summary)
        }
    }

    private var summary: String {
        guard loader.current.count == 1, let info = loader.current.first else {
            return "每個影片擷取多張時，會存成一個「影片名稱-畫面」資料夾。"
        }
        let n = session.options.times(duration: info.duration)?.count ?? 0
        if n == 0 { return "請輸入正確的時間點。" }
        return n > 1 ? "將擷取 \(n) 張，存成「\(CVFileNaming.baseName(of: info.url))-畫面」資料夾。" : "將擷取 1 張圖片。"
    }
}
