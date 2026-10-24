import AppKit
import SwiftUI

/// 選單列圖示與選單（AppKit 實作；依「在選單列顯示圖示」設定顯示或隱藏）。
@MainActor
final class StatusItemController: NSObject, NSMenuDelegate {
    static let shared = StatusItemController()
    private var item: NSStatusItem?
    private var observer: NSObjectProtocol?

    private static let favorites = ["office.word-to-pdf", "office.pdf-to-word", "pdf.merge", "pdf.compress",
                                    "image.compress", "image.remove-bg", "video.compress", "video.to-gif"]

    func start() {
        update()
        observer = NotificationCenter.default.addObserver(forName: UserDefaults.didChangeNotification, object: nil, queue: .main) { _ in
            MainActor.assumeIsolated { StatusItemController.shared.update() }
        }
    }

    func update() {
        let show = UserDefaults.standard.object(forKey: AppPreferences.menuBarKey) as? Bool ?? true
        if show, item == nil {
            let i = NSStatusBar.system.statusItem(withLength: NSStatusItem.squareLength)
            i.button?.image = AppMark.templateImage()
            i.button?.toolTip = "QUICK_SEEMD"
            let menu = NSMenu()
            menu.delegate = self
            i.menu = menu
            item = i
        } else if !show, let i = item {
            NSStatusBar.system.removeStatusItem(i)
            item = nil
        }
    }

    func menuNeedsUpdate(_ menu: NSMenu) {
        menu.removeAllItems()
        func add(_ m: NSMenu, _ title: String, _ action: Selector?, key: String = "", represented: Any? = nil, state: Bool? = nil) {
            let it = NSMenuItem(title: title, action: action, keyEquivalent: key)
            it.target = self
            it.representedObject = represented
            if let state { it.state = state ? .on : .off }
            m.addItem(it)
        }
        let updater = AppUpdater.shared
        switch updater.state {
        case .available(let release):
            add(menu, "更新到最新版本（\(release.version)）", #selector(installUpdate))
            menu.items.last?.image = NSImage(systemSymbolName: "arrow.down.circle.fill", accessibilityDescription: nil)
            menu.addItem(.separator())
        case .updating:
            add(menu, "更新中…（完成後會自動重新開啟）", nil)
            menu.addItem(.separator())
        default:
            break
        }
        add(menu, "開啟 QUICK_SEEMD 工具箱", #selector(openMain))
        add(menu, "選擇檔案…", #selector(pickFiles))
        menu.addItem(.separator())

        let favItem = NSMenuItem(title: "常用工具", action: nil, keyEquivalent: "")
        let fav = NSMenu()
        for t in Self.favorites.compactMap({ ToolRegistry.tool(id: $0) }) { add(fav, t.title, #selector(openTool(_:)), represented: t.id) }
        favItem.submenu = fav
        menu.addItem(favItem)

        let allItem = NSMenuItem(title: "所有工具", action: nil, keyEquivalent: "")
        let all = NSMenu()
        for c in ToolCategory.allCases {
            let tools = ToolRegistry.tools(in: c)
            guard !tools.isEmpty else { continue }
            all.addItem(NSMenuItem.sectionHeader(title: c.title))
            for t in tools { add(all, t.title, #selector(openTool(_:)), represented: t.id) }
        }
        allItem.submenu = all
        menu.addItem(allItem)
        menu.addItem(.separator())

        add(menu, "拖曳轉換輪盤", #selector(toggleWheel), state: DragWheelController.isEnabled)
        add(menu, "登入時自動啟動", #selector(toggleLogin), state: AppPreferences.launchAtLogin)
        if updater.canUpdate, updater.state != .updating {
            add(menu, "檢查更新…", #selector(checkUpdate))
        }
        add(menu, "設定…", #selector(openSettings), key: ",")
        menu.addItem(.separator())
        add(menu, "結束 QUICK_SEEMD", #selector(quit), key: "q")
    }

    @objc private func openMain() { MainWindow.shared.show() }
    @objc private func pickFiles() { AppPreferences.pickFiles() }
    @objc private func openTool(_ sender: NSMenuItem) {
        guard let id = sender.representedObject as? String else { return }
        AppState.shared.openFromOutside(toolID: id, files: [])
    }
    @objc private func toggleWheel() { DragWheelController.isEnabled.toggle() }
    @objc private func toggleLogin() { AppPreferences.launchAtLogin.toggle() }
    @objc private func openSettings() { SettingsWindow.shared.show() }
    @objc private func checkUpdate() { AppUpdater.shared.checkInteractively() }
    @objc private func installUpdate() { AppUpdater.shared.update() }
    @objc private func quit() { NSApp.terminate(nil) }
}

/// 設定視窗（⌘,）。
@MainActor
final class SettingsWindow {
    static let shared = SettingsWindow()
    private var window: NSWindow?

    func show() {
        if window == nil {
            let host = NSHostingController(rootView: SettingsView())
            let w = NSWindow(contentViewController: host)
            w.title = "QUICK_SEEMD 設定"
            w.styleMask = [.titled, .closable]
            w.isReleasedWhenClosed = false
            w.center()
            window = w
        }
        NSApp.activate(ignoringOtherApps: true)
        window?.makeKeyAndOrderFront(nil)
    }
}

/// 設定內容。
struct SettingsView: View {
    @AppStorage(DragWheelController.enabledKey) private var wheel = true
    @AppStorage(AppPreferences.menuBarKey) private var menuBar = true
    @AppStorage(AppPreferences.backgroundKey) private var background = true
    @State private var login = AppPreferences.launchAtLogin

    var body: some View {
        Form {
            Section("快速轉換") {
                Toggle(isOn: $wheel) {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("拖曳轉換輪盤")
                        Text("拖著檔案時點一下按鍵叫出輪盤；放開按鍵輪盤仍會留著，點另一個鍵切換，Esc 取消").font(.caption).foregroundStyle(.secondary)
                    }
                }
                .onChange(of: wheel) { _, on in DragWheelController.isEnabled = on }
                if wheel { WheelKeySettings() }
                LabeledContent("右鍵一鍵轉換") {
                    Button("開啟 Finder 延伸功能設定") { IntegrationView.openExtensionSettings() }
                }
            }
            Section("執行") {
                Toggle("關閉視窗後繼續在背景執行", isOn: $background)
                Toggle("在選單列顯示圖示", isOn: $menuBar)
                Toggle("登入時自動啟動", isOn: $login)
                    .onChange(of: login) { _, on in AppPreferences.launchAtLogin = on }
            }
            Section("更新") { UpdateSettings() }
            Section("輸出") {
                LabeledContent("工具箱的輸出位置") { OutputLocationPicker() }
                Text("右鍵與拖曳轉換的結果一律存在原始檔案旁邊，不會覆蓋任何檔案。")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
        .formStyle(.grouped)
        .frame(width: 520)
        .fixedSize(horizontal: false, vertical: true)
    }
}

/// 設定 › 更新：目前版本、自動檢查、一鍵更新。
struct UpdateSettings: View {
    @ObservedObject private var updater = AppUpdater.shared
    @AppStorage(AppUpdater.autoCheckKey) private var autoCheck = true

    var body: some View {
        LabeledContent("目前版本") { Text(updater.versionText).monospaced() }
        if updater.canUpdate {
            Toggle("自動檢查更新", isOn: $autoCheck)
            LabeledContent(statusText) {
                switch updater.state {
                case .available:
                    Button("立即更新") { updater.update() }.keyboardShortcut(.defaultAction)
                case .updating, .checking:
                    ProgressView().controlSize(.small)
                default:
                    Button("檢查更新") { Task { await updater.check() } }
                }
            }
            if case .available(let release) = updater.state {
                Link("查看 \(release.version) 更新內容", destination: release.page).font(.caption)
            }
            Text("更新會從 GitHub 下載最新的發佈版本，確認是同一張憑證簽署後替換並重新開啟，約 1 分鐘。已經允許過的權限都會保留。")
                .font(.caption).foregroundStyle(.secondary)
        } else {
            Text("這一版沒有記錄更新來源，無法自動更新。").font(.caption).foregroundStyle(.secondary)
        }
    }

    private var statusText: String {
        switch updater.state {
        case .idle: return "尚未檢查"
        case .checking: return "檢查中…"
        case .upToDate: return "已是最新版本"
        case .available(let release): return "有新版本 \(release.version)"
        case .updating: return "更新中，完成後會自動重新開啟…"
        case .failed(let reason): return reason
        }
    }
}
