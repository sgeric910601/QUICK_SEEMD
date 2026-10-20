import AppKit
import QuickLookThumbnailing
import SwiftUI

/// 工具圖示：彩色圓角方塊＋白色符號＋右下角檔案類型標籤。
struct ToolIcon: View {
    let tool: ToolDefinition
    var size: CGFloat = 46

    var body: some View {
        ZStack(alignment: .bottomTrailing) {
            RoundedRectangle(cornerRadius: size * 0.26, style: .continuous)
                .fill(LinearGradient(colors: [tool.tint.opacity(0.88), tool.tint], startPoint: .top, endPoint: .bottom))
                .overlay(
                    RoundedRectangle(cornerRadius: size * 0.26, style: .continuous)
                        .strokeBorder(Color.white.opacity(0.18), lineWidth: 1)
                )
                .overlay(
                    Image(systemName: tool.symbol)
                        .font(.system(size: size * 0.44, weight: .semibold))
                        .foregroundStyle(.white)
                )
                .frame(width: size, height: size)
                .shadow(color: tool.tint.opacity(0.35), radius: size * 0.12, y: size * 0.06)
            if let badge = tool.badge, size >= 34 {
                Text(badge)
                    .font(.system(size: max(8, size * 0.19), weight: .heavy, design: .rounded))
                    .foregroundStyle(tool.tint)
                    .padding(.horizontal, size * 0.08).padding(.vertical, size * 0.02)
                    .background(Capsule().fill(.white))
                    .overlay(Capsule().strokeBorder(tool.tint.opacity(0.25), lineWidth: 0.5))
                    .offset(x: size * 0.14, y: size * 0.1)
            }
        }
        .frame(width: size, height: size)
    }
}

/// 檔案縮圖（系統預覽 產生；PDF、Office、圖片、影片都有真實預覽）。
struct FileThumbnail: View {
    let url: URL
    var maxSize: CGSize = CGSize(width: 140, height: 160)
    @State private var image: NSImage?

    var body: some View {
        ZStack {
            if let image {
                Image(nsImage: image)
                    .resizable()
                    .interpolation(.high)
                    .aspectRatio(contentMode: .fit)
                    .clipShape(RoundedRectangle(cornerRadius: 4, style: .continuous))
                    .shadow(color: .black.opacity(0.18), radius: 3, y: 1.5)
            } else {
                Image(nsImage: NSWorkspace.shared.icon(forFile: url.path))
                    .resizable()
                    .aspectRatio(contentMode: .fit)
                    .padding(maxSize.width * 0.12)
            }
        }
        .frame(width: maxSize.width, height: maxSize.height)
        .task(id: url) { image = await ToolThumbnails.shared.thumbnail(for: url, size: maxSize) }
    }
}

actor ToolThumbnails {
    static let shared = ToolThumbnails()
    private var cache: [String: NSImage] = [:]

    func thumbnail(for url: URL, size: CGSize) async -> NSImage? {
        let mod = (try? url.resourceValues(forKeys: [.contentModificationDateKey]))?.contentModificationDate?.timeIntervalSince1970 ?? 0
        let key = "\(url.path)|\(mod)|\(Int(size.width))x\(Int(size.height))"
        if let hit = cache[key] { return hit }
        let request = QLThumbnailGenerator.Request(fileAt: url, size: size, scale: NSScreen.main?.backingScaleFactor ?? 2, representationTypes: .thumbnail)
        guard let rep = try? await QLThumbnailGenerator.shared.generateBestRepresentation(for: request) else { return nil }
        let image = rep.nsImage
        cache[key] = image
        return image
    }
}

/// 大的主要按鈕（工具頁的「合併 PDF ➜」）。
struct ToolPrimaryButtonStyle: ButtonStyle {
    let tint: Color
    @Environment(\.isEnabled) private var isEnabled

    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.title3.weight(.semibold))
            .foregroundStyle(.white)
            .padding(.horizontal, 22)
            .frame(minHeight: 46)
            .background(
                RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .fill(isEnabled ? tint : Color.gray.opacity(0.45))
                    .brightness(configuration.isPressed ? -0.08 : 0)
            )
            .shadow(color: isEnabled ? tint.opacity(0.35) : .clear, radius: 8, y: 3)
            .scaleEffect(configuration.isPressed ? 0.985 : 1)
            .contentShape(Rectangle())
    }
}

/// 次要按鈕（白底外框）。
struct ToolSecondaryButtonStyle: ButtonStyle {
    func makeBody(configuration: Configuration) -> some View {
        configuration.label
            .font(.body.weight(.medium))
            .padding(.horizontal, 16)
            .frame(minHeight: 36)
            .background(
                RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .fill(Color(nsColor: .controlBackgroundColor))
                    .brightness(configuration.isPressed ? -0.05 : 0)
            )
            .overlay(RoundedRectangle(cornerRadius: 10, style: .continuous).strokeBorder(Color.primary.opacity(0.14)))
            .contentShape(Rectangle())
    }
}

/// 輸出位置設定（所有工具共用，記住上次的選擇）。
final class ToolOutputSettings: ObservableObject {
    static let shared = ToolOutputSettings()
    @AppStorage("Tools.outputSameFolder") var sameFolder = true { willSet { objectWillChange.send() } }
    @AppStorage("Tools.outputFolder") var folderPath = "" { willSet { objectWillChange.send() } }

    var output: ToolContext.Output {
        if !sameFolder, !folderPath.isEmpty { return .folder(URL(fileURLWithPath: folderPath)) }
        return .sameAsSource
    }

    var label: String {
        if sameFolder || folderPath.isEmpty { return "與原始檔案相同的資料夾" }
        return (folderPath as NSString).lastPathComponent
    }

    @MainActor
    func chooseFolder() {
        let panel = NSOpenPanel()
        panel.canChooseDirectories = true
        panel.canChooseFiles = false
        panel.canCreateDirectories = true
        panel.prompt = "選擇"
        panel.message = "轉換後的檔案要存到哪裡？"
        if panel.runModal() == .OK, let url = panel.url {
            folderPath = url.path
            sameFolder = false
        }
    }
}

struct OutputLocationPicker: View {
    @ObservedObject var settings = ToolOutputSettings.shared

    var body: some View {
        Menu {
            Button { settings.sameFolder = true } label: {
                Label("與原始檔案相同的資料夾", systemImage: settings.sameFolder ? "checkmark" : "folder")
            }
            if !settings.folderPath.isEmpty {
                Button { settings.sameFolder = false } label: {
                    Label((settings.folderPath as NSString).abbreviatingWithTildeInPath, systemImage: settings.sameFolder ? "folder" : "checkmark")
                }
            }
            Divider()
            Button("選擇其他資料夾…") { settings.chooseFolder() }
        } label: {
            HStack(spacing: 6) {
                Image(systemName: "folder")
                Text("儲存到：\(settings.label)").lineLimit(1).truncationMode(.middle)
            }
            .font(.callout)
            .foregroundStyle(.secondary)
        }
        .menuStyle(.borderlessButton)
        .fixedSize(horizontal: false, vertical: true)
    }
}

/// 拖放檔案時用的「網址清單」讀取。
enum ToolDrop {
    static func loadURLs(_ providers: [NSItemProvider], completion: @escaping @MainActor ([URL]) -> Void) {
        let group = DispatchGroup()
        let lock = NSLock()
        var urls = [URL?](repeating: nil, count: providers.count)
        for (i, p) in providers.enumerated() where p.canLoadObject(ofClass: URL.self) {
            group.enter()
            _ = p.loadObject(ofClass: URL.self) { url, _ in
                if let url, url.isFileURL { lock.lock(); urls[i] = url; lock.unlock() }
                group.leave()
            }
        }
        group.notify(queue: .main) {
            MainActor.assumeIsolated { completion(urls.compactMap { $0 }) }
        }
    }
}
