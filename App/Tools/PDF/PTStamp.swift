import PDFKit
import SwiftUI
import UniformTypeIdentifiers

// 加入浮水印、加入頁碼：原頁面以向量方式重畫，再把文字疊在上方（或放在下方）。

extension PDFToolCatalog {
    static let watermark = ToolDefinition(
        id: "pdf.watermark",
        category: .pdf,
        title: "加入浮水印",
        subtitle: "在頁面上加入文字浮水印，可調整字型、顏色、透明度、角度與位置。",
        symbol: "seal",
        badge: "PDF",
        inputTypes: [.pdf],
        inputLabel: "PDF 檔案",
        actionTitle: "加入浮水印",
        keywords: ["watermark", "stamp", "confidential", "draft", "浮水印", "印章", "機密", "草稿"],
        makeSession: { PTWatermarkSession() }
    )

    static let pageNumbers = ToolDefinition(
        id: "pdf.page-numbers",
        category: .pdf,
        title: "加入頁碼",
        subtitle: "在每頁的頁首或頁尾加上頁碼，可選位置、格式與起始號碼。",
        symbol: "number.square",
        badge: "PDF",
        inputTypes: [.pdf],
        inputLabel: "PDF 檔案",
        actionTitle: "加入頁碼",
        keywords: ["page numbers", "numbering", "bates", "footer", "header", "頁碼", "編號", "頁尾"],
        quickActions: [
            "pdf.add-page-numbers": { PTPageNumberSession.job(PTPageNumberOptions()) },
        ],
        makeSession: { PTPageNumberSession() }
    )
}

// MARK: - 預覽

/// 選項面板上方的小預覽：第一頁縮圖＋實際的浮水印／頁碼繪製。
struct PTStampPreview: View {
    let page: CGImage?
    let pageSize: CGSize
    let draw: (CGContext, CGSize) -> Void

    var body: some View {
        let size = pageSize.width > 0 && pageSize.height > 0 ? pageSize : CGSize(width: 595, height: 842)
        let height: CGFloat = 200
        let width = height * size.width / size.height
        Canvas { context, canvasSize in
            context.withCGContext { cg in
                cg.saveGState()
                cg.translateBy(x: 0, y: canvasSize.height)
                cg.scaleBy(x: 1, y: -1)
                cg.setFillColor(CGColor(gray: 1, alpha: 1))
                cg.fill(CGRect(origin: .zero, size: canvasSize))
                if let page { cg.draw(page, in: CGRect(origin: .zero, size: canvasSize)) }
                cg.scaleBy(x: canvasSize.width / size.width, y: canvasSize.height / size.height)
                draw(cg, size)
                cg.restoreGState()
            }
        }
        .frame(width: min(width, 280), height: height)
        .clipShape(RoundedRectangle(cornerRadius: 4))
        .overlay(RoundedRectangle(cornerRadius: 4).strokeBorder(Color.primary.opacity(0.15)))
        .shadow(color: .black.opacity(0.08), radius: 4, y: 2)
        .frame(maxWidth: .infinity)
    }
}

/// 有預覽的工作階段：讀第一個檔案的第一頁。
class PTPreviewSession: PTSession {
    @Published var previewImage: CGImage?
    @Published var previewSize = CGSize(width: 595, height: 842)
    private var previewFile: URL?

    override func filesDidChange(_ files: [URL]) {
        super.filesDidChange(files)
        guard let first = files.first, first != previewFile else { return }
        previewFile = first
        previewImage = nil
        Task.detached(priority: .userInitiated) {
            guard let doc = PDFDocument(url: first), !doc.isLocked, let page = doc.page(at: 0), let ref = page.pageRef else { return }
            let size = PTPDF.visualSize(page)
            let image = CVPDFConverter.render(page: ref, dpi: 40)
            let box = PTImageBox(image: image)
            await MainActor.run { [weak self] in
                guard let self, self.previewFile == first else { return }
                self.previewSize = size
                self.previewImage = box.image
            }
        }
    }
}

/// 讓 CGImage 跨執行緒傳遞（建立後不再修改）。
struct PTImageBox: @unchecked Sendable {
    let image: CGImage?
}

// MARK: - 浮水印

enum PTWatermarkLayout: String, Sendable {
    case single, tiled
}

struct PTWatermarkOptions: Sendable {
    var text = "機密文件"
    var font: PTFontStyle = .sans
    var bold = true
    var size = 60.0
    var ink: PTInk = .gray
    var opacity = 30.0
    var rotation = 45
    var layout: PTWatermarkLayout = .single
    var position: PTGridPosition = .center
    var behind = false
    var pages = ""
}

enum PTWatermarkDrawer {
    static func draw(_ o: PTWatermarkOptions, in ctx: CGContext, size: CGSize) {
        let text = o.text.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !text.isEmpty else { return }
        let line = PTTextLine(text, fontName: o.font.name(bold: o.bold), size: CGFloat(o.size))
        guard line.width > 0 else { return }
        ctx.saveGState()
        defer { ctx.restoreGState() }
        ctx.setFillColor(o.ink.cgColor)
        ctx.setAlpha(CGFloat(o.opacity / 100))
        let angle = CGFloat(o.rotation) * .pi / 180
        switch o.layout {
        case .tiled:
            ctx.clip(to: CGRect(origin: .zero, size: size))
            let stepX = line.width + CGFloat(o.size) * 2
            let stepY = line.height * 3.2
            let half = hypot(size.width, size.height) / 2 + stepX
            ctx.translateBy(x: size.width / 2, y: size.height / 2)
            ctx.rotate(by: angle)
            var row = 0
            var y = -half
            while y <= half {
                var x = -half - (row % 2 == 0 ? 0 : stepX / 2)
                while x <= half {
                    line.draw(in: ctx, x: x, baseline: y)
                    x += stepX
                }
                y += stepY
                row += 1
            }
        case .single:
            let margin: CGFloat = 36
            let c = abs(cos(angle)), s = abs(sin(angle))
            let box = CGSize(width: line.width * c + line.height * s, height: line.width * s + line.height * c)
            // 太大時自動縮小，確保整段文字都在頁面內
            let fit = min(1, (size.width - margin * 2) / box.width, (size.height - margin * 2) / box.height)
            let scaled = CGSize(width: box.width * fit, height: box.height * fit)
            let center = o.position.center(of: scaled, in: size, margin: margin)
            ctx.translateBy(x: center.x, y: center.y)
            ctx.rotate(by: angle)
            ctx.scaleBy(x: fit, y: fit)
            line.drawCentered(in: ctx)
        }
    }
}

final class PTWatermarkSession: PTPreviewSession {
    @Published var options = PTWatermarkOptions()

    override func validate(_ files: [URL]) -> String? {
        if let reason = super.validate(files) { return reason }
        if options.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { return "請輸入浮水印文字" }
        if let count = pageCount, PageRange.parse(options.pages, pageCount: count) == nil { return "頁碼範圍不正確" }
        return nil
    }

    override func optionsView() -> AnyView { AnyView(PTWatermarkPanel(session: self)) }

    override func makeJob() -> ToolJob { Self.job(options) }

    nonisolated static func job(_ o: PTWatermarkOptions) -> ToolJob {
        .perFile { file, context in
            let doc = try PTPDF.open(file)
            let pages = Set(try PTPDF.pages(o.pages, count: doc.pageCount, file: file).map { $0 - 1 })
            let tmp = try context.makeWorkDirectory().appendingPathComponent("watermark.pdf")
            try PTRedraw.run(doc, to: tmp, context: context, placement: o.behind ? .under : .over,
                             message: { "加入浮水印：第 \($0) / \($1) 頁…" }) { ctx, canvas in
                guard pages.contains(canvas.index) else { return false }
                PTWatermarkDrawer.draw(o, in: ctx, size: canvas.size)
                return true
            }
            return [try context.deliver(tmp, as: context.outputName(for: file, tag: "浮水印", ext: "pdf"), for: file)]
        }
    }
}

private struct PTWatermarkPanel: View {
    @ObservedObject var session: PTWatermarkSession

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            PTStampPreview(page: session.previewImage, pageSize: session.previewSize) { ctx, size in
                PTWatermarkDrawer.draw(session.options, in: ctx, size: size)
            }
            OptionGroup(title: "文字", systemImage: "pencil.line") {
                VStack(alignment: .leading, spacing: 12) {
                    OptionTextField(title: "浮水印文字", text: $session.options.text, placeholder: "例如：機密文件、草稿、CONFIDENTIAL")
                    HStack {
                        SegmentedOption(selection: $session.options.font, choices: [
                            OptionChoice(value: .sans, title: "黑體"),
                            OptionChoice(value: .serif, title: "明體"),
                        ])
                        .frame(width: 120)
                        Spacer()
                        Toggle("粗體", isOn: $session.options.bold).toggleStyle(.checkbox)
                    }
                    OptionSlider(title: "字級", value: $session.options.size, range: 12...160, step: 2, format: { "\(Int($0)) pt" })
                }
            }
            OptionGroup(title: "顏色與透明度", systemImage: "paintpalette") {
                VStack(alignment: .leading, spacing: 12) {
                    PTInkPicker(selection: $session.options.ink)
                    OptionSlider(title: "不透明度", value: $session.options.opacity, range: 10...100, step: 5, format: { "\(Int($0))%" })
                }
            }
            OptionGroup(title: "角度", systemImage: "angle") {
                SegmentedOption(selection: $session.options.rotation, choices: [
                    OptionChoice(value: 0, title: "0°"),
                    OptionChoice(value: 45, title: "45°"),
                    OptionChoice(value: -45, title: "-45°"),
                ])
            }
            OptionGroup(title: "位置", systemImage: "square.grid.3x3") {
                VStack(alignment: .leading, spacing: 12) {
                    SegmentedOption(selection: $session.options.layout, choices: [
                        OptionChoice(value: .single, title: "單一位置"),
                        OptionChoice(value: .tiled, title: "平鋪整頁"),
                    ])
                    if session.options.layout == .single {
                        PTPositionPicker(selection: $session.options.position)
                    }
                }
            }
            OptionGroup(title: "圖層", systemImage: "square.3.layers.3d") {
                VStack(alignment: .leading, spacing: 6) {
                    SegmentedOption(selection: $session.options.behind, choices: [
                        OptionChoice(value: false, title: "疊在內容上方"),
                        OptionChoice(value: true, title: "放在內容下方"),
                    ])
                    if session.options.behind {
                        Text("頁面有白色底色時，下方的浮水印會被蓋住。").font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
            OptionGroup(title: "頁面", systemImage: "doc.on.doc") {
                PageRangeField(title: "要加浮水印的頁面", text: $session.options.pages, pageCount: session.pageCount)
            }
        }
    }
}

// MARK: - 頁碼

enum PTNumberFormat: String, Sendable, CaseIterable {
    case plain, chinese, slash, chineseTotal, english

    func text(_ n: Int, total: Int) -> String {
        switch self {
        case .plain: return "\(n)"
        case .chinese: return "第 \(n) 頁"
        case .slash: return "\(n) / \(total)"
        case .chineseTotal: return "第 \(n) 頁，共 \(total) 頁"
        case .english: return "Page \(n)"
        }
    }

    var sample: String {
        switch self {
        case .plain: return "1"
        case .chinese: return "第 1 頁"
        case .slash: return "1 / N"
        case .chineseTotal: return "第 1 頁，共 N 頁"
        case .english: return "Page 1"
        }
    }
}

struct PTPageNumberOptions: Sendable {
    var position: PTGridPosition = .bottom
    var format: PTNumberFormat = .plain
    var start = 1
    var size = 11.0
    var margin = 30.0
    var skipFirst = false
    var ink: PTInk = .black
    var font: PTFontStyle = .sans
}

enum PTPageNumberDrawer {
    /// 第 index 頁（0 起算）要顯示的文字；nil 代表不加頁碼。
    static func label(_ o: PTPageNumberOptions, index: Int, count: Int) -> String? {
        if o.skipFirst && index == 0 { return nil }
        let numbered = o.skipFirst ? count - 1 : count
        let n = o.start + (o.skipFirst ? index - 1 : index)
        return o.format.text(n, total: o.start + numbered - 1)
    }

    static func draw(_ o: PTPageNumberOptions, text: String, in ctx: CGContext, size: CGSize) {
        let line = PTTextLine(text, fontName: o.font.name(bold: false), size: CGFloat(o.size))
        let m = CGFloat(o.margin)
        let x: CGFloat
        switch o.position.column {
        case 0: x = m
        case 1: x = (size.width - line.width) / 2
        default: x = size.width - m - line.width
        }
        let y = o.position.row == 0 ? size.height - m - line.ascent : m + line.descent
        ctx.saveGState()
        ctx.setFillColor(o.ink.cgColor)
        line.draw(in: ctx, x: x, baseline: y)
        ctx.restoreGState()
    }
}

final class PTPageNumberSession: PTPreviewSession {
    @Published var options = PTPageNumberOptions()

    override func optionsView() -> AnyView { AnyView(PTPageNumberPanel(session: self)) }

    override func makeJob() -> ToolJob { Self.job(options) }

    nonisolated static func job(_ o: PTPageNumberOptions) -> ToolJob {
        .perFile { file, context in
            let doc = try PTPDF.open(file)
            let tmp = try context.makeWorkDirectory().appendingPathComponent("numbered.pdf")
            try PTRedraw.run(doc, to: tmp, context: context, message: { "加入頁碼：第 \($0) / \($1) 頁…" }) { ctx, canvas in
                guard let text = PTPageNumberDrawer.label(o, index: canvas.index, count: canvas.count) else { return false }
                PTPageNumberDrawer.draw(o, text: text, in: ctx, size: canvas.size)
                return true
            }
            return [try context.deliver(tmp, as: context.outputName(for: file, tag: "已加頁碼", ext: "pdf"), for: file)]
        }
    }
}

private struct PTPageNumberPanel: View {
    @ObservedObject var session: PTPageNumberSession

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            PTStampPreview(page: session.previewImage, pageSize: session.previewSize) { ctx, size in
                let o = session.options
                let count = max(session.pageCount ?? 10, 2)
                // 預覽第一個有頁碼的頁面
                let index = o.skipFirst ? 1 : 0
                if let text = PTPageNumberDrawer.label(o, index: index, count: count) {
                    PTPageNumberDrawer.draw(o, text: text, in: ctx, size: size)
                }
            }
            OptionGroup(title: "位置", systemImage: "square.grid.3x2") {
                PTPositionPicker(selection: $session.options.position, rows: [0, 2])
            }
            OptionGroup(title: "格式", systemImage: "number") {
                ChoiceChips(selection: $session.options.format, choices: PTNumberFormat.allCases.map {
                    OptionChoice(value: $0, title: $0.sample)
                })
            }
            OptionGroup(title: "編號", systemImage: "list.number") {
                VStack(alignment: .leading, spacing: 12) {
                    PTNumberField(title: "起始號碼", value: $session.options.start, range: 0...99999)
                    OptionToggle(title: "第一頁不加頁碼", subtitle: "封面不編號，從第 2 頁開始以起始號碼編號", isOn: $session.options.skipFirst)
                }
            }
            OptionGroup(title: "文字樣式", systemImage: "paintbrush.pointed") {
                VStack(alignment: .leading, spacing: 12) {
                    OptionSlider(title: "字級", value: $session.options.size, range: 7...36, step: 1, format: { "\(Int($0)) pt" })
                    PTInkPicker(selection: $session.options.ink, choices: [.black, .gray, .blue, .red])
                    VStack(alignment: .leading, spacing: 4) {
                        Text("與頁面邊緣的距離").font(.callout)
                        SegmentedOption(selection: $session.options.margin, choices: [
                            OptionChoice(value: 18.0, title: "窄"),
                            OptionChoice(value: 30.0, title: "標準"),
                            OptionChoice(value: 48.0, title: "寬"),
                        ])
                    }
                }
            }
        }
    }
}
