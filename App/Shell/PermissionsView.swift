import AppKit
import Security
import SwiftUI

/// 權限狀態：簽署方式（決定權限會不會被記住）、控制 Office、存取資料夾。
enum AppPermissions {
    /// 用固定憑證簽署（scripts/sign.sh）時，macOS 會把每一版都當成同一個 App，權限不必重給。
    static var isStablySigned: Bool {
        var code: SecCode?
        guard SecCodeCopySelf([], &code) == errSecSuccess, let code else { return false }
        var staticCode: SecStaticCode?
        guard SecCodeCopyStaticCode(code, [], &staticCode) == errSecSuccess, let staticCode else { return false }
        var info: CFDictionary?
        guard SecCodeCopySigningInformation(staticCode, SecCSFlags(rawValue: kSecCSSigningInformation), &info) == errSecSuccess,
              let dict = info as? [String: Any] else { return false }
        return !((dict[kSecCodeInfoCertificates as String] as? [SecCertificate]) ?? []).isEmpty
    }

    struct OfficeApp: Identifiable, Sendable {
        let name: String
        let bundleID: String
        var id: String { bundleID }
    }

    static let officeApps = [OfficeApp(name: "Word", bundleID: "com.microsoft.Word"),
                             OfficeApp(name: "Excel", bundleID: "com.microsoft.Excel"),
                             OfficeApp(name: "PowerPoint", bundleID: "com.microsoft.Powerpoint")]

    enum AutomationStatus: Sendable {
        case allowed, denied, notAsked, notRunning, notInstalled

        var text: String {
            switch self {
            case .allowed: return "已允許"
            case .denied: return "已拒絕"
            case .notAsked: return "尚未詢問"
            case .notRunning: return "第一次轉換時詢問"
            case .notInstalled: return "未安裝"
            }
        }
    }

    /// 查詢是否允許控制某個 App。對方沒開著時系統無法查詢；askIfNeeded 為 true 時會跳出系統詢問。
    static func automationStatus(_ app: OfficeApp, askIfNeeded: Bool = false) -> AutomationStatus {
        guard NSWorkspace.shared.urlForApplication(withBundleIdentifier: app.bundleID) != nil else { return .notInstalled }
        let target = NSAppleEventDescriptor(bundleIdentifier: app.bundleID)
        let status = AEDeterminePermissionToAutomateTarget(target.aeDesc, typeWildCard, typeWildCard, askIfNeeded)
        switch status {
        case noErr: return .allowed
        case OSStatus(errAEEventNotPermitted): return .denied
        case OSStatus(errAEEventWouldRequireUserConsent): return .notAsked
        default: return .notRunning
        }
    }

    static func openPrivacySettings(_ pane: String) {
        if let url = URL(string: "x-apple.systempreferences:com.apple.preference.security?\(pane)") {
            NSWorkspace.shared.open(url)
        }
    }
}

/// 「右鍵、拖曳與空白鍵預覽」頁的權限卡片。
struct PermissionsCard: View {
    @State private var stable = AppPermissions.isStablySigned
    @State private var office: [String: AppPermissions.AutomationStatus] = [:]
    @State private var checking = false

    var body: some View {
        HStack(alignment: .top, spacing: 16) {
            ZStack {
                RoundedRectangle(cornerRadius: 12, style: .continuous).fill(ToolPalette.slate)
                Image(systemName: "lock.shield").font(.system(size: 22, weight: .semibold)).foregroundStyle(.white)
            }
            .frame(width: 48, height: 48)

            VStack(alignment: .leading, spacing: 12) {
                HStack(spacing: 8) {
                    Text("權限").font(.title3.weight(.bold))
                    Label(stable ? "會記住權限，更新後不用再給" : "臨時簽署：重新安裝後可能要再給一次",
                          systemImage: stable ? "checkmark.circle.fill" : "exclamationmark.triangle.fill")
                        .font(.callout.weight(.medium))
                        .foregroundStyle(stable ? Color.green : Color.orange)
                }
                Text(stable
                     ? "這一版用本機固定憑證簽署，系統把每次更新都當成同一個 App，已經允許過的權限都會保留。"
                     : "請用 scripts/install.sh 安裝，或在選單列按「更新到最新版本」，之後的權限就會被記住。")
                    .foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)

                Grid(alignment: .leadingFirstTextBaseline, horizontalSpacing: 14, verticalSpacing: 10) {
                    GridRow {
                        Text("控制 Office").fontWeight(.semibold)
                        VStack(alignment: .leading, spacing: 4) {
                            Text(officeSummary).foregroundStyle(.secondary)
                            Text("用 Office 引擎轉換文件時才需要；第一次會跳出系統詢問，按「好」即可。")
                                .font(.caption).foregroundStyle(.tertiary)
                        }
                        HStack(spacing: 8) {
                            if office.values.contains(where: { $0 == .notAsked }) {
                                Button("現在允許") { askOffice() }
                            }
                            Button("自動化設定") { AppPermissions.openPrivacySettings("Privacy_Automation") }
                        }
                    }
                    GridRow {
                        Text("桌面、文件、下載").fontWeight(.semibold)
                        Text("右鍵轉換這些資料夾裡的檔案時，系統會詢問一次。").foregroundStyle(.secondary)
                        Button("檔案與資料夾設定") { AppPermissions.openPrivacySettings("Privacy_FilesAndFolders") }
                    }
                    GridRow {
                        Text("拖曳輪盤").fontWeight(.semibold)
                        Text("不需要額外權限。").foregroundStyle(.secondary)
                        Color.clear.frame(width: 1, height: 1)
                    }
                }
                .font(.callout)

                Button(checking ? "檢查中…" : "重新檢查") { Task { await refresh() } }
                    .disabled(checking)
            }
            Spacer(minLength: 0)
        }
        .padding(18)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 16, style: .continuous).fill(Color(nsColor: .controlBackgroundColor)))
        .overlay(RoundedRectangle(cornerRadius: 16, style: .continuous).strokeBorder(Color.primary.opacity(0.08)))
        .task { await refresh() }
    }

    private var officeSummary: String {
        let installed = AppPermissions.officeApps.filter { office[$0.bundleID] != nil && office[$0.bundleID] != .notInstalled }
        if office.isEmpty { return "檢查中…" }
        if installed.isEmpty { return "沒有安裝 Office（會改用其他引擎，不需要這個權限）" }
        return installed.map { "\($0.name)：\(office[$0.bundleID]!.text)" }.joined(separator: "　")
    }

    private func refresh() async {
        checking = true
        stable = AppPermissions.isStablySigned
        office = await Task.detached {
            Dictionary(uniqueKeysWithValues: AppPermissions.officeApps.map { ($0.bundleID, AppPermissions.automationStatus($0)) })
        }.value
        checking = false
    }

    private func askOffice() {
        let pending = AppPermissions.officeApps.filter { office[$0.bundleID] == .notAsked }
        Task {
            _ = await Task.detached { pending.map { AppPermissions.automationStatus($0, askIfNeeded: true) } }.value
            await refresh()
        }
    }
}
