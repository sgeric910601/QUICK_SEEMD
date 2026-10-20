import AppKit

/// 不開介面執行一個工具後結束（測試與自動化用）：
/// QUICK_SEEMD --qs-run <工具或右鍵動作代碼> [--qs-out <資料夾>] [--qs-result <結果.json>] --qs-files <檔案…>
/// 以 `open -n -W QUICK_SEEMD.app --args …` 啟動時，系統權限（例如控制 Microsoft Office）會記在 QUICK_SEEMD 名下。
final class ToolHeadlessMessage: @unchecked Sendable {
    private let lock = NSLock()
    private var message: String?
    func set(_ m: String) { lock.lock(); message = m; lock.unlock() }
    var value: String? { lock.lock(); defer { lock.unlock() }; return message }
}

enum ToolHeadless {
    static var isRequested: Bool { CommandLine.arguments.contains("--qs-run") }

    @MainActor
    static func runAndExit() {
        Task { @MainActor in
            let a = CommandLine.arguments
            func value(_ key: String) -> String? {
                guard let i = a.firstIndex(of: key), i + 1 < a.count else { return nil }
                return a[i + 1]
            }
            let id = value("--qs-run") ?? ""
            let out = value("--qs-out").map { URL(fileURLWithPath: $0) }
            var files: [URL] = []
            if let i = a.firstIndex(of: "--qs-files") { files = a[(i + 1)...].map { URL(fileURLWithPath: $0) } }
            var result: [String: Any] = ["action": id]
            let start = Date()
            let lastMessage = ToolHeadlessMessage()
            do {
                if let out { try FileManager.default.createDirectory(at: out, withIntermediateDirectories: true) }
                let outputs = try await ToolCLI.run(id, tools: ToolRegistry.all, files: files, output: out.map { .folder($0) } ?? .sameAsSource,
                                                    progress: { _, msg in if let msg { lastMessage.set(msg) } })
                result["outputs"] = outputs.map(\.path)
            } catch {
                result["error"] = error.localizedDescription
            }
            result["seconds"] = Date().timeIntervalSince(start)
            if let summary = lastMessage.value { result["summary"] = summary }
            if let data = try? JSONSerialization.data(withJSONObject: result, options: [.prettyPrinted, .sortedKeys]) {
                if let path = value("--qs-result") { try? data.write(to: URL(fileURLWithPath: path)) }
                FileHandle.standardOutput.write(data)
            }
            NSApp.terminate(nil)
        }
    }
}
