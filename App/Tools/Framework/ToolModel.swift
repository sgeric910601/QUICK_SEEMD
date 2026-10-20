import AppKit
import SwiftUI
import UniformTypeIdentifiers

// MARK: - 分類

enum ToolCategory: String, CaseIterable, Identifiable, Sendable {
    case pdf, office, image, media, other

    var id: String { rawValue }

    var title: String {
        switch self {
        case .pdf: return "PDF 工具"
        case .office: return "Office 文件"
        case .image: return "圖片工具"
        case .media: return "影音工具"
        case .other: return "其他工具"
        }
    }

    var shortTitle: String {
        switch self {
        case .pdf: return "PDF"
        case .office: return "Office"
        case .image: return "圖片"
        case .media: return "影音"
        case .other: return "其他"
        }
    }

    var symbol: String {
        switch self {
        case .pdf: return "doc.richtext"
        case .office: return "doc.text"
        case .image: return "photo"
        case .media: return "film"
        case .other: return "square.grid.2x2"
        }
    }

    var tint: Color { ToolPalette.category(self) }
}

/// 各分類的工具配色。
enum ToolPalette {
    static let pdf = Color(red: 0.89, green: 0.20, blue: 0.18)       // PDF 紅
    static let word = Color(red: 0.11, green: 0.36, blue: 0.74)      // Word 藍
    static let excel = Color(red: 0.11, green: 0.50, blue: 0.29)     // Excel 綠
    static let powerpoint = Color(red: 0.83, green: 0.32, blue: 0.16) // PowerPoint 橘
    static let image = Color(red: 0.95, green: 0.62, blue: 0.10)     // 圖片 琥珀
    static let video = Color(red: 0.49, green: 0.33, blue: 0.95)     // 影片 紫
    static let audio = Color(red: 0.86, green: 0.24, blue: 0.55)     // 音訊 洋紅
    static let teal = Color(red: 0.10, green: 0.60, blue: 0.62)      // 其他 青
    static let slate = Color(red: 0.36, green: 0.42, blue: 0.52)     // 中性 藍灰

    static func category(_ c: ToolCategory) -> Color {
        switch c {
        case .pdf: return pdf
        case .office: return word
        case .image: return image
        case .media: return video
        case .other: return teal
        }
    }
}

// MARK: - 工具定義

/// 一個工具的「身分證」：工具大廳的卡片、工具頁的標頭、右鍵選單的動作都從這裡來。
struct ToolDefinition: Identifiable {
    let id: String
    let category: ToolCategory
    let title: String
    let subtitle: String
    let symbol: String
    let tint: Color
    /// 圖示右下角的小標籤，例如 "W"、"PDF"、"JPG"
    let badge: String?
    /// 接受的輸入類型（以 UTType 相容性判斷；資料夾請放 .folder）
    let inputTypes: [UTType]
    /// 顯示在「選擇 ___」按鈕上的文字，例如「PDF 檔案」
    let inputLabel: String
    let allowsMultiple: Bool
    let minimumFiles: Int
    /// 檔案順序有意義（例如合併）時，允許拖曳排序
    let reorderable: Bool
    /// 執行按鈕文字，例如「合併 PDF」
    let actionTitle: String
    /// 搜尋用關鍵字（英文、別名）
    let keywords: [String]
    /// 右鍵一鍵轉換：QuickActionManifest 裡的動作 id → 依預設選項產生工作
    let quickActions: [String: @MainActor () -> ToolJob]
    /// 完全自訂的頁面（例如萬用格式轉換），不使用標準工具頁
    let customPage: (@MainActor () -> AnyView)?
    let makeSession: @MainActor () -> ToolSession

    init(
        id: String,
        category: ToolCategory,
        title: String,
        subtitle: String,
        symbol: String,
        tint: Color? = nil,
        badge: String? = nil,
        inputTypes: [UTType],
        inputLabel: String,
        allowsMultiple: Bool = true,
        minimumFiles: Int = 1,
        reorderable: Bool = false,
        actionTitle: String,
        keywords: [String] = [],
        quickActions: [String: @MainActor () -> ToolJob] = [:],
        customPage: (@MainActor () -> AnyView)? = nil,
        makeSession: @escaping @MainActor () -> ToolSession
    ) {
        self.id = id
        self.category = category
        self.title = title
        self.subtitle = subtitle
        self.symbol = symbol
        self.tint = tint ?? category.tint
        self.badge = badge
        self.inputTypes = inputTypes
        self.inputLabel = inputLabel
        self.allowsMultiple = allowsMultiple
        self.minimumFiles = minimumFiles
        self.reorderable = reorderable
        self.actionTitle = actionTitle
        self.keywords = keywords
        self.quickActions = quickActions
        self.customPage = customPage
        self.makeSession = makeSession
    }

    func accepts(_ url: URL) -> Bool {
        if inputTypes.isEmpty { return true }
        let type = ToolFiles.contentType(of: url)
        return inputTypes.contains { type.conforms(to: $0) }
    }

    func matches(search: String) -> Bool {
        let q = search.trimmingCharacters(in: .whitespaces).lowercased()
        if q.isEmpty { return true }
        return ([title, subtitle, inputLabel] + keywords).contains { $0.lowercased().contains(q) }
    }
}

// MARK: - 工具工作階段

/// 一次使用工具時的狀態（主要是選項）。每個工具繼承這個類別：
/// - 用 `@Published` 存選項，`optionsView()` 回傳右側選項面板
/// - `makeJob()` 在主執行緒把目前選項「拍照」成可在背景執行的 `ToolJob`
@MainActor
class ToolSession: ObservableObject {
    init() {}

    /// 右側選項面板（預設沒有選項）。
    func optionsView() -> AnyView { AnyView(EmptyView()) }

    /// 檔案清單改變時通知（例如讀 PDF 頁數給選項用）。
    func filesDidChange(_ files: [URL]) {}

    /// 不能執行時回傳原因（會顯示在執行按鈕上方）。
    func validate(_ files: [URL]) -> String? { nil }

    /// 依目前選項產生背景工作。
    func makeJob() -> ToolJob { ToolJob { _, _ in [] } }

    /// 執行按鈕文字；nil 代表使用 ToolDefinition.actionTitle。
    var actionTitleOverride: String? { nil }

    /// 取代預設的檔案縮圖區（例如 PDF 頁面整理）；nil 代表用預設。
    func customFilesView(files: Binding<[URL]>) -> AnyView? { nil }
}

// MARK: - 背景工作

/// 可在背景執行的工作。選項已在建立時捕捉，因此可跨執行緒安全使用。
struct ToolJob: Sendable {
    let run: @Sendable (_ files: [URL], _ context: ToolContext) async throws -> [URL]

    init(_ run: @escaping @Sendable (_ files: [URL], _ context: ToolContext) async throws -> [URL]) {
        self.run = run
    }

    /// 每個檔案各自處理的便利寫法；進度會自動依檔案分段。
    static func perFile(_ body: @escaping @Sendable (_ file: URL, _ context: ToolContext) async throws -> [URL]) -> ToolJob {
        ToolJob { files, context in
            var outputs: [URL] = []
            for (i, file) in files.enumerated() {
                try context.checkCancellation()
                let sub = context.segment(i, of: files.count)
                sub.progress(0, "處理中：\(file.lastPathComponent)")
                outputs += try await body(file, sub)
                sub.progress(1)
            }
            return outputs
        }
    }
}

/// 工作執行時的環境：輸出位置、進度回報、暫存資料夾、取消檢查。
final class ToolContext: @unchecked Sendable {
    enum Output: Sendable, Equatable {
        case sameAsSource
        case folder(URL)
    }

    let output: Output
    private let report: @Sendable (Double, String?) -> Void
    private let lower: Double
    private let upper: Double

    init(output: Output, progress: @escaping @Sendable (Double, String?) -> Void) {
        self.output = output
        self.report = progress
        self.lower = 0
        self.upper = 1
    }

    private init(output: Output, report: @escaping @Sendable (Double, String?) -> Void, lower: Double, upper: Double) {
        self.output = output
        self.report = report
        self.lower = lower
        self.upper = upper
    }

    /// 回報進度（0…1，指的是「這一段」的進度）與目前狀態文字。
    func progress(_ fraction: Double, _ message: String? = nil) {
        let f = min(max(fraction, 0), 1)
        report(lower + (upper - lower) * f, message)
    }

    /// 切出第 index 段（共 count 段）的子進度。
    func segment(_ index: Int, of count: Int) -> ToolContext {
        let width = (upper - lower) / Double(max(count, 1))
        return ToolContext(output: output, report: report, lower: lower + width * Double(index), upper: lower + width * Double(index + 1))
    }

    func checkCancellation() throws {
        if Task.isCancelled { throw ConversionError.cancelled }
    }

    /// 輸出資料夾：與原檔相同，或使用者指定的資料夾。
    func outputDirectory(for input: URL) -> URL {
        switch output {
        case .sameAsSource: return input.deletingLastPathComponent()
        case .folder(let url): return url
        }
    }

    /// 暫存資料夾（工作結束後可丟棄）。
    func makeWorkDirectory() throws -> URL {
        try CVFileNaming.makeScratchDirectory()
    }

    /// 依原檔產生輸出檔名：report.pdf → report.docx；加上 tag 時 → report-已壓縮.pdf
    func outputName(for input: URL, tag: String? = nil, ext: String) -> String {
        let base = CVFileNaming.baseName(of: input)
        let name = tag.map { "\(base)-\($0)" } ?? base
        return CVFileNaming.fileName(CVFileNaming.sanitize(name), ext)
    }

    /// 把暫存結果放到輸出位置（同名時自動改成「名稱 (1).副檔名」，絕不覆蓋），回傳最終位置。
    func deliver(_ temp: URL, as fileName: String, for input: URL) throws -> URL {
        let directory = outputDirectory(for: input)
        try ConversionEngine.ensureWritable(directory)
        let staging = try CVFileNaming.makeScratchDirectory()
        let named = staging.appendingPathComponent(fileName)
        try FileManager.default.moveItem(at: temp, to: named)
        CVPDFPostProcess.apply(to: named)
        defer { try? FileManager.default.removeItem(at: staging) }
        return try CVFileNaming.commit(named, into: directory)
    }
}

// MARK: - 檔案小工具

enum ToolFiles {
    static func contentType(of url: URL) -> UTType {
        if let t = (try? url.resourceValues(forKeys: [.contentTypeKey]))?.contentType { return t }
        return UTType(filenameExtension: url.pathExtension) ?? .data
    }

    static func size(of url: URL) -> Int64 {
        Int64((try? url.resourceValues(forKeys: [.fileSizeKey]))?.fileSize ?? 0)
    }

    static func formattedSize(_ bytes: Int64) -> String {
        ByteCountFormatter.string(fromByteCount: bytes, countStyle: .file)
    }

    static func kind(of url: URL) -> String {
        if let d = contentType(of: url).localizedDescription { return d }
        return url.pathExtension.uppercased()
    }
}

// MARK: - 頁碼範圍

/// 解析「1-3, 5, 8-」這類頁碼範圍（頁碼從 1 開始）。
enum PageRange {
    static func parse(_ text: String, pageCount: Int) -> [Int]? {
        let trimmed = text.trimmingCharacters(in: .whitespaces)
        if trimmed.isEmpty { return Array(1...max(pageCount, 1)) }
        var pages: [Int] = []
        for part in trimmed.replacingOccurrences(of: "，", with: ",").split(separator: ",") {
            let p = part.trimmingCharacters(in: .whitespaces)
            if p.isEmpty { continue }
            if let dash = p.firstIndex(where: { $0 == "-" || $0 == "–" || $0 == "~" }) {
                let a = Int(p[..<dash].trimmingCharacters(in: .whitespaces)) ?? 1
                let bText = p[p.index(after: dash)...].trimmingCharacters(in: .whitespaces)
                let b = bText.isEmpty ? pageCount : (Int(bText) ?? -1)
                guard a >= 1, b >= a, b <= pageCount else { return nil }
                pages += Array(a...b)
            } else {
                guard let n = Int(p), n >= 1, n <= pageCount else { return nil }
                pages.append(n)
            }
        }
        return pages.isEmpty ? nil : pages
    }
}
