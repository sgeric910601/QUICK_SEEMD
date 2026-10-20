import AppKit
import os
import SwiftUI

private let runnerLog = Logger(subsystem: "dev.quickseemd.app", category: "quick-actions")

/// 背景轉換佇列：右鍵一鍵轉換、拖曳轉換輪盤都丟到這裡，依序執行，並在螢幕右上角顯示進度。
/// 輸出一律存在原始檔案旁邊（同名時自動加上編號，不覆蓋）。
@MainActor
final class QuickActionRunner: ObservableObject {
    static let shared = QuickActionRunner()

    struct Item: Identifiable {
        enum Status { case waiting, running, done([URL]), failed(String) }
        let id = UUID()
        let title: String
        let symbol: String
        let tint: Color
        let files: [URL]
        let toolID: String?
        var status: Status = .waiting
        var progress: Double = 0
        var message = ""
    }

    @Published private(set) var items: [Item] = []
    private var jobs: [UUID: ToolJob] = [:]
    private var running: Task<Void, Never>?
    private var current: UUID?
    private var hideWork: DispatchWorkItem?

    /// 執行 QuickActionManifest 裡的動作。
    func run(actionID: String, files: [URL]) {
        guard !files.isEmpty else { return }
        guard let make = ToolRegistry.quickJob(for: actionID) else {
            enqueue(title: "找不到動作", symbol: "questionmark", tint: .gray, files: files, toolID: nil,
                    job: ToolJob { _, _ in throw ConversionError.other("這個版本的 QUICK_SEEMD 不認得動作「\(actionID)」。") })
            return
        }
        let info = QuickActionManifest.action(id: actionID)
        let tool = info.flatMap { ToolRegistry.tool(id: $0.toolID) }
        enqueue(title: info?.title ?? actionID, symbol: info?.symbol ?? tool?.symbol ?? "wand.and.stars",
                tint: tool?.tint ?? .accentColor, files: files, toolID: info?.toolID, job: make())
    }

    /// 以萬用轉換引擎把檔案轉成某個格式（拖曳輪盤用）。
    func convert(files: [URL], to format: ConversionFormat) {
        let job = ToolJob { files, context in
            var outputs: [URL] = []
            for (i, file) in files.enumerated() {
                try context.checkCancellation()
                let sub = context.segment(i, of: files.count)
                sub.progress(0, file.lastPathComponent)
                outputs += try await ConversionEngine.convert(file, to: format, outputDirectory: sub.outputDirectory(for: file),
                                                              progress: { sub.progress($0) })
            }
            return outputs
        }
        enqueue(title: "轉成 \(format.displayName)", symbol: format.category.symbol, tint: ToolPalette.teal,
                files: files, toolID: "other.convert", job: job)
    }

    func enqueue(title: String, symbol: String, tint: Color, files: [URL], toolID: String?, job: ToolJob) {
        let item = Item(title: title, symbol: symbol, tint: tint, files: files, toolID: toolID)
        jobs[item.id] = job
        items.append(item)
        if items.count > 6 { items.removeFirst(items.count - 6) }
        QuickActionHUD.shared.show()
        hideWork?.cancel()
        startNext()
    }

    /// 只顯示一則完成訊息（拷貝、拷貝路徑這類瞬間完成的動作）。
    func notify(title: String, symbol: String, message: String) {
        var item = Item(title: title, symbol: symbol, tint: ToolPalette.slate, files: [], toolID: nil)
        item.status = .done([])
        item.progress = 1
        item.message = message
        items.append(item)
        if items.count > 6 { items.removeFirst(items.count - 6) }
        QuickActionHUD.shared.show()
        scheduleHide()
    }

    func cancel(_ id: UUID) {
        if current == id { running?.cancel() }
        if let i = items.firstIndex(where: { $0.id == id }), case .waiting = items[i].status {
            items[i].status = .failed("已取消")
            jobs[id] = nil
        }
    }

    func dismiss(_ id: UUID) {
        items.removeAll { $0.id == id }
        if items.isEmpty { QuickActionHUD.shared.hide() }
    }

    private func startNext() {
        guard current == nil,
              let index = items.firstIndex(where: { if case .waiting = $0.status { return true } else { return false } }),
              let job = jobs[items[index].id] else { return }
        let id = items[index].id
        let files = items[index].files
        current = id
        items[index].status = .running
        let context = ToolContext(output: .sameAsSource) { [weak self] p, msg in
            Task { @MainActor in self?.update(id, progress: p, message: msg) }
        }
        running = Task.detached(priority: .userInitiated) {
            let status: Item.Status
            do {
                status = .done(try await job.run(files, context))
            } catch {
                status = .failed(Task.isCancelled ? "已取消" : error.localizedDescription)
            }
            await MainActor.run { QuickActionRunner.shared.finish(id, status) }
        }
    }

    private func update(_ id: UUID, progress: Double, message: String?) {
        guard let i = items.firstIndex(where: { $0.id == id }) else { return }
        defer { QuickActionHUD.shared.position() }
        items[i].progress = max(items[i].progress, progress)
        if let message { items[i].message = message }
    }

    private func finish(_ id: UUID, _ status: Item.Status) {
        switch status {
        case .done(let outputs): runnerLog.notice("完成：\(outputs.map(\.lastPathComponent).joined(separator: ", "), privacy: .public)")
        case .failed(let reason): runnerLog.error("失敗：\(reason, privacy: .public)")
        default: break
        }
        if let i = items.firstIndex(where: { $0.id == id }) {
            items[i].status = status
            items[i].progress = 1
        }
        DispatchQueue.main.async { QuickActionHUD.shared.position() }
        jobs[id] = nil
        current = nil
        running = nil
        if items.contains(where: { if case .waiting = $0.status { return true } else { return false } }) {
            startNext()
        } else {
            scheduleHide()
        }
    }

    func scheduleHide() {
        hideWork?.cancel()
        let work = DispatchWorkItem { [weak self] in
            guard let self, !QuickActionHUD.shared.isHovering else { self?.scheduleHide(); return }
            let anyFailed = self.items.contains { if case .failed = $0.status { return true } else { return false } }
            if !anyFailed {
                QuickActionHUD.shared.hide()
                self.items.removeAll()
            }
        }
        hideWork = work
        DispatchQueue.main.asyncAfter(deadline: .now() + 7, execute: work)
    }
}

/// 螢幕右上角的浮動進度面板（不搶焦點）。
@MainActor
final class QuickActionHUD {
    static let shared = QuickActionHUD()
    private var panel: NSPanel?
    var isHovering = false

    func show() {
        if panel == nil {
            let p = NSPanel(contentRect: NSRect(x: 0, y: 0, width: 360, height: 120),
                            styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
            p.isFloatingPanel = true
            p.level = .floating
            p.backgroundColor = .clear
            p.isOpaque = false
            p.hasShadow = false
            p.hidesOnDeactivate = false
            p.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary, .stationary]
            let host = NSHostingView(rootView: QuickActionHUDView(onHover: { [weak self] in self?.isHovering = $0 })
                .environmentObject(QuickActionRunner.shared))
            host.sizingOptions = [.intrinsicContentSize]
            p.contentView = host
            panel = p
        }
        position()
        panel?.orderFrontRegardless()
        // SwiftUI 版面算好後再調一次大小
        DispatchQueue.main.async { self.position() }
    }

    func position() {
        guard let panel, let screen = NSScreen.main else { return }
        var size = panel.contentView?.intrinsicContentSize ?? .zero
        if size.width < 10 || size.height < 10 || size.width == NSView.noIntrinsicMetric {
            size = NSSize(width: 360, height: CGFloat(max(QuickActionRunner.shared.items.count, 1)) * 96 + 20)
        }
        let frame = screen.visibleFrame
        panel.contentView?.layoutSubtreeIfNeeded()
        let fitting = panel.contentView?.fittingSize ?? .zero
        if fitting.height > size.height { size.height = fitting.height }
        panel.setFrame(NSRect(x: frame.maxX - size.width - 14, y: frame.maxY - size.height - 10, width: size.width, height: size.height), display: true)
    }

    func hide() { panel?.orderOut(nil) }
}

private struct QuickActionHUDView: View {
    @EnvironmentObject var runner: QuickActionRunner
    var onHover: (Bool) -> Void

    var body: some View {
        VStack(spacing: 8) {
            ForEach(runner.items) { item in
                QuickActionRow(item: item)
            }
        }
        .padding(10)
        .frame(width: 360)
        .onHover(perform: onHover)
        .onChange(of: runner.items.count) { _, _ in
            DispatchQueue.main.async { QuickActionHUD.shared.position() }
        }
    }
}

private struct QuickActionRow: View {
    let item: QuickActionRunner.Item
    @EnvironmentObject var runner: QuickActionRunner

    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            ZStack {
                RoundedRectangle(cornerRadius: 9, style: .continuous).fill(item.tint)
                Image(systemName: item.symbol).font(.system(size: 16, weight: .semibold)).foregroundStyle(.white)
            }
            .frame(width: 34, height: 34)
            VStack(alignment: .leading, spacing: 4) {
                HStack {
                    Text(item.title).font(.callout.weight(.semibold)).lineLimit(1)
                    Spacer(minLength: 4)
                    Button { runner.dismiss(item.id) } label: { Image(systemName: "xmark").font(.caption.weight(.bold)) }
                        .buttonStyle(.plain).foregroundStyle(.secondary)
                }
                switch item.status {
                case .waiting:
                    Text("等待中：\(fileSummary)").font(.caption).foregroundStyle(.secondary).lineLimit(1)
                case .running:
                    ProgressView(value: item.progress).progressViewStyle(.linear).tint(item.tint)
                    HStack {
                        Text(item.message.isEmpty ? fileSummary : item.message).lineLimit(1)
                        Spacer()
                        Button("取消") { runner.cancel(item.id) }.buttonStyle(.plain).foregroundStyle(item.tint)
                    }
                    .font(.caption).foregroundStyle(.secondary)
                case .done(let outputs) where outputs.isEmpty:
                    Label(item.message.isEmpty ? "完成" : item.message, systemImage: "checkmark.circle.fill")
                        .font(.caption).foregroundStyle(.green).lineLimit(2)
                case .done(let outputs):
                    Label(outputs.count == 1 ? outputs[0].lastPathComponent : "完成 \(outputs.count) 個檔案", systemImage: "checkmark.circle.fill")
                        .font(.caption).foregroundStyle(.green).lineLimit(1)
                    HStack(spacing: 12) {
                        Button("在 Finder 中顯示") { NSWorkspace.shared.activateFileViewerSelecting(outputs) }
                        if outputs.count <= 3 { Button("開啟") { outputs.forEach { NSWorkspace.shared.open($0) } } }
                    }
                    .buttonStyle(.plain).font(.caption.weight(.semibold)).foregroundStyle(item.tint)
                case .failed(let reason):
                    Label(reason, systemImage: "exclamationmark.triangle.fill")
                        .font(.caption).foregroundStyle(.orange).lineLimit(3)
                    if let toolID = item.toolID, ToolRegistry.tool(id: toolID) != nil {
                        Button("在 QUICK_SEEMD 中調整選項…") {
                            AppState.shared.openFromOutside(toolID: toolID, files: item.files)
                        }
                        .buttonStyle(.plain).font(.caption.weight(.semibold)).foregroundStyle(item.tint)
                    }
                }
            }
        }
        .padding(12)
        .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(.regularMaterial))
        .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous).strokeBorder(Color.primary.opacity(0.1)))
        .shadow(color: .black.opacity(0.18), radius: 12, y: 4)
    }

    private var fileSummary: String {
        item.files.count == 1 ? item.files[0].lastPathComponent : "\(item.files.count) 個檔案"
    }
}
