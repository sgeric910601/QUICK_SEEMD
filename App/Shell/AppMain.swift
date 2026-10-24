import AppKit
import os
import PDFKit
import ServiceManagement
import SwiftUI

/// 用 AppKit 自己管理生命週期：主視窗、選單列圖示、設定視窗都由我們控制，
/// quickseemd:// 網址（右鍵一鍵轉換）也不會被 SwiftUI 的場景系統攔走。
@main
enum AppMain {
    static let delegate = AppDelegate()

    static func main() {
        let app = NSApplication.shared
        app.delegate = delegate
        MainActor.assumeIsolated { app.mainMenu = MainMenu.build(target: delegate) }
        app.run()
    }
}

/// 主選單（App、檔案、編輯、視窗）。
@MainActor
enum MainMenu {
    static func build(target: AppDelegate) -> NSMenu {
        let main = NSMenu()
        func submenu(_ title: String) -> NSMenu {
            let item = NSMenuItem(title: title, action: nil, keyEquivalent: "")
            let menu = NSMenu(title: title)
            item.submenu = menu
            main.addItem(item)
            return menu
        }
        func add(_ menu: NSMenu, _ title: String, _ action: Selector?, _ key: String = "", _ mods: NSEvent.ModifierFlags = .command, target: AnyObject? = nil) {
            let item = NSMenuItem(title: title, action: action, keyEquivalent: key)
            item.keyEquivalentModifierMask = mods
            item.target = target
            menu.addItem(item)
        }

        let app = submenu("QUICK_SEEMD")
        add(app, "關於 QUICK_SEEMD", #selector(NSApplication.orderFrontStandardAboutPanel(_:)))
        app.addItem(.separator())
        add(app, "設定…", #selector(AppDelegate.showSettings(_:)), ",", target: target)
        app.addItem(.separator())
        let services = NSMenuItem(title: "服務", action: nil, keyEquivalent: "")
        let servicesMenu = NSMenu(title: "服務")
        services.submenu = servicesMenu
        NSApp.servicesMenu = servicesMenu
        app.addItem(services)
        app.addItem(.separator())
        add(app, "隱藏 QUICK_SEEMD", #selector(NSApplication.hide(_:)), "h")
        add(app, "隱藏其他", #selector(NSApplication.hideOtherApplications(_:)), "h", [.command, .option])
        add(app, "全部顯示", #selector(NSApplication.unhideAllApplications(_:)))
        app.addItem(.separator())
        add(app, "結束 QUICK_SEEMD", #selector(NSApplication.terminate(_:)), "q")

        let file = submenu("檔案")
        add(file, "開啟 QUICK_SEEMD 工具箱", #selector(AppDelegate.showMain(_:)), "n", target: target)
        add(file, "選擇檔案…", #selector(AppDelegate.pickFiles(_:)), "o", target: target)
        file.addItem(.separator())
        add(file, "關閉視窗", #selector(NSWindow.performClose(_:)), "w")

        let edit = submenu("編輯")
        add(edit, "還原", Selector(("undo:")), "z")
        add(edit, "重做", Selector(("redo:")), "z", [.command, .shift])
        edit.addItem(.separator())
        add(edit, "剪下", #selector(NSText.cut(_:)), "x")
        add(edit, "拷貝", #selector(NSText.copy(_:)), "c")
        add(edit, "貼上", #selector(NSText.paste(_:)), "v")
        add(edit, "全選", #selector(NSText.selectAll(_:)), "a")

        let window = submenu("視窗")
        add(window, "縮到最小", #selector(NSWindow.performMiniaturize(_:)), "m")
        add(window, "縮放", #selector(NSWindow.performZoom(_:)))
        window.addItem(.separator())
        add(window, "將此程式所有視窗移至最前", #selector(NSApplication.arrangeInFront(_:)))
        NSApp.windowsMenu = window
        return main
    }
}

/// 使用者偏好（也給選單列、設定視窗、整合頁共用）。
enum AppPreferences {
    static let menuBarKey = "MenuBar.show"
    static let backgroundKey = "Background.enabled"

    /// 關閉視窗後仍在背景執行（讓右鍵轉換、拖曳輪盤隨時可用）
    static var keepsRunningInBackground: Bool {
        UserDefaults.standard.object(forKey: backgroundKey) as? Bool ?? true
    }

    static var launchAtLogin: Bool {
        get { SMAppService.mainApp.status == .enabled }
        set {
            do {
                if newValue { try SMAppService.mainApp.register() } else { try SMAppService.mainApp.unregister() }
            } catch {
                NSSound.beep()
            }
        }
    }

    @MainActor
    static func pickFiles() {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = true
        panel.canChooseDirectories = true
        panel.prompt = "選擇"
        panel.message = "選擇檔案，接著挑選要使用的工具"
        if panel.runModal() == .OK { AppState.shared.receiveFromOutside(panel.urls) }
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    private let log = Logger(subsystem: "dev.quickseemd.app", category: "app")

    @objc func showMain(_ sender: Any?) { MainActor.assumeIsolated { MainWindow.shared.show() } }
    @objc func pickFiles(_ sender: Any?) { MainActor.assumeIsolated { AppPreferences.pickFiles() } }
    @objc func showSettings(_ sender: Any?) { MainActor.assumeIsolated { SettingsWindow.shared.show() } }

    /// 直接向系統註冊 quickseemd:// 網址事件（右鍵一鍵轉換由 Finder 擴充以網址送進來）。
    private func installURLHandler() {
        NSAppleEventManager.shared().setEventHandler(self, andSelector: #selector(handleGetURL(_:withReplyEvent:)),
                                                     forEventClass: AEEventClass(kInternetEventClass), andEventID: AEEventID(kAEGetURL))
    }

    @objc private func handleGetURL(_ event: NSAppleEventDescriptor, withReplyEvent reply: NSAppleEventDescriptor) {
        guard let text = event.paramDescriptor(forKeyword: AEKeyword(keyDirectObject))?.stringValue, let url = URL(string: text) else { return }
        log.notice("收到網址：\(url.host ?? "", privacy: .public) \(url.query?.count ?? 0, privacy: .public) 字元")
        Task { @MainActor in AppState.shared.handle(url: url) }
    }

    func applicationWillFinishLaunching(_ notification: Notification) {
        installURLHandler()
    }

    func applicationDidFinishLaunching(_ notification: Notification) {
        installURLHandler()
        // 所有輸出的 PDF：修正 macOS 把「文、一、日…」寫成康熙部首的問題（先驗證、確定可開才寫回）
        CVPDFPostProcess.handler = { url in
            guard let data = try? Data(contentsOf: url), let fixed = OTPDFUnicodeFix.fixed(data),
                  fixed != data, PDFDocument(data: fixed) != nil else { return }
            try? fixed.write(to: url, options: .atomic)
        }
        if AppUpdater.isHeadlessRequested {
            NSApp.setActivationPolicy(.accessory)
            MainActor.assumeIsolated { AppUpdater.shared.runHeadlessUpdate() }
            return
        }
        if ToolHeadless.isRequested {
            NSApp.setActivationPolicy(.accessory)
            ToolHeadless.runAndExit()
            return
        }
        NSApp.servicesProvider = self
        NSUpdateDynamicServices()
        DragWheelController.shared.start()
        MainActor.assumeIsolated { StatusItemController.shared.start() }
        MainActor.assumeIsolated { AppUpdater.shared.start() }
        MainActor.assumeIsolated { InstallLocation.checkOnLaunch() }

        // 一般啟動才開主視窗；以登入項目、或被右鍵動作 / 開檔喚起時，安靜地在背景待命。
        let isDefaultLaunch = (notification.userInfo?[NSApplication.launchIsDefaultUserInfoKey] as? Bool) ?? true
        if isDefaultLaunch && !Self.launchedAsLoginItem {
            MainWindow.shared.show()
        } else {
            NSApp.setActivationPolicy(.accessory)
        }
    }

    private static var launchedAsLoginItem: Bool {
        guard let event = NSAppleEventManager.shared().currentAppleEvent else { return false }
        return event.eventID == AEEventID(kAEOpenApplication)
            && event.paramDescriptor(forKeyword: AEKeyword(keyAEPropData))?.enumCodeValue == OSType(keyAELaunchedAsLogInItem)
    }

    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool {
        !ToolHeadless.isRequested && !AppUpdater.isHeadlessRequested && !AppPreferences.keepsRunningInBackground
    }

    func applicationShouldHandleReopen(_ sender: NSApplication, hasVisibleWindows flag: Bool) -> Bool {
        if !flag { Task { @MainActor in MainWindow.shared.show() } }
        return true
    }

    /// 拖到 Dock 圖示、「打開檔案的應用程式」、quickseemd:// 網址（右鍵一鍵轉換）
    func application(_ application: NSApplication, open urls: [URL]) {
        Task { @MainActor in
            let files = urls.filter(\.isFileURL)
            for url in urls where url.scheme == "quickseemd" { AppState.shared.handle(url: url) }
            if !files.isEmpty { AppState.shared.receiveFromOutside(files) }
        }
    }

    /// Finder 右鍵 →「服務」→「用 QUICK_SEEMD 轉換格式」
    @objc func convertFiles(_ pboard: NSPasteboard, userData: String?, error: AutoreleasingUnsafeMutablePointer<NSString?>) {
        let urls = pboard.readObjects(forClasses: [NSURL.self], options: [.urlReadingFileURLsOnly: true]) as? [URL] ?? []
        Task { @MainActor in AppState.shared.receiveFromOutside(urls) }
    }
}

/// 主視窗（自行管理，才能在背景啟動時不跳出視窗、關閉後保留在選單列）。
@MainActor
final class MainWindow: NSObject, NSWindowDelegate {
    static let shared = MainWindow()
    private var window: NSWindow?

    func show() {
        if window == nil {
            let w = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 1160, height: 760),
                             styleMask: [.titled, .closable, .miniaturizable, .resizable, .fullSizeContentView],
                             backing: .buffered, defer: false)
            w.title = "QUICK_SEEMD"
            w.titleVisibility = .hidden
            w.toolbar = NSToolbar(identifier: "AppMain")
            w.toolbarStyle = .unified
            w.isReleasedWhenClosed = false
            w.minSize = NSSize(width: 980, height: 640)
            w.delegate = self
            let host = NSHostingController(rootView: MainView().environmentObject(AppState.shared))
            host.sizingOptions = []
            w.contentViewController = host
            w.setContentSize(NSSize(width: 1160, height: 760))
            if !w.setFrameUsingName("AppMain") { w.center() }
            w.setFrameAutosaveName("AppMain")
            window = w
        }
        NSApp.setActivationPolicy(.regular)
        window?.makeKeyAndOrderFront(nil)
        NSApp.activate(ignoringOtherApps: true)
    }

    func windowWillClose(_ notification: Notification) {
        if AppPreferences.keepsRunningInBackground {
            DispatchQueue.main.async { NSApp.setActivationPolicy(.accessory) }
        }
    }
}
