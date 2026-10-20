import AppKit
import SwiftUI

/// 動作輪盤（預設 ⌃ Control）上的檔案動作；檔案、資料夾、混合選取都能用。
enum WheelFileAction: String, CaseIterable {
    case airDrop, float, copy, move, copyPath, terminal

    var title: String {
        switch self {
        case .airDrop: return "AirDrop"
        case .float: return "浮動暫存"
        case .copy: return "拷貝"
        case .move: return "移動到…"
        case .copyPath: return "拷貝路徑"
        case .terminal: return "終端機"
        }
    }

    var symbol: String {
        switch self {
        case .airDrop: return "dot.radiowaves.left.and.right"
        case .float: return "tray.and.arrow.down"
        case .copy: return "doc.on.doc"
        case .move: return "folder"
        case .copyPath: return "link"
        case .terminal: return "terminal"
        }
    }
}

@MainActor
enum WheelFileActions {
    static func perform(_ action: WheelFileAction, files: [URL]) {
        guard !files.isEmpty else { return }
        let count = files.count == 1 ? "「\(files[0].lastPathComponent)」" : "\(files.count) 個項目"
        switch action {
        case .airDrop:
            NSApp.activate(ignoringOtherApps: true)
            if let service = NSSharingService(named: .sendViaAirDrop), service.canPerform(withItems: files) {
                service.perform(withItems: files)
            } else {
                QuickActionRunner.shared.notify(title: "AirDrop", symbol: action.symbol, message: "這台 Mac 目前無法使用 AirDrop")
            }
        case .float:
            FloatShelf.shared.add(files)
        case .copy:
            NSPasteboard.general.clearContents()
            NSPasteboard.general.writeObjects(files as [NSURL])
            QuickActionRunner.shared.notify(title: "已拷貝", symbol: action.symbol, message: "\(count)，可到 Finder 貼上")
        case .copyPath:
            NSPasteboard.general.clearContents()
            NSPasteboard.general.setString(files.map(\.path).joined(separator: "\n"), forType: .string)
            QuickActionRunner.shared.notify(title: "已拷貝路徑", symbol: action.symbol, message: files.count == 1 ? files[0].path : "\(files.count) 個路徑")
        case .terminal:
            let first = files[0]
            let folder = CVFileNaming.isDirectory(first) && !CVFileNaming.isPackage(first) ? first : first.deletingLastPathComponent()
            let terminal = URL(fileURLWithPath: "/System/Applications/Utilities/Terminal.app")
            NSWorkspace.shared.open([folder], withApplicationAt: terminal, configuration: NSWorkspace.OpenConfiguration())
        case .move:
            NSApp.activate(ignoringOtherApps: true)
            let panel = NSOpenPanel()
            panel.canChooseFiles = false
            panel.canChooseDirectories = true
            panel.canCreateDirectories = true
            panel.prompt = "移到這裡"
            panel.message = "要把\(count)移到哪裡？"
            panel.directoryURL = files[0].deletingLastPathComponent()
            guard panel.runModal() == .OK, let destination = panel.url else { return }
            QuickActionRunner.shared.enqueue(title: "移動到「\(destination.lastPathComponent)」", symbol: action.symbol, tint: ToolPalette.slate,
                                             files: files, toolID: nil, job: ToolJob { files, context in
                var moved: [URL] = []
                for (i, f) in files.enumerated() {
                    try context.checkCancellation()
                    if f.deletingLastPathComponent().standardizedFileURL == destination.standardizedFileURL { moved.append(f); continue }
                    moved.append(try CVFileNaming.commit(f, into: destination))
                    context.progress(Double(i + 1) / Double(files.count), f.lastPathComponent)
                }
                return moved
            })
        }
    }
}

/// 浮動暫存：先把檔案放在螢幕邊的小架子上，之後再拖到別的地方（不會搬動或複製原檔）。
@MainActor
final class FloatShelf: ObservableObject {
    static let shared = FloatShelf()
    @Published var items: [URL] = []
    private var panel: NSPanel?

    func add(_ urls: [URL]) {
        for u in urls where !items.contains(u) { items.append(u) }
        show()
    }

    func remove(_ url: URL) {
        items.removeAll { $0 == url }
        if items.isEmpty { hide() } else { resize() }
    }

    func clear() {
        items.removeAll()
        hide()
    }

    private func show() {
        if panel == nil {
            let p = NSPanel(contentRect: NSRect(x: 0, y: 0, width: 250, height: 120),
                            styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
            p.isFloatingPanel = true
            p.level = .floating
            p.backgroundColor = .clear
            p.isOpaque = false
            p.hasShadow = true
            p.hidesOnDeactivate = false
            p.isMovableByWindowBackground = true
            p.collectionBehavior = [.canJoinAllSpaces, .fullScreenAuxiliary]
            p.contentView = NSHostingView(rootView: FloatShelfView().environmentObject(self))
            panel = p
            if let screen = NSScreen.main {
                let f = screen.visibleFrame
                p.setFrameOrigin(NSPoint(x: f.maxX - 270, y: f.midY))
            }
        }
        resize()
        panel?.orderFrontRegardless()
    }

    private func resize() {
        guard let panel else { return }
        let height = CGFloat(min(items.count, 7)) * 46 + 58
        var frame = panel.frame
        frame.origin.y += frame.height - height
        frame.size = NSSize(width: 250, height: height)
        panel.setFrame(frame, display: true, animate: true)
    }

    private func hide() { panel?.orderOut(nil) }
}

private struct FloatShelfView: View {
    @EnvironmentObject var shelf: FloatShelf
    @State private var dropTargeted = false

    var body: some View {
        VStack(spacing: 0) {
            HStack {
                Image(systemName: "tray.and.arrow.down").foregroundStyle(.secondary)
                Text("浮動暫存").font(.headline)
                Text("\(shelf.items.count)").font(.caption.weight(.bold)).foregroundStyle(.secondary)
                Spacer()
                Button { shelf.clear() } label: { Image(systemName: "xmark.circle.fill").foregroundStyle(.secondary) }
                    .buttonStyle(.plain)
                    .help("清空")
            }
            .padding(.horizontal, 12).padding(.vertical, 9)
            Divider()
            ScrollView {
                VStack(spacing: 2) {
                    ForEach(shelf.items, id: \.self) { url in
                        ShelfRow(url: url)
                    }
                }
                .padding(6)
            }
        }
        .frame(width: 250)
        .background(RoundedRectangle(cornerRadius: 14, style: .continuous).fill(.regularMaterial))
        .overlay(RoundedRectangle(cornerRadius: 14, style: .continuous)
            .strokeBorder(dropTargeted ? Color.accentColor : Color.primary.opacity(0.12), lineWidth: dropTargeted ? 2 : 1))
        .onDrop(of: [.fileURL], isTargeted: $dropTargeted) { providers in
            ToolDrop.loadURLs(providers) { shelf.add($0) }
            return true
        }
    }
}

private struct ShelfRow: View {
    let url: URL
    @EnvironmentObject var shelf: FloatShelf
    @State private var hover = false

    var body: some View {
        HStack(spacing: 8) {
            Image(nsImage: NSWorkspace.shared.icon(forFile: url.path)).resizable().frame(width: 30, height: 30)
            Text(url.lastPathComponent).font(.callout).lineLimit(1).truncationMode(.middle)
            Spacer(minLength: 0)
            if hover {
                Button { shelf.remove(url) } label: { Image(systemName: "minus.circle.fill").foregroundStyle(.secondary) }
                    .buttonStyle(.plain)
            }
        }
        .padding(.horizontal, 6).padding(.vertical, 4)
        .background(RoundedRectangle(cornerRadius: 8).fill(hover ? Color.primary.opacity(0.07) : .clear))
        .contentShape(Rectangle())
        .onHover { hover = $0 }
        .onTapGesture(count: 2) { NSWorkspace.shared.open(url) }
        .onDrag { NSItemProvider(contentsOf: url) ?? NSItemProvider() }
        .help("拖到任何地方使用；雙按打開")
    }
}
