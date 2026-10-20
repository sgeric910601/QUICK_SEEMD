import Foundation

/// 所有工具（工具大廳、右鍵動作、--qs-run 都從這裡找）。
enum ToolRegistry {
    static let all: [ToolDefinition] =
        PDFToolCatalog.tools + OfficeToolCatalog.tools + ImageToolCatalog.tools + MediaToolCatalog.tools + OtherToolCatalog.tools

    static func tool(id: String) -> ToolDefinition? { all.first { $0.id == id } }

    static func tools(in category: ToolCategory) -> [ToolDefinition] { all.filter { $0.category == category } }

    /// 能處理所有這些檔案的工具。
    static func tools(accepting files: [URL]) -> [ToolDefinition] {
        all.filter { tool in
            files.count >= 1 && (tool.allowsMultiple || files.count == 1) && files.allSatisfy { tool.accepts($0) }
        }
    }

    /// 右鍵動作代碼 → 產生工作的方法。
    static func quickJob(for actionID: String) -> (@MainActor () -> ToolJob)? {
        all.lazy.compactMap { $0.quickActions[actionID] }.first
    }
}
