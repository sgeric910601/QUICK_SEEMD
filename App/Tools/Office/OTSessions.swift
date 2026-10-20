import SwiftUI

// 各 Office 工具的工作階段（選項）與右側選項面板。

/// 共用：記住目前的檔案，並檢查引擎能不能用。
class OTSessionBase: ToolSession {
    @Published var files: [URL] = []
    @Published var engine: OTEngine = .auto

    var app: OTOfficeApp { .word }
    var support: OTSupport { .none }

    override func filesDidChange(_ files: [URL]) { self.files = files }

    func engineProblem() -> String? {
        if OTEngines.candidates(engine, app: app, support: support).isEmpty {
            if engine == .auto { return support.builtInNote ?? "沒有可用的轉換引擎" }
            if !OTEngines.isInstalled(engine, app: app) { return "\(OTEngines.displayName(engine, app: app)) 沒有安裝，請改選其他引擎" }
            return "\(OTEngines.displayName(engine, app: app)) 無法處理這個轉換，請改選其他引擎"
        }
        return nil
    }

    override func validate(_ files: [URL]) -> String? { engineProblem() }
}

// MARK: - Word 轉 PDF

final class OTWordToPDFSession: OTSessionBase {
    override var app: OTOfficeApp { .word }
    override var support: OTSupport { OTPipeline.support(files, OTPipeline.wordToPDFSupport) }
    override func optionsView() -> AnyView { AnyView(OTWordToPDFOptions(session: self)) }
    override func makeJob() -> ToolJob { OTJobFactory.wordToPDF(engine: engine) }
}

private struct OTWordToPDFOptions: View {
    @ObservedObject var session: OTWordToPDFSession

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            OTEngineGroup(selection: $session.engine, app: .word, support: session.support)
            OptionNote(text: "Microsoft Word 轉出的 PDF 與原稿最接近；macOS 內建引擎不需安裝任何軟體，適合一般文件（頁首頁尾、註腳與分節可能略有差異）。")
        }
    }
}

// MARK: - Excel 轉 PDF

final class OTExcelToPDFSession: OTSessionBase {
    @Published var orientation = "auto"
    @Published var fitWidth = true
    @Published var firstSheetOnly = false
    @Published var gridlines = false

    override var app: OTOfficeApp { .excel }
    override var support: OTSupport {
        OTPipeline.support(files) { OTPipeline.excelToPDFSupport($0, firstSheetOnly: firstSheetOnly) }
    }

    var sheetOptions: OTBuiltIn.SheetOptions {
        OTBuiltIn.SheetOptions(orientation: orientation, fitWidth: fitWidth, firstSheetOnly: firstSheetOnly, gridlines: gridlines)
    }

    override func optionsView() -> AnyView { AnyView(OTExcelToPDFOptions(session: self)) }
    override func makeJob() -> ToolJob { OTJobFactory.excelToPDF(engine: engine, sheet: sheetOptions) }
}

private struct OTExcelToPDFOptions: View {
    @ObservedObject var session: OTExcelToPDFSession

    var body: some View {
        let effective = OTEngines.candidates(session.engine, app: .excel, support: session.support).first
        VStack(alignment: .leading, spacing: 20) {
            OptionGroup(title: "頁面方向", systemImage: "rectangle.portrait.rotate") {
                SegmentedOption(selection: $session.orientation, choices: [
                    OptionChoice(value: "auto", title: "自動"),
                    OptionChoice(value: "portrait", title: "直向"),
                    OptionChoice(value: "landscape", title: "橫向"),
                ])
            }
            OptionGroup(title: "版面", systemImage: "arrow.left.and.right.square") {
                OptionToggle(title: "縮放成一頁寬", subtitle: "欄位太多時自動縮小，不會被切到下一頁", isOn: $session.fitWidth)
                OptionToggle(title: "加印格線", subtitle: "沒有框線的表格比較好閱讀", isOn: $session.gridlines)
            }
            OptionGroup(title: "工作表", systemImage: "square.stack.3d.up") {
                ChoiceChips(selection: $session.firstSheetOnly, choices: [
                    OptionChoice(value: false, title: "全部工作表"),
                    OptionChoice(value: true, title: "只有第一個"),
                ])
            }
            OTEngineGroup(selection: $session.engine, app: .excel, support: session.support)
            if effective == .libreOffice {
                OptionNote(text: "LibreOffice 會沿用檔案本身的版面設定（方向與縮放）。")
            }
        }
    }
}

// MARK: - PowerPoint 轉 PDF

final class OTPowerPointToPDFSession: OTSessionBase {
    @Published var notes = false

    override var app: OTOfficeApp { .powerpoint }
    override var support: OTSupport { OTPipeline.support(files, OTPipeline.powerpointToPDFSupport) }
    override func optionsView() -> AnyView { AnyView(OTPowerPointToPDFOptions(session: self)) }
    override func makeJob() -> ToolJob { OTJobFactory.powerpointToPDF(engine: engine, notes: notes) }
}

private struct OTPowerPointToPDFOptions: View {
    @ObservedObject var session: OTPowerPointToPDFSession

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            OptionGroup(title: "版面", systemImage: "rectangle.split.1x2") {
                ChoiceCards(selection: $session.notes, choices: [
                    OptionChoice(value: false, title: "只有投影片", subtitle: "每張投影片一頁，比例與原檔相同", systemImage: "rectangle"),
                    OptionChoice(value: true, title: "投影片＋備忘稿", subtitle: "上方投影片、下方備忘稿（A4 直式），適合講者練習或發講義", systemImage: "rectangle.split.1x2"),
                ])
            }
            OTEngineGroup(selection: $session.engine, app: .powerpoint, support: session.support)
        }
    }
}

// MARK: - PDF 轉 Word

final class OTPDFToWordSession: OTSessionBase {
    @Published var mode = OTPipeline.PDFWordMode.editable
    @Published var ocr = true
    @Published var dpi: Double = 200

    override var app: OTOfficeApp { .word }
    override var support: OTSupport { OTPipeline.pdfToWordSupport }
    override func validate(_ files: [URL]) -> String? { mode == .layout ? nil : engineProblem() }
    override func optionsView() -> AnyView { AnyView(OTPDFToWordOptions(session: self)) }
    override func makeJob() -> ToolJob { OTJobFactory.pdfToWord(mode: mode, engine: engine, ocr: ocr, dpi: dpi) }
}

private struct OTPDFToWordOptions: View {
    @ObservedObject var session: OTPDFToWordSession

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            OptionGroup(title: "轉換方式", systemImage: "doc.text") {
                ChoiceCards(selection: $session.mode, choices: [
                    OptionChoice(value: .editable, title: "可編輯文字", subtitle: "重建段落、標題、表格與圖片，可以直接修改文字（Word 引擎版面最佳）", systemImage: "character.cursor.ibeam", badge: "推薦"),
                    OptionChoice(value: .layout, title: "保留原始版面", subtitle: "每頁一張圖，外觀和 PDF 完全一樣，但文字不能編輯", systemImage: "photo.on.rectangle"),
                ])
            }
            if session.mode == .editable {
                OTEngineGroup(selection: $session.engine, app: .word, support: session.support)
                if session.engine == .office {
                    OptionNote(text: "Word 會跳出「轉換 PDF」的確認視窗，請切換到 Word 按「確定」。Word 重排版面的效果最好，但需要較長時間。",
                               systemImage: "exclamationmark.bubble")
                }
                OptionGroup(title: "掃描檔", systemImage: "text.viewfinder") {
                    OptionToggle(title: "辨識掃描頁面的文字", subtitle: "沒有文字層的頁面用 macOS 文字辨識（中英日韓）", isOn: $session.ocr)
                }
            } else {
                OptionGroup(title: "圖片品質", systemImage: "photo") {
                    SegmentedOption(selection: $session.dpi, choices: [
                        OptionChoice(value: 150, title: "標準"),
                        OptionChoice(value: 200, title: "高畫質"),
                        OptionChoice(value: 300, title: "印刷"),
                    ])
                }
            }
        }
    }
}

// MARK: - PDF 轉 Excel

final class OTPDFToExcelSession: ToolSession {
    @Published var merge = false
    @Published var numbers = true
    @Published var ocr = true

    override func optionsView() -> AnyView { AnyView(OTPDFToExcelOptions(session: self)) }
    override func makeJob() -> ToolJob {
        OTJobFactory.pdfToExcel(OTPDFConvert.ExcelOptions(mergeSheets: merge, convertNumbers: numbers, ocr: ocr))
    }
}

private struct OTPDFToExcelOptions: View {
    @ObservedObject var session: OTPDFToExcelSession

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            OptionGroup(title: "工作表", systemImage: "square.stack.3d.up") {
                ChoiceCards(selection: $session.merge, choices: [
                    OptionChoice(value: false, title: "每頁一個工作表", subtitle: "第 1 頁、第 2 頁…各自分開", systemImage: "square.grid.2x2"),
                    OptionChoice(value: true, title: "全部合併成一個工作表", subtitle: "跨頁的長表格接在一起，重複的表頭只留一次", systemImage: "rectangle.grid.1x2"),
                ])
            }
            OptionGroup(title: "內容", systemImage: "number") {
                OptionToggle(title: "數字轉成數值", subtitle: "12,400、23.5% 可以直接計算", isOn: $session.numbers)
                OptionToggle(title: "辨識掃描頁面的文字", subtitle: "沒有文字層的頁面用 macOS 文字辨識", isOn: $session.ocr)
            }
            OptionNote(text: "以 macOS 內建引擎依文字位置重建欄列；合併儲存格與框線樣式不會保留。")
        }
    }
}

// MARK: - PDF 轉 PowerPoint

final class OTPDFToPowerPointSession: ToolSession {
    @Published var slideSize = OTPDFConvert.SlideSize.auto
    @Published var quality = 1
    @Published var notes = false

    override func optionsView() -> AnyView { AnyView(OTPDFToPowerPointOptions(session: self)) }
    override func makeJob() -> ToolJob {
        let dpi: Double = quality == 0 ? 150 : (quality == 1 ? 220 : 300)
        return OTJobFactory.pdfToPowerPoint(OTPDFConvert.PowerPointOptions(slideSize: slideSize, dpi: dpi, png: quality == 2, notesFromText: notes))
    }
}

private struct OTPDFToPowerPointOptions: View {
    @ObservedObject var session: OTPDFToPowerPointSession

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            OptionGroup(title: "投影片大小", systemImage: "rectangle.ratio.16.to.9") {
                ChoiceCards(selection: $session.slideSize, choices: [
                    OptionChoice(value: .auto, title: "與 PDF 相同", subtitle: "每頁剛好填滿投影片", systemImage: "wand.and.stars"),
                    OptionChoice(value: .wide, title: "寬螢幕 16:9", subtitle: "現在最常用的投影片比例", systemImage: "rectangle.ratio.16.to.9"),
                    OptionChoice(value: .standard, title: "標準 4:3", subtitle: "舊式投影機", systemImage: "rectangle.ratio.4.to.3"),
                ])
            }
            OptionGroup(title: "畫質", systemImage: "photo") {
                SegmentedOption(selection: $session.quality, choices: [
                    OptionChoice(value: 0, title: "標準"),
                    OptionChoice(value: 1, title: "高畫質"),
                    OptionChoice(value: 2, title: "最高"),
                ])
                Text(session.quality == 2 ? "PNG 無損壓縮，檔案較大" : (session.quality == 1 ? "JPEG 220 dpi，清晰且檔案適中" : "JPEG 150 dpi，檔案最小"))
                    .font(.caption).foregroundStyle(.secondary)
            }
            OptionGroup(title: "備忘稿", systemImage: "note.text") {
                OptionToggle(title: "把每頁的文字放進備忘稿", subtitle: "方便搜尋與上台提詞", isOn: $session.notes)
            }
        }
    }
}

// MARK: - Word 格式轉換

final class OTWordConvertSession: OTSessionBase {
    @Published var target = "docx"
    private var userPicked = false

    override var app: OTOfficeApp { .word }
    override var support: OTSupport { OTPipeline.support(files) { OTPipeline.wordConvertSupport($0, target: target) } }

    override func filesDidChange(_ files: [URL]) {
        super.filesDidChange(files)
        if !userPicked { target = files.allSatisfy { ["docx", "docm"].contains(OTKind.ext($0)) } && !files.isEmpty ? "pdf" : "docx" }
    }

    func pick(_ t: String) { target = t; userPicked = true }

    override func validate(_ files: [URL]) -> String? {
        if let same = files.first(where: { OTKind.ext($0) == target }) { return "「\(same.lastPathComponent)」已經是 \(target.uppercased())" }
        return engineProblem()
    }

    override func optionsView() -> AnyView { AnyView(OTWordConvertOptions(session: self)) }
    override func makeJob() -> ToolJob { OTJobFactory.wordConvert(target: target, engine: engine) }
}

private struct OTWordConvertOptions: View {
    @ObservedObject var session: OTWordConvertSession

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            OptionGroup(title: "轉成", systemImage: "arrow.right.doc.on.clipboard") {
                ChoiceChips(selection: Binding(get: { session.target }, set: { session.pick($0) }), choices: [
                    OptionChoice(value: "docx", title: "DOCX", subtitle: "Word 2007 以後的格式"),
                    OptionChoice(value: "doc", title: "DOC", subtitle: "Word 97–2004"),
                    OptionChoice(value: "pdf", title: "PDF"),
                    OptionChoice(value: "rtf", title: "RTF", subtitle: "格式化文字"),
                    OptionChoice(value: "odt", title: "ODT", subtitle: "OpenDocument 文字"),
                    OptionChoice(value: "txt", title: "TXT", subtitle: "純文字（UTF-8）"),
                    OptionChoice(value: "html", title: "HTML", subtitle: "網頁（圖片內嵌）"),
                    OptionChoice(value: "md", title: "Markdown", subtitle: "標題、清單、表格、粗斜體"),
                ])
            }
            OTEngineGroup(selection: $session.engine, app: .word, support: session.support)
        }
    }
}

// MARK: - Excel 格式轉換

final class OTExcelConvertSession: OTSessionBase {
    @Published var target = "xlsx"
    @Published var encoding = OTCSVEncoding.utf8BOM
    @Published var sheets = OTSheetScope.each
    private var userPicked = false

    override var app: OTOfficeApp { .excel }
    override var support: OTSupport { OTPipeline.support(files) { OTPipeline.excelConvertSupport($0, target: target) } }

    override func filesDidChange(_ files: [URL]) {
        super.filesDidChange(files)
        if !userPicked {
            let exts = Set(files.map(OTKind.ext))
            target = exts.isSubset(of: ["xlsx", "xlsm"]) && !exts.isEmpty ? "csv" : "xlsx"
        }
    }

    func pick(_ t: String) { target = t; userPicked = true }

    override func validate(_ files: [URL]) -> String? {
        if target != "csv", let same = files.first(where: { OTKind.ext($0) == target }) {
            return "「\(same.lastPathComponent)」已經是 \(target.uppercased())"
        }
        return engineProblem()
    }

    override func optionsView() -> AnyView { AnyView(OTExcelConvertOptions(session: self)) }
    override func makeJob() -> ToolJob { OTJobFactory.excelConvert(target: target, engine: engine, encoding: encoding, sheets: sheets) }
}

private struct OTExcelConvertOptions: View {
    @ObservedObject var session: OTExcelConvertSession

    var body: some View {
        let text = ["csv", "tsv"].contains(session.target)
        let tables = OTPipeline.tableTargets.contains(session.target)
        VStack(alignment: .leading, spacing: 20) {
            OptionGroup(title: "轉成", systemImage: "arrow.right.doc.on.clipboard") {
                ChoiceChips(selection: Binding(get: { session.target }, set: { session.pick($0) }), choices: [
                    OptionChoice(value: "xlsx", title: "XLSX"),
                    OptionChoice(value: "xls", title: "XLS", subtitle: "Excel 97–2004"),
                    OptionChoice(value: "csv", title: "CSV"),
                    OptionChoice(value: "tsv", title: "TSV", subtitle: "Tab 分隔"),
                    OptionChoice(value: "ods", title: "ODS", subtitle: "OpenDocument 試算表"),
                    OptionChoice(value: "json", title: "JSON"),
                    OptionChoice(value: "html", title: "HTML", subtitle: "網頁表格"),
                    OptionChoice(value: "pdf", title: "PDF"),
                ])
            }
            if text {
                OptionGroup(title: "文字編碼", systemImage: "character.book.closed") {
                    ChoiceCards(selection: $session.encoding, choices: [
                        OptionChoice(value: .utf8BOM, title: "UTF-8（含 BOM）", subtitle: "Excel 直接開啟中文不會亂碼", badge: "推薦"),
                        OptionChoice(value: .utf8, title: "UTF-8", subtitle: "通用格式，適合程式與資料庫匯入"),
                        OptionChoice(value: .big5, title: "Big5", subtitle: "舊版 Windows 或政府、銀行系統"),
                    ])
                }
            }
            if tables {
                OptionGroup(title: "工作表", systemImage: "square.stack.3d.up") {
                    ChoiceChips(selection: $session.sheets, choices: [
                        OptionChoice(value: .each, title: text ? "每個工作表各存一檔" : "全部工作表"),
                        OptionChoice(value: .first, title: "只有第一個"),
                    ])
                }
            }
            OTEngineGroup(selection: $session.engine, app: .excel, support: session.support)
            if tables {
                OptionNote(text: "CSV、TSV、JSON 只保存儲存格的值；公式會換成計算結果（用 Excel 引擎時會先重新計算）。")
            }
        }
    }
}

// MARK: - PowerPoint 格式轉換

final class OTPowerPointConvertSession: OTSessionBase {
    @Published var target = "pptx"
    @Published var imageWidth = 1920
    private var userPicked = false

    override var app: OTOfficeApp { .powerpoint }
    override var support: OTSupport { OTPipeline.support(files) { OTPipeline.powerpointConvertSupport($0, target: target) } }

    override func filesDidChange(_ files: [URL]) {
        super.filesDidChange(files)
        if !userPicked {
            target = files.allSatisfy { ["pptx", "pptm"].contains(OTKind.ext($0)) } && !files.isEmpty ? "pdf" : "pptx"
        }
    }

    func pick(_ t: String) { target = t; userPicked = true }

    override func validate(_ files: [URL]) -> String? {
        if let same = files.first(where: { OTKind.ext($0) == target }) { return "「\(same.lastPathComponent)」已經是 \(target.uppercased())" }
        return engineProblem()
    }

    override func optionsView() -> AnyView { AnyView(OTPowerPointConvertOptions(session: self)) }
    override func makeJob() -> ToolJob { OTJobFactory.powerpointConvert(target: target, engine: engine, imageWidth: imageWidth) }
}

private struct OTPowerPointConvertOptions: View {
    @ObservedObject var session: OTPowerPointConvertSession

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            OptionGroup(title: "轉成", systemImage: "arrow.right.doc.on.clipboard") {
                ChoiceChips(selection: Binding(get: { session.target }, set: { session.pick($0) }), choices: [
                    OptionChoice(value: "pptx", title: "PPTX"),
                    OptionChoice(value: "ppt", title: "PPT", subtitle: "PowerPoint 97–2003"),
                    OptionChoice(value: "odp", title: "ODP", subtitle: "OpenDocument 簡報"),
                    OptionChoice(value: "pdf", title: "PDF"),
                    OptionChoice(value: "png", title: "PNG 圖片", subtitle: "每張投影片一張圖"),
                    OptionChoice(value: "jpg", title: "JPG 圖片", subtitle: "每張投影片一張圖"),
                ])
            }
            if ["png", "jpg"].contains(session.target) {
                OptionGroup(title: "圖片寬度", systemImage: "arrow.left.and.right") {
                    ChoiceChips(selection: $session.imageWidth, choices: [
                        OptionChoice(value: 1280, title: "1280 px"),
                        OptionChoice(value: 1920, title: "1920 px（Full HD）"),
                        OptionChoice(value: 3840, title: "3840 px（4K）"),
                    ])
                }
            }
            OTEngineGroup(selection: $session.engine, app: .powerpoint, support: session.support)
        }
    }
}
