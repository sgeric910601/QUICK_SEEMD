import Cocoa
import FinderSync
import UniformTypeIdentifiers

/// Finder 右鍵選單：依選取的檔案類型列出 QUICK_SEEMD 的一鍵轉換，點了就交給主程式在背景執行。
final class FinderSync: FIFinderSync {
    private var actionIDs: [String] = []

    override init() {
        super.init()
        FIFinderSyncController.default().directoryURLs = [URL(fileURLWithPath: "/")]
    }

    override func menu(for menuKind: FIMenuKind) -> NSMenu? {
        guard menuKind == .contextualMenuForItems,
              let files = FIFinderSyncController.default().selectedItemURLs(), !files.isEmpty else { return nil }
        let actions = QuickActionManifest.all.filter { applies($0, to: files) }
        actionIDs = actions.map(\.id)

        let sub = NSMenu(title: "QUICK_SEEMD")
        var lastGroup: String?
        for (index, action) in actions.enumerated() {
            if action.group != lastGroup {
                if lastGroup != nil { sub.addItem(.separator()) }
                let header = NSMenuItem(title: action.group, action: nil, keyEquivalent: "")
                header.isEnabled = false
                sub.addItem(header)
                lastGroup = action.group
            }
            let item = NSMenuItem(title: action.title, action: #selector(runAction(_:)), keyEquivalent: "")
            item.target = self
            item.tag = index
            item.image = NSImage(systemSymbolName: action.symbol, accessibilityDescription: nil)
            sub.addItem(item)
        }
        if !actions.isEmpty { sub.addItem(.separator()) }
        let more = NSMenuItem(title: "更多工具與選項…", action: #selector(openInApp(_:)), keyEquivalent: "")
        more.target = self
        more.image = NSImage(systemSymbolName: "square.grid.2x2", accessibilityDescription: nil)
        sub.addItem(more)

        let root = NSMenuItem(title: "QUICK_SEEMD 轉換", action: nil, keyEquivalent: "")
        root.image = AppMark.templateImage(size: 16)
        root.submenu = sub
        let menu = NSMenu(title: "")
        menu.addItem(root)
        return menu
    }

    @objc private func runAction(_ sender: NSMenuItem) {
        guard sender.tag >= 0, sender.tag < actionIDs.count,
              let files = FIFinderSyncController.default().selectedItemURLs(), !files.isEmpty,
              let url = QuickActionManifest.url(action: actionIDs[sender.tag], files: files) else { return }
        NSWorkspace.shared.open(url)
    }

    @objc private func openInApp(_ sender: NSMenuItem) {
        guard let files = FIFinderSyncController.default().selectedItemURLs(),
              let url = QuickActionManifest.openURL(files: files) else { return }
        NSWorkspace.shared.open(url)
    }

    private func applies(_ action: QuickActionInfo, to files: [URL]) -> Bool {
        let types = action.inputTypes.compactMap { UTType($0) }
        guard !types.isEmpty else { return false }
        if action.id.contains("merge") && files.count < 2 { return false }
        return files.allSatisfy { file in
            let t = contentType(of: file)
            return types.contains { t.conforms(to: $0) }
        }
    }

    private func contentType(of url: URL) -> UTType {
        if let t = (try? url.resourceValues(forKeys: [.contentTypeKey]))?.contentType { return t }
        if url.hasDirectoryPath { return .folder }
        return UTType(filenameExtension: url.pathExtension) ?? .data
    }
}
