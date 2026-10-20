import Foundation

/// 不開介面直接執行工具：命令列測試工具與 App 的 `--qs-run` 模式都用這裡。
enum ToolCLI {
    struct Failure: LocalizedError {
        let message: String
        var errorDescription: String? { message }
    }

    /// 以工具（目前選項或自訂設定）或右鍵預設動作執行。
    /// - Parameters:
    ///   - id: 工具代碼（例如 "pdf.rotate"）或右鍵動作代碼（例如 "pdf.rotate-right"）
    ///   - configure: 用工具代碼時，可在執行前調整工作階段的選項
    @MainActor
    static func run(_ id: String, tools: [ToolDefinition], files: [URL], output: ToolContext.Output,
                    configure: ((ToolSession) -> Void)? = nil,
                    progress: @escaping @Sendable (Double, String?) -> Void = { _, _ in }) async throws -> [URL] {
        let job: ToolJob
        if let tool = tools.first(where: { $0.id == id }) {
            let session = tool.makeSession()
            session.filesDidChange(files)
            configure?(session)
            if files.count < tool.minimumFiles { throw Failure(message: "至少需要 \(tool.minimumFiles) 個檔案") }
            if let reason = session.validate(files) { throw Failure(message: reason) }
            job = session.makeJob()
        } else if let make = tools.lazy.compactMap({ $0.quickActions[id] }).first {
            job = make()
        } else {
            throw Failure(message: "找不到工具或動作：\(id)")
        }
        let context = ToolContext(output: output, progress: progress)
        return try await job.run(files, context)
    }

    /// 命令列進入點：`<工具或動作代碼> [--out 資料夾] 檔案…`；`--list` 列出所有工具與動作。
    @MainActor
    static func main(arguments: [String], tools: [ToolDefinition]) async -> Int32 {
        var args = Array(arguments.dropFirst())
        if args.first == "--list" || args.isEmpty {
            for t in tools {
                print("\(t.id)\t\(t.title)\t[\(t.inputLabel)]")
                for qa in t.quickActions.keys.sorted() { print("  ↳ \(qa)") }
            }
            return args.isEmpty ? 1 : 0
        }
        let id = args.removeFirst()
        var output = ToolContext.Output.sameAsSource
        if let i = args.firstIndex(of: "--out"), i + 1 < args.count {
            let dir = URL(fileURLWithPath: args[i + 1])
            try? FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
            output = .folder(dir)
            args.removeSubrange(i...(i + 1))
        }
        let files = args.map { URL(fileURLWithPath: $0) }
        let start = Date()
        do {
            let outputs = try await run(id, tools: tools, files: files, output: output, progress: { p, msg in
                FileHandle.standardError.write(Data(String(format: "\r%3d%% %@\u{1B}[K", Int(p * 100), msg ?? "").utf8))
            })
            FileHandle.standardError.write(Data("\n".utf8))
            for o in outputs { print(o.path) }
            FileHandle.standardError.write(Data(String(format: "完成，%.2f 秒\n", Date().timeIntervalSince(start)).utf8))
            return 0
        } catch {
            FileHandle.standardError.write(Data("\n錯誤：\(error.localizedDescription)\n".utf8))
            return 2
        }
    }
}
