import AppKit
import Foundation
import PDFKit
import UniformTypeIdentifiers

// Office 文件轉換的「引擎」層：使用者可以選 Microsoft Office、LibreOffice 或 macOS 內建，
// 「自動」則依序挑第一個裝了、而且支援這個轉換的引擎。

/// 使用者可選的轉換引擎。
enum OTEngine: String, CaseIterable, Identifiable, Sendable {
    case auto, office, libreOffice, builtIn

    var id: String { rawValue }

    var title: String {
        switch self {
        case .auto: return "自動（最佳可用）"
        case .office: return "Microsoft Office"
        case .libreOffice: return "LibreOffice"
        case .builtIn: return "macOS 內建"
        }
    }

    var symbol: String {
        switch self {
        case .auto: return "wand.and.stars"
        case .office: return "briefcase"
        case .libreOffice: return "shippingbox"
        case .builtIn: return "apple.logo"
        }
    }
}

/// Microsoft Office 的三個 App。
enum OTOfficeApp: String, CaseIterable, Sendable {
    case word, excel, powerpoint

    var bundleID: String {
        switch self {
        case .word: return "com.microsoft.Word"
        case .excel: return "com.microsoft.Excel"
        case .powerpoint: return "com.microsoft.Powerpoint"
        }
    }

    var appName: String {
        switch self {
        case .word: return "Microsoft Word"
        case .excel: return "Microsoft Excel"
        case .powerpoint: return "Microsoft PowerPoint"
        }
    }

    var shortName: String {
        switch self {
        case .word: return "Word"
        case .excel: return "Excel"
        case .powerpoint: return "PowerPoint"
        }
    }

    /// 測試用：設定 QUICKSEEMD_OFFICE_DISABLED=1 時當作沒裝 Office（避免在命令列測試時去控制 Office）。
    static var simulatedMissing: Bool {
        ProcessInfo.processInfo.environment["QUICKSEEMD_OFFICE_DISABLED"] == "1"
    }

    var applicationURL: URL? {
        if Self.simulatedMissing { return nil }
        return NSWorkspace.shared.urlForApplication(withBundleIdentifier: bundleID)
    }

    var isInstalled: Bool { applicationURL != nil }

    var isRunning: Bool { !NSRunningApplication.runningApplications(withBundleIdentifier: bundleID).isEmpty }
}

/// 某個轉換在三種引擎上是否可行（不考慮是否安裝）。
struct OTSupport: Equatable, Sendable {
    var office: Bool
    var libreOffice: Bool
    var builtIn: Bool
    /// 內建引擎不支援時的說明（完整句子，例如「輸出 ODP 需要安裝 LibreOffice」）
    var builtInNote: String? = nil
    /// 內建引擎就能無損完成（例如 XLSX → CSV）：「自動」優先用內建，不必啟動 Office
    var preferBuiltIn = false

    static let none = OTSupport(office: false, libreOffice: false, builtIn: false)

    func supports(_ engine: OTEngine) -> Bool {
        switch engine {
        case .auto: return office || libreOffice || builtIn
        case .office: return office
        case .libreOffice: return libreOffice
        case .builtIn: return builtIn
        }
    }

    /// 兩組條件都要成立（多個檔案時取交集）。
    func intersect(_ other: OTSupport) -> OTSupport {
        OTSupport(office: office && other.office, libreOffice: libreOffice && other.libreOffice,
                  builtIn: builtIn && other.builtIn, builtInNote: builtInNote ?? other.builtInNote,
                  preferBuiltIn: preferBuiltIn && other.preferBuiltIn)
    }
}

/// 引擎目前狀態（UI 顯示用）。
struct OTEngineState: Identifiable, Sendable {
    let engine: OTEngine
    let available: Bool
    let reason: String?
    let detail: String
    var id: String { engine.rawValue }
}

enum OTEngines {
    static var libreOfficeAvailable: Bool { CVExternalTools.isAvailable(.libreOffice) }

    static func isInstalled(_ engine: OTEngine, app: OTOfficeApp) -> Bool {
        switch engine {
        case .auto, .builtIn: return true
        case .office: return app.isInstalled
        case .libreOffice: return libreOfficeAvailable
        }
    }

    /// 依使用者選擇與支援度，排出要嘗試的引擎順序（自動模式會依序後援）。
    static func candidates(_ choice: OTEngine, app: OTOfficeApp, support: OTSupport) -> [OTEngine] {
        switch choice {
        case .auto:
            var list: [OTEngine] = []
            if support.office && app.isInstalled { list.append(.office) }
            if support.libreOffice && libreOfficeAvailable { list.append(.libreOffice) }
            if support.builtIn { list.insert(.builtIn, at: support.preferBuiltIn ? 0 : list.count) }
            return list
        default:
            return support.supports(choice) && isInstalled(choice, app: app) ? [choice] : []
        }
    }

    /// 選項面板用：每個引擎能不能選、為什麼。
    static func states(app: OTOfficeApp, support: OTSupport) -> [OTEngineState] {
        let auto = candidates(.auto, app: app, support: support)
        var result: [OTEngineState] = []
        result.append(OTEngineState(
            engine: .auto, available: !auto.isEmpty,
            reason: auto.isEmpty ? "沒有可用的引擎" : nil,
            detail: auto.first.map { "目前會使用：\(displayName($0, app: app))" } ?? (support.builtInNote ?? "這個轉換需要 Office 或 LibreOffice")))
        let officeInstalled = app.isInstalled
        result.append(OTEngineState(
            engine: .office, available: officeInstalled && support.office,
            reason: !officeInstalled ? "未安裝" : (!support.office ? "不支援" : nil),
            detail: !officeInstalled ? "這台 Mac 沒有安裝 \(app.appName)" :
                (!support.office ? "\(app.shortName) 不適用於這個轉換" : "由 \(app.shortName) 本身轉換，版面最準確")))
        let libre = libreOfficeAvailable
        result.append(OTEngineState(
            engine: .libreOffice, available: libre && support.libreOffice,
            reason: !libre ? "未安裝" : (!support.libreOffice ? "不支援" : nil),
            detail: !libre ? "免費開源的 Office 套件，安裝後即可選用" :
                (!support.libreOffice ? "LibreOffice 無法處理這個轉換" : "免費開源的 Office 相容套件")))
        result.append(OTEngineState(
            engine: .builtIn, available: support.builtIn,
            reason: support.builtIn ? nil : "不支援",
            detail: support.builtIn ? "不需安裝任何軟體，使用系統的預覽引擎" : (support.builtInNote ?? "這個轉換需要 Office 或 LibreOffice")))
        return result
    }

    static func displayName(_ engine: OTEngine, app: OTOfficeApp) -> String {
        switch engine {
        case .office: return app.appName
        default: return engine.title
        }
    }

    /// 依引擎順序執行；自動模式下前一個失敗就換下一個（取消除外）。全部失敗時丟出第一個錯誤。
    static func run<T: Sendable>(_ choice: OTEngine, app: OTOfficeApp, support: OTSupport, context: ToolContext,
                                 scope: OTOfficeScope? = nil, _ body: (OTEngine) async throws -> T) async throws -> T {
        let list = candidates(choice, app: app, support: support)
        guard !list.isEmpty else {
            if choice == .auto || support.supports(choice) {
                throw ConversionError.other(choice == .auto ? (support.builtInNote ?? "這個轉換需要 Microsoft Office 或 LibreOffice") + "。"
                                                             : "\(displayName(choice, app: app)) 沒有安裝。")
            }
            throw ConversionError.other("\(displayName(choice, app: app)) 不支援這個轉換，請改選其他引擎。")
        }
        var firstError: Error?
        var failed: String?
        for engine in list {
            try context.checkCancellation()
            do {
                let result = try await body(engine)
                scope?.record(engine: displayName(engine, app: app), fallbackFrom: failed)
                return result
            } catch {
                if case ConversionError.cancelled = error { throw error }
                if error is CancellationError || Task.isCancelled { throw ConversionError.cancelled }
                if firstError == nil { firstError = error; failed = displayName(engine, app: app) }
                if engine != list.last {
                    context.progress(0.05, "\(displayName(engine, app: app)) 失敗，改用\(displayName(list[list.index(after: list.firstIndex(of: engine)!)], app: app))…")
                }
            }
        }
        throw firstError ?? ConversionError.other("轉換失敗。")
    }
}

// MARK: - 檔案類型

/// Office 類檔案的家族與副檔名判斷。
enum OTKind {
    static let wordExtensions: Set<String> = ["doc", "docx", "docm", "dot", "dotx", "dotm", "rtf", "odt"]
    static let excelExtensions: Set<String> = ["xls", "xlsx", "xlsm", "xlsb", "xltx", "ods", "csv", "tsv"]
    static let powerpointExtensions: Set<String> = ["ppt", "pptx", "pptm", "pps", "ppsx", "potx", "odp"]

    static func ext(_ url: URL) -> String { url.pathExtension.lowercased() }

    static func app(for url: URL) -> OTOfficeApp? {
        let e = ext(url)
        if wordExtensions.contains(e) { return .word }
        if excelExtensions.contains(e) { return .excel }
        if powerpointExtensions.contains(e) { return .powerpoint }
        return nil
    }

    static func types(_ ids: [String]) -> [UTType] { ids.compactMap { UTType($0) } }

    static let wordTypes = types([
        "com.microsoft.word.doc", "org.openxmlformats.wordprocessingml.document",
        "org.openxmlformats.wordprocessingml.document.macroenabled", "org.openxmlformats.wordprocessingml.template",
        "com.microsoft.word.dot", "public.rtf", "org.oasis-open.opendocument.text",
    ])
    static let excelTypes = types([
        "com.microsoft.excel.xls", "org.openxmlformats.spreadsheetml.sheet", "org.openxmlformats.spreadsheetml.sheet.macroenabled",
        "com.microsoft.excel.sheet.binary.macroenabled", "org.oasis-open.opendocument.spreadsheet",
        "public.comma-separated-values-text", "public.tab-separated-values-text",
    ])
    static let powerpointTypes = types([
        "com.microsoft.powerpoint.ppt", "org.openxmlformats.presentationml.presentation",
        "org.openxmlformats.presentationml.presentation.macroenabled", "com.microsoft.powerpoint.pps",
        "org.openxmlformats.presentationml.slideshow", "org.oasis-open.opendocument.presentation",
    ])

    // 右鍵選單用的 UTType 識別碼
    static let wordTypeIDs = ["com.microsoft.word.doc", "org.openxmlformats.wordprocessingml.document",
                              "org.openxmlformats.wordprocessingml.document.macroenabled", "public.rtf",
                              "org.oasis-open.opendocument.text"]
    static let excelTypeIDs = ["com.microsoft.excel.xls", "org.openxmlformats.spreadsheetml.sheet",
                               "org.openxmlformats.spreadsheetml.sheet.macroenabled", "org.oasis-open.opendocument.spreadsheet"]
    static let powerpointTypeIDs = ["com.microsoft.powerpoint.ppt", "org.openxmlformats.presentationml.presentation",
                                    "org.openxmlformats.presentationml.presentation.macroenabled",
                                    "org.openxmlformats.presentationml.slideshow", "org.oasis-open.opendocument.presentation"]
}

// MARK: - 共用小工具

enum OTWork {
    /// 同步、吃 CPU 的工作放到背景執行緒。
    static func background<T: Sendable>(_ body: @escaping @Sendable () throws -> T) async throws -> T {
        try await Task.detached(priority: .userInitiated, operation: body).value
    }

    /// 在暫存資料夾裡用指定檔名放一個檔案（避免交付時出現奇怪的中間檔名）。
    static func named(_ file: URL, _ name: String) throws -> URL {
        let dir = try CVFileNaming.makeScratchDirectory()
        let dest = dir.appendingPathComponent(name)
        try FileManager.default.moveItem(at: file, to: dest)
        return dest
    }

    /// 移除隔離屬性（從網路下載的檔案），讓 Office 開啟副本時不會跳出警告。
    static func stripQuarantine(_ url: URL) {
        _ = url.withUnsafeFileSystemRepresentation { path in
            removexattr(path, "com.apple.quarantine", 0)
        }
    }

    /// 把 zip 封裝（OOXML）的資料夾壓成檔案。
    static func zipDirectory(_ root: URL, to out: URL, first: [String] = ["[Content_Types].xml"]) async throws {
        let fm = FileManager.default
        let items = ((try? fm.contentsOfDirectory(atPath: root.path)) ?? []).filter { !$0.hasPrefix(".") }
        let ordered = first.filter { items.contains($0) } + items.filter { !first.contains($0) }.sorted()
        let result = try await OTProcess.run("/usr/bin/zip", ["-X", "-r", "-q", out.path] + ordered, timeout: 600, currentDirectory: root)
        guard result.succeeded else { throw ConversionError.toolFailed(tool: "zip", message: result.stderrText) }
    }

    /// 丟掉中間產物所在的暫存資料夾（只處理 QUICK_SEEMD 自己建立的 QuickSeemdConvert-… 資料夾）。
    static func discard(_ file: URL?) {
        guard let file else { return }
        var dir = file.deletingLastPathComponent()
        // 有些結果放在暫存資料夾裡的子資料夾
        for _ in 0..<3 where !dir.lastPathComponent.hasPrefix("QuickSeemdConvert-") { dir = dir.deletingLastPathComponent() }
        guard dir.lastPathComponent.hasPrefix("QuickSeemdConvert-"),
              dir.path.hasPrefix(FileManager.default.temporaryDirectory.resolvingSymlinksInPath().path)
                || dir.path.hasPrefix(FileManager.default.temporaryDirectory.path) else { return }
        try? FileManager.default.removeItem(at: dir)
    }

    static func writeText(_ text: String, to url: URL) throws {
        try FileManager.default.createDirectory(at: url.deletingLastPathComponent(), withIntermediateDirectories: true)
        try Data(text.utf8).write(to: url)
    }

    /// 合併多個 PDF（依序；以物件層級複製頁面，文字層完整保留）。
    static func mergePDFs(_ files: [URL], to out: URL) throws {
        guard let first = files.first else { throw ConversionError.emptyContent("沒有產生任何頁面。") }
        if files.count == 1 {
            try FileManager.default.copyItem(at: first, to: out)
            return
        }
        let docs = files.compactMap { PDFDocument(url: $0) }
        let entries = docs.flatMap { doc in (0..<doc.pageCount).map { PTPageEntry(doc: doc, index: $0) } }
        guard !entries.isEmpty else { throw ConversionError.emptyContent("沒有產生任何頁面。") }
        try PTAssemble.write(entries, outline: [], info: [:], to: out)
    }
}

/// 允許 ToolJob 把一組選項以 Sendable 方式帶進背景工作。
struct OTSendable<T>: @unchecked Sendable {
    let value: T
    init(_ value: T) { self.value = value }
}
