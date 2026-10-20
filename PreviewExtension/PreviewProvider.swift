import Cocoa
import os
import Quartz
import UniformTypeIdentifiers

private let log = Logger(subsystem: "dev.quickseemd.preview", category: "preview")

/// 空白鍵預覽擴充：在 Finder 按空白鍵時由系統呼叫，回傳 HTML 給系統預覽視窗顯示。
final class PreviewProvider: QLPreviewProvider, QLPreviewingController {
    func providePreview(for request: QLFilePreviewRequest) async throws -> QLPreviewReply {
        let start = Date()
        let doc = QSPreviewEngine.render(url: request.fileURL)
        let ms = Int(Date().timeIntervalSince(start) * 1000)
        log.notice("QUICK_SEEMD 預覽 \(request.fileURL.lastPathComponent, privacy: .public)（\(ms) ms，\(doc.html.utf8.count / 1024) KB）")
        let reply = QLPreviewReply(dataOfContentType: .html, contentSize: doc.preferredSize) { reply in
            reply.stringEncoding = .utf8
            reply.attachments = doc.attachments.mapValues {
                QLPreviewReplyAttachment(data: $0.data, contentType: $0.contentType)
            }
            return Data(doc.html.utf8)
        }
        return reply
    }
}
