import SwiftUI

/// 「右鍵、拖曳與空白鍵預覽」：系統整合的狀態、開關與使用說明。
struct IntegrationView: View {
    enum Status { case checking, enabled, disabled, missing }

    @State private var previewStatus: Status = .checking
    @State private var finder: Status = .checking
    @State private var message: String?
    @AppStorage(DragWheelController.enabledKey) private var wheel = true
    @AppStorage(AppPreferences.menuBarKey) private var menuBar = true
    @AppStorage(AppPreferences.backgroundKey) private var background = true
    @State private var login = AppPreferences.launchAtLogin
    @StateObject private var wheelPreview = WheelModel()

    static let previewExtensionID = "dev.quickseemd.app.Preview"
    static let finderExtensionID = "dev.quickseemd.app.FinderSync"

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 18) {
                VStack(alignment: .leading, spacing: 6) {
                    Text("在任何地方使用 QUICK_SEEMD").font(.system(size: 28, weight: .heavy))
                    Text("不用打開 App：右鍵一鍵轉換、拖曳時點一下 Shift 叫出轉換輪盤、按空白鍵預覽。")
                        .font(.title3).foregroundStyle(.secondary)
                }
                .padding(.bottom, 4)

                card(icon: "cursorarrow.click.2", tint: ToolPalette.pdf, title: "右鍵一鍵轉換",
                     status: finder, statusText: statusText(finder, enabled: "Finder 右鍵選單已啟用", disabled: "Finder 延伸功能尚未啟用"),
                     detail: "在 Finder 對檔案按右鍵 →「QUICK_SEEMD」，依檔案類型列出可以直接轉換的格式與工具（例如 Word 轉 PDF、壓縮圖片、影片轉 GIF），點一下就完成，結果存在原檔旁邊。") {
                    Button("開啟延伸功能設定") { Self.openExtensionSettings() }
                    Button("重新檢查") { Task { await refresh() } }
                }

                HStack(alignment: .top, spacing: 18) {
                    card(icon: "hand.draw", tint: ToolPalette.image, title: "拖曳轉換輪盤",
                         status: wheel ? .enabled : .disabled, statusText: wheel ? "已開啟" : "已關閉",
                         detail: "從 Finder、桌面或任何 App 拖著檔案時，點一下按鍵叫出輪盤：轉換輪盤（格式）、工具輪盤（壓縮、去背、拆分…）、動作輪盤（AirDrop、浮動暫存、拷貝、移動、拷貝路徑、終端機）。放開按鍵輪盤仍會留著，拖曳中點另一個鍵可切換，Esc 取消。把檔案放到想要的項目上就開始處理；放在中間則打開 QUICK_SEEMD。") {
                        VStack(alignment: .leading, spacing: 10) {
                            Toggle("啟用拖曳轉換輪盤", isOn: $wheel)
                                .toggleStyle(.switch)
                                .onChange(of: wheel) { _, on in DragWheelController.isEnabled = on }
                            if wheel { WheelKeySettings().frame(maxWidth: 380) }
                        }
                    }
                    WheelView(model: wheelPreview)
                        .scaleEffect(0.5)
                        .frame(width: 220, height: 220)
                        .allowsHitTesting(false)
                }

                card(icon: "doc.viewfinder", tint: ToolPalette.slate, title: "空白鍵預覽",
                     status: previewStatus, statusText: statusText(previewStatus, enabled: "預覽擴充已啟用", disabled: "預覽擴充尚未啟用"),
                     detail: "在 Finder 選取檔案按空白鍵：程式碼上色、Markdown 排版、JSON 自動排版、壓縮檔內容、SQLite 資料表、二進位檔資訊……都能直接看。") {
                    Button("開啟延伸功能設定") { Self.openExtensionSettings() }
                    Button("重新整理預覽快取") { Task { await resetPreviewCache() } }
                }

                PermissionsCard()

                card(icon: "menubar.rectangle", tint: ToolPalette.teal, title: "選單列與背景執行",
                     status: .enabled, statusText: nil,
                     detail: "關閉視窗後 QUICK_SEEMD 會留在選單列待命，右鍵轉換與拖曳輪盤才能隨時使用。") {
                    Toggle("在選單列顯示圖示", isOn: $menuBar).toggleStyle(.switch)
                    Toggle("關閉視窗後繼續在背景執行", isOn: $background).toggleStyle(.switch)
                    Toggle("登入時自動啟動", isOn: $login).toggleStyle(.switch)
                        .onChange(of: login) { _, on in AppPreferences.launchAtLogin = on }
                }

                if let message { Text(message).font(.callout).foregroundStyle(.secondary) }
            }
            .padding(28)
            .frame(maxWidth: 980, alignment: .leading)
            .frame(maxWidth: .infinity)
        }
        .background(HubBackground(tint: ToolPalette.slate))
        .task {
            if let sample = Bundle.main.url(forResource: "AppIcon", withExtension: "icns") {
                wheelPreview.configure(files: [sample], mode: .convert)
                wheelPreview.hovered = 1
                wheelPreview.appeared = true
            }
            await refresh()
        }
    }

    private func statusText(_ s: Status, enabled: String, disabled: String) -> String {
        switch s {
        case .checking: return "檢查中…"
        case .enabled: return enabled
        case .disabled: return disabled + "（請在系統設定中打開）"
        case .missing: return "系統尚未登記（請把 QUICK_SEEMD 放在「應用程式」資料夾並打開一次）"
        }
    }

    @ViewBuilder
    private func card<Actions: View>(icon: String, tint: Color, title: String, status: Status, statusText: String?, detail: String,
                                     @ViewBuilder actions: () -> Actions) -> some View {
        HStack(alignment: .top, spacing: 16) {
            ZStack {
                RoundedRectangle(cornerRadius: 12, style: .continuous).fill(tint)
                Image(systemName: icon).font(.system(size: 22, weight: .semibold)).foregroundStyle(.white)
            }
            .frame(width: 48, height: 48)
            VStack(alignment: .leading, spacing: 8) {
                HStack(spacing: 8) {
                    Text(title).font(.title3.weight(.bold))
                    if let statusText {
                        Label(statusText, systemImage: status == .enabled ? "checkmark.circle.fill" : (status == .checking ? "hourglass" : "exclamationmark.triangle.fill"))
                            .font(.callout.weight(.medium))
                            .foregroundStyle(status == .enabled ? Color.green : (status == .checking ? Color.secondary : Color.orange))
                    }
                }
                Text(detail).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                HStack(spacing: 10) { actions() }
                    .padding(.top, 2)
            }
            Spacer(minLength: 0)
        }
        .padding(18)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 16, style: .continuous).fill(Color(nsColor: .controlBackgroundColor)))
        .overlay(RoundedRectangle(cornerRadius: 16, style: .continuous).strokeBorder(Color.primary.opacity(0.08)))
    }

    // MARK: 系統狀態

    static func openExtensionSettings() {
        for s in ["x-apple.systempreferences:com.apple.LoginItems-Settings.extension", "x-apple.systempreferences:com.apple.ExtensionsPreferences"] {
            if let url = URL(string: s), NSWorkspace.shared.open(url) { return }
        }
    }

    private static func run(_ path: String, _ args: [String]) async -> String {
        await Task.detached {
            let p = Process()
            p.executableURL = URL(fileURLWithPath: path)
            p.arguments = args
            let pipe = Pipe()
            p.standardOutput = pipe
            p.standardError = pipe
            do { try p.run() } catch { return "" }
            let data = pipe.fileHandleForReading.readDataToEndOfFile()
            p.waitUntilExit()
            return String(decoding: data, as: UTF8.self)
        }.value
    }

    private static func status(of id: String) async -> Status {
        let out = await run("/usr/bin/pluginkit", ["-m", "-i", id])
        guard let line = out.split(separator: "\n").first(where: { $0.contains(id) }) else { return .missing }
        return line.trimmingCharacters(in: .whitespaces).first == "-" ? .disabled : .enabled
    }

    private func refresh() async {
        previewStatus = .checking
        finder = .checking
        previewStatus = await Self.status(of: Self.previewExtensionID)
        finder = await Self.status(of: Self.finderExtensionID)
    }

    private func resetPreviewCache() async {
        _ = await Self.run("/usr/bin/qlmanage", ["-r"])
        _ = await Self.run("/usr/bin/qlmanage", ["-r", "cache"])
        message = "已重新整理預覽快取（\(Date().formatted(date: .omitted, time: .standard))）"
        await refresh()
    }
}
