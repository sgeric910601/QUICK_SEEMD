import Foundation

// LibreOffice 引擎：`soffice --headless --convert-to …`。QUICK_SEEMD 不會安裝它，只在使用者自己裝了時使用。
// 偵測沿用轉換器的 CVExternalTools（測試時可用 QUICKSEEMD_CONVERTER_TOOLS_DIR 指到假的 soffice）。

enum OTLibreOffice {
    private static let lock = OTAsyncLock()

    /// --convert-to 的「副檔名:篩選器」。
    static func filter(target: String, app: OTOfficeApp) -> String? {
        switch (app, target) {
        case (.word, "pdf"): return "pdf:writer_pdf_Export"
        case (.word, "docx"): return "docx:MS Word 2007 XML"
        case (.word, "doc"): return "doc:MS Word 97"
        case (.word, "odt"): return "odt:writer8"
        case (.word, "rtf"): return "rtf:Rich Text Format"
        case (.word, "txt"): return "txt:Text (encoded):UTF8"
        case (.word, "html"): return "html:HTML (StarWriter)"
        case (.excel, "pdf"): return "pdf:calc_pdf_Export"
        case (.excel, "xlsx"): return "xlsx:Calc MS Excel 2007 XML"
        case (.excel, "xls"): return "xls:MS Excel 97"
        case (.excel, "ods"): return "ods:calc8"
        case (.excel, "html"): return "html:HTML (StarCalc)"
        case (.powerpoint, "pdf"): return "pdf:impress_pdf_Export"
        case (.powerpoint, "pptx"): return "pptx:Impress MS PowerPoint 2007 XML"
        case (.powerpoint, "ppt"): return "ppt:MS PowerPoint 97"
        case (.powerpoint, "odp"): return "odp:impress8"
        default: return nil
        }
    }

    static func supports(target: String, app: OTOfficeApp) -> Bool { filter(target: target, app: app) != nil }

    /// 指令參數（可單獨測試）。使用獨立的設定檔目錄，不影響使用者自己開著的 LibreOffice。
    static func arguments(input: String, outDir: String, filter: String, profileURL: String, inFilter: String? = nil) -> [String] {
        var args = ["-env:UserInstallation=" + profileURL, "--headless", "--invisible", "--norestore", "--nologo",
                    "--nodefault", "--nolockcheck"]
        if let inFilter { args.append("--infilter=" + inFilter) }
        args += ["--convert-to", filter, "--outdir", outDir, input]
        return args
    }

    static var profileDirectory: URL {
        let caches = FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask).first ?? FileManager.default.temporaryDirectory
        return caches.appendingPathComponent("QUICK_SEEMD/LibreOfficeProfile", isDirectory: true)
    }

    /// 轉換一個檔案；回傳 QUICK_SEEMD 暫存資料夾中的結果。
    /// - Parameter inFilter: 例如 PDF 轉 Word 時用 "writer_pdf_import"
    static func convert(_ input: URL, app: OTOfficeApp, to target: String, inFilter: String? = nil,
                        context: ToolContext) async throws -> URL {
        guard let exe = CVExternalTools.executable(for: .libreOffice) else {
            throw OTEngineError(message: "找不到 LibreOffice。可以用 Homebrew 安裝：brew install --cask libreoffice")
        }
        guard let filter = filter(target: target, app: app) else {
            throw OTEngineError(message: "LibreOffice 不支援輸出這個格式。")
        }
        let work = try CVFileNaming.makeScratchDirectory()
        let outDir = work.appendingPathComponent("out", isDirectory: true)
        try FileManager.default.createDirectory(at: outDir, withIntermediateDirectories: true)
        // 用不含特殊字元的檔名交給 soffice（它對某些字元很挑剔），結果再改回原名
        let base = CVFileNaming.sanitize(CVFileNaming.baseName(of: input))
        let staged = work.appendingPathComponent("source." + OTKind.ext(input))
        try FileManager.default.copyItem(at: input, to: staged)
        try FileManager.default.createDirectory(at: profileDirectory, withIntermediateDirectories: true)
        let args = arguments(input: staged.path, outDir: outDir.path, filter: filter,
                             profileURL: profileDirectory.absoluteString, inFilter: inFilter)
        context.progress(0.15, "LibreOffice 轉換中…")
        let size = Double(ToolFiles.size(of: input)) / 1_000_000
        let timeout = min(1200, max(180, 120 + size * 30))
        let output = try await lock.withLock {
            try await OTProcess.run(exe.path, args, timeout: timeout)
        }
        if !output.succeeded {
            let message = output.stderrText.isEmpty ? output.stdoutText : output.stderrText
            throw OTEngineError(message: ConversionError.toolFailed(tool: "LibreOffice", message: message).localizedDescription)
        }
        let produced = ((try? FileManager.default.contentsOfDirectory(at: outDir, includingPropertiesForKeys: nil)) ?? [])
        let ext = String(filter.split(separator: ":").first ?? Substring(target))
        guard let file = produced.first(where: { $0.pathExtension.lowercased() == ext }) else {
            throw OTEngineError(message: "LibreOffice 沒有產生輸出檔。" + (output.stdoutText.isEmpty ? "" : output.stdoutText))
        }
        let final = work.appendingPathComponent(CVFileNaming.fileName(base, target))
        if target == "html" {
            try OTHTMLTools.inlineResources(file, to: final)
        } else if target == "txt" {
            try FileManager.default.moveItem(at: file, to: final)
        } else {
            try FileManager.default.moveItem(at: file, to: final)
        }
        return final
    }
}

/// 執行外部程式並加上逾時（逾時或取消都會終止子程序）。
/// 輸出寫到暫存檔（不用管線，避免子程序的子程序佔住管線而卡住），以輪詢等待結束，絕不會無限等待。
enum OTProcess {
    static func run(_ executable: String, _ arguments: [String], timeout: TimeInterval,
                    environment: [String: String]? = nil, currentDirectory: URL? = nil) async throws -> CVProcess.Output {
        let dir = try CVFileNaming.makeScratchDirectory()
        defer { try? FileManager.default.removeItem(at: dir) }
        let outURL = dir.appendingPathComponent("stdout"), errURL = dir.appendingPathComponent("stderr")
        FileManager.default.createFile(atPath: outURL.path, contents: nil)
        FileManager.default.createFile(atPath: errURL.path, contents: nil)
        let outHandle = try FileHandle(forWritingTo: outURL)
        let errHandle = try FileHandle(forWritingTo: errURL)
        defer { try? outHandle.close(); try? errHandle.close() }

        let process = Process()
        process.executableURL = URL(fileURLWithPath: executable)
        process.arguments = arguments
        if let environment { process.environment = ProcessInfo.processInfo.environment.merging(environment) { _, new in new } }
        if let currentDirectory { process.currentDirectoryURL = currentDirectory }
        process.standardInput = FileHandle.nullDevice
        process.standardOutput = outHandle
        process.standardError = errHandle
        do {
            try process.run()
        } catch {
            throw OTEngineError(message: "無法執行 \((executable as NSString).lastPathComponent)：\(error.localizedDescription)")
        }
        let pid = process.processIdentifier
        let deadline = Date().addingTimeInterval(max(1, timeout))
        func alive() -> Bool { process.isRunning && kill(pid, 0) == 0 }
        var interval: UInt64 = 20_000_000
        while alive() {
            if Task.isCancelled {
                process.terminate()
                throw ConversionError.cancelled
            }
            if Date() > deadline {
                process.terminate()
                try? await Task.sleep(nanoseconds: 300_000_000)
                if alive() { kill(pid, SIGKILL) }
                throw OTEngineError(message: "\((executable as NSString).lastPathComponent) 執行逾時（超過 \(Int(timeout)) 秒）。")
            }
            try? await Task.sleep(nanoseconds: interval)
            interval = min(interval * 2, 200_000_000)
        }
        // 已結束；若 Foundation 還沒收到結束通知，就以有沒有錯誤輸出判斷
        var status: Int32 = 0
        for _ in 0..<20 where process.isRunning { try? await Task.sleep(nanoseconds: 25_000_000) }
        let stderrData = (try? Data(contentsOf: errURL)) ?? Data()
        if !process.isRunning { status = process.terminationStatus } else if !stderrData.isEmpty { status = 1 }
        return CVProcess.Output(status: status, stdout: (try? Data(contentsOf: outURL)) ?? Data(), stderr: stderrData)
    }
}
