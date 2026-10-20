import SwiftUI
import UniformTypeIdentifiers

// Office 文件工具：Word／Excel／PowerPoint ↔ PDF 與 Office 格式互轉。
// 每個工具都能選轉換引擎：自動、Microsoft Office、LibreOffice、macOS 內建。

enum OfficeToolCatalog {
    static let tools: [ToolDefinition] = [
        wordToPDF, excelToPDF, powerpointToPDF,
        pdfToWord, pdfToExcel, pdfToPowerPoint,
        wordConvert, excelConvert, powerpointConvert,
    ]

    // MARK: Office → PDF

    static let wordToPDF = ToolDefinition(
        id: "office.word-to-pdf",
        category: .office,
        title: "Word 轉 PDF",
        subtitle: "把 Word 文件（DOC、DOCX、RTF、ODT）轉成 PDF，保留字型、表格與圖片。",
        symbol: "doc.text",
        tint: ToolPalette.word,
        badge: "W",
        inputTypes: OTKind.wordTypes,
        inputLabel: "Word 文件",
        actionTitle: "轉成 PDF",
        keywords: ["word", "doc", "docx", "rtf", "odt", "pdf", "文件", "轉檔"],
        quickActions: [
            "office.to-pdf": { OTJobFactory.anyToPDF() },
        ],
        makeSession: { OTWordToPDFSession() }
    )

    static let excelToPDF = ToolDefinition(
        id: "office.excel-to-pdf",
        category: .office,
        title: "Excel 轉 PDF",
        subtitle: "把試算表轉成 PDF：可選方向、縮放成一頁寬，輸出全部或第一個工作表。",
        symbol: "tablecells",
        tint: ToolPalette.excel,
        badge: "X",
        inputTypes: OTKind.excelTypes,
        inputLabel: "Excel 試算表",
        actionTitle: "轉成 PDF",
        keywords: ["excel", "xls", "xlsx", "csv", "ods", "pdf", "試算表", "表格"],
        makeSession: { OTExcelToPDFSession() }
    )

    static let powerpointToPDF = ToolDefinition(
        id: "office.ppt-to-pdf",
        category: .office,
        title: "PowerPoint 轉 PDF",
        subtitle: "每張投影片一頁、比例不變，也可以把備忘稿一起印出來。",
        symbol: "play.rectangle",
        tint: ToolPalette.powerpoint,
        badge: "P",
        inputTypes: OTKind.powerpointTypes,
        inputLabel: "PowerPoint 簡報",
        actionTitle: "轉成 PDF",
        keywords: ["powerpoint", "ppt", "pptx", "odp", "pdf", "簡報", "投影片"],
        makeSession: { OTPowerPointToPDFSession() }
    )

    // MARK: PDF → Office

    static let pdfToWord = ToolDefinition(
        id: "office.pdf-to-word",
        category: .office,
        title: "PDF 轉 Word",
        subtitle: "把 PDF 變成可以編輯的 Word 文件，或每頁原封不動保留版面。掃描檔可自動辨識文字。",
        symbol: "doc.text.magnifyingglass",
        tint: ToolPalette.word,
        badge: "W",
        inputTypes: [.pdf],
        inputLabel: "PDF 檔案",
        actionTitle: "轉成 Word",
        keywords: ["pdf", "word", "docx", "編輯", "ocr", "文字辨識"],
        quickActions: [
            "office.pdf-to-docx": { OTJobFactory.pdfToWord(mode: .editable, engine: .auto, ocr: true, dpi: 200) },
        ],
        makeSession: { OTPDFToWordSession() }
    )

    static let pdfToExcel = ToolDefinition(
        id: "office.pdf-to-excel",
        category: .office,
        title: "PDF 轉 Excel",
        subtitle: "從 PDF 擷取表格到 Excel：依文字位置重建欄列，數字存成可計算的數值。",
        symbol: "tablecells.badge.ellipsis",
        tint: ToolPalette.excel,
        badge: "X",
        inputTypes: [.pdf],
        inputLabel: "PDF 檔案",
        actionTitle: "轉成 Excel",
        keywords: ["pdf", "excel", "xlsx", "表格", "擷取"],
        quickActions: [
            "office.pdf-to-xlsx": { OTJobFactory.pdfToExcel(OTPDFConvert.ExcelOptions()) },
        ],
        makeSession: { OTPDFToExcelSession() }
    )

    static let pdfToPowerPoint = ToolDefinition(
        id: "office.pdf-to-ppt",
        category: .office,
        title: "PDF 轉 PowerPoint",
        subtitle: "每一頁變成一張投影片，畫面完全一致，可以直接拿去簡報。",
        symbol: "rectangle.stack",
        tint: ToolPalette.powerpoint,
        badge: "P",
        inputTypes: [.pdf],
        inputLabel: "PDF 檔案",
        actionTitle: "轉成 PowerPoint",
        keywords: ["pdf", "powerpoint", "pptx", "簡報", "投影片"],
        quickActions: [
            "office.pdf-to-pptx": { OTJobFactory.pdfToPowerPoint(OTPDFConvert.PowerPointOptions()) },
        ],
        makeSession: { OTPDFToPowerPointSession() }
    )

    // MARK: 格式轉換

    static let wordConvert = ToolDefinition(
        id: "office.word-convert",
        category: .office,
        title: "Word 格式轉換",
        subtitle: "DOCX、DOC、PDF、RTF、ODT、TXT、HTML、Markdown 互相轉換。",
        symbol: "arrow.triangle.2.circlepath",
        tint: ToolPalette.word,
        badge: "W",
        inputTypes: OTKind.wordTypes,
        inputLabel: "Word 文件",
        actionTitle: "轉換格式",
        keywords: ["word", "doc", "docx", "rtf", "odt", "txt", "html", "markdown", "md", "升級"],
        quickActions: [
            "office.doc-to-docx": { OTJobFactory.wordConvert(target: "docx", engine: .auto) },
        ],
        makeSession: { OTWordConvertSession() }
    )

    static let excelConvert = ToolDefinition(
        id: "office.excel-convert",
        category: .office,
        title: "Excel 格式轉換",
        subtitle: "XLSX、XLS、CSV、TSV、ODS、JSON、HTML、PDF 互轉；CSV 可選擇編碼，Excel 開啟不亂碼。",
        symbol: "arrow.triangle.2.circlepath",
        tint: ToolPalette.excel,
        badge: "X",
        inputTypes: OTKind.excelTypes,
        inputLabel: "Excel 試算表",
        actionTitle: "轉換格式",
        keywords: ["excel", "xls", "xlsx", "csv", "tsv", "ods", "json", "html", "big5", "bom"],
        quickActions: [
            "office.xls-to-xlsx": { OTJobFactory.excelConvert(target: "xlsx", engine: .auto, encoding: .utf8BOM, sheets: .each) },
            "office.excel-to-csv": { OTJobFactory.excelConvert(target: "csv", engine: .auto, encoding: .utf8BOM, sheets: .each) },
        ],
        makeSession: { OTExcelConvertSession() }
    )

    static let powerpointConvert = ToolDefinition(
        id: "office.ppt-convert",
        category: .office,
        title: "PowerPoint 格式轉換",
        subtitle: "PPTX、PPT、ODP、PDF 互轉，或把每張投影片存成 PNG／JPG 圖片。",
        symbol: "arrow.triangle.2.circlepath",
        tint: ToolPalette.powerpoint,
        badge: "P",
        inputTypes: OTKind.powerpointTypes,
        inputLabel: "PowerPoint 簡報",
        actionTitle: "轉換格式",
        keywords: ["powerpoint", "ppt", "pptx", "odp", "png", "jpg", "圖片", "投影片", "升級"],
        quickActions: [
            "office.ppt-to-pptx": { OTJobFactory.powerpointConvert(target: "pptx", engine: .auto, imageWidth: 1920) },
            "office.ppt-to-png": { OTJobFactory.powerpointConvert(target: "png", engine: .auto, imageWidth: 1920) },
            "office.ppt-to-jpg": { OTJobFactory.powerpointConvert(target: "jpg", engine: .auto, imageWidth: 1920) },
        ],
        makeSession: { OTPowerPointConvertSession() }
    )
}

// MARK: - 背景工作

/// 依選項產生 ToolJob（工具頁與右鍵動作共用）。
enum OTJobFactory {
    /// 右鍵「轉成 PDF」：依檔案類型自動分流，使用最佳引擎。
    static func anyToPDF() -> ToolJob {
        OTJobs.perFile { file, context, scope in
            let pdf: URL
            switch OTKind.app(for: file) {
            case .word: pdf = try await OTPipeline.wordToPDF(file, engine: .auto, scope: scope, context: context)
            case .excel: pdf = try await OTPipeline.excelToPDF(file, engine: .auto, sheet: OTBuiltIn.SheetOptions(), scope: scope, context: context)
            case .powerpoint: pdf = try await OTPipeline.powerpointToPDF(file, engine: .auto, notes: false, scope: scope, context: context)
            case nil: throw ConversionError.unsupportedTarget(source: file.lastPathComponent, target: "PDF")
            }
            return [try OTPipeline.deliver(pdf, for: file, ext: "pdf", scope: scope, context: context)]
        }
    }

    static func wordToPDF(engine: OTEngine) -> ToolJob {
        OTJobs.perFile { file, context, scope in
            let pdf = try await OTPipeline.wordToPDF(file, engine: engine, scope: scope, context: context)
            return [try OTPipeline.deliver(pdf, for: file, ext: "pdf", scope: scope, context: context)]
        }
    }

    static func excelToPDF(engine: OTEngine, sheet: OTBuiltIn.SheetOptions) -> ToolJob {
        OTJobs.perFile { file, context, scope in
            let pdf = try await OTPipeline.excelToPDF(file, engine: engine, sheet: sheet, scope: scope, context: context)
            return [try OTPipeline.deliver(pdf, for: file, ext: "pdf", scope: scope, context: context)]
        }
    }

    static func powerpointToPDF(engine: OTEngine, notes: Bool) -> ToolJob {
        OTJobs.perFile { file, context, scope in
            let pdf = try await OTPipeline.powerpointToPDF(file, engine: engine, notes: notes, scope: scope, context: context)
            let withNotes = pdf.lastPathComponent.hasPrefix("notes-")
            return [try OTPipeline.deliver(pdf, for: file, ext: "pdf", tag: withNotes ? "備忘稿" : nil, scope: scope, context: context)]
        }
    }

    static func pdfToWord(mode: OTPipeline.PDFWordMode, engine: OTEngine, ocr: Bool, dpi: Double) -> ToolJob {
        OTJobs.perFile { file, context, scope in
            let docx = try await OTPipeline.pdfToWord(file, mode: mode, engine: engine, ocr: ocr, dpi: dpi, scope: scope, context: context)
            return [try OTPipeline.deliver(docx, for: file, ext: "docx", scope: scope, context: context)]
        }
    }

    static func pdfToExcel(_ options: OTPDFConvert.ExcelOptions) -> ToolJob {
        OTJobs.perFile { file, context, scope in
            let xlsx = try await OTPDFConvert.toExcel(file, options: options, context: context)
            scope.record(engine: OTEngine.builtIn.title)
            return [try OTPipeline.deliver(xlsx, for: file, ext: "xlsx", scope: scope, context: context)]
        }
    }

    static func pdfToPowerPoint(_ options: OTPDFConvert.PowerPointOptions) -> ToolJob {
        OTJobs.perFile { file, context, scope in
            let pptx = try await OTPDFConvert.toPowerPoint(file, options: options, context: context)
            scope.record(engine: OTEngine.builtIn.title)
            return [try OTPipeline.deliver(pptx, for: file, ext: "pptx", scope: scope, context: context)]
        }
    }

    static func wordConvert(target: String, engine: OTEngine) -> ToolJob {
        OTJobs.perFile { file, context, scope in
            if OTKind.ext(file) == target { throw ConversionError.other("「\(file.lastPathComponent)」已經是 \(target.uppercased()) 格式。") }
            let out = try await OTPipeline.wordConvert(file, target: target, engine: engine, scope: scope, context: context)
            return [try OTPipeline.deliver(out, for: file, ext: target, scope: scope, context: context)]
        }
    }

    static func excelConvert(target: String, engine: OTEngine, encoding: OTCSVEncoding, sheets: OTSheetScope) -> ToolJob {
        OTJobs.perFile { file, context, scope in
            if OTKind.ext(file) == target && target != "csv" { throw ConversionError.other("「\(file.lastPathComponent)」已經是 \(target.uppercased()) 格式。") }
            let out = try await OTPipeline.excelConvert(file, target: target, engine: engine, encoding: encoding, sheets: sheets,
                                                        scope: scope, context: context)
            // CSV → CSV（換編碼）時加上標記，避免和原檔同名
            let tag: String? = OTKind.ext(file) == target ? encoding.title : nil
            return [try OTPipeline.deliver(out, for: file, ext: target, tag: tag, scope: scope, context: context)]
        }
    }

    static func powerpointConvert(target: String, engine: OTEngine, imageWidth: Int) -> ToolJob {
        OTJobs.perFile { file, context, scope in
            if OTKind.ext(file) == target { throw ConversionError.other("「\(file.lastPathComponent)」已經是 \(target.uppercased()) 格式。") }
            let out = try await OTPipeline.powerpointConvert(file, target: target, engine: engine, imageWidth: imageWidth, scope: scope, context: context)
            return [try OTPipeline.deliver(out, for: file, ext: out.pathExtension.isEmpty ? nil : out.pathExtension, scope: scope, context: context)]
        }
    }
}
