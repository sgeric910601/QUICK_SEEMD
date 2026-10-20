import SwiftUI
import UniformTypeIdentifiers
import WebKit

/// 在 App 裡直接試看預覽效果（和 Finder 空白鍵看到的是同一套渲染）。
struct PreviewTestView: View {
    @State private var fileURL: URL?
    @State private var html: String?
    @State private var targeted = false

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 10) {
                if let url = fileURL {
                    Image(nsImage: NSWorkspace.shared.icon(forFile: url.path)).resizable().frame(width: 22, height: 22)
                    Text(url.lastPathComponent).font(.headline).lineLimit(1).truncationMode(.middle)
                } else {
                    Text("預覽測試").font(.headline)
                }
                Spacer()
                if fileURL != nil {
                    Button { fileURL.map { NSWorkspace.shared.activateFileViewerSelecting([$0]) } } label: { Label("在 Finder 中顯示", systemImage: "folder") }
                }
                Button { choose() } label: { Label("選擇檔案…", systemImage: "doc.badge.plus") }
                    .buttonStyle(.borderedProminent)
            }
            .padding(.horizontal, 18).padding(.vertical, 12)
            .background(.bar)
            Divider()
            ZStack {
                if let html {
                    HTMLView(html: html).id(html.hashValue)
                } else {
                    DropHint(targeted: targeted)
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
            .onDrop(of: [.fileURL], isTargeted: $targeted) { providers in
                guard let p = providers.first else { return false }
                _ = p.loadObject(ofClass: URL.self) { url, _ in
                    if let url { Task { @MainActor in load(url) } }
                }
                return true
            }
        }
    }

    private func choose() {
        let panel = NSOpenPanel()
        panel.prompt = "預覽"
        if panel.runModal() == .OK, let url = panel.url { load(url) }
    }

    private func load(_ url: URL) {
        fileURL = url
        Task.detached(priority: .userInitiated) {
            let doc = QSPreviewEngine.render(url: url)
            var page = doc.html
            for (name, att) in doc.attachments {
                let mime = att.contentType.preferredMIMEType ?? "application/octet-stream"
                page = page.replacingOccurrences(of: "cid:\(name)", with: "data:\(mime);base64,\(att.data.base64EncodedString())")
            }
            let finished = page
            await MainActor.run { html = finished }
        }
    }
}

private struct DropHint: View {
    let targeted: Bool
    var body: some View {
        VStack(spacing: 14) {
            Image(systemName: "doc.viewfinder")
                .font(.system(size: 54, weight: .light))
                .foregroundStyle(targeted ? Color.accentColor : .secondary)
            Text("把任何檔案拖到這裡").font(.title3.weight(.semibold))
            Text("程式碼、Markdown、JSON、CSV、壓縮檔、SQLite、二進位檔…\n這裡看到的，就是在 Finder 按空白鍵會看到的預覽。")
                .multilineTextAlignment(.center).foregroundStyle(.secondary)
        }
        .padding(40)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(
            RoundedRectangle(cornerRadius: 18)
                .strokeBorder(style: StrokeStyle(lineWidth: 2, dash: [8, 6]))
                .foregroundStyle(targeted ? Color.accentColor : Color.secondary.opacity(0.35))
                .padding(24)
        )
        .animation(.easeOut(duration: 0.15), value: targeted)
    }
}

struct HTMLView: NSViewRepresentable {
    let html: String
    func makeNSView(context: Context) -> WKWebView {
        let config = WKWebViewConfiguration()
        config.defaultWebpagePreferences.allowsContentJavaScript = false
        let view = WKWebView(frame: .zero, configuration: config)
        view.setValue(false, forKey: "drawsBackground")
        view.loadHTMLString(html, baseURL: nil)
        return view
    }
    func updateNSView(_ view: WKWebView, context: Context) {}
}
