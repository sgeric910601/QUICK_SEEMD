import Foundation
import PDFKit

// 每個工具的轉換流程：決定能用哪些引擎（支援度），依使用者的引擎選擇執行，最後交付結果。
// 這些函式都可以在背景執行，工具頁與右鍵動作共用。

enum OTPipeline {
    // MARK: - 支援度

    static func wordToPDFSupport(_ ext: String) -> OTSupport {
        OTSupport(office: OTKind.wordExtensions.contains(ext), libreOffice: true,
                  builtIn: OTWordBuiltIn.readable.contains(ext), builtInNote: "macOS 內建引擎無法讀取 .\(ext) 文件，需要 Word 或 LibreOffice")
    }

    static func excelToPDFSupport(_ ext: String, firstSheetOnly: Bool) -> OTSupport {
        OTSupport(office: OTKind.excelExtensions.contains(ext), libreOffice: !firstSheetOnly,
                  builtIn: ["xls", "xlsx", "xlsm", "xltx", "csv", "tsv"].contains(ext),
                  builtInNote: "macOS 內建引擎無法讀取 .\(ext) 試算表，需要 Excel 或 LibreOffice")
    }

    static func powerpointToPDFSupport(_ ext: String) -> OTSupport {
        OTSupport(office: OTKind.powerpointExtensions.contains(ext), libreOffice: true,
                  builtIn: OTBuiltIn.systemPreviewSlideExtensions.contains(ext),
                  builtInNote: "macOS 內建引擎無法讀取 .\(ext) 簡報，需要 PowerPoint 或 LibreOffice")
    }

    /// PDF 轉 Word：Word 開 PDF 時一定會跳出「轉換確認」視窗，所以「自動」優先用內建引擎（不打擾、可 OCR）；
    /// 使用者明確選 Word 時才用 Word（我們會把 Word 叫到前面讓使用者按「確定」）。
    static let pdfToWordSupport = OTSupport(office: true, libreOffice: true, builtIn: true, preferBuiltIn: true)

    static func wordConvertSupport(_ ext: String, target: String) -> OTSupport {
        if target == "pdf" { return wordToPDFSupport(ext) }
        return OTSupport(office: OTOffice.wordFormats.contains(target) && OTKind.wordExtensions.contains(ext),
                         libreOffice: OTLibreOffice.supports(target: target, app: .word),
                         builtIn: OTWordBuiltIn.readable.contains(ext) && OTWordBuiltIn.writable.contains(target),
                         builtInNote: OTWordBuiltIn.readable.contains(ext) ? "macOS 內建引擎無法輸出 \(target.uppercased())"
                                                                           : "macOS 內建引擎無法讀取 .\(ext) 文件，需要 Word 或 LibreOffice",
                         preferBuiltIn: target == "md")
    }

    static let tableTargets: Set<String> = ["csv", "tsv", "json", "html"]

    static func excelConvertSupport(_ ext: String, target: String) -> OTSupport {
        if target == "pdf" { return excelToPDFSupport(ext, firstSheetOnly: false) }
        let table = tableTargets.contains(target)
        let readable = OTSpreadsheet.readable.contains(ext)
        let textInput = ext == "csv" || ext == "tsv"
        let note: String
        if !readable { note = "macOS 內建引擎無法讀取 .\(ext) 試算表，需要 Excel 或 LibreOffice" }
        else if target == "ods" { note = "輸出 ODS 需要安裝 LibreOffice" }
        else { note = "輸出 \(target.uppercased()) 需要 Excel 或 LibreOffice" }
        return OTSupport(office: OTKind.excelExtensions.contains(ext) && (["xlsx", "xls"].contains(target) || table) && !(textInput && table),
                         libreOffice: (["xlsx", "xls", "ods", "html"].contains(target) || table) && !(textInput && table),
                         builtIn: readable && (table || target == "xlsx"),
                         builtInNote: note,
                         // CSV／JSON 這類只要儲存格的值：內建引擎就夠了，不必啟動 Excel
                         preferBuiltIn: (table && ["xlsx", "xlsm", "csv", "tsv"].contains(ext)) || (target == "xlsx" && textInput))
    }

    static func powerpointConvertSupport(_ ext: String, target: String) -> OTSupport {
        let viaPDF = ["pdf", "png", "jpg"].contains(target)
        let note: String
        if viaPDF { note = "macOS 內建引擎無法讀取 .\(ext) 簡報，需要 PowerPoint 或 LibreOffice" }
        else if target == "odp" { note = "輸出 ODP 需要安裝 LibreOffice" }
        else { note = "輸出 \(target.uppercased()) 需要 PowerPoint 或 LibreOffice" }
        return OTSupport(office: OTKind.powerpointExtensions.contains(ext) && (["pptx", "ppt"].contains(target) || viaPDF),
                         libreOffice: ["pptx", "ppt", "odp"].contains(target) || viaPDF,
                         builtIn: viaPDF && OTBuiltIn.systemPreviewSlideExtensions.contains(ext),
                         builtInNote: note)
    }

    /// 多個檔案時取交集。
    static func support(_ files: [URL], _ body: (String) -> OTSupport) -> OTSupport {
        let exts = Set(files.map(OTKind.ext))
        guard let first = exts.first else { return body("") }
        return exts.dropFirst().reduce(body(first)) { $0.intersect(body($1)) }
    }

    // MARK: - 交付

    static func deliver(_ temp: URL, for input: URL, ext: String? = nil, tag: String? = nil, scope: OTOfficeScope? = nil,
                        context: ToolContext) throws -> URL {
        var pages: Int?
        if (ext ?? temp.pathExtension).lowercased() == "pdf" {
            OTPDFUnicodeFix.fix(temp)
            pages = PDFDocument(url: temp)?.pageCount
        }
        scope?.recordOutput(pages: pages)
        let name: String
        if CVFileNaming.isDirectory(temp) && !CVFileNaming.isPackage(temp) {
            name = temp.lastPathComponent
        } else {
            name = context.outputName(for: input, tag: tag, ext: ext ?? temp.pathExtension)
        }
        let delivered = try context.deliver(temp, as: name, for: input)
        OTWork.discard(temp)   // 清掉暫存資料夾（系統預覽 預覽、原檔副本等）
        return delivered
    }

    // MARK: - Word → PDF

    static func wordToPDF(_ file: URL, engine: OTEngine, scope: OTOfficeScope, context: ToolContext) async throws -> URL {
        let ext = OTKind.ext(file)
        return try await OTEngines.run(engine, app: .word, support: wordToPDFSupport(ext), context: context, scope: scope) { e in
            switch e {
            case .office: return try await OTOffice.convert(file, app: .word, to: "pdf", scope: scope, context: context)
            case .libreOffice: return try await OTLibreOffice.convert(file, app: .word, to: "pdf", context: context)
            default: return try await OTBuiltIn.wordToPDF(file, context: context)
            }
        }
    }

    // MARK: - Excel → PDF

    static func excelToPDF(_ file: URL, engine: OTEngine, sheet: OTBuiltIn.SheetOptions, scope: OTOfficeScope,
                           context: ToolContext) async throws -> URL {
        let ext = OTKind.ext(file)
        return try await OTEngines.run(engine, app: .excel, support: excelToPDFSupport(ext, firstSheetOnly: sheet.firstSheetOnly), context: context, scope: scope) { e in
            switch e {
            case .office:
                // CSV 先轉成 XLSX（Excel 直接開 UTF-8 CSV 時中文常變亂碼）
                var source = file
                if ext == "csv" || ext == "tsv" { source = try await OTSpreadsheet.csvToXLSX(file) }
                var options = OTOffice.ExcelPDFOptions()
                options.firstSheetOnly = sheet.firstSheetOnly
                let wantsLayout = sheet.orientation != "auto" || sheet.fitWidth || sheet.gridlines
                if wantsLayout, ["xlsx", "xlsm"].contains(OTKind.ext(source)) {
                    // 直接改檔案裡的版面設定（不需要印表機，最可靠）
                    let adjusted = try await OTXLSXPageSetup.apply(to: source, orientation: sheet.orientation, fitWidth: sheet.fitWidth, gridlines: sheet.gridlines)
                    if source != file { OTWork.discard(source) }
                    source = adjusted
                } else {
                    // .xls／.ods：改用 Excel 的版面設定（有設定印表機時才會生效）
                    options.orientation = sheet.orientation
                    options.fitWidth = sheet.fitWidth
                    options.gridlines = sheet.gridlines ? "1" : "keep"
                }
                defer { if source != file { OTWork.discard(source) } }
                return try await OTOffice.convert(source, app: .excel, to: "pdf", excel: options, scope: scope, context: context)
            case .libreOffice:
                return try await OTLibreOffice.convert(file, app: .excel, to: "pdf", context: context)
            default:
                return try await OTBuiltIn.excelToPDF(file, options: sheet, context: context)
            }
        }
    }

    // MARK: - PowerPoint → PDF

    static func powerpointToPDF(_ file: URL, engine: OTEngine, notes: Bool, scope: OTOfficeScope, context: ToolContext) async throws -> URL {
        let ext = OTKind.ext(file)
        var used = OTEngine.builtIn
        let slides = try await OTEngines.run(engine, app: .powerpoint, support: powerpointToPDFSupport(ext), context: context, scope: scope) { e in
            used = e
            switch e {
            case .office: return try await OTOffice.convert(file, app: .powerpoint, to: "pdf", scope: scope, context: context)
            case .libreOffice: return try await OTLibreOffice.convert(file, app: .powerpoint, to: "pdf", context: context)
            default: return try await OTBuiltIn.powerpointToPDF(file, context: context)
            }
        }
        guard notes else { return slides }
        context.progress(0.9, "正在加入備忘稿…")
        // 備忘稿從 .pptx 讀；舊格式先請 Office／LibreOffice 存成 .pptx
        var pptx: URL?
        if ["pptx", "pptm", "ppsx", "potx"].contains(ext) {
            pptx = file
        } else if used == .office {
            pptx = try? await OTOffice.convert(file, app: .powerpoint, to: "pptx", scope: scope, context: context)
        } else if used == .libreOffice {
            pptx = try? await OTLibreOffice.convert(file, app: .powerpoint, to: "pptx", context: context)
        }
        defer { if let pptx, pptx != file { OTWork.discard(pptx) } }
        guard let pptx, let list = try? await OTPresentationNotes.read(pptx),
              list.contains(where: { !$0.isEmpty }) else { return slides }
        let out = slides.deletingLastPathComponent().appendingPathComponent("notes-" + slides.lastPathComponent)
        try OTNotesPages.compose(slides: slides, notes: list, to: out)
        return out
    }

    // MARK: - PDF → Word

    enum PDFWordMode: String, Sendable { case editable, layout }

    static func pdfToWord(_ file: URL, mode: PDFWordMode, engine: OTEngine, ocr: Bool, dpi: Double, scope: OTOfficeScope,
                          context: ToolContext) async throws -> URL {
        if mode == .layout {
            scope.record(engine: OTEngine.builtIn.title)
            return try await OTPDFConvert.toWordImages(file, dpi: dpi, context: context)
        }
        var choice = engine
        // 掃描檔（沒有文字層）在自動模式下交給內建引擎做文字辨識
        if engine == .auto && ocr && !hasTextLayer(file) { choice = .builtIn }
        return try await OTEngines.run(choice, app: .word, support: pdfToWordSupport, context: context, scope: scope) { e in
            switch e {
            case .office: return try await OTOffice.convert(file, app: .word, to: "docx", scope: scope, context: context)
            case .libreOffice: return try await OTLibreOffice.convert(file, app: .word, to: "docx", inFilter: "writer_pdf_import", context: context)
            default: return try await OTPDFConvert.toWordText(file, ocr: ocr, context: context)
            }
        }
    }

    static func hasTextLayer(_ file: URL) -> Bool {
        guard let doc = PDFDocument(url: file) else { return true }
        for i in 0..<min(doc.pageCount, 5) {
            if let s = doc.page(at: i)?.string, !s.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { return true }
        }
        return false
    }

    // MARK: - Word 格式轉換

    static func wordConvert(_ file: URL, target: String, engine: OTEngine, scope: OTOfficeScope, context: ToolContext) async throws -> URL {
        if target == "pdf" { return try await wordToPDF(file, engine: engine, scope: scope, context: context) }
        let ext = OTKind.ext(file)
        return try await OTEngines.run(engine, app: .word, support: wordConvertSupport(ext, target: target), context: context, scope: scope) { e in
            switch e {
            case .office: return try await OTOffice.convert(file, app: .word, to: target, scope: scope, context: context)
            case .libreOffice: return try await OTLibreOffice.convert(file, app: .word, to: target, context: context)
            default:
                context.progress(0.3, "正在轉換…")
                return try await OTWordBuiltIn.convert(file, to: target)
            }
        }
    }

    // MARK: - Excel 格式轉換

    static func excelConvert(_ file: URL, target: String, engine: OTEngine, encoding: OTCSVEncoding, sheets: OTSheetScope,
                             scope: OTOfficeScope, context: ToolContext) async throws -> URL {
        if target == "pdf" {
            return try await excelToPDF(file, engine: engine, sheet: OTBuiltIn.SheetOptions(), scope: scope, context: context)
        }
        let ext = OTKind.ext(file)
        let base = CVFileNaming.baseName(of: file)
        let table = tableTargets.contains(target)
        return try await OTEngines.run(engine, app: .excel, support: excelConvertSupport(ext, target: target), context: context, scope: scope) { e in
            switch e {
            case .office:
                var source = file
                if ext == "csv" || ext == "tsv" { source = try await OTSpreadsheet.csvToXLSX(file) }
                defer { if source != file { OTWork.discard(source) } }
                if !table { return try await OTOffice.convert(source, app: .excel, to: target, scope: scope, context: context) }
                // 先請 Excel 存成 XLSX（公式會重新計算），再由內建引擎輸出 CSV／JSON…
                let xlsx = try await OTOffice.convert(source, app: .excel, to: "xlsx", scope: scope, context: context)
                defer { OTWork.discard(xlsx) }
                context.progress(0.9, "正在輸出 \(target.uppercased())…")
                return try await OTSpreadsheet.write(try await OTSpreadsheet.readTables(xlsx), baseName: base, target: target,
                                                     encoding: encoding, scope: sheets)
            case .libreOffice:
                if !table || target == "html" { return try await OTLibreOffice.convert(file, app: .excel, to: target, context: context) }
                let xlsx = try await OTLibreOffice.convert(file, app: .excel, to: "xlsx", context: context)
                defer { OTWork.discard(xlsx) }
                return try await OTSpreadsheet.write(try await OTSpreadsheet.readTables(xlsx), baseName: base, target: target,
                                                     encoding: encoding, scope: sheets)
            default:
                context.progress(0.2, "正在讀取試算表…")
                let tables = try await OTSpreadsheet.readTables(file)
                context.progress(0.7, "正在輸出 \(target.uppercased())…")
                return try await OTSpreadsheet.write(tables, baseName: base, target: target, encoding: encoding, scope: sheets)
            }
        }
    }

    // MARK: - PowerPoint 格式轉換

    static func powerpointConvert(_ file: URL, target: String, engine: OTEngine, imageWidth: Int, scope: OTOfficeScope,
                                  context: ToolContext) async throws -> URL {
        if ["pdf", "png", "jpg"].contains(target) {
            let pdf = try await powerpointToPDF(file, engine: engine, notes: false, scope: scope, context: context.segment(0, of: target == "pdf" ? 1 : 2))
            if target == "pdf" { return pdf }
            defer { OTWork.discard(pdf) }
            OTPDFUnicodeFix.fix(pdf)
            let sub = context.segment(1, of: 2)
            return try OTPDFConvert.toImages(pdf, baseName: CVFileNaming.sanitize(CVFileNaming.baseName(of: file)), format: target,
                                             width: imageWidth, context: sub)
        }
        let ext = OTKind.ext(file)
        return try await OTEngines.run(engine, app: .powerpoint, support: powerpointConvertSupport(ext, target: target), context: context, scope: scope) { e in
            switch e {
            case .office: return try await OTOffice.convert(file, app: .powerpoint, to: target, scope: scope, context: context)
            default: return try await OTLibreOffice.convert(file, app: .powerpoint, to: target, context: context)
            }
        }
    }
}
