import AppKit
import SwiftUI

enum SidebarItem: Hashable {
    case allTools
    case category(ToolCategory)
    case converter
    case preview
    case integration
}

/// 主視窗的導覽狀態，以及從外部（Dock、Finder 服務、右鍵、拖曳輪盤、quickseemd:// 網址）送進來的檔案。
@MainActor
final class AppState: ObservableObject {
    static let shared = AppState()

    @Published var sidebar: SidebarItem? = .allTools
    @Published var openToolID: String?
    @Published var pageToken = UUID()
    @Published var pendingFiles: [URL] = []
    /// 從外部收到、還沒決定要用哪個工具的檔案
    @Published var inbox: [URL] = []
    let converter = ConverterModel()

    var openTool: ToolDefinition? { openToolID.flatMap { ToolRegistry.tool(id: $0) } }

    func select(_ item: SidebarItem) {
        sidebar = item
        openToolID = nil
    }

    func open(_ tool: ToolDefinition, files: [URL] = []) {
        if tool.customPage != nil, !files.isEmpty, tool.id == OtherToolCatalog.converterID {
            converter.add(urls: files)
        }
        pendingFiles = files
        openToolID = tool.id
        pageToken = UUID()
        if !files.isEmpty { inbox = [] }
    }

    func closeTool() {
        openToolID = nil
        pendingFiles = []
    }

    /// 收到檔案：到工具大廳，只列出能處理它們的工具。
    func receive(_ files: [URL]) {
        guard !files.isEmpty else { return }
        inbox = files
        openToolID = nil
        sidebar = .allTools
    }

    func inboxFits(_ tool: ToolDefinition) -> Bool {
        guard !inbox.isEmpty else { return true }
        if tool.id == OtherToolCatalog.converterID { return true }
        return (tool.allowsMultiple || inbox.count == 1) && inbox.allSatisfy { tool.accepts($0) }
    }

    func inboxFiles(for tool: ToolDefinition) -> [URL] {
        !inbox.isEmpty && inboxFits(tool) ? inbox : []
    }

    // MARK: 從外部進來

    func receiveFromOutside(_ files: [URL]) {
        receive(files)
        MainWindow.shared.show()
    }

    func openFromOutside(toolID: String, files: [URL]) {
        if let tool = ToolRegistry.tool(id: toolID), files.allSatisfy({ tool.accepts($0) }) {
            open(tool, files: files)
        } else if let preferred = ToolRegistry.tool(id: toolID),
                  let alt = ToolRegistry.tools(accepting: files).first(where: { $0.category == preferred.category && $0.customPage == nil }) {
            open(alt, files: files)
        } else {
            receive(files)
        }
        MainWindow.shared.show()
    }

    /// quickseemd://run?action=…&f=…（右鍵一鍵轉換）、quickseemd://open?tool=…&f=…
    func handle(url: URL) {
        guard let c = URLComponents(url: url, resolvingAgainstBaseURL: false) else { return }
        let items = c.queryItems ?? []
        let files = items.filter { $0.name == "f" }.compactMap { $0.value }.map { URL(fileURLWithPath: $0) }
            .filter { FileManager.default.fileExists(atPath: $0.path) }
        switch c.host {
        case "run":
            if let action = items.first(where: { $0.name == "action" })?.value {
                QuickActionRunner.shared.run(actionID: action, files: files)
            }
        case "open":
            if let tool = items.first(where: { $0.name == "tool" })?.value {
                openFromOutside(toolID: tool, files: files)
            } else if files.isEmpty {
                MainWindow.shared.show()
            } else {
                receiveFromOutside(files)
            }
        default:
            MainWindow.shared.show()
        }
    }
}
