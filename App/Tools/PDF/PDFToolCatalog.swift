import PDFKit
import SwiftUI
import UniformTypeIdentifiers

/// PDF 工具。每個工具＝定義＋工作階段（選項）＋選項面板＋背景工作＋右鍵預設動作。
/// 定義分散在 App/Tools/PDF 各檔案的 `extension PDFToolCatalog` 裡。
enum PDFToolCatalog {
    static let tools: [ToolDefinition] = [
        merge, split, compress, toImages, fromImages, organize, deletePages, rotate,
        watermark, pageNumbers, protect, unlock, ocr, toText, fromHTML, metadata,
    ]

    static let rotate = ToolDefinition(
        id: "pdf.rotate",
        category: .pdf,
        title: "旋轉 PDF",
        subtitle: "把整份或指定的頁面轉 90° 或 180°，可一次處理多個檔案。",
        symbol: "rotate.right",
        badge: "PDF",
        inputTypes: [.pdf],
        inputLabel: "PDF 檔案",
        actionTitle: "旋轉 PDF",
        keywords: ["rotate", "turn", "轉向", "方向", "橫向", "直向"],
        quickActions: [
            "pdf.rotate-right": { PTRotateSession.job(angle: 90, pages: "") },
        ],
        makeSession: { PTRotateSession() }
    )
}

final class PTRotateSession: PTSession {
    @Published var angle = 90
    @Published var pages = ""

    override func validate(_ files: [URL]) -> String? {
        if let reason = super.validate(files) { return reason }
        if let count = pageCount, PageRange.parse(pages, pageCount: count) == nil { return "頁碼範圍不正確" }
        return nil
    }

    override func optionsView() -> AnyView { AnyView(PTRotateOptions(session: self)) }

    override func makeJob() -> ToolJob { Self.job(angle: angle, pages: pages) }

    nonisolated static func job(angle: Int, pages: String) -> ToolJob {
        .perFile { file, context in
            let doc = try PTPDF.open(file)
            let list = try PTPDF.pages(pages, count: doc.pageCount, file: file)
            for (i, n) in list.enumerated() {
                try context.checkCancellation()
                if let page = doc.page(at: n - 1) { page.rotation = (page.rotation + angle) % 360 }
                context.progress(Double(i + 1) / Double(list.count) * 0.8)
            }
            let tmp = try context.makeWorkDirectory().appendingPathComponent("rotated.pdf")
            try PTPDF.write(doc, to: tmp)
            return [try context.deliver(tmp, as: context.outputName(for: file, tag: "已旋轉", ext: "pdf"), for: file)]
        }
    }
}

private struct PTRotateOptions: View {
    @ObservedObject var session: PTRotateSession

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            OptionGroup(title: "旋轉方向", systemImage: "arrow.clockwise") {
                ChoiceCards(selection: $session.angle, choices: [
                    OptionChoice(value: 90, title: "向右轉 90°", subtitle: "順時針", systemImage: "rotate.right"),
                    OptionChoice(value: 270, title: "向左轉 90°", subtitle: "逆時針", systemImage: "rotate.left"),
                    OptionChoice(value: 180, title: "轉 180°", subtitle: "上下顛倒", systemImage: "arrow.up.arrow.down"),
                ])
            }
            OptionGroup(title: "頁面", systemImage: "doc.on.doc") {
                PageRangeField(title: "要旋轉的頁面", text: $session.pages, pageCount: session.pageCount)
            }
        }
    }
}
