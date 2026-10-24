import AppKit
import os
import Security

private let updateLog = Logger(subsystem: "dev.quickseemd.app", category: "update")

/// 一鍵更新：從 GitHub Release 下載最新的 DMG，確認是同一張憑證簽署的，替換自己並重新開啟。
/// 每一版都用同一張本機憑證簽署（scripts/release.sh），macOS 會把它當成同一個 App，已經給過的權限都會保留。
@MainActor
final class AppUpdater: ObservableObject {
    static let shared = AppUpdater()

    struct Release: Equatable, Sendable {
        let version: String
        let notes: String
        let page: URL
        let assetName: String
        /// API 下載網址（私人倉庫要帶權杖才能下載）
        let assetURL: URL
    }

    enum State: Equatable {
        case idle
        case checking
        case upToDate
        case available(Release)
        case updating
        case failed(String)
    }

    @Published private(set) var state: State = .idle
    @Published private(set) var lastChecked: Date?

    nonisolated static let autoCheckKey = "Update.autoCheck"
    nonisolated static let repositoryKey = "Update.repository"
    nonisolated private static let pendingKey = "Update.pendingFrom"
    nonisolated private static let notifiedKey = "Update.notified"
    nonisolated static let bundleID = "dev.quickseemd.app"
    private var timer: Timer?

    let currentVersion = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "0"

    /// 編譯時的原始碼提交（由 scripts/build.sh 寫入 Info.plist），顯示用。
    let localCommit: String = {
        let value = Bundle.main.object(forInfoDictionaryKey: "QSSourceCommit") as? String ?? ""
        return value.hasPrefix("$(") ? "" : value
    }()

    /// 「擁有者/倉庫」。可用 `defaults write dev.quickseemd.app Update.repository 擁有者/倉庫` 改成別的倉庫。
    var repository: String? {
        if let custom = UserDefaults.standard.string(forKey: Self.repositoryKey), let parsed = Self.parseRepository(custom) {
            return parsed
        }
        return Self.parseRepository(Bundle.main.object(forInfoDictionaryKey: "QSSourceRepository") as? String ?? "")
    }

    var canUpdate: Bool { repository != nil }
    var versionText: String { localCommit.isEmpty ? currentVersion : "\(currentVersion)（\(localCommit.prefix(7))）" }

    static var autoCheck: Bool {
        get { UserDefaults.standard.object(forKey: autoCheckKey) as? Bool ?? true }
        set { UserDefaults.standard.set(newValue, forKey: autoCheckKey) }
    }

    /// 接受 `擁有者/倉庫`、`https://github.com/擁有者/倉庫(.git)`、`git@github.com:擁有者/倉庫.git`。
    nonisolated static func parseRepository(_ text: String) -> String? {
        var s = text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !s.isEmpty, !s.hasPrefix("$(") else { return nil }
        for prefix in ["https://github.com/", "http://github.com/", "git@github.com:", "ssh://git@github.com/"] where s.hasPrefix(prefix) {
            s.removeFirst(prefix.count)
        }
        if s.hasSuffix(".git") { s.removeLast(4) }
        let parts = s.split(separator: "/")
        guard parts.count == 2, parts.allSatisfy({ !$0.isEmpty && !$0.contains(":") }) else { return nil }
        return parts.joined(separator: "/")
    }

    /// 1.10.0 比 1.9.3 新。
    nonisolated static func isNewer(_ candidate: String, than current: String) -> Bool {
        let a = candidate.split(separator: ".").map { Int($0) ?? 0 }
        let b = current.split(separator: ".").map { Int($0) ?? 0 }
        for i in 0..<max(a.count, b.count) {
            let x = i < a.count ? a[i] : 0, y = i < b.count ? b[i] : 0
            if x != y { return x > y }
        }
        return false
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
            if case .available(let release) = state, UserDefaults.standard.string(forKey: Self.notifiedKey) != release.version {
                UserDefaults.standard.set(release.version, forKey: Self.notifiedKey)
                QuickActionRunner.shared.notify(title: "QUICK_SEEMD \(release.version) 已發佈", symbol: "arrow.down.circle",
                                                message: "到選單列按「更新到最新版本」，約 1 分鐘完成")
            }
        }
    }

    /// 更新後重新開啟時，依安裝記錄告訴使用者結果。
    private func reportFinishedUpdate() {
        guard UserDefaults.standard.string(forKey: Self.pendingKey) != nil else { return }
        UserDefaults.standard.removeObject(forKey: Self.pendingKey)
        let log = (try? String(contentsOf: Self.logURL, encoding: .utf8)) ?? ""
        if log.contains("✓ 完成") {
            QuickActionRunner.shared.notify(title: "已更新到 \(currentVersion)", symbol: "checkmark.seal",
                                            message: "權限設定都已保留")
        } else {
            QuickActionRunner.shared.notify(title: "更新沒有完成", symbol: "exclamationmark.triangle",
                                            message: "詳細記錄：\(Self.logURL.path)")
        }
    }

    // MARK: 檢查

    func check() async {
        guard let repository else { state = .failed("這一版沒有記錄更新來源，無法自動更新。"); return }
        guard state != .updating else { return }
        state = .checking
        do {
            let release = try await Self.latestRelease(repository: repository)
            lastChecked = Date()
            state = Self.isNewer(release.version, than: currentVersion) ? .available(release) : .upToDate
            updateLog.notice("檢查更新：目前 \(self.currentVersion, privacy: .public)，最新 \(release.version, privacy: .public)")
        } catch {
            updateLog.error("檢查更新失敗：\(error.localizedDescription, privacy: .public)")
            state = .failed(error.localizedDescription)
        }
    }

    /// 選單列的「檢查更新…」：有新版本就直接問要不要更新。
    func checkInteractively() {
        Task {
            await check()
            switch state {
            case .available(let release):
                NSApp.activate(ignoringOtherApps: true)
                let alert = NSAlert()
                alert.messageText = "QUICK_SEEMD \(release.version) 已發佈"
                let notes = release.notes.count > 600 ? String(release.notes.prefix(600)) + "…" : release.notes
                alert.informativeText = (notes.isEmpty ? "" : notes + "\n\n") + "更新約 1 分鐘，完成後自動重新開啟，已經給過的權限都會保留。"
                alert.addButton(withTitle: "立即更新")
                alert.addButton(withTitle: "稍後")
                if alert.runModal() == .alertFirstButtonReturn { update() }
            case .upToDate:
                QuickActionRunner.shared.notify(title: "已是最新版本", symbol: "checkmark.seal", message: "版本 \(currentVersion)")
            case .failed(let reason):
                QuickActionRunner.shared.notify(title: "無法檢查更新", symbol: "exclamationmark.triangle", message: reason)
            default:
                break
            }
        }
    }

    // MARK: 更新

    func update() {
        guard case .available(let release) = state else { return }
        state = .updating
        let requirement = Self.currentRequirement
        let from = currentVersion
        let destination = Bundle.main.bundleURL
        let job = ToolJob { _, context in
            do {
                try await Self.install(release, requirement: requirement, destination: destination, from: from) { context.progress($0, $1) }
                await MainActor.run { NSApp.terminate(nil) }
                return []
            } catch {
                updateLog.error("更新失敗：\(error.localizedDescription, privacy: .public)")
                await MainActor.run { AppUpdater.shared.state = .failed(error.localizedDescription) }
                throw error
            }
        }
        QuickActionRunner.shared.enqueue(title: "更新到 \(release.version)", symbol: "arrow.down.circle", tint: ToolPalette.teal,
                                         files: [], toolID: nil, job: job)
    }

    /// 命令列測試用：`open -n -W QUICK_SEEMD.app --args --qs-update`，不顯示介面；有新版本就直接更新。
    nonisolated static var isHeadlessRequested: Bool { CommandLine.arguments.contains("--qs-update") }

    func runHeadlessUpdate() {
        Task {
            await check()
            if case .available(let release) = state {
                do {
                    try await Self.install(release, requirement: Self.currentRequirement, destination: Bundle.main.bundleURL,
                                           from: currentVersion) { _, message in updateLog.notice("\(message, privacy: .public)") }
                } catch {
                    updateLog.error("更新失敗：\(error.localizedDescription, privacy: .public)")
                }
            }
            NSApp.terminate(nil)
        }
    }

    // MARK: 背景工作（不在主執行緒）

    nonisolated static var supportDirectory: URL {
        FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("QUICK_SEEMD", isDirectory: true)
    }
    nonisolated static var logURL: URL {
        FileManager.default.urls(for: .libraryDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("Logs/QUICK_SEEMD/update.log")
    }

    /// 目前這一版的簽署條件（固定憑證＋套件代碼）。新版本必須符合，才代表是同一個人發佈、權限可以沿用。
    nonisolated static var currentRequirement: String? {
        guard AppPermissions.isStablySigned else { return nil }
        var code: SecCode?
        guard SecCodeCopySelf([], &code) == errSecSuccess, let code else { return nil }
        var staticCode: SecStaticCode?
        guard SecCodeCopyStaticCode(code, [], &staticCode) == errSecSuccess, let staticCode else { return nil }
        var requirement: SecRequirement?
        guard SecCodeCopyDesignatedRequirement(staticCode, [], &requirement) == errSecSuccess, let requirement else { return nil }
        var text: CFString?
        guard SecRequirementCopyString(requirement, [], &text) == errSecSuccess else { return nil }
        return text as String?
    }

    /// 私人倉庫要帶權杖：借用 GitHub 命令列工具已登入的帳號；沒有就以未登入身分查詢（公開倉庫可用）。
    nonisolated private static func githubToken() async -> String? {
        let home = FileManager.default.homeDirectoryForCurrentUser.path
        let candidates = ["/opt/homebrew/bin/gh", "/usr/local/bin/gh", "/opt/local/bin/gh", home + "/.local/bin/gh", home + "/bin/gh"]
        guard let gh = candidates.first(where: { FileManager.default.isExecutableFile(atPath: $0) }),
              let output = try? await CVProcess.run(gh, ["auth", "token"], environment: ["GH_PROMPT_DISABLED": "1"]),
              output.succeeded else { return nil }
        let token = output.stdoutText.trimmingCharacters(in: .whitespacesAndNewlines)
        return token.isEmpty ? nil : token
    }

    nonisolated private static func latestRelease(repository: String) async throws -> Release {
        guard let url = URL(string: "https://api.github.com/repos/\(repository)/releases/latest") else {
            throw ConversionError.other("更新來源格式不正確：\(repository)")
        }
        var request = URLRequest(url: url, timeoutInterval: 20)
        request.setValue("application/vnd.github+json", forHTTPHeaderField: "Accept")
        let token = await githubToken()
        if let token { request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        let data: Data, response: URLResponse
        do { (data, response) = try await URLSession.shared.data(for: request) } catch {
            throw ConversionError.other("連不上 GitHub，請確認網路連線。")
        }
        switch (response as? HTTPURLResponse)?.statusCode ?? 0 {
        case 200: break
        case 404 where token == nil: throw ConversionError.other("讀不到發佈版本。倉庫是私人的，請先在終端機執行 gh auth login 登入一次。")
        case 404: throw ConversionError.other("GitHub 上還沒有發佈任何版本。")
        case 401, 403: throw ConversionError.other("GitHub 拒絕存取，請在終端機執行 gh auth login 重新登入。")
        case let status: throw ConversionError.other("GitHub 回應錯誤（\(status)）。")
        }

        struct Payload: Decodable {
            struct Asset: Decodable { let name: String; let url: URL }
            let tag_name: String
            let body: String?
            let html_url: URL
            let assets: [Asset]
        }
        let payload = try JSONDecoder().decode(Payload.self, from: data)
        guard let asset = payload.assets.first(where: { $0.name.lowercased().hasSuffix(".dmg") }) else {
            throw ConversionError.other("最新的發佈版本沒有附 DMG。")
        }
        let version = payload.tag_name.hasPrefix("v") ? String(payload.tag_name.dropFirst()) : payload.tag_name
        return Release(version: version, notes: (payload.body ?? "").trimmingCharacters(in: .whitespacesAndNewlines),
                       page: payload.html_url, assetName: asset.name, assetURL: asset.url)
    }

    /// 下載 → 掛載 DMG 取出 App → 驗證簽署 → 交給獨立的安裝程序（之後由呼叫端結束 App）。
    nonisolated static func install(_ release: Release, requirement: String?, destination: URL, from: String,
                                    progress: @escaping @Sendable (Double, String) -> Void) async throws {
        let parent = destination.deletingLastPathComponent()
        guard FileManager.default.isWritableFile(atPath: parent.path) else {
            throw ConversionError.other("沒有權限寫入「\(parent.path)」，無法替換 App。")
        }
        let work = supportDirectory.appendingPathComponent("Update", isDirectory: true)
        try? FileManager.default.removeItem(at: work)
        try FileManager.default.createDirectory(at: work, withIntermediateDirectories: true)

        progress(0.1, "下載 \(release.assetName)…")
        let dmg = try await download(release, to: work)
        progress(0.6, "檢查簽署…")
        let app = try await extractApp(from: dmg, to: work)
        try await verify(app, requirement: requirement)
        progress(0.9, "安裝並重新開啟…")
        UserDefaults.standard.set(from, forKey: pendingKey)
        try launchInstaller(app: app, destination: destination, enableExtensions: false)
    }

    nonisolated private static func download(_ release: Release, to directory: URL) async throws -> URL {
        var request = URLRequest(url: release.assetURL, timeoutInterval: 120)
        request.setValue("application/octet-stream", forHTTPHeaderField: "Accept")
        if let token = await githubToken() { request.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization") }
        let session = URLSession(configuration: .ephemeral, delegate: RedirectWithoutToken(), delegateQueue: nil)
        defer { session.finishTasksAndInvalidate() }
        let (temp, response) = try await session.download(for: request)
        guard (response as? HTTPURLResponse)?.statusCode == 200 else {
            throw ConversionError.other("下載失敗（\((response as? HTTPURLResponse)?.statusCode ?? 0)）。")
        }
        let dmg = directory.appendingPathComponent(release.assetName)
        try FileManager.default.moveItem(at: temp, to: dmg)
        return dmg
    }

    nonisolated private static func extractApp(from dmg: URL, to directory: URL) async throws -> URL {
        let mount = directory.appendingPathComponent("mount", isDirectory: true)
        try FileManager.default.createDirectory(at: mount, withIntermediateDirectories: true)
        try await CVProcess.runChecked("/usr/bin/hdiutil", ["attach", dmg.path, "-nobrowse", "-readonly", "-noautoopen",
                                                           "-mountpoint", mount.path], toolName: "hdiutil")
        let staged = directory.appendingPathComponent("QUICK_SEEMD.app", isDirectory: true)
        let result: Result<Void, Error>
        do {
            let contents = try FileManager.default.contentsOfDirectory(at: mount, includingPropertiesForKeys: nil)
            guard let app = contents.first(where: { $0.pathExtension == "app" }) else {
                throw ConversionError.other("DMG 裡找不到 App。")
            }
            try await CVProcess.runChecked("/usr/bin/ditto", [app.path, staged.path], toolName: "ditto")
            result = .success(())
        } catch {
            result = .failure(error)
        }
        _ = try? await CVProcess.run("/usr/bin/hdiutil", ["detach", mount.path, "-force", "-quiet"])
        try result.get()
        return staged
    }

    /// 必須是同一個套件代碼、簽章完整，而且符合目前這一版的簽署條件（同一張憑證）。
    nonisolated private static func verify(_ app: URL, requirement: String?) async throws {
        guard Bundle(url: app)?.bundleIdentifier == bundleID else {
            throw ConversionError.other("下載的不是 QUICK_SEEMD，已停止更新。")
        }
        var arguments = ["--verify", "--deep", "--strict"]
        if let requirement { arguments.append("-R=" + requirement) }
        let output = try await CVProcess.run("/usr/bin/codesign", arguments + [app.path])
        guard output.succeeded else {
            updateLog.error("簽署不符：\(output.stderrText, privacy: .public)")
            throw ConversionError.other("新版本的簽署跟目前這一版不同，為了安全已停止更新。")
        }
    }

    /// 開一個獨立的安裝程序：等這個 App 結束後替換、重新登記擴充並重新開啟。
    nonisolated static func launchInstaller(app: URL, destination: URL, enableExtensions: Bool) throws {
        let script = """
        #!/bin/bash
        APP="$1"; DEST="$2"; PID="$3"; ENABLE="$4"
        LSREGISTER=/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister
        IDS="dev.quickseemd.app.Preview dev.quickseemd.app.FinderSync"
        for _ in $(seq 1 120); do kill -0 "$PID" 2>/dev/null || break; sleep 0.5; done
        pkill -x QUICK_SEEMD 2>/dev/null
        # 原本有啟用的擴充，換新版後維持啟用；第一次安裝則全部啟用
        if [ "$ENABLE" != all ]; then
          WAS=""
          for id in $IDS; do pluginkit -m -i "$id" 2>/dev/null | grep -q '^ *+' && WAS="$WAS $id"; done
          IDS="$WAS"
        fi
        echo "▸ 安裝到 $DEST"
        rm -rf "$DEST.old"
        [ -d "$DEST" ] && mv "$DEST" "$DEST.old"
        if ! ditto "$APP" "$DEST"; then
          echo "✗ 安裝失敗，還原舊版"
          rm -rf "$DEST"; mv "$DEST.old" "$DEST"; open "$DEST"; exit 1
        fi
        rm -rf "$DEST.old"
        "$LSREGISTER" -f -R -trusted "$DEST"
        for x in "$DEST"/Contents/PlugIns/*.appex; do pluginkit -a "$x"; done
        for id in $IDS; do pluginkit -e use -i "$id"; done
        qlmanage -r >/dev/null 2>&1
        open "$DEST"
        echo "✓ 完成"
        """
        let scriptURL = supportDirectory.appendingPathComponent("install-update.sh")
        try FileManager.default.createDirectory(at: supportDirectory, withIntermediateDirectories: true)
        try script.write(to: scriptURL, atomically: true, encoding: .utf8)
        try FileManager.default.createDirectory(at: logURL.deletingLastPathComponent(), withIntermediateDirectories: true)
        try spawnDetached("/bin/bash", [scriptURL.path, app.path, destination.path, String(getpid()),
                                        enableExtensions ? "all" : "none"], log: logURL)
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

/// 下載 GitHub 附件會轉址到另一台主機的簽名網址，轉址時不能再帶 GitHub 權杖。
private final class RedirectWithoutToken: NSObject, URLSessionTaskDelegate {
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest) async -> URLRequest? {
        var next = request
        if request.url?.host != task.originalRequest?.url?.host {
            next.setValue(nil, forHTTPHeaderField: "Authorization")
        }
        return next
    }
}

/// 從 DMG 或「下載項目」直接打開時，搬到「應用程式」；第一次在「應用程式」打開時啟用右鍵選單與空白鍵預覽。
@MainActor
enum InstallLocation {
    private static let enabledKey = "Extensions.enabledOnce"

    static func checkOnLaunch() {
        let path = Bundle.main.bundlePath
        if path.contains("/AppTranslocation/") {
            askToDragIntoApplications()
        } else if path.hasPrefix("/Volumes/") {
            offerMove()
        } else if !UserDefaults.standard.bool(forKey: enabledKey), let plugins = Bundle.main.builtInPlugInsURL {
            UserDefaults.standard.set(true, forKey: enabledKey)
            Task.detached {
                for appex in (try? FileManager.default.contentsOfDirectory(at: plugins, includingPropertiesForKeys: nil)) ?? [] {
                    _ = try? await CVProcess.run("/usr/bin/pluginkit", ["-a", appex.path])
                }
                for id in ["dev.quickseemd.app.Preview", "dev.quickseemd.app.FinderSync"] {
                    _ = try? await CVProcess.run("/usr/bin/pluginkit", ["-e", "use", "-i", id])
                }
            }
        }
    }

    /// 從網路下載、還沒搬過的 App 會被系統放在隔離位置執行；要由使用者自己在 Finder 拖過去，系統才會解除。
    private static func askToDragIntoApplications() {
        NSApp.activate(ignoringOtherApps: true)
        let alert = NSAlert()
        alert.messageText = "請先把 QUICK_SEEMD 拖到「應用程式」"
        alert.informativeText = "在 Finder 把 QUICK_SEEMD 拖進「應用程式」資料夾，再從「應用程式」打開。直接從 DMG 或下載的位置打開時，右鍵選單、空白鍵預覽與更新都無法運作。"
        alert.addButton(withTitle: "打開「應用程式」資料夾並結束")
        alert.addButton(withTitle: "暫時不要")
        guard alert.runModal() == .alertFirstButtonReturn else { return }
        NSWorkspace.shared.open(URL(fileURLWithPath: "/Applications", isDirectory: true))
        NSApp.terminate(nil)
    }

    /// 從掛載的 DMG 打開：先複製一份到暫存區（DMG 退出後也不受影響），再交給安裝程序搬到「應用程式」。
    private static func offerMove() {
        let applications = URL(fileURLWithPath: "/Applications", isDirectory: true)
        let folder = FileManager.default.isWritableFile(atPath: applications.path)
            ? applications
            : FileManager.default.homeDirectoryForCurrentUser.appendingPathComponent("Applications", isDirectory: true)
        NSApp.activate(ignoringOtherApps: true)
        let alert = NSAlert()
        alert.messageText = "把 QUICK_SEEMD 搬到「應用程式」？"
        alert.informativeText = "直接從 DMG 或下載的位置打開時，右鍵選單、空白鍵預覽與更新都無法運作。搬過去後會自動重新開啟。"
        alert.addButton(withTitle: "搬到「應用程式」")
        alert.addButton(withTitle: "暫時不要")
        guard alert.runModal() == .alertFirstButtonReturn else { return }
        do {
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            let staged = AppUpdater.supportDirectory.appendingPathComponent("Update/QUICK_SEEMD.app", isDirectory: true)
            try? FileManager.default.removeItem(at: staged.deletingLastPathComponent())
            try FileManager.default.createDirectory(at: staged.deletingLastPathComponent(), withIntermediateDirectories: true)
            try FileManager.default.copyItem(at: Bundle.main.bundleURL, to: staged)
            UserDefaults.standard.set(true, forKey: enabledKey)
            try AppUpdater.launchInstaller(app: staged, destination: folder.appendingPathComponent("QUICK_SEEMD.app"),
                                           enableExtensions: true)
            NSApp.terminate(nil)
        } catch {
            let failure = NSAlert(error: error)
            failure.runModal()
        }
    }
}
