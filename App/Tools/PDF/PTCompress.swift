import PDFKit
import Quartz
import SwiftUI
import UniformTypeIdentifiers

// 壓縮 PDF：用 Quartz 濾鏡重新壓縮、縮小圖片（JPEG），文字與向量圖形保持原樣、可選取。

extension PDFToolCatalog {
    static let compress = ToolDefinition(
        id: "pdf.compress",
        category: .pdf,
        title: "壓縮 PDF",
        subtitle: "縮小 PDF 檔案大小，文字保持清晰可選取，圖片重新壓縮。",
        symbol: "arrow.down.right.and.arrow.up.left",
        badge: "PDF",
        inputTypes: [.pdf],
        inputLabel: "PDF 檔案",
        actionTitle: "壓縮 PDF",
        keywords: ["compress", "reduce size", "shrink", "optimize", "壓縮", "縮小", "減少大小", "瘦身"],
        quickActions: [
            "pdf.compress-recommended": { PTCompressSession.job(level: .recommended) },
        ],
        makeSession: { PTCompressSession() }
    )
}

enum PTCompressLevel: String, Sendable, CaseIterable {
    case extreme, recommended, low

    /// (JPEG 品質, 目標解析度 dpi, 圖片最長邊像素上限)
    var settings: (quality: Double, dpi: Double, maxPixels: Int) {
        switch self {
        case .extreme: return (0.40, 96, 1600)
        case .recommended: return (0.65, 150, 2400)
        case .low: return (0.82, 220, 4000)
        }
    }

    var title: String {
        switch self {
        case .extreme: return "極度壓縮"
        case .recommended: return "建議壓縮"
        case .low: return "低度壓縮"
        }
    }
}

enum PTCompressor {
    /// 與系統「減少檔案大小」相同格式的 Quartz 濾鏡，但依程度調整參數。
    static func filterProperties(_ level: PTCompressLevel) -> [String: Any] {
        let s = level.settings
        return [
            "Domains": ["Applications": true, "Printing": true],
            "FilterData": [
                "ColorSettings": [
                    "ImageSettings": [
                        "Compression Quality": s.quality,
                        "ImageCompression": "ImageJPEGCompress",
                        "ImageScaleSettings": [
                            "ImageResolution": s.dpi,
                            "ImageScaleInterpolate": true,
                            "ImageSizeMax": s.maxPixels,
                            "ImageSizeMin": 0,
                        ],
                    ],
                ],
            ],
            "FilterType": 1,
            "Name": "QUICK_SEEMD \(level.title)",
        ]
    }

    /// 主要方式：物件層級複製，只重新壓縮與縮小圖片；字型、文字與向量內容原封不動。回傳重新壓縮的圖片數。
    static func compress(_ doc: PDFDocument, level: PTCompressLevel, to out: URL, context: ToolContext) throws -> Int {
        let s = level.settings
        let writer = PTPDFWriter()
        writer.retain(doc)
        writer.imagePolicy = PTImagePolicy(quality: s.quality, dpi: s.dpi, maxPixels: s.maxPixels, minSaving: level == .low ? 0.9 : 0.95)
        writer.isCancelled = { Task.isCancelled }
        writer.progress = { f in context.progress(0.1 + 0.85 * f, "壓縮中…（\(Int(f * 100))%）") }
        for i in 0..<doc.pageCount {
            guard let ref = doc.page(at: i)?.pageRef else { throw PTWriterError.unsupported("page") }
            writer.addPage(ref)
        }
        writer.outline = PTOutline.nodes(from: doc, map: { $0 })
        writer.info = PTPDF.info(of: doc)
        writer.catalogSource = doc.documentRef
        writer.catalogKeys = PTPDFWriter.wholeDocumentKeys
        try writer.write(to: out)
        guard PDFDocument(url: out)?.pageCount == doc.pageCount else { throw PTWriterError.unsupported("verify") }
        return writer.imagesRecompressed
    }

    /// 備援：PDFKit 寫檔時套用 Quartz 濾鏡（少數特殊 PDF 才會用到）。
    static func compressWithFilter(_ doc: PDFDocument, level: PTCompressLevel, to out: URL) -> Bool {
        guard let filter = QuartzFilter(properties: filterProperties(level)) else { return false }
        return doc.write(to: out, withOptions: [PDFDocumentWriteOption(rawValue: "QuartzFilter"): filter])
            && PDFDocument(url: out)?.pageCount == doc.pageCount
    }
}

final class PTCompressSession: PTSession {
    @Published var level: PTCompressLevel = .recommended
    @Published var totalSize: Int64 = 0

    override func filesDidChange(_ files: [URL]) {
        super.filesDidChange(files)
        totalSize = files.reduce(0) { $0 + ToolFiles.size(of: $1) }
    }

    override func optionsView() -> AnyView { AnyView(PTCompressOptions(session: self)) }

    override func makeJob() -> ToolJob { Self.job(level: level) }

    nonisolated static func job(level: PTCompressLevel) -> ToolJob {
        .perFile { file, context in
            let doc = try PTPDF.open(file)
            context.progress(0.05, "壓縮「\(file.lastPathComponent)」（\(doc.pageCount) 頁）…")
            let tmp = try context.makeWorkDirectory().appendingPathComponent("compressed.pdf")
            do {
                _ = try PTCompressor.compress(doc, level: level, to: tmp, context: context)
            } catch let error as ConversionError {
                throw error
            } catch {
                try? FileManager.default.removeItem(at: tmp)
                guard PTCompressor.compressWithFilter(doc, level: level, to: tmp) else {
                    throw ConversionError.writeFailed("無法壓縮「\(file.lastPathComponent)」。")
                }
            }
            try context.checkCancellation()
            let before = PTPDF.size(of: file)
            let after = PTPDF.size(of: tmp)
            let name = context.outputName(for: file, tag: "已壓縮", ext: "pdf")
            if after <= 0 || after >= before {
                // 沒有變小：保留原始內容
                try? FileManager.default.removeItem(at: tmp)
                try FileManager.default.copyItem(at: file, to: tmp)
                let url = try context.deliver(tmp, as: name, for: file)
                context.progress(1, "「\(file.lastPathComponent)」已經很精簡，壓縮後沒有變小，保留原始內容（\(ToolFiles.formattedSize(before))）")
                return [url]
            }
            let saved = Int((1 - Double(after) / Double(before)) * 100)
            let url = try context.deliver(tmp, as: name, for: file)
            context.progress(1, "已壓縮：\(ToolFiles.formattedSize(before)) → \(ToolFiles.formattedSize(after))（減少 \(saved)%）")
            return [url]
        }
    }
}

private struct PTCompressOptions: View {
    @ObservedObject var session: PTCompressSession

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            OptionGroup(title: "壓縮程度", systemImage: "gauge.with.dots.needle.33percent") {
                ChoiceCards(selection: $session.level, choices: [
                    OptionChoice(value: .extreme, title: "極度壓縮", subtitle: "檔案最小，圖片畫質較低（約 96 dpi）", systemImage: "arrow.down.to.line.compact"),
                    OptionChoice(value: .recommended, title: "建議壓縮", subtitle: "檔案明顯變小，畫質維持良好（約 150 dpi）", systemImage: "checkmark.seal", badge: "推薦"),
                    OptionChoice(value: .low, title: "低度壓縮", subtitle: "畫質最好，檔案稍微變小（約 220 dpi）", systemImage: "photo"),
                ])
            }
            if session.totalSize > 0 {
                OptionNote(text: "目前大小：\(ToolFiles.formattedSize(session.totalSize))", systemImage: "internaldrive")
            }
            OptionNote(text: "只重新壓縮與縮小圖片，文字與向量圖形保持原樣、仍可選取與搜尋。若壓縮後沒有變小，會保留原始內容。")
        }
    }
}
