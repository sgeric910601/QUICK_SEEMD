import AppKit
import Foundation
import os

private let officeLog = Logger(subsystem: "dev.quickseemd.app", category: "office")

// 用 AppleScript（/usr/bin/osascript 子程序）驅動 Microsoft Word／Excel／PowerPoint 轉檔。
//
// 重點：
// - Office 是沙盒 App，只能寫自己的資料夾。我們把原檔「複製」到 Office 可讀寫的交換資料夾
//   （/var/folders/…/C/<bundle id>/QUICK_SEEMD/QS-<uuid>/），請 Office 開副本、另存到同一個資料夾，
//   再由 QUICK_SEEMD 搬回來交付。因為開的是副本，使用者自己開著的文件完全不會被碰到。
// - 每個 Apple Event 都有逾時；整支腳本也有外部逾時（逾時就終止 osascript 並關掉我們開的文件）。
// - Office 原本沒開時，我們在背景啟動它（不搶焦點），整批工作做完、而且沒有其他文件開著時才結束它。
// - 錯誤 -1743（沒有自動化權限）會告訴使用者去「系統設定 › 隱私權與安全性 › 自動化」打開。

/// 引擎層級的錯誤（自動模式會改用下一個引擎）。
struct OTEngineError: LocalizedError, Sendable {
    let message: String
    var errorDescription: String? { message }
}

// MARK: - 非同步鎖（同一個 Office App 一次只處理一份文件）

actor OTAsyncLock {
    private var busy = false
    private var waiters: [CheckedContinuation<Void, Never>] = []

    func acquire() async {
        if !busy { busy = true; return }
        await withCheckedContinuation { waiters.append($0) }
    }

    func release() {
        if waiters.isEmpty { busy = false } else { waiters.removeFirst().resume() }
    }

    func withLock<T: Sendable>(_ body: @Sendable () async throws -> T) async throws -> T {
        await acquire()
        do {
            let value = try await body()
            release()
            return value
        } catch {
            release()
            throw error
        }
    }
}

// MARK: - Office App 的使用狀態

/// 記錄哪些 Office App 是 QUICK_SEEMD 啟動的，以及目前有幾個工作在用。
final class OTOfficeState: @unchecked Sendable {
    static let shared = OTOfficeState()

    private let lock = NSLock()
    private var launched = Set<OTOfficeApp>()
    private var users: [OTOfficeApp: Int] = [:]
    let locks: [OTOfficeApp: OTAsyncLock] = Dictionary(uniqueKeysWithValues: OTOfficeApp.allCases.map { ($0, OTAsyncLock()) })
    /// 上次成功的交換資料夾種類（0：App 快取資料夾，1：Office 群組容器）
    private var preferredExchange: [OTOfficeApp: Int] = [:]
    /// QUICK_SEEMD 啟動 Office 時它自己開的空白文件（例如「活頁簿1」），結束時可以一起關掉
    private var launchDocuments: [OTOfficeApp: [String]] = [:]

    func setLaunchDocuments(_ app: OTOfficeApp, _ names: [String]) {
        lock.lock(); launchDocuments[app] = names; lock.unlock()
    }

    func takeLaunchDocuments(_ app: OTOfficeApp) -> [String] {
        lock.lock(); defer { lock.unlock() }
        return launchDocuments.removeValue(forKey: app) ?? []
    }

    func beginUse(_ app: OTOfficeApp) {
        lock.lock(); users[app, default: 0] += 1; lock.unlock()
    }

    func markLaunched(_ app: OTOfficeApp) {
        lock.lock(); launched.insert(app); lock.unlock()
    }

    func exchangeOrder(_ app: OTOfficeApp) -> [Int] {
        lock.lock(); defer { lock.unlock() }
        return preferredExchange[app] == 1 ? [1, 0] : [0, 1]
    }

    func rememberExchange(_ app: OTOfficeApp, _ kind: Int) {
        lock.lock(); preferredExchange[app] = kind; lock.unlock()
    }

    /// 工作結束：最後一個使用者離開、而且 App 是我們啟動的，就在沒有其他文件時結束它。
    func endUse(_ app: OTOfficeApp) async {
        let shouldQuit = lock.withLock { () -> Bool in
            users[app, default: 1] -= 1
            let quit = users[app, default: 0] <= 0 && launched.contains(app)
            if quit { launched.remove(app) }
            return quit
        }
        if shouldQuit { await OTOffice.quitIfIdle(app) }
    }
}

/// 一個工具工作（可能有多個檔案）期間使用 Office 的範圍；結束時才決定要不要關掉 Office。
final class OTOfficeScope: @unchecked Sendable {
    private let lock = NSLock()
    private var used = Set<OTOfficeApp>()
    private var engines: [String] = []
    private var fallbacks: [String] = []
    private var pages = 0
    private var outputs = 0

    /// 記錄這次實際使用的引擎與改用其他引擎的原因（最後顯示在結果頁）。
    func record(engine: String, fallbackFrom failed: String? = nil) {
        lock.withLock {
            if !engines.contains(engine) { engines.append(engine) }
            if let failed, !fallbacks.contains(failed) { fallbacks.append(failed) }
        }
    }

    func recordOutput(pages: Int?) {
        lock.withLock {
            outputs += 1
            pages.map { self.pages += $0 }
        }
    }

    /// 例如「完成：共 3 頁 · 使用 Microsoft Word」
    var summary: String? {
        lock.withLock { () -> String? in
            guard outputs > 0 else { return nil }
            var parts: [String] = []
            parts.append(outputs > 1 ? "完成 \(outputs) 個檔案" : "完成")
            if pages > 0 { parts.append("共 \(pages) 頁") }
            if !engines.isEmpty { parts.append("使用 " + engines.joined(separator: "、")) }
            var text = parts.joined(separator: " · ")
            if !fallbacks.isEmpty { text += "（\(fallbacks.joined(separator: "、")) 無法使用，已自動改用其他引擎）" }
            return text
        }
    }

    func use(_ app: OTOfficeApp) {
        lock.lock()
        let isNew = used.insert(app).inserted
        lock.unlock()
        if isNew { OTOfficeState.shared.beginUse(app) }
    }

    func finish() async {
        let apps = lock.withLock { () -> Set<OTOfficeApp> in
            let apps = used
            used.removeAll()
            return apps
        }
        // 即使工作被取消也要收尾，所以在不會被取消的獨立 Task 裡做
        await Task.detached {
            for app in apps { await OTOfficeState.shared.endUse(app) }
        }.value
    }
}

enum OTJobs {
    /// 逐檔處理（像 ToolJob.perFile），整批結束時才收掉 QUICK_SEEMD 啟動的 Office。
    static func perFile(_ body: @escaping @Sendable (_ file: URL, _ context: ToolContext, _ scope: OTOfficeScope) async throws -> [URL]) -> ToolJob {
        ToolJob { files, context in
            let scope = OTOfficeScope()
            do {
                var outputs: [URL] = []
                for (i, file) in files.enumerated() {
                    try context.checkCancellation()
                    let sub = context.segment(i, of: files.count)
                    sub.progress(0, "處理中：\(file.lastPathComponent)")
                    outputs += try await body(file, sub, scope)
                    sub.progress(1)
                }
                await scope.finish()
                context.progress(1, scope.summary)
                return outputs
            } catch {
                await scope.finish()
                throw error
            }
        }
    }
}

// MARK: - 轉換

enum OTOffice {
    /// Word 能另存的格式
    static let wordFormats: Set<String> = ["pdf", "docx", "doc", "rtf", "odt", "txt", "html"]
    static let excelFormats: Set<String> = ["pdf", "xlsx", "xls"]
    static let powerpointFormats: Set<String> = ["pdf", "pptx", "ppt"]

    struct ExcelPDFOptions: Sendable {
        var orientation = "auto"   // auto / portrait / landscape
        var fitWidth = false
        var firstSheetOnly = false
        var gridlines = "keep"     // keep / 1 / 0
    }

    /// 用 Office 把檔案轉成指定格式，回傳 QUICK_SEEMD 暫存資料夾中的結果。
    static func convert(_ input: URL, app: OTOfficeApp, to format: String, excel: ExcelPDFOptions = ExcelPDFOptions(),
                        scope: OTOfficeScope, context: ToolContext) async throws -> URL {
        guard let appURL = app.applicationURL else { throw OTEngineError(message: "這台 Mac 沒有安裝 \(app.appName)。") }
        scope.use(app)
        if !app.isRunning && OTUnresponsiveMemo.recentlyStuck(app.bundleID) {
            throw OTEngineError(message: "\(app.appName) 上次啟動時停在對話框沒有回應，這次先改用其他引擎。打開 \(app.shortName) 處理完畫面後就會恢復使用。")
        }
        let lock = OTOfficeState.shared.locks[app]!
        context.progress(0.05, "等候 \(app.shortName)…")
        return try await lock.withLock {
            try context.checkCancellation()
            let launched = try await ensureRunning(app, appURL: appURL, context: context)
            try await waitUntilReady(app, launched: launched, context: context)
            let order = OTOfficeState.shared.exchangeOrder(app)
            var firstFailure: OTScriptFailure?
            for (i, kind) in order.enumerated() {
                do {
                    let result = try await convertOnce(input, app: app, format: format, excel: excel, exchangeKind: kind, context: context)
                    OTOfficeState.shared.rememberExchange(app, kind)
                    return result
                } catch let failure as OTScriptFailure {
                    officeLog.error("\(app.appName, privacy: .public) 轉換失敗：\(failure.code, privacy: .public) \(failure.message, privacy: .public)")
                    if firstFailure == nil { firstFailure = failure }
                    // 開檔／存檔失敗時換另一個交換資料夾再試一次（權限、逾時等就不用再試）
                    if failure.retryable && i + 1 < order.count && !Task.isCancelled { continue }
                    // 備用資料夾根本建立不了（9003）時，回報第一次真正的錯誤
                    throw (failure.code == 9003 ? firstFailure ?? failure : failure).userError(app: app, file: input)
                }
            }
            throw OTEngineError(message: "\(app.appName) 轉換失敗。")
        }
    }

    private static func convertOnce(_ input: URL, app: OTOfficeApp, format: String, excel: ExcelPDFOptions, exchangeKind: Int,
                                    context: ToolContext) async throws -> URL {
        let fm = FileManager.default
        let marker = "QS-" + UUID().uuidString
        let exchange = try exchangeDirectory(app, kind: exchangeKind).appendingPathComponent(marker, isDirectory: true)
        do {
            try fm.createDirectory(at: exchange.appendingPathComponent("out"), withIntermediateDirectories: true)
        } catch {
            throw OTScriptFailure(code: 9003, message: error.localizedDescription, retryable: true)
        }
        defer { try? fm.removeItem(at: exchange) }

        // 開副本，絕不動到使用者的原檔或已開啟的文件
        let base = CVFileNaming.sanitize(CVFileNaming.baseName(of: input))
        let copy = exchange.appendingPathComponent(CVFileNaming.fileName(base, OTKind.ext(input)))
        do { try fm.copyItem(at: input, to: copy) } catch { throw ConversionError.unreadable(error.localizedDescription) }
        OTWork.stripQuarantine(copy)
        let outExt = format == "html" ? "htm" : format
        let out = exchange.appendingPathComponent("out").appendingPathComponent(CVFileNaming.fileName(base, outExt))

        let size = Double(ToolFiles.size(of: input)) / 1_000_000
        let isPDF = OTKind.ext(input) == "pdf"
        let seconds = Int(min(900, max(120, 90 + size * 25 + (isPDF ? 210 : 0))))
        if isPDF {
            // Word 開 PDF 會跳出「將 PDF 轉換成可編輯文件」的確認視窗：把 Word 叫到前面讓使用者看得到
            await MainActor.run {
                _ = NSRunningApplication.runningApplications(withBundleIdentifier: app.bundleID).first?.activate()
            }
            context.progress(0.2, "請在 Word 的確認視窗按「確定」…")
        }
        var args = [copy.path, out.path, format, marker, String(seconds)]
        if app == .excel {
            args += [excel.orientation, excel.fitWidth ? "1" : "0", excel.firstSheetOnly ? "first" : "all", excel.gridlines]
        }
        context.progress(0.2, "\(app.shortName) 轉換中…")
        do {
            _ = try await OTAppleScript.run(script(for: app), args: args, timeout: TimeInterval(seconds + 15))
        } catch {
            // 取消或逾時：把我們開的那份文件關掉（不存檔），避免留在 Office 裡
            await closeDocuments(app, marker: marker)
            throw error
        }
        context.progress(0.85)

        // 收集結果
        let produced = ((try? fm.contentsOfDirectory(at: out.deletingLastPathComponent(), includingPropertiesForKeys: nil)) ?? [])
            .filter { !$0.lastPathComponent.hasPrefix(".") && !$0.lastPathComponent.hasPrefix("~$") }
        let work = try CVFileNaming.makeScratchDirectory()
        let final = work.appendingPathComponent(CVFileNaming.fileName(base, format))
        if format == "pdf" {
            let pdfs = produced.filter { $0.pathExtension.lowercased() == "pdf" }
                .sorted { $0.lastPathComponent.localizedStandardCompare($1.lastPathComponent) == .orderedAscending }
            guard !pdfs.isEmpty else { throw OTScriptFailure(code: 9004, message: "沒有產生 PDF。", retryable: true) }
            try OTWork.mergePDFs(pdfs, to: final)
        } else if format == "html" {
            guard let html = produced.first(where: { ["htm", "html"].contains($0.pathExtension.lowercased()) }) else {
                throw OTScriptFailure(code: 9004, message: "沒有產生 HTML。", retryable: true)
            }
            try OTHTMLTools.inlineResources(html, to: final)
        } else if format == "txt" {
            guard let txt = produced.first else { throw OTScriptFailure(code: 9004, message: "沒有產生文字檔。", retryable: true) }
            // Word 輸出 UTF-16（Unicode 文字），統一轉成 UTF-8
            let decoded = try CVTextCodec.decode(txt)
            try Data(CVTextCodec.normalizeLineEndings(decoded.text, to: .lf).utf8).write(to: final)
        } else {
            guard let file = produced.first(where: { $0.pathExtension.lowercased() == outExt }) ?? produced.first else {
                throw OTScriptFailure(code: 9004, message: "沒有產生輸出檔。", retryable: true)
            }
            try fm.moveItem(at: file, to: final)
        }
        return final
    }

    // MARK: 啟動與結束

    /// 回傳 true 代表這次是 QUICK_SEEMD 啟動的。
    private static func ensureRunning(_ app: OTOfficeApp, appURL: URL, context: ToolContext) async throws -> Bool {
        if app.isRunning { return false }
        context.progress(0.08, "正在啟動 \(app.appName)…")
        let config = NSWorkspace.OpenConfiguration()
        // 不搶走使用者目前的視窗；不隱藏，萬一 Office 跳出登入或其他對話框，使用者才看得到
        config.activates = false
        config.addsToRecentItems = false
        config.promptsUserIfNeeded = false
        do {
            let running = try await NSWorkspace.shared.openApplication(at: appURL, configuration: config)
            OTOfficeState.shared.markLaunched(app)
            for _ in 0..<120 where !running.isFinishedLaunching {
                try await Task.sleep(nanoseconds: 250_000_000)
            }
            return true
        } catch is CancellationError {
            throw ConversionError.cancelled
        } catch {
            throw OTEngineError(message: "無法啟動 \(app.appName)：\(error.localizedDescription)")
        }
    }

    static func collection(_ app: OTOfficeApp) -> String {
        switch app {
        case .word: return "documents"
        case .excel: return "workbooks"
        case .powerpoint: return "presentations"
        }
    }

    /// 等 Office 能回應 Apple Event（剛啟動還在初始化，或正顯示對話框時會卡住）。
    /// 剛剛才卡住過的程式只快速試一下，讓「自動」引擎馬上改用下一個，不必每次空等。
    /// 同時記下啟動時自動開的空白文件，結束時才知道哪些可以一起關。
    private static func waitUntilReady(_ app: OTOfficeApp, launched: Bool, context: ToolContext) async throws {
        let source = """
        with timeout of 10 seconds
            tell application id "\(app.bundleID)"
                set out to ""
                repeat with d in (get \(collection(app)))
                    try
                        set out to out & (name of d) & linefeed
                    end try
                end repeat
                return out
            end tell
        end timeout
        """
        let recentlyStuck = OTUnresponsiveMemo.recentlyStuck(app.bundleID)
        let deadline = Date().addingTimeInterval(recentlyStuck ? 6 : (launched ? 60 : 30))
        context.progress(0.1, launched ? "等待 \(app.appName) 準備好…" : "連線 \(app.appName)…")
        while true {
            try context.checkCancellation()
            do {
                let names = try await OTAppleScript.run(source, args: [], timeout: recentlyStuck ? 8 : 15)
                if launched {
                    OTOfficeState.shared.setLaunchDocuments(app, names.split(whereSeparator: \.isNewline).map(String.init))
                }
                OTUnresponsiveMemo.clear(app.bundleID)
                officeLog.notice("\(app.appName, privacy: .public) 已就緒")
                return
            } catch let failure as OTScriptFailure {
                officeLog.error("\(app.appName, privacy: .public) 檢查回應：\(failure.code, privacy: .public) \(failure.message, privacy: .public)")
                if [-1743, -1744].contains(failure.code) { throw failure.userError(app: app, file: URL(fileURLWithPath: "/")) }
                // 只有逾時、連不上、還沒啟動完才算「沒回應」；程式回了其他錯誤（例如 -10003）代表它其實有在回應
                let notReady: Set<Int> = [-1712, -600, -609, -1708, -10810, -903, 9999]
                if !notReady.contains(failure.code) && failure.code != 0 {
                    OTUnresponsiveMemo.clear(app.bundleID)
                    officeLog.notice("\(app.appName, privacy: .public) 有回應（\(failure.code, privacy: .public)），視為就緒")
                    return
                }
            } catch {
                officeLog.error("\(app.appName, privacy: .public) 尚未回應：\(error.localizedDescription, privacy: .public)")
            }
            if Date() > deadline { break }
            try await Task.sleep(nanoseconds: 2_000_000_000)
        }
        OTUnresponsiveMemo.mark(app.bundleID)
        throw OTEngineError(message: "\(app.appName) 沒有回應，可能正在顯示對話框（例如第一次啟動的歡迎、登入或授權畫面）。請切換到 \(app.shortName) 處理後再試，或改用其他轉換引擎。")
    }

    /// 只剩 QUICK_SEEMD 啟動時它自己開的空白文件（沒有改過）時才結束；使用者途中自己開了文件就保留。
    static func quitIfIdle(_ app: OTOfficeApp) async {
        guard app.isRunning else { return }
        let launchDocs = OTOfficeState.shared.takeLaunchDocuments(app)
        let source = """
        on run argv
            with timeout of 20 seconds
                tell application id "\(app.bundleID)"
                    set extra to 0
                    repeat with d in (get \(collection(app)))
                        set n to ""
                        try
                            set n to (name of d) as text
                        end try
                        set isSaved to true
                        try
                            set isSaved to saved of d
                        end try
                        if (argv does not contain n) or (isSaved is false) then set extra to extra + 1
                    end repeat
                    if extra is 0 then quit saving no
                end tell
            end timeout
        end run
        """
        _ = try? await OTAppleScript.run(source, args: launchDocs, timeout: 25)
    }

    /// 關掉路徑含有 marker 的文件（不存檔）。
    static func closeDocuments(_ app: OTOfficeApp, marker: String) async {
        guard app.isRunning else { return }
        let source: String
        switch app {
        case .word:
            source = """
            on run argv
                with timeout of 15 seconds
                    tell application id "com.microsoft.Word"
                        repeat with d in (get documents)
                            try
                                if (posix full name of d) contains (item 1 of argv) then close d saving no
                            end try
                        end repeat
                    end tell
                end timeout
            end run
            """
        case .excel:
            source = """
            on run argv
                with timeout of 15 seconds
                    tell application id "com.microsoft.Excel"
                        repeat with w in (get workbooks)
                            try
                                if (full name of w) contains (item 1 of argv) then close w saving no
                            end try
                        end repeat
                    end tell
                end timeout
            end run
            """
        case .powerpoint:
            source = """
            on run argv
                with timeout of 15 seconds
                    tell application id "com.microsoft.Powerpoint"
                        repeat with p in (get presentations)
                            try
                                if (full name of p) contains (item 1 of argv) then close p saving no
                            end try
                        end repeat
                    end tell
                end timeout
            end run
            """
        }
        await Task.detached { _ = try? await OTAppleScript.run(source, args: [marker], timeout: 20) }.value
    }

    // MARK: 交換資料夾

    /// 0：Office 自己的快取資料夾（/var/folders/…/C/<bundle id>，沙盒內可讀寫、不受「其他 App 資料」保護）
    /// 1：Office 群組容器（三個 Office App 共用，部分系統會詢問「存取其他 App 的資料」）
    static func exchangeDirectory(_ app: OTOfficeApp, kind: Int) throws -> URL {
        let fm = FileManager.default
        let dir: URL
        if kind == 0 {
            var buffer = [CChar](repeating: 0, count: Int(PATH_MAX))
            let n = confstr(_CS_DARWIN_USER_CACHE_DIR, &buffer, buffer.count)
            guard n > 0 else { throw OTScriptFailure(code: 9003, message: "找不到快取資料夾", retryable: true) }
            let cache = URL(fileURLWithPath: String(cString: buffer), isDirectory: true).resolvingSymlinksInPath()
            dir = cache.appendingPathComponent(app.bundleID, isDirectory: true).appendingPathComponent("QUICK_SEEMD", isDirectory: true)
        } else {
            dir = fm.homeDirectoryForCurrentUser
                .appendingPathComponent("Library/Group Containers/UBF8T346G9.Office/QUICK_SEEMD", isDirectory: true)
        }
        do {
            try fm.createDirectory(at: dir, withIntermediateDirectories: true)
        } catch {
            throw OTScriptFailure(code: 9003, message: error.localizedDescription, retryable: true)
        }
        return dir
    }

    // MARK: AppleScript

    static func script(for app: OTOfficeApp) -> String {
        switch app {
        case .word: return wordScript
        case .excel: return excelScript
        case .powerpoint: return powerpointScript
        }
    }

    static let wordScript = #"""
    on run argv
        set inPath to item 1 of argv
        set outPath to item 2 of argv
        set fmt to item 3 of argv
        set marker to item 4 of argv
        set secs to (item 5 of argv) as integer
        set inFile to POSIX file inPath
        set outHFS to (POSIX file outPath) as text
        with timeout of secs seconds
            tell application id "com.microsoft.Word"
                set oldAlerts to missing value
                try
                    set oldAlerts to display alerts
                    set display alerts to alerts none
                end try
                try
                    open inFile confirm conversions false read only true add to recent files false
                on error
                    open inFile
                end try
                set theDoc to missing value
                repeat 240 times
                    try
                        repeat with d in (get documents)
                            try
                                if (posix full name of d) contains marker then
                                    set theDoc to contents of d
                                    exit repeat
                                end if
                            end try
                        end repeat
                    end try
                    if theDoc is not missing value then exit repeat
                    delay 0.25
                end repeat
                if theDoc is missing value then
                    try
                        if oldAlerts is not missing value then set display alerts to oldAlerts
                    end try
                    error "QS_OPEN_FAILED" number 9001
                end if
                try
                    if fmt is "pdf" then
                        save as theDoc file name outHFS file format format PDF
                    else if fmt is "docx" then
                        save as theDoc file name outHFS file format format document
                    else if fmt is "doc" then
                        save as theDoc file name outHFS file format format document97
                    else if fmt is "rtf" then
                        save as theDoc file name outHFS file format format rtf
                    else if fmt is "odt" then
                        save as theDoc file name outHFS file format format ODT
                    else if fmt is "txt" then
                        save as theDoc file name outHFS file format format Unicode text
                    else if fmt is "html" then
                        save as theDoc file name outHFS file format format filtered HTML
                    else
                        error "QS_BAD_FORMAT" number 9002
                    end if
                on error errMsg number errNum
                    my closeMarked(marker)
                    try
                        if oldAlerts is not missing value then set display alerts to oldAlerts
                    end try
                    error errMsg number errNum
                end try
                -- 另存成其他格式後，文件會變成新檔：用路徑中的 marker 找出來關掉
                my closeMarked(marker)
                try
                    if oldAlerts is not missing value then set display alerts to oldAlerts
                end try
            end tell
        end timeout
        return "ok"
    end run

    on closeMarked(marker)
        tell application id "com.microsoft.Word"
            repeat with d in (get documents)
                try
                    if (posix full name of d) contains marker then close d saving no
                end try
            end repeat
        end tell
    end closeMarked
    """#

    static let excelScript = #"""
    on run argv
        set inPath to item 1 of argv
        set outPath to item 2 of argv
        set fmt to item 3 of argv
        set marker to item 4 of argv
        set secs to (item 5 of argv) as integer
        set orient to item 6 of argv
        set fitWide to item 7 of argv
        set sheetScope to item 8 of argv
        set grid to item 9 of argv
        set inHFS to (POSIX file inPath) as text
        set outHFS to (POSIX file outPath) as text
        with timeout of secs seconds
            tell application id "com.microsoft.Excel"
                set oldAlerts to display alerts
                set display alerts to false
                set wb to missing value
                try
                    set wb to open workbook workbook file name inHFS update links do not update links read only true ignore read only recommended true add to mru false
                end try
                if wb is missing value then
                    repeat 120 times
                        try
                            repeat with w in (get workbooks)
                                try
                                    if (full name of w) contains marker then
                                        set wb to contents of w
                                        exit repeat
                                    end if
                                end try
                            end repeat
                        end try
                        if wb is not missing value then exit repeat
                        delay 0.25
                    end repeat
                end if
                if wb is missing value then
                    set display alerts to oldAlerts
                    error "QS_OPEN_FAILED" number 9001
                end if
                try
                    if fmt is "pdf" then
                        if orient is not "auto" or fitWide is "1" or grid is not "keep" then
                            repeat with sh in (get worksheets of wb)
                                set ps to missing value
                                try
                                    set ps to page setup object of sh
                                end try
                                if ps is not missing value then
                                    if orient is "landscape" then
                                        try
                                            set orientation of ps to landscape
                                        end try
                                    else if orient is "portrait" then
                                        try
                                            set orientation of ps to portrait
                                        end try
                                    end if
                                    if fitWide is "1" then
                                        try
                                            set zoom of ps to false
                                        end try
                                        try
                                            set fit to pages wide of ps to 1
                                        end try
                                        try
                                            set fit to pages tall of ps to false
                                        end try
                                        try
                                            set fit to pages tall of ps to 0
                                        end try
                                    end if
                                    if grid is "1" then
                                        try
                                            set print gridlines of ps to true
                                        end try
                                    else if grid is "0" then
                                        try
                                            set print gridlines of ps to false
                                        end try
                                    end if
                                end if
                            end repeat
                        end if
                        if sheetScope is "first" then
                            -- 只要第一個工作表：在副本裡刪掉其他工作表（副本不會存檔）
                            try
                                repeat while (count of sheets of wb) > 1
                                    delete sheet 2 of wb
                                end repeat
                            end try
                        end if
                        save workbook as wb filename outHFS file format PDF file format
                    else if fmt is "xlsx" then
                        save workbook as wb filename outHFS file format Excel XML file format
                    else if fmt is "xls" then
                        save workbook as wb filename outHFS file format Excel98to2004 file format
                    else
                        error "QS_BAD_FORMAT" number 9002
                    end if
                on error errMsg number errNum
                    my closeMarked(marker)
                    set display alerts to oldAlerts
                    error errMsg number errNum
                end try
                my closeMarked(marker)
                set display alerts to oldAlerts
            end tell
        end timeout
        return "ok"
    end run

    on closeMarked(marker)
        tell application id "com.microsoft.Excel"
            repeat with w in (get workbooks)
                try
                    if (full name of w) contains marker then close w saving no
                end try
            end repeat
        end tell
    end closeMarked
    """#

    static let powerpointScript = #"""
    on run argv
        set inPath to item 1 of argv
        set outPath to item 2 of argv
        set fmt to item 3 of argv
        set marker to item 4 of argv
        set secs to (item 5 of argv) as integer
        set inFile to POSIX file inPath
        set outHFS to (POSIX file outPath) as text
        with timeout of secs seconds
            tell application id "com.microsoft.Powerpoint"
                open inFile
                set thePres to missing value
                repeat 240 times
                    try
                        repeat with p in (get presentations)
                            try
                                if (full name of p) contains marker then
                                    set thePres to contents of p
                                    exit repeat
                                end if
                            end try
                        end repeat
                    end try
                    if thePres is not missing value then exit repeat
                    delay 0.25
                end repeat
                if thePres is missing value then error "QS_OPEN_FAILED" number 9001
                try
                    if fmt is "pdf" then
                        save thePres in outHFS as save as PDF
                    else if fmt is "pptx" then
                        save thePres in outHFS as save as Open XML presentation
                    else if fmt is "ppt" then
                        save thePres in outHFS as save as presentation
                    else
                        error "QS_BAD_FORMAT" number 9002
                    end if
                on error errMsg number errNum
                    my closeMarked(marker)
                    error errMsg number errNum
                end try
                my closeMarked(marker)
            end tell
        end timeout
        return "ok"
    end run

    on closeMarked(marker)
        tell application id "com.microsoft.Powerpoint"
            repeat with p in (get presentations)
                try
                    if (full name of p) contains marker then close p saving no
                end try
            end repeat
        end tell
    end closeMarked
    """#
}

// MARK: - osascript 執行

/// AppleScript 執行失敗（含錯誤碼），之後再轉成給使用者看的中文訊息。
struct OTScriptFailure: Error, Sendable {
    let code: Int
    let message: String
    /// 換個交換資料夾再試一次可能會成功（開檔、存檔失敗）
    var retryable: Bool

    func userError(app: OTOfficeApp, file: URL) -> Error {
        switch code {
        case -1743, -1744:
            return OTEngineError(message: "QUICK_SEEMD 沒有權限控制 \(app.appName)。請到「系統設定 › 隱私權與安全性 › 自動化」，在 QUICK_SEEMD 底下打開「\(app.appName)」，然後再試一次。")
        case -1712, 9999:
            return OTEngineError(message: "\(app.appName) 沒有回應（等候逾時）。如果畫面上有「QUICK_SEEMD 想要控制 \(app.appName)」的詢問請按「好」；\(app.shortName) 也可能正在顯示登入或其他對話框，請切換過去處理後再試，或改用其他轉換引擎。")
        case -600, -609, -10810, -10814:
            return OTEngineError(message: "\(app.appName) 沒有回應或意外結束，請稍後再試。")
        case 9001:
            return OTEngineError(message: "\(app.appName) 無法開啟「\(file.lastPathComponent)」，檔案可能已損毀、受密碼保護，或不是 \(app.shortName) 支援的格式。")
        case 9002:
            return OTEngineError(message: "\(app.appName) 不支援輸出這個格式。")
        default:
            let detail = message.trimmingCharacters(in: .whitespacesAndNewlines)
            return OTEngineError(message: "\(app.appName) 轉換失敗" + (detail.isEmpty ? "。" : "：\(detail)（\(code)）"))
        }
    }
}

enum OTAppleScript {
    /// 以 osascript 執行 AppleScript 原始碼（參數經由 `on run argv` 傳入），逾時就終止。
    /// 取消工作時 osascript 也會被終止。
    @discardableResult
    static func run(_ source: String, args: [String], timeout: TimeInterval) async throws -> String {
        let dir = try CVFileNaming.makeScratchDirectory()
        defer { try? FileManager.default.removeItem(at: dir) }
        let script = dir.appendingPathComponent("convert.applescript")
        try Data(source.utf8).write(to: script)
        let output: CVProcess.Output
        do {
            output = try await OTProcess.run("/usr/bin/osascript", [script.path] + args, timeout: timeout)
        } catch let error as OTEngineError where error.message.contains("逾時") {
            throw OTScriptFailure(code: 9999, message: "timeout", retryable: false)
        }
        guard output.succeeded else { throw parseFailure(output.stderrText) }
        return output.stdoutText.trimmingCharacters(in: .whitespacesAndNewlines)
    }

    /// osascript 的錯誤格式：「…: execution error: Microsoft Word got an error: …（-1712）」
    static func parseFailure(_ stderr: String) -> OTScriptFailure {
        let text = stderr.trimmingCharacters(in: .whitespacesAndNewlines)
        var code = -1
        if let range = text.range(of: #"\((-?\d+)\)\s*$"#, options: .regularExpression) {
            code = Int(text[range].trimmingCharacters(in: CharacterSet(charactersIn: "() \n"))) ?? -1
        }
        var message = text
        if let r = message.range(of: "execution error: ") { message = String(message[r.upperBound...]) }
        if let r = message.range(of: #"\s*\(-?\d+\)\s*$"#, options: .regularExpression) { message.removeSubrange(r) }
        if message.contains("QS_OPEN_FAILED") { code = 9001 }
        if message.contains("QS_BAD_FORMAT") { code = 9002 }
        let permanent: Set<Int> = [-1743, -1744, -1712, -600, -609, -10810, -10814, 9002, 9999]
        return OTScriptFailure(code: code, message: message, retryable: !permanent.contains(code))
    }
}

// MARK: - HTML 小工具

enum OTHTMLTools {
    /// Word 的「網頁（已篩選）」會把圖片放在旁邊的資料夾；這裡改成內嵌（data:），輸出單一 UTF-8 HTML 檔。
    static func inlineResources(_ html: URL, to out: URL) throws {
        let decoded = try CVTextCodec.decode(html)
        var text = decoded.text
        let base = html.deletingLastPathComponent()
        let pattern = #"(src|href)\s*=\s*"([^"]+)""#
        guard let regex = try? NSRegularExpression(pattern: pattern, options: [.caseInsensitive]) else { return }
        let ns = text as NSString
        var replacements: [(NSRange, String)] = []
        for match in regex.matches(in: text, range: NSRange(location: 0, length: ns.length)) {
            let attr = ns.substring(with: match.range(at: 1))
            let value = ns.substring(with: match.range(at: 2))
            if value.hasPrefix("data:") || value.contains("://") || value.hasPrefix("#") || value.hasPrefix("mailto:") { continue }
            let path = value.removingPercentEncoding ?? value
            let file = base.appendingPathComponent(path)
            guard let data = try? Data(contentsOf: file), data.count < 30_000_000 else { continue }
            let mime: String
            switch file.pathExtension.lowercased() {
            case "png": mime = "image/png"
            case "jpg", "jpeg": mime = "image/jpeg"
            case "gif": mime = "image/gif"
            case "svg": mime = "image/svg+xml"
            case "css": mime = "text/css"
            default: continue
            }
            replacements.append((match.range, "\(attr)=\"data:\(mime);base64,\(data.base64EncodedString())\""))
        }
        let mutable = NSMutableString(string: text)
        for (range, value) in replacements.reversed() { mutable.replaceCharacters(in: range, with: value) }
        text = mutable as String
        // 統一宣告為 UTF-8
        text = text.replacingOccurrences(of: #"charset\s*=\s*["']?[A-Za-z0-9_\-]+"#, with: "charset=utf-8", options: [.regularExpression, .caseInsensitive])
        try Data(text.utf8).write(to: out)
    }
}

/// 記住最近沒有回應的 Office 程式（例如卡在第一次啟動的歡迎畫面）。存在使用者設定裡，重開 App 也記得；
/// 6 小時內它沒在執行時就不去啟動它（自動引擎直接改用下一個）；它在執行時只快速確認一下，能回應就恢復使用。
enum OTUnresponsiveMemo {
    private static func key(_ bundleID: String) -> String { "Office.unresponsive.\(bundleID)" }

    static func mark(_ bundleID: String) { UserDefaults.standard.set(Date(), forKey: key(bundleID)) }
    static func clear(_ bundleID: String) { UserDefaults.standard.removeObject(forKey: key(bundleID)) }
    static func recentlyStuck(_ bundleID: String) -> Bool {
        guard let t = UserDefaults.standard.object(forKey: key(bundleID)) as? Date else { return false }
        return Date().timeIntervalSince(t) < 6 * 3600
    }
}
