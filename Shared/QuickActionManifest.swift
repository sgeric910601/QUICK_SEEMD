import Foundation

/// 右鍵一鍵轉換的一個動作。主程式與 Finder 延伸功能共用這份清單：
/// Finder 依檔案類型挑出可用的動作顯示在右鍵選單；按下後由主程式以預設選項執行。
struct QuickActionInfo: Sendable {
    /// 唯一代碼，例如 "pdf.to-word"；主程式的 ToolDefinition.quickActions 用同一個代碼對應到工作
    let id: String
    /// 選單文字，例如「轉成 Word（.docx）」
    let title: String
    /// SF Symbol 名稱
    let symbol: String
    /// 選單分組標題，例如「PDF」「Office」「圖片」「影音」「其他」
    let group: String
    /// 可接受的檔案類型（UTType 識別碼，以相容性判斷，例如 "com.adobe.pdf"、"public.image"）
    let inputTypes: [String]
    /// 對應的工具代碼（「更多選項…」會開啟這個工具）
    let toolID: String
    /// 選取多個檔案時是否合併成一個輸出（例如「合併成一個 PDF」），否則逐檔處理
    var combinesFiles: Bool = false
}

enum QuickActionManifest {
    static var all: [QuickActionInfo] { pdf + office + image + media + other }

    static func action(id: String) -> QuickActionInfo? { all.first { $0.id == id } }

    /// 主程式接收右鍵動作用的網址：quickseemd://run?action=<id>&f=<路徑>&f=<路徑>
    static func url(action: String, files: [URL]) -> URL? {
        var c = URLComponents()
        c.scheme = "quickseemd"
        c.host = "run"
        c.queryItems = [URLQueryItem(name: "action", value: action)] + files.map { URLQueryItem(name: "f", value: $0.path) }
        return c.url
    }

    /// 「在 QUICK_SEEMD 中開啟」：quickseemd://open?f=<路徑>…
    static func openURL(files: [URL], tool: String? = nil) -> URL? {
        var c = URLComponents()
        c.scheme = "quickseemd"
        c.host = "open"
        c.queryItems = (tool.map { [URLQueryItem(name: "tool", value: $0)] } ?? []) + files.map { URLQueryItem(name: "f", value: $0.path) }
        return c.url
    }
}
