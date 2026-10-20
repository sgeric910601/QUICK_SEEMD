import AppKit
import Foundation

/// 文件互轉：透過 NSAttributedString（與 textutil 相同的系統引擎）
/// 讀寫 TXT、RTF、RTFD、HTML、DOC、DOCX、ODT、Web 封存檔，並以 TextKit 分頁輸出 PDF。
enum CVDocumentConverter {
    /// 這些輸出格式由 NSAttributedString 處理。
    static let attributedOutputs: [ConversionFormat] = [
        ConversionFormats.pdf, ConversionFormats.docx, ConversionFormats.doc, ConversionFormats.odt, ConversionFormats.rtf,
        ConversionFormats.rtfd, ConversionFormats.html, ConversionFormats.webarchive, ConversionFormats.txt,
    ]

    static func documentType(_ kind: CVInputProfile.DocumentKind) -> NSAttributedString.DocumentType {
        switch kind {
        case .plain: return .plain
        case .rtf: return .rtf
        case .rtfd: return .rtfd
        case .html: return .html
        case .doc: return .docFormat
        case .docx: return .officeOpenXML
        case .odt: return .openDocument
        case .webArchive: return .webArchive
        }
    }

    static var bodyFont: NSFont { NSFont(name: "Helvetica Neue", size: 12) ?? NSFont.systemFont(ofSize: 12) }
    static var codeFont: NSFont { NSFont(name: "Menlo", size: 10.5) ?? NSFont.monospacedSystemFont(ofSize: 10.5, weight: .regular) }

    // MARK: - 讀取

    struct Loaded {
        let text: NSAttributedString
        let attributes: [NSAttributedString.DocumentAttributeKey: Any]
    }

    /// 讀取文件。HTML／Web 封存檔的匯入需要在主執行緒（WebKit）。
    @MainActor
    static func load(_ url: URL, profile: CVInputProfile, options: ConversionOptions) throws -> Loaded {
        if profile.isMarkdown {
            let decoded = try CVTextCodec.decode(url, forced: options.sourceEncoding.encoding)
            let title = CVMarkdownRenderer.firstHeading(decoded.text) ?? CVFileNaming.baseName(of: url)
            let html = CVMarkdownRenderer.renderDocument(decoded.text, title: title, forImport: true)
            return try loadHTML(html, baseURL: url.deletingLastPathComponent())
        }
        guard let kind = profile.documentKind else { throw ConversionError.unreadable("不是可辨識的文件格式。") }
        if kind == .plain {
            let decoded = try CVTextCodec.decode(url, forced: options.sourceEncoding.encoding)
            let paragraph = NSMutableParagraphStyle()
            paragraph.lineHeightMultiple = profile.isSourceCode ? 1.1 : 1.2
            let attrs: [NSAttributedString.Key: Any] = [
                .font: profile.isSourceCode || profile.dataKind != nil ? codeFont : bodyFont,
                .foregroundColor: NSColor.black,
                .paragraphStyle: paragraph,
            ]
            let text = CVTextCodec.normalizeLineEndings(decoded.text, to: .lf)
            return Loaded(text: NSAttributedString(string: text, attributes: attrs), attributes: [:])
        }
        var docAttrs: NSDictionary?
        var readOptions: [NSAttributedString.DocumentReadingOptionKey: Any] = [.documentType: documentType(kind)]
        if kind == .html { readOptions[.timeout] = 15.0 }
        do {
            let text = try NSAttributedString(url: url, options: readOptions, documentAttributes: &docAttrs)
            let attrs = (docAttrs as? [NSAttributedString.DocumentAttributeKey: Any]) ?? [:]
            return Loaded(text: text, attributes: attrs)
        } catch {
            throw ConversionError.unreadable("系統無法解析這份文件（\(error.localizedDescription)）。")
        }
    }

    @MainActor
    static func loadHTML(_ html: String, baseURL: URL?) throws -> Loaded {
        var docAttrs: NSDictionary?
        var opts: [NSAttributedString.DocumentReadingOptionKey: Any] = [
            .documentType: NSAttributedString.DocumentType.html,
            .characterEncoding: String.Encoding.utf8.rawValue,
            .timeout: 15.0,
        ]
        if let baseURL { opts[.baseURL] = baseURL }
        do {
            let text = try NSAttributedString(data: Data(html.utf8), options: opts, documentAttributes: &docAttrs)
            return Loaded(text: text, attributes: (docAttrs as? [NSAttributedString.DocumentAttributeKey: Any]) ?? [:])
        } catch {
            throw ConversionError.unreadable("HTML 解析失敗（\(error.localizedDescription)）。")
        }
    }

    // MARK: - 寫入

    @MainActor
    static func write(_ loaded: Loaded, formatID: String, to out: URL) throws {
        let text = loaded.text
        let range = NSRange(location: 0, length: text.length)
        var attrs: [NSAttributedString.DocumentAttributeKey: Any] = [:]
        for key in [NSAttributedString.DocumentAttributeKey.paperSize, .leftMargin, .rightMargin, .topMargin, .bottomMargin,
                    .title, .author, .company] {
            if let v = loaded.attributes[key] { attrs[key] = v }
        }
        do {
            switch formatID {
            case "txt":
                let plain = text.string.replacingOccurrences(of: "\u{FFFC}", with: "")
                try Data(plain.utf8).write(to: out)
            case "pdf":
                try renderPDF(text, attributes: loaded.attributes, to: out)
            case "rtfd":
                attrs[.documentType] = NSAttributedString.DocumentType.rtfd
                let wrapper = try text.fileWrapper(from: range, documentAttributes: attrs)
                try wrapper.write(to: out, options: .atomic, originalContentsURL: nil)
            default:
                let type: NSAttributedString.DocumentType
                switch formatID {
                case "rtf": type = text.containsAttachments ? .rtfd : .rtf
                case "html": type = .html
                case "doc": type = .docFormat
                case "docx": type = .officeOpenXML
                case "odt": type = .openDocument
                case "webarchive": type = .webArchive
                default: throw ConversionError.other("不支援的文件格式：\(formatID)")
                }
                if formatID == "rtf" && type == .rtfd {
                    // RTF 無法內嵌圖片：去掉附件後輸出純 RTF
                    let stripped = NSMutableAttributedString(attributedString: text)
                    stripped.mutableString.replaceOccurrences(of: "\u{FFFC}", with: "", options: [], range: NSRange(location: 0, length: stripped.length))
                    attrs[.documentType] = NSAttributedString.DocumentType.rtf
                    let data = try stripped.data(from: NSRange(location: 0, length: stripped.length), documentAttributes: attrs)
                    try data.write(to: out)
                    return
                }
                attrs[.documentType] = type
                if type == .html { attrs[.characterEncoding] = String.Encoding.utf8.rawValue }
                let data = try text.data(from: range, documentAttributes: attrs)
                try data.write(to: out)
            }
        } catch let error as ConversionError {
            throw error
        } catch {
            throw ConversionError.writeFailed(error.localizedDescription)
        }
    }

    // MARK: - 分頁 PDF

    /// 以 TextKit 1 分頁排版，輸出多頁 PDF（支援圖片附件、表格、分頁符號 \f）。
    @MainActor
    static func renderPDF(_ text: NSAttributedString, attributes: [NSAttributedString.DocumentAttributeKey: Any] = [:], to out: URL) throws {
        var paper = (attributes[.paperSize] as? NSValue)?.sizeValue ?? CGSize(width: 595.28, height: 841.89) // A4
        if paper.width < 144 || paper.height < 144 { paper = CGSize(width: 595.28, height: 841.89) }
        func margin(_ key: NSAttributedString.DocumentAttributeKey, _ fallback: CGFloat) -> CGFloat {
            if let v = attributes[key] as? CGFloat, v >= 18, v < paper.width / 3 { return v }
            if let v = attributes[key] as? Double, v >= 18, v < Double(paper.width) / 3 { return CGFloat(v) }
            return fallback
        }
        let left = margin(.leftMargin, 64), right = margin(.rightMargin, 64)
        let top = margin(.topMargin, 64), bottom = margin(.bottomMargin, 64)
        let textSize = CGSize(width: paper.width - left - right, height: paper.height - top - bottom)

        let storage = NSTextStorage(attributedString: text.length > 0 ? text : NSAttributedString(string: " "))
        let layout = NSLayoutManager()
        layout.allowsNonContiguousLayout = false
        storage.addLayoutManager(layout)
        var containers: [NSTextContainer] = []
        while containers.count < 10_000 {
            let container = NSTextContainer(size: textSize)
            container.lineFragmentPadding = 0
            layout.addTextContainer(container)
            containers.append(container)
            let glyphs = layout.glyphRange(for: container)
            if NSMaxRange(glyphs) >= layout.numberOfGlyphs { break }
        }

        var mediaBox = CGRect(origin: .zero, size: paper)
        let info: [CFString: Any] = [kCGPDFContextCreator: "QUICK_SEEMD"]
        guard let ctx = CGContext(out as CFURL, mediaBox: &mediaBox, info as CFDictionary) else {
            throw ConversionError.writeFailed("無法建立 PDF。")
        }
        let appearance = NSAppearance(named: .aqua) ?? NSAppearance.currentDrawing()
        appearance.performAsCurrentDrawingAppearance {
            for container in containers {
                let glyphs = layout.glyphRange(for: container)
                ctx.beginPDFPage(nil)
                ctx.saveGState()
                ctx.translateBy(x: 0, y: paper.height)
                ctx.scaleBy(x: 1, y: -1)
                let gc = NSGraphicsContext(cgContext: ctx, flipped: true)
                NSGraphicsContext.saveGraphicsState()
                NSGraphicsContext.current = gc
                let origin = CGPoint(x: left, y: top)
                if glyphs.length > 0 {
                    layout.drawBackground(forGlyphRange: glyphs, at: origin)
                    layout.drawGlyphs(forGlyphRange: glyphs, at: origin)
                }
                NSGraphicsContext.restoreGraphicsState()
                ctx.restoreGState()
                ctx.endPDFPage()
            }
        }
        ctx.closePDF()
    }

    // MARK: - 入口

    static func convert(_ input: URL, profile: CVInputProfile, to format: ConversionFormat, options: ConversionOptions,
                        workDir: URL) async throws -> [URL] {
        let out = workDir.appendingPathComponent(CVFileNaming.fileName(CVFileNaming.baseName(of: input), format.fileExtension))
        // Markdown → HTML 直接用自己的渲染器（保留樣式、支援深色模式）
        if profile.isMarkdown && format.id == "html" {
            let decoded = try CVTextCodec.decode(input, forced: options.sourceEncoding.encoding)
            let title = CVMarkdownRenderer.firstHeading(decoded.text) ?? CVFileNaming.baseName(of: input)
            let html = CVMarkdownRenderer.renderDocument(decoded.text, title: title, forImport: false)
            try Data(html.utf8).write(to: out)
            return [out]
        }
        if profile.isPDF {
            // 取文字（可能需要 OCR）在背景進行，寫檔才回主執行緒
            let box = try await Task.detached(priority: .userInitiated) {
                CVAttributedBox(try CVPDFConverter.attributedString(input))
            }.value
            try await MainActor.run {
                try write(Loaded(text: box.value, attributes: [:]), formatID: format.id, to: out)
            }
            return [out]
        }
        try await MainActor.run {
            let loaded = try load(input, profile: profile, options: options)
            try write(loaded, formatID: format.id, to: out)
        }
        return [out]
    }
}

/// 讓 NSAttributedString 跨執行緒傳遞（建立後不再修改）。
final class CVAttributedBox: @unchecked Sendable {
    let value: NSAttributedString
    init(_ value: NSAttributedString) { self.value = value }
}
