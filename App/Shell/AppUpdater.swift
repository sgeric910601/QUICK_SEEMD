import AppKit
import os

private let updateLog = Logger(subsystem: "dev.quickseemd.app", category: "update")

/// 一鍵更新：從 GitHub 取得最新原始碼、在這台 Mac 上編譯，用本機固定憑證簽署後替換自己並重新開啟。
/// 每一版都用同一張憑證簽署，macOS 會把它當成同一個 App，已經給過的權限都會保留。
@MainActor
final class AppUpdater: ObservableObject {
    static let shared = AppUpdater()

    enum State: Equatable {
        case idle
        case checking
        case upToDate
        /// 這一版比 GitHub 上的還新（還沒推送），不提供更新以免裝回舊版。
        case unpublished
        case available(String)
        case updating
        case failed(String)
    }

    @Published private(set) var state: State = .idle
    @Published private(set) var lastChecked: Date?

    nonisolated static let autoCheckKey = "Update.autoCheck"
    nonisolated static let repositoryKey = "Update.repository"
    nonisolated private static let pendingKey = "Update.pendingFrom"
    nonisolated private static let notifiedKey = "Update.notified"
    private var timer: Timer?
    /// 正在進行的檢查；同一時間只跑一個，後來的呼叫等它完成（避免兩個 git 同時動原始碼資料夾）。
    private var runningCheck: Task<Void, Never>?
    /// 上次比對過的 GitHub 版本與結果；版本沒變就不必重新下載原始碼。
    private var resolvedRemote: (commit: String, state: State)?

    /// 這一版編譯時的原始碼提交（由 scripts/build.sh 寫入 Info.plist）。
    let localCommit: String = {
        let value = Bundle.main.object(forInfoDictionaryKey: "QSSourceCommit") as? String ?? ""
        return value.hasPrefix("$(") ? "" : value
    }()

    /// 這一版原始碼提交的時間（Unix 秒），用來判斷 GitHub 上的版本是新是舊。
    let localCommitTime: Int = {
        Int(Bundle.main.object(forInfoDictionaryKey: "QSSourceTime") as? String ?? "") ?? 0
    }()

    /// 更新來源；可用 `defaults write dev.quickseemd.app Update.repository <網址>` 改成別的倉庫。
    /// 只接受 https:// 、ssh:// 或 git@ 開頭的網址，其他值（例如以 - 開頭、會被 git 當成選項的字串）一律不用。
    var repository: String {
        let custom = UserDefaults.standard.string(forKey: Self.repositoryKey) ?? ""
        let value = custom.isEmpty ? Bundle.main.object(forInfoDictionaryKey: "QSSourceRepository") as? String ?? "" : custom
        return Self.isAllowedRepository(value) ? value : ""
    }

    nonisolated static func isAllowedRepository(_ value: String) -> Bool {
        guard !value.contains(where: \.isWhitespace) else { return false }
        if value.hasPrefix("https://") || value.hasPrefix("ssh://") { return true }
        // git@主機:路徑；主機只能是一般網域字元，否則 git 可能把它當成本機路徑
        return value.range(of: #"^git@[A-Za-z0-9.-]+:[^/-]"#, options: .regularExpression) != nil
    }

    var canUpdate: Bool { !localCommit.isEmpty && !repository.isEmpty }
    var versionText: String { localCommit.isEmpty ? "開發版" : String(localCommit.prefix(7)) }

    static var autoCheck: Bool {
        get { UserDefaults.standard.object(forKey: autoCheckKey) as? Bool ?? true }
        set { UserDefaults.standard.set(newValue, forKey: autoCheckKey) }
    }

    // MARK: 啟動與自動檢查

    func start() {
        reportFinishedUpdate()
        guard canUpdate else { return }
        DispatchQueue.main.asyncAfter(deadline: .now() + 15) { [weak self] in self?.autoCheckIfNeeded() }
        timer = Timer.scheduledTimer(withTimeInterval: 6 * 3600, repeats: true) { _ in
            MainActor.assumeIsolated { AppUpdater.shared.autoCheckIfNeeded() }
        }
    }

    private func autoCheckIfNeeded() {
        guard Self.autoCheck, state != .updating else { return }
        Task {
            await check()
            if case .available(let remote) = state, UserDefaults.standard.string(forKey: Self.notifiedKey) != remote {
                UserDefaults.standard.set(remote, forKey: Self.notifiedKey)
                QuickActionRunner.shared.notify(title: "QUICK_SEEMD 有新版本", symbol: "arrow.down.circle",
                                                message: "到選單列按「更新到最新版本」，約 3–5 分鐘完成")
            }
        }
    }

    /// 更新後重新開啟時，依安裝記錄告訴使用者結果。
    private func reportFinishedUpdate() {
        guard UserDefaults.standard.string(forKey: Self.pendingKey) != nil else { return }
        UserDefaults.standard.removeObject(forKey: Self.pendingKey)
        let log = (try? String(contentsOf: Self.logURL, encoding: .utf8)) ?? ""
        if log.contains("✓ 完成") {
            QuickActionRunner.shared.notify(title: "已更新到最新版本", symbol: "checkmark.seal",
                                            message: "版本 \(versionText)，權限設定都已保留")
        } else {
            QuickActionRunner.shared.notify(title: "更新沒有完成", symbol: "exclamationmark.triangle",
                                            message: "詳細記錄：\(Self.logURL.path)")
        }
    }

    // MARK: 檢查

    func check() async {
        guard canUpdate else { state = .failed("這一版不是用安裝腳本編譯的，無法自動更新。"); return }
        guard state != .updating else { return }
        if let runningCheck { await runningCheck.value; return }
        let task = Task { await performCheck() }
        runningCheck = task
        await task.value
        runningCheck = nil
    }

    private func performCheck() async {
        state = .checking
        let result: State
        do {
            let out = try await Self.git(["ls-remote", "--", repository, "refs/heads/main"])
            guard let remote = out.split(whereSeparator: { $0 == "\t" || $0 == "\n" }).first.map(String.init), remote.count == 40 else {
                throw ConversionError.other("讀不到 GitHub 上的版本。")
            }
            if remote == localCommit {
                result = .upToDate
            } else if let resolvedRemote, resolvedRemote.commit == remote {
                result = resolvedRemote.state
            } else {
                // 版本不同：先抓下 GitHub 上的最新提交，比較時間（也處理過強制推送的情況）
                try await Self.prepareSource(repository: repository)
                let time = Int(try await Self.git(["log", "-1", "--format=%ct", "HEAD"], in: Self.sourceDirectory)
                    .trimmingCharacters(in: .whitespacesAndNewlines)) ?? 0
                result = time > localCommitTime ? .available(remote) : .unpublished
                resolvedRemote = (remote, result)
            }
            lastChecked = Date()
            updateLog.notice("檢查更新：本機 \(self.localCommit.prefix(7), privacy: .public)，GitHub \(remote.prefix(7), privacy: .public)")
        } catch {
            updateLog.error("檢查更新失敗：\(error.localizedDescription, privacy: .public)")
            result = .failed(error.localizedDescription)
        }
        // 檢查期間已經開始更新的話，不要把「更新中」蓋掉
        if state == .checking { state = result }
    }

    /// 選單列的「檢查更新…」：有新版本就直接問要不要更新。
    func checkInteractively() {
        Task {
            await check()
            switch state {
            case .available:
                NSApp.activate(ignoringOtherApps: true)
                let alert = NSAlert()
                alert.messageText = "有新版本可以更新"
                alert.informativeText = "會在背景下載並編譯（約 3–5 分鐘），完成後自動重新開啟。已經給過的權限都會保留。"
                alert.addButton(withTitle: "立即更新")
                alert.addButton(withTitle: "稍後")
                if alert.runModal() == .alertFirstButtonReturn { update() }
            case .upToDate:
                QuickActionRunner.shared.notify(title: "已是最新版本", symbol: "checkmark.seal", message: "版本 \(versionText)")
            case .unpublished:
                QuickActionRunner.shared.notify(title: "已是最新版本", symbol: "checkmark.seal",
                                                message: "這一版（\(versionText)）比 GitHub 上的還新")
            case .failed(let reason):
                QuickActionRunner.shared.notify(title: "無法檢查更新", symbol: "exclamationmark.triangle", message: reason)
            default:
                break
            }
        }
    }

    // MARK: 更新

    func update() {
        guard canUpdate, state != .updating else { return }
        state = .updating
        let repository = repository
        let from = localCommit
        let pendingCheck = runningCheck
        let job = ToolJob { _, context in
            do {
                // 還有檢查在動原始碼資料夾的話，等它結束再開始
                await pendingCheck?.value
                try await Self.performUpdate(repository: repository, from: from) { context.progress($0, $1) }
                await MainActor.run { NSApp.terminate(nil) }
                return []
            } catch {
                updateLog.error("更新失敗：\(error.localizedDescription, privacy: .public)")
                await MainActor.run { AppUpdater.shared.state = .failed(error.localizedDescription) }
                throw error
            }
        }
        QuickActionRunner.shared.enqueue(title: "更新 QUICK_SEEMD", symbol: "arrow.down.circle", tint: ToolPalette.teal,
                                         files: [], toolID: nil, job: job)
    }

    /// 命令列測試用：`open -n -W QUICK_SEEMD.app --args --qs-update`，不顯示介面，跑完整個更新流程。
    nonisolated static var isHeadlessRequested: Bool { CommandLine.arguments.contains("--qs-update") }

    func runHeadlessUpdate() {
        let repository = repository
        let from = localCommit
        Task {
            do {
                guard canUpdate else { throw ConversionError.other("這一版沒有記錄原始碼版本，無法更新。") }
                try await Self.performUpdate(repository: repository, from: from) { _, message in
                    updateLog.notice("\(message, privacy: .public)")
                }
            } catch {
                updateLog.error("更新失敗：\(error.localizedDescription, privacy: .public)")
            }
            NSApp.terminate(nil)
        }
    }

    // MARK: 背景工作（不在主執行緒）

    /// 下載 → 編譯 → 交給獨立的安裝程序（之後由呼叫端結束 App）。
    nonisolated static func performUpdate(repository: String, from: String,
                                          progress: @escaping @Sendable (Double, String) -> Void) async throws {
        progress(0.05, "下載最新原始碼…")
        try await prepareSource(repository: repository)
        progress(0.15, "編譯中（約 3–5 分鐘）…")
        let app = try await build { line, fraction in progress(fraction, line) }
        progress(0.95, "安裝並重新開啟…")
        try launchInstaller(app: app)
        // 安裝程序確定啟動了才記下；它會等這個 App 結束才替換，不會搶在這之前重新開啟
        UserDefaults.standard.set(from, forKey: pendingKey)
    }

    nonisolated static var supportDirectory: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("QUICK_SEEMD", isDirectory: true)
    }
    nonisolated static var sourceDirectory: URL { supportDirectory.appendingPathComponent("Source", isDirectory: true) }
    nonisolated static var buildDirectory: URL {
        FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("QUICK_SEEMD/Build", isDirectory: true)
    }
    nonisolated static var logURL: URL {
        FileManager.default.urls(for: .libraryDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("Logs/QUICK_SEEMD/update.log")
    }

    /// 私人倉庫需要登入：有安裝 GitHub 命令列工具就借用它已登入的帳號。
    nonisolated private static var credentialArguments: [String] {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        let candidates = ["/opt/homebrew/bin/gh", "/usr/local/bin/gh", "/opt/local/bin/gh", home + "/.local/bin/gh", home + "/bin/gh"]
        guard let gh = candidates.first(where: { FileManager.default.isExecutableFile(atPath: $0) }) else { return [] }
        return ["-c", "credential.helper=", "-c", "credential.helper=!\"\(gh)\" auth git-credential"]
    }

    @discardableResult
    nonisolated private static func git(_ arguments: [String], in directory: URL? = nil) async throws -> String {
        let output = try await CVProcess.run("/usr/bin/git", credentialArguments + arguments,
                                             environment: ["GIT_TERMINAL_PROMPT": "0"], currentDirectory: directory)
        guard output.succeeded else {
            let message = output.stderrText.trimmingCharacters(in: .whitespacesAndNewlines)
            if ["could not read Username", "Authentication failed", "Repository not found", "terminal prompts disabled"]
                .contains(where: message.contains) {
                throw ConversionError.other("無法登入 GitHub 倉庫。請先在終端機執行 gh auth login 登入一次。")
            }
            if message.contains("Could not resolve host") || message.contains("unable to access") {
                throw ConversionError.other("連不上 GitHub，請確認網路連線。")
            }
            throw ConversionError.other("git：" + (message.split(separator: "\n").last.map(String.init) ?? "未知錯誤"))
        }
        return output.stdoutText
    }

    /// 第一次先複製一份原始碼，之後只抓最新版本（這個資料夾由 App 自己管理，不放使用者的修改）。
    nonisolated private static func prepareSource(repository: String) async throws {
        let fm = FileManager.default
        let source = sourceDirectory
        if fm.fileExists(atPath: source.appendingPathComponent(".git").path) {
            try await git(["remote", "set-url", "origin", "--", repository], in: source)
            try await git(["fetch", "--depth", "1", "origin", "main"], in: source)
            try await git(["reset", "--hard", "FETCH_HEAD"], in: source)
            try await git(["clean", "-fdx", "--quiet"], in: source)
        } else {
            try? fm.removeItem(at: source)
            try fm.createDirectory(at: supportDirectory, withIntermediateDirectories: true)
            try await git(["clone", "--depth", "1", "--branch", "main", "--", repository, source.path])
        }
    }

    /// 執行 scripts/build.sh，回傳編譯並簽署好的 App。
    nonisolated private static func build(progress: @escaping @Sendable (String, Double) -> Void) async throws -> URL {
        let script = sourceDirectory.appendingPathComponent("scripts/build.sh")
        let start = Date()
        let collected = UpdateLines()
        let output = try await CVProcess.run("/bin/bash", [script.path], environment: ["BUILD_DIR": buildDirectory.path],
                                             currentDirectory: sourceDirectory) { line in
            collected.append(line)
            guard line.hasPrefix("▸") else { return }
            // 編譯大約 4 分鐘：依經過時間推估進度
            let fraction = min(0.9, 0.15 + Date().timeIntervalSince(start) / 240 * 0.75)
            progress(String(line.dropFirst()).trimmingCharacters(in: .whitespaces), fraction)
        }
        let lines = collected.lines
        guard output.succeeded, let path = lines.last(where: { $0.hasPrefix("APP=") })?.dropFirst(4) else {
            let reason = lines.last(where: { $0.hasPrefix("✗") }) ?? "編譯失敗"
            throw ConversionError.other(reason + "（記錄：\(buildDirectory.appendingPathComponent("build.log").path)）")
        }
        return URL(fileURLWithPath: String(path))
    }

    /// 開一個獨立的安裝程序：等這個 App 結束後替換、重新登記擴充並重新開啟。
    nonisolated private static func launchInstaller(app: URL) throws {
        let script = sourceDirectory.appendingPathComponent("scripts/install.sh").path
        try FileManager.default.createDirectory(at: logURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        try spawnDetached("/bin/bash", [script, "--app", app.path, "--wait-pid", String(getpid())], log: logURL)
    }

    /// 用新的工作階段啟動（POSIX_SPAWN_SETSID），App 結束後安裝程序才不會被一起關掉。
    nonisolated private static func spawnDetached(_ executable: String, _ arguments: [String], log: URL) throws {
        var attributes: posix_spawnattr_t?
        posix_spawnattr_init(&attributes)
        defer { posix_spawnattr_destroy(&attributes) }
        posix_spawnattr_setflags(&attributes, Int16(POSIX_SPAWN_SETSID))
        var actions: posix_spawn_file_actions_t?
        posix_spawn_file_actions_init(&actions)
        defer { posix_spawn_file_actions_destroy(&actions) }
        posix_spawn_file_actions_addopen(&actions, 0, "/dev/null", O_RDONLY, 0)
        posix_spawn_file_actions_addopen(&actions, 1, log.path, O_WRONLY | O_CREAT | O_TRUNC, 0o644)
        posix_spawn_file_actions_adddup2(&actions, 1, 2)
        let argv: [UnsafeMutablePointer<CChar>?] = ([executable] + arguments).map { strdup($0) } + [nil]
        defer { argv.forEach { free($0) } }
        var pid: pid_t = 0
        let status = posix_spawn(&pid, executable, &actions, &attributes, argv, environ)
        guard status == 0 else { throw ConversionError.other("無法啟動安裝程序（\(status)）。") }
        updateLog.notice("安裝程序已啟動：\(pid)")
    }
}

/// 收集編譯腳本輸出的每一行（在背景執行緒寫入）。
private final class UpdateLines: @unchecked Sendable {
    private let lock = NSLock()
    private var storage: [String] = []
    func append(_ line: String) { lock.lock(); storage.append(line); lock.unlock() }
    var lines: [String] { lock.lock(); defer { lock.unlock() }; return storage }
}
