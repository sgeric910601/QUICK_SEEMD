import CoreText
import PDFKit
import SwiftUI
import UniformTypeIdentifiers
import Vision

// OCR（加入看不見但可選取、可搜尋的文字層）與 PDF 轉文字。

extension PDFToolCatalog {
    static let ocr = ToolDefinition(
        id: "pdf.ocr",
        category: .pdf,
        title: "OCR PDF",
        subtitle: "辨識掃描檔中的文字，讓 PDF 可以搜尋、選取與複製，外觀不變。",
        symbol: "text.viewfinder",
        badge: "OCR",
        inputTypes: [.pdf],
        inputLabel: "PDF 檔案",
        actionTitle: "辨識文字",
        keywords: ["ocr", "searchable", "scan", "recognize text", "文字辨識", "可搜尋", "掃描", "辨識"],
        quickActions: [
            "pdf.ocr-searchable": { PTOCRSession.job(PTOCROptions()) },
        ],
        makeSession: { PTOCRSession() }
    )

    static let toText = ToolDefinition(
        id: "pdf.to-text",
        category: .pdf,
        title: "PDF 轉文字",
        subtitle: "取出 PDF 中的文字存成 .txt；掃描檔會自動以 OCR 辨識。",
        symbol: "doc.plaintext",
        tint: ToolPalette.slate,
        badge: "TXT",
        inputTypes: [.pdf],
        inputLabel: "PDF 檔案",
        actionTitle: "轉成文字",
        keywords: ["pdf to text", "txt", "extract text", "copy text", "轉文字", "純文字", "擷取文字"],
        makeSession: { PTToTextSession() }
    )
}

// MARK: - 語言

enum PTOCRLanguage: String, Sendable, CaseIterable {
    case traditional, simplified, japanese, korean, english

    var codes: [String] {
        switch self {
        case .traditional: return ["zh-Hant", "en-US"]
        case .simplified: return ["zh-Hans", "en-US"]
        case .japanese: return ["ja-JP", "en-US"]
        case .korean: return ["ko-KR", "en-US"]
        case .english: return ["en-US"]
        }
    }

    /// 不可見文字層用的字型（需含有對應的字元，複製出來才會正確）。
    var fontName: String {
        switch self {
        case .traditional: return "PingFangTC-Regular"
        case .simplified: return "PingFangSC-Regular"
        case .japanese: return "HiraginoSans-W3"
        case .korean: return "AppleSDGothicNeo-Regular"
        case .english: return "Helvetica"
        }
    }

    var title: String {
        switch self {
        case .traditional: return "繁體中文＋英文"
        case .simplified: return "簡體中文＋英文"
        case .japanese: return "日文＋英文"
        case .korean: return "韓文＋英文"
        case .english: return "英文"
        }
    }
}

struct PTOCROptions: Sendable {
    var language: PTOCRLanguage = .traditional
    var accurate = true
    var skipText = true
    var pages = ""
}

/// 一行辨識結果（座標為頁面左下角原點、單位 pt）。
struct PTOCRLine: Sendable {
    let text: String
    let rect: CGRect
}

enum PTRecognizer {
    /// 快速模式只支援拉丁語系；中日韓會自動改用精確模式。
    static func effectiveAccurate(_ o: PTOCROptions) -> Bool {
        if o.accurate { return true }
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = .fast
        let supported = (try? request.supportedRecognitionLanguages()) ?? []
        return !o.language.codes.allSatisfy { supported.contains($0) }
    }

    static func recognize(_ image: CGImage, pageSize: CGSize, language: PTOCRLanguage, accurate: Bool) throws -> [PTOCRLine] {
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = accurate ? .accurate : .fast
        request.usesLanguageCorrection = accurate
        let supported = (try? request.supportedRecognitionLanguages()) ?? []
        let langs = language.codes.filter { supported.contains($0) }
        if !langs.isEmpty { request.recognitionLanguages = langs }
        request.automaticallyDetectsLanguage = false
        try VNImageRequestHandler(cgImage: image, options: [:]).perform([request])
        return (request.results ?? []).compactMap { obs in
            guard let text = obs.topCandidates(1).first?.string.trimmingCharacters(in: .whitespaces), !text.isEmpty else { return nil }
            let b = obs.boundingBox
            return PTOCRLine(text: text, rect: CGRect(x: b.minX * pageSize.width, y: b.minY * pageSize.height,
                                                     width: b.width * pageSize.width, height: b.height * pageSize.height))
        }
    }

    /// 以適合 OCR 的解析度畫出頁面（約 300 dpi，最長邊不超過 4200 px）。
    static func renderForOCR(_ page: CGPDFPage, size: CGSize) -> CGImage? {
        let dpi = min(300, 4200 / max(size.width, size.height) * 72)
        return CVPDFConverter.render(page: page, dpi: max(dpi, 72))
    }

    /// 把辨識結果畫成看不見的文字（可選取、可搜尋），每行水平縮放到與原圖文字同寬。
    static func drawInvisible(_ lines: [PTOCRLine], fontName: String, in ctx: CGContext) {
        ctx.saveGState()
        defer { ctx.restoreGState() }
        ctx.setTextDrawingMode(.invisible)
        for line in lines where line.rect.width > 1 && line.rect.height > 1 {
            let probe = PTTextLine(line.text, fontName: fontName, size: 100)
            guard probe.width > 0, probe.height > 0 else { continue }
            let size = max(1, line.rect.height / probe.height * 100)
            let text = PTTextLine(line.text, fontName: fontName, size: size)
            guard text.width > 0 else { continue }
            ctx.saveGState()
            ctx.translateBy(x: line.rect.minX, y: line.rect.minY + text.descent)
            ctx.scaleBy(x: line.rect.width / text.width, y: 1)
            text.draw(in: ctx, x: 0, baseline: 0)
            ctx.restoreGState()
        }
    }
}

// MARK: - OCR

final class PTOCRSession: PTSession {
    @Published var options = PTOCROptions()

    override func validate(_ files: [URL]) -> String? {
        if let reason = super.validate(files) { return reason }
        if let count = pageCount, PageRange.parse(options.pages, pageCount: count) == nil { return "頁碼範圍不正確" }
        return nil
    }

    override func optionsView() -> AnyView { AnyView(PTOCRPanel(session: self)) }

    override func makeJob() -> ToolJob { Self.job(options) }

    nonisolated static func job(_ o: PTOCROptions) -> ToolJob {
        .perFile { file, context in
            let doc = try PTPDF.open(file)
            let pages = Set(try PTPDF.pages(o.pages, count: doc.pageCount, file: file).map { $0 - 1 })
            let accurate = PTRecognizer.effectiveAccurate(o)
            var recognized = 0, skipped = 0, found = 0
            // 先判斷哪些頁面已經有文字
            var hasText = Set<Int>()
            if o.skipText {
                for i in pages {
                    if let s = doc.page(at: i)?.string, !s.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { hasText.insert(i) }
                }
            }
            let tmp = try context.makeWorkDirectory().appendingPathComponent("ocr.pdf")
            try PTRedraw.run(doc, to: tmp, context: context, span: 0.02...0.95,
                             message: { "辨識第 \($0) / \($1) 頁…" + (accurate ? "" : "（快速）") }) { ctx, canvas in
                guard pages.contains(canvas.index) else { return false }
                if hasText.contains(canvas.index) { skipped += 1; return false }
                try context.checkCancellation()
                guard let image = PTRecognizer.renderForOCR(canvas.page, size: canvas.size) else { return false }
                let lines = try PTRecognizer.recognize(image, pageSize: canvas.size, language: o.language, accurate: accurate)
                PTRecognizer.drawInvisible(lines, fontName: o.language.fontName, in: ctx)
                recognized += 1
                found += lines.count
                return !lines.isEmpty
            }
            if recognized == 0 && skipped > 0 {
                throw ConversionError.other("「\(file.lastPathComponent)」的頁面已經有文字層，不需要 OCR。若要強制辨識，請關閉「略過已有文字的頁面」。")
            }
            if found == 0 {
                throw ConversionError.emptyContent("在「\(file.lastPathComponent)」中辨識不到任何文字。")
            }
            let url = try context.deliver(tmp, as: context.outputName(for: file, tag: "可搜尋", ext: "pdf"), for: file)
            var note = "已辨識 \(recognized) 頁、\(found) 行文字"
            if skipped > 0 { note += "，略過 \(skipped) 頁已有文字的頁面" }
            if !o.accurate && accurate { note += "（快速模式不支援此語言，已改用精確模式）" }
            context.progress(1, note)
            return [url]
        }
    }
}

private struct PTOCRPanel: View {
    @ObservedObject var session: PTOCRSession

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            OptionGroup(title: "文件語言", systemImage: "character.book.closed") {
                ChoiceCards(selection: $session.options.language, choices: [
                    OptionChoice(value: .traditional, title: "繁體中文＋英文", subtitle: "台灣、香港的文件", systemImage: "character"),
                    OptionChoice(value: .simplified, title: "簡體中文＋英文", subtitle: "中國大陸的文件", systemImage: "character"),
                    OptionChoice(value: .japanese, title: "日文＋英文", systemImage: "character.ja"),
                    OptionChoice(value: .korean, title: "韓文＋英文", systemImage: "character.ko"),
                    OptionChoice(value: .english, title: "英文", subtitle: "以及其他拉丁字母語言", systemImage: "globe"),
                ])
            }
            OptionGroup(title: "辨識方式", systemImage: "speedometer") {
                VStack(alignment: .leading, spacing: 6) {
                    SegmentedOption(selection: $session.options.accurate, choices: [
                        OptionChoice(value: false, title: "快速"),
                        OptionChoice(value: true, title: "精確"),
                    ])
                    Text(session.options.accurate ? "準確度最高，適合正式文件。" : "速度較快；中日韓文會自動改用精確模式。")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            OptionGroup(title: "頁面", systemImage: "doc.on.doc") {
                VStack(alignment: .leading, spacing: 12) {
                    PageRangeField(title: "要辨識的頁面", text: $session.options.pages, pageCount: session.pageCount)
                    OptionToggle(title: "略過已有文字的頁面", subtitle: "只辨識掃描或圖片頁面", isOn: $session.options.skipText)
                }
            }
            OptionNote(text: "頁面外觀完全不變，只在下方加入看不見的文字層，讓你可以搜尋、選取與複製。全程在這台 Mac 上處理，不會上傳。")
        }
    }
}

// MARK: - PDF 轉文字

struct PTToTextOptions: Sendable {
    var pages = ""
    var autoOCR = true
    var separators = true
    var language: PTOCRLanguage = .traditional
}

final class PTToTextSession: PTSession {
    @Published var options = PTToTextOptions()

    override func validate(_ files: [URL]) -> String? {
        if let reason = super.validate(files) { return reason }
        if let count = pageCount, PageRange.parse(options.pages, pageCount: count) == nil { return "頁碼範圍不正確" }
        return nil
    }

    override func optionsView() -> AnyView { AnyView(PTToTextPanel(session: self)) }

    override func makeJob() -> ToolJob { Self.job(options) }

    nonisolated static func job(_ o: PTToTextOptions) -> ToolJob {
        .perFile { file, context in
            let doc = try PTPDF.open(file)
            let list = try PTPDF.pages(o.pages, count: doc.pageCount, file: file)
            var parts: [String] = []
            var ocrPages = 0
            var anyText = false
            for (i, n) in list.enumerated() {
                try context.checkCancellation()
                context.progress(Double(i) / Double(list.count), "讀取第 \(n) 頁…")
                guard let page = doc.page(at: n - 1) else { continue }
                var text = page.string?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
                if text.isEmpty && o.autoOCR, let ref = page.pageRef {
                    context.progress(Double(i) / Double(list.count), "OCR 辨識第 \(n) 頁…")
                    let size = PTPDF.visualSize(page)
                    if let image = PTRecognizer.renderForOCR(ref, size: size) {
                        let lines = try PTRecognizer.recognize(image, pageSize: size, language: o.language, accurate: true)
                        text = PTToTextSession.join(lines)
                        if !text.isEmpty { ocrPages += 1 }
                    }
                }
                if !text.isEmpty { anyText = true }
                parts.append(o.separators ? "──── 第 \(n) 頁 ────\n\(text)" : text)
            }
            let body = parts.joined(separator: o.separators ? "\n\n" : "\n\u{0C}\n")
            if !anyText {
                throw ConversionError.emptyContent(o.autoOCR ? "「\(file.lastPathComponent)」中沒有文字，OCR 也辨識不到內容。"
                                                             : "「\(file.lastPathComponent)」沒有文字層，可開啟「自動 OCR」再試。")
            }
            let tmp = try context.makeWorkDirectory().appendingPathComponent("text.txt")
            try Data((body + "\n").utf8).write(to: tmp)
            if ocrPages > 0 { context.progress(1, "有 \(ocrPages) 頁以 OCR 辨識") }
            return [try context.deliver(tmp, as: context.outputName(for: file, ext: "txt"), for: file)]
        }
    }

    /// 依位置由上而下、由左而右排列辨識結果，同一行以空白相連。
    nonisolated static func join(_ lines: [PTOCRLine]) -> String {
        let sorted = lines.sorted { a, b in
            if abs(a.rect.midY - b.rect.midY) > min(a.rect.height, b.rect.height) * 0.5 { return a.rect.midY > b.rect.midY }
            return a.rect.minX < b.rect.minX
        }
        var out: [String] = []
        var last: CGRect?
        for line in sorted {
            if let l = last, abs(line.rect.midY - l.midY) <= min(line.rect.height, l.height) * 0.5, !out.isEmpty {
                out[out.count - 1] += " " + line.text
            } else {
                out.append(line.text)
            }
            last = line.rect
        }
        return out.joined(separator: "\n")
    }
}

private struct PTToTextPanel: View {
    @ObservedObject var session: PTToTextSession

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            OptionGroup(title: "頁面", systemImage: "doc.on.doc") {
                PageRangeField(title: "要轉換的頁面", text: $session.options.pages, pageCount: session.pageCount)
            }
            OptionGroup(title: "格式", systemImage: "text.alignleft") {
                OptionToggle(title: "標示頁碼分隔線", subtitle: "每頁開頭加上「第 N 頁」", isOn: $session.options.separators)
            }
            OptionGroup(title: "掃描檔", systemImage: "text.viewfinder") {
                VStack(alignment: .leading, spacing: 12) {
                    OptionToggle(title: "沒有文字時自動 OCR", subtitle: "以 Vision 辨識掃描或圖片頁面", isOn: $session.options.autoOCR)
                    if session.options.autoOCR {
                        Picker("辨識語言", selection: $session.options.language) {
                            ForEach(PTOCRLanguage.allCases, id: \.self) { Text($0.title).tag($0) }
                        }
                    }
                }
            }
            OptionNote(text: "輸出 UTF-8 純文字檔（.txt）。")
        }
    }
}
