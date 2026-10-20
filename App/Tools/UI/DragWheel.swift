import AppKit
import SwiftUI
import UniformTypeIdentifiers

/// 拖曳轉換輪盤：
/// 從 Finder、桌面或任何 App 拖著檔案時「點一下」按鍵 → 游標旁出現輪盤，放開按鍵輪盤仍會留著；
/// 預設 Shift＝轉換、Command＝工具、Control＝動作；拖曳中點另一個鍵即可切換輪盤，按 Esc 取消。
/// 把檔案放到想要的項目上就開始處理，結果存在原檔旁邊；放在中間則打開 QUICK_SEEMD。
@MainActor
final class DragWheelController {
    static let shared = DragWheelController()

    enum Mode: String, CaseIterable {
        case convert, tools, actions

        var title: String {
            switch self {
            case .convert: return "轉換"
            case .tools: return "工具"
            case .actions: return "動作"
            }
        }

        var defaultKey: WheelKey {
            switch self {
            case .convert: return .shift
            case .tools: return .command
            case .actions: return .control
            }
        }

        var keySetting: String { "Wheel.key.\(rawValue)" }

        /// 目前設定的觸發按鍵
        var key: WheelKey {
            get { WheelKey(rawValue: UserDefaults.standard.string(forKey: keySetting) ?? "") ?? defaultKey }
            nonmutating set { UserDefaults.standard.set(newValue.rawValue, forKey: keySetting) }
        }
    }

    static let enabledKey = "Wheel.enabled"
    static var isEnabled: Bool {
        get { UserDefaults.standard.object(forKey: enabledKey) as? Bool ?? true }
        set { UserDefaults.standard.set(newValue, forKey: enabledKey); newValue ? shared.start() : shared.stop() }
    }

    private var monitors: [Any] = []
    private var dragCountAtMouseDown = 0
    private var panel: NSPanel?
    private var poll: Timer?
    private var dragFiles: [URL] = []
    private var previousFlags: NSEvent.ModifierFlags = []
    private var cancelledThisDrag = false
    let model = WheelModel()

    func start() {
        stop()
        guard Self.isEnabled else { return }
        if let m = NSEvent.addGlobalMonitorForEvents(matching: .leftMouseDown, handler: { _ in
            MainActor.assumeIsolated { DragWheelController.shared.mouseDown() }
        }) { monitors.append(m) }
        if let m = NSEvent.addGlobalMonitorForEvents(matching: .leftMouseDragged, handler: { _ in
            MainActor.assumeIsolated { DragWheelController.shared.dragged() }
        }) { monitors.append(m) }
    }

    func stop() {
        monitors.forEach { NSEvent.removeMonitor($0) }
        monitors.removeAll()
        endDrag()
    }

    private func mouseDown() {
        dragCountAtMouseDown = NSPasteboard(name: .drag).changeCount
        cancelledThisDrag = false
    }

    /// 偵測到「有檔案正在被拖曳」後開始追蹤按鍵；拖曳中按鍵沒有滑鼠移動也偵測得到。
    private func dragged() {
        guard poll == nil else { return }
        let pb = NSPasteboard(name: .drag)
        guard pb.changeCount != dragCountAtMouseDown else { return }
        let urls = (pb.readObjects(forClasses: [NSURL.self], options: [.urlReadingFileURLsOnly: true]) as? [URL]) ?? []
        guard !urls.isEmpty else { return }
        dragFiles = urls
        previousFlags = []
        poll = Timer.scheduledTimer(withTimeInterval: 0.04, repeats: true) { _ in
            MainActor.assumeIsolated { DragWheelController.shared.tick() }
        }
        tick()
    }

    private func tick() {
        // 放開滑鼠：拖曳結束
        if NSEvent.pressedMouseButtons & 1 == 0 {
            poll?.invalidate()
            poll = nil
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.25) { DragWheelController.shared.hideIfDragEnded() }
            return
        }
        // Esc：取消輪盤（這次拖曳不再自動開啟，除非再點一次按鍵）
        if CGEventSource.keyState(.combinedSessionState, key: 0x35) {
            if panel?.isVisible == true { hide() }
            cancelledThisDrag = true
        }
        let flags = NSEvent.modifierFlags.intersection([.shift, .command, .control, .option])
        let pressed = flags.subtracting(previousFlags)   // 這一瞬間「剛按下」的鍵
        previousFlags = flags
        guard !pressed.isEmpty else { return }
        // 依設定找出對應的輪盤（同時按了好幾個鍵時，取第一個符合的）
        guard let mode = Mode.allCases.first(where: { pressed.contains($0.key.flag) }) else { return }
        cancelledThisDrag = false
        if panel?.isVisible == true {
            if model.mode != mode { model.configure(files: model.files, mode: mode) }
        } else {
            show(files: dragFiles, mode: mode, at: NSEvent.mouseLocation)
        }
    }

    private func hideIfDragEnded() {
        if NSEvent.pressedMouseButtons & 1 == 0 { endDrag() }
    }

    private func endDrag() {
        poll?.invalidate()
        poll = nil
        dragFiles = []
        hide()
    }

    func show(files: [URL], mode: Mode, at point: NSPoint) {
        model.configure(files: files, mode: mode)
        let size = WheelModel.diameter + 40
        if panel == nil {
            let p = NSPanel(contentRect: NSRect(x: 0, y: 0, width: size, height: size),
                            styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
            p.isFloatingPanel = true
            p.level = .popUpMenu
            p.backgroundColor = .clear
            p.isOpaque = false
            p.hasShadow = false
            p.hidesOnDeactivate = false
            p.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .transient]
            let container = NSView(frame: NSRect(x: 0, y: 0, width: size, height: size))
            let host = NSHostingView(rootView: WheelView(model: model))
            host.frame = container.bounds
            host.autoresizingMask = [.width, .height]
            let drop = WheelDropView(frame: container.bounds, model: model)
            drop.autoresizingMask = [.width, .height]
            container.addSubview(host)
            container.addSubview(drop)
            p.contentView = container
            panel = p
        }
        var origin = NSPoint(x: point.x - size / 2, y: point.y - size / 2)
        if let screen = NSScreen.screens.first(where: { NSMouseInRect(point, $0.frame, false) }) {
            let f = screen.visibleFrame
            origin.x = min(max(origin.x, f.minX), f.maxX - size)
            origin.y = min(max(origin.y, f.minY), f.maxY - size)
        }
        panel?.setFrame(NSRect(origin: origin, size: NSSize(width: size, height: size)), display: true)
        panel?.alphaValue = 0
        panel?.orderFrontRegardless()
        NSAnimationContext.runAnimationGroup { ctx in
            ctx.duration = 0.12
            panel?.animator().alphaValue = 1
        }
        model.appeared = true
    }

    func hide() {
        model.hovered = nil
        model.centerHovered = false
        model.appeared = false
        panel?.orderOut(nil)
    }

    /// 放到某一格。
    func perform(_ segment: WheelSegment, files: [URL]) {
        switch segment.action {
        case .quick(let id): QuickActionRunner.shared.run(actionID: id, files: files)
        case .format(let format): QuickActionRunner.shared.convert(files: files, to: format)
        case .fileAction(let action): WheelFileActions.perform(action, files: files)
        case .openApp: AppState.shared.receiveFromOutside(files)
        }
        hide()
    }
}

/// 可用來叫出輪盤的按鍵
enum WheelKey: String, CaseIterable, Identifiable {
    case shift, command, control, option
    var id: String { rawValue }
    var flag: NSEvent.ModifierFlags {
        switch self {
        case .shift: return .shift
        case .command: return .command
        case .control: return .control
        case .option: return .option
        }
    }
    var symbol: String {
        switch self {
        case .shift: return "⇧"
        case .command: return "⌘"
        case .control: return "⌃"
        case .option: return "⌥"
        }
    }
    var title: String {
        switch self {
        case .shift: return "⇧ Shift"
        case .command: return "⌘ Command"
        case .control: return "⌃ Control"
        case .option: return "⌥ Option"
        }
    }
}

struct WheelSegment: Identifiable {
    enum Action {
        case quick(String)
        case format(ConversionFormat)
        case fileAction(WheelFileAction)
        case openApp
    }
    let id: String
    let title: String
    let subtitle: String?
    let symbol: String
    let tint: Color
    let action: Action
}

@MainActor
final class WheelModel: ObservableObject {
    static let diameter: CGFloat = 400
    static let innerRadius: CGFloat = 68
    static let outerRadius: CGFloat = 196

    @Published var files: [URL] = []
    @Published var mode: DragWheelController.Mode = .convert
    @Published var segments: [WheelSegment] = []
    @Published var hovered: Int?
    @Published var centerHovered = false
    @Published var appeared = false

    func configure(files: [URL], mode: DragWheelController.Mode) {
        self.files = files
        self.mode = mode
        hovered = nil
        segments = WheelCatalog.segments(for: files, mode: mode)
    }

    /// 由相對中心點的位置算出是哪一格（nil 代表中心或外圍）。
    func segmentIndex(dx: CGFloat, dy: CGFloat) -> Int? {
        let r = sqrt(dx * dx + dy * dy)
        guard r >= Self.innerRadius, r <= Self.outerRadius + 24, !segments.isEmpty else { return nil }
        // 0° 在正上方，順時針增加（AppKit 座標 y 向上）
        var angle = atan2(dx, dy) * 180 / .pi
        if angle < 0 { angle += 360 }
        let width = 360 / Double(segments.count)
        return min(Int((angle + width / 2).truncatingRemainder(dividingBy: 360) / width), segments.count - 1)
    }
}

/// 依檔案挑選輪盤上的格式與工具。
enum WheelCatalog {
    static let maxSegments = 10

    /// 常見格式排前面（依副檔名）。
    private static let preferred = ["pdf", "docx", "jpg", "png", "heic", "webp", "mp4", "gif", "mov", "m4a", "mp3", "wav", "flac",
                                    "xlsx", "csv", "pptx", "txt", "html", "md", "json", "yaml", "avif", "tiff", "ico", "icns",
                                    "aiff", "zip", "tar.gz"]

    static func applicable(_ info: QuickActionInfo, _ files: [URL]) -> Bool {
        let types = info.inputTypes.compactMap { UTType($0) }
        guard !types.isEmpty else { return false }
        if let tool = ToolRegistry.tool(id: info.toolID), files.count < tool.minimumFiles { return false }
        return files.allSatisfy { f in
            let t = ToolFiles.contentType(of: f)
            return types.contains { t.conforms(to: $0) }
        }
    }

    static func shortTitle(_ title: String) -> String {
        var t = title
        for prefix in ["轉成 ", "轉成", "轉換成 "] where t.hasPrefix(prefix) { t = String(t.dropFirst(prefix.count)) }
        if let paren = t.firstIndex(where: { $0 == "（" || $0 == "(" }) { t = String(t[..<paren]) }
        return t.trimmingCharacters(in: .whitespaces)
    }

    @MainActor
    static func segments(for files: [URL], mode: DragWheelController.Mode) -> [WheelSegment] {
        let sample = Array(files.prefix(20))
        let actions = QuickActionManifest.all.filter { applicable($0, sample) }
        var result: [WheelSegment] = []
        func tint(for info: QuickActionInfo) -> Color { ToolRegistry.tool(id: info.toolID)?.tint ?? .accentColor }

        switch mode {
        case .convert:
            let quick = actions.filter { $0.title.hasPrefix("轉成") }
            let primaryQuick = quick.filter { !$0.id.hasPrefix("other.") }
            let secondaryQuick = quick.filter { $0.id.hasPrefix("other.") }
            func addQuick(_ a: QuickActionInfo) {
                result.append(WheelSegment(id: a.id, title: shortTitle(a.title), subtitle: nil, symbol: a.symbol, tint: tint(for: a), action: .quick(a.id)))
            }
            func addFormat(_ f: ConversionFormat) {
                let name = f.displayName
                if result.contains(where: { $0.title.lowercased() == name.lowercased() || $0.id == "fmt.\(f.id)" }) { return }
                result.append(WheelSegment(id: "fmt.\(f.id)", title: name, subtitle: f.category.title, symbol: f.category.symbol,
                                           tint: ToolPalette.teal, action: .format(f)))
            }
            primaryQuick.forEach(addQuick)
            let targets = ConversionEngine.commonTargets(sample.prefix(5).map { ConversionEngine.targets(for: $0) })
                .sorted { rank($0) < rank($1) }
            // 壓縮檔、文字編碼類放在「更多格式」裡，除非來源本身就是壓縮檔
            let isArchive = sample.allSatisfy { ToolFiles.contentType(of: $0).conforms(to: .archive) }
            let main = targets.filter { isArchive || ($0.category != .archive && $0.category != .text) }
            let room = maxSegments - 1 - secondaryQuick.count
            for f in main where result.count < room { addFormat(f) }
            secondaryQuick.forEach(addQuick)
        case .actions:
            // 動作輪盤：檔案、資料夾、混合選取都能用，不需要「更多」
            return WheelFileAction.allCases.map {
                WheelSegment(id: "act.\($0.rawValue)", title: $0.title, subtitle: nil, symbol: $0.symbol, tint: ToolPalette.slate, action: .fileAction($0))
            }
        case .tools:
            for a in actions where !a.title.hasPrefix("轉成") && result.count < maxSegments - 1 {
                result.append(WheelSegment(id: a.id, title: shortTitle(a.title), subtitle: nil, symbol: a.symbol, tint: tint(for: a), action: .quick(a.id)))
            }
        }
        result.append(WheelSegment(id: "open", title: mode == .convert ? "更多格式" : "更多工具", subtitle: nil,
                                   symbol: "ellipsis.circle", tint: .gray, action: .openApp))
        return result
    }

    private static func rank(_ f: ConversionFormat) -> Int {
        let ext = f.fileExtension.lowercased()
        if let i = preferred.firstIndex(of: ext) { return i }
        return preferred.count + f.category.order * 10
    }
}

// MARK: - 外觀

private struct SectorShape: Shape {
    let start: Angle
    let end: Angle
    let inner: CGFloat
    let outer: CGFloat

    func path(in rect: CGRect) -> Path {
        let c = CGPoint(x: rect.midX, y: rect.midY)
        var p = Path()
        // SwiftUI 角度：0° 在右側、順時針；我們要 0° 在上方
        p.addArc(center: c, radius: outer, startAngle: start - .degrees(90), endAngle: end - .degrees(90), clockwise: false)
        p.addArc(center: c, radius: inner, startAngle: end - .degrees(90), endAngle: start - .degrees(90), clockwise: true)
        p.closeSubpath()
        return p
    }
}

struct WheelView: View {
    @ObservedObject var model: WheelModel

    var body: some View {
        let n = max(model.segments.count, 1)
        let width = 360.0 / Double(n)
        ZStack {
            Circle()
                .fill(.regularMaterial)
                .frame(width: WheelModel.outerRadius * 2, height: WheelModel.outerRadius * 2)
                .shadow(color: .black.opacity(0.28), radius: 18, y: 6)
            ForEach(Array(model.segments.enumerated()), id: \.element.id) { i, seg in
                let start = Angle.degrees(Double(i) * width - width / 2)
                let end = Angle.degrees(Double(i) * width + width / 2)
                let hovered = model.hovered == i
                SectorShape(start: start, end: end, inner: WheelModel.innerRadius, outer: WheelModel.outerRadius)
                    .fill(hovered ? seg.tint : Color.clear)
                SectorShape(start: start, end: end, inner: WheelModel.innerRadius, outer: WheelModel.outerRadius)
                    .stroke(Color.primary.opacity(0.08), lineWidth: 1)
                let mid = (Double(i) * width - 90) * .pi / 180
                let r = (WheelModel.innerRadius + WheelModel.outerRadius) / 2 + 6
                VStack(spacing: 3) {
                    Image(systemName: seg.symbol).font(.system(size: 17, weight: .semibold))
                    Text(seg.title).font(.system(size: 12, weight: .bold)).lineLimit(2).multilineTextAlignment(.center).minimumScaleFactor(0.75)
                }
                .frame(width: 92)
                .foregroundStyle(hovered ? Color.white : (seg.tint == .gray ? Color.secondary : seg.tint))
                .scaleEffect(hovered ? 1.12 : 1)
                .offset(x: cos(mid) * r, y: sin(mid) * r)
                .animation(.easeOut(duration: 0.12), value: hovered)
            }
            // 中心：檔案與模式
            Circle()
                .fill(model.centerHovered ? Color.accentColor.opacity(0.25) : Color(nsColor: .windowBackgroundColor))
                .frame(width: WheelModel.innerRadius * 2 - 8, height: WheelModel.innerRadius * 2 - 8)
                .overlay(
                    VStack(spacing: 2) {
                        if let f = model.files.first {
                            Image(nsImage: NSWorkspace.shared.icon(forFile: f.path)).resizable().frame(width: 34, height: 34)
                        }
                        Text(model.files.count > 1 ? "\(model.files.count) 個檔案・\(model.mode.title)" : model.mode.title)
                            .font(.system(size: 12, weight: .bold))
                        Text(DragWheelController.Mode.allCases.filter { $0 != model.mode }.map { "\($0.key.symbol) \($0.title)" }.joined(separator: "  ") + "  esc 取消")
                            .font(.system(size: 8.5)).foregroundStyle(.secondary)
                            .lineLimit(1).minimumScaleFactor(0.7)
                            .frame(width: WheelModel.innerRadius * 2 - 22)
                    }
                )
                .shadow(color: .black.opacity(0.12), radius: 4)
        }
        .frame(width: WheelModel.diameter + 40, height: WheelModel.diameter + 40)
        .scaleEffect(model.appeared ? 1 : 0.85)
        .animation(.spring(response: 0.25, dampingFraction: 0.75), value: model.appeared)
    }
}

/// 接收拖放的透明層：依游標位置計算目前在哪一格，放開時執行。
final class WheelDropView: NSView {
    private let model: WheelModel

    init(frame: NSRect, model: WheelModel) {
        self.model = model
        super.init(frame: frame)
        registerForDraggedTypes([.fileURL])
    }

    required init?(coder: NSCoder) { fatalError() }

    private func locate(_ sender: NSDraggingInfo) -> (Int?, Bool) {
        let p = convert(sender.draggingLocation, from: nil)
        let dx = p.x - bounds.midX, dy = p.y - bounds.midY
        let center = sqrt(dx * dx + dy * dy) < WheelModel.innerRadius
        return (MainActor.assumeIsolated { model.segmentIndex(dx: dx, dy: dy) }, center)
    }

    private func update(_ sender: NSDraggingInfo) -> NSDragOperation {
        let (index, center) = locate(sender)
        MainActor.assumeIsolated {
            if model.hovered != index { model.hovered = index; NSHapticFeedbackManager.defaultPerformer.perform(.alignment, performanceTime: .now) }
            model.centerHovered = center
        }
        return (index != nil || center) ? .copy : []
    }

    override func draggingEntered(_ sender: NSDraggingInfo) -> NSDragOperation { update(sender) }
    override func draggingUpdated(_ sender: NSDraggingInfo) -> NSDragOperation { update(sender) }

    override func draggingExited(_ sender: NSDraggingInfo?) {
        MainActor.assumeIsolated { model.hovered = nil; model.centerHovered = false }
    }

    override func performDragOperation(_ sender: NSDraggingInfo) -> Bool {
        let urls = (sender.draggingPasteboard.readObjects(forClasses: [NSURL.self], options: [.urlReadingFileURLsOnly: true]) as? [URL]) ?? []
        let (index, center) = locate(sender)
        guard !urls.isEmpty else { return false }
        return MainActor.assumeIsolated {
            if let index, index < model.segments.count {
                DragWheelController.shared.perform(model.segments[index], files: urls)
                return true
            }
            if center {
                DragWheelController.shared.perform(WheelSegment(id: "open", title: "", subtitle: nil, symbol: "", tint: .gray, action: .openApp), files: urls)
                return true
            }
            return false
        }
    }
}

/// 設定三種輪盤各用哪個按鍵（設定視窗與整合頁共用）。
struct WheelKeySettings: View {
    @AppStorage(DragWheelController.Mode.convert.keySetting) private var convertKey = WheelKey.shift.rawValue
    @AppStorage(DragWheelController.Mode.tools.keySetting) private var toolsKey = WheelKey.command.rawValue
    @AppStorage(DragWheelController.Mode.actions.keySetting) private var actionsKey = WheelKey.control.rawValue

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            row("轉換輪盤", "arrow.triangle.2.circlepath", $convertKey)
            row("工具輪盤", "wrench.and.screwdriver", $toolsKey)
            row("動作輪盤", "bolt", $actionsKey)
            if Set([convertKey, toolsKey, actionsKey]).count < 3 {
                Label("有兩個輪盤用了同一個按鍵，只會開啟排在前面的那個", systemImage: "exclamationmark.triangle.fill")
                    .font(.caption).foregroundStyle(.orange)
            }
        }
    }

    private func row(_ title: String, _ symbol: String, _ value: Binding<String>) -> some View {
        HStack {
            Label(title, systemImage: symbol)
            Spacer()
            Picker("", selection: value) {
                ForEach(WheelKey.allCases) { Text($0.title).tag($0.rawValue) }
            }
            .labelsHidden()
            .frame(width: 160)
        }
    }
}
