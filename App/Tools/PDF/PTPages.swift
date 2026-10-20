import PDFKit
import SwiftUI
import UniformTypeIdentifiers

// 合併、拆分、刪除頁面。

extension PDFToolCatalog {
    static let merge = ToolDefinition(
        id: "pdf.merge",
        category: .pdf,
        title: "合併 PDF",
        subtitle: "依照你排好的順序，把多個 PDF 合併成一份。",
        symbol: "rectangle.stack.badge.plus",
        badge: "PDF",
        inputTypes: [.pdf],
        inputLabel: "PDF 檔案",
        minimumFiles: 2,
        reorderable: true,
        actionTitle: "合併 PDF",
        keywords: ["merge", "combine", "join", "append", "合併", "結合", "串接"],
        quickActions: [
            "pdf.merge-pdfs": { PTMergeSession.job(bookmarks: true, duplex: false, name: "") },
        ],
        makeSession: { PTMergeSession() }
    )

    static let split = ToolDefinition(
        id: "pdf.split",
        category: .pdf,
        title: "拆分 PDF",
        subtitle: "依範圍、固定頁數或每一頁拆成多個 PDF，也能只擷取需要的頁面。",
        symbol: "scissors",
        badge: "PDF",
        inputTypes: [.pdf],
        inputLabel: "PDF 檔案",
        actionTitle: "拆分 PDF",
        keywords: ["split", "extract", "separate", "burst", "拆分", "分割", "擷取", "抽頁"],
        quickActions: [
            "pdf.split-pages": { PTSplitSession.job(mode: .everyPage, ranges: "", chunk: 1, extract: "", separate: false, folder: true) },
        ],
        makeSession: { PTSplitSession() }
    )

    static let deletePages = ToolDefinition(
        id: "pdf.delete-pages",
        category: .pdf,
        title: "刪除頁面",
        subtitle: "移除不需要的頁面，例如指定頁碼、奇數或偶數頁、空白頁。",
        symbol: "doc.badge.ellipsis",
        badge: "PDF",
        inputTypes: [.pdf],
        inputLabel: "PDF 檔案",
        actionTitle: "刪除頁面",
        keywords: ["delete", "remove pages", "blank pages", "刪頁", "移除頁面", "空白頁"],
        makeSession: { PTDeletePagesSession() }
    )
}

// MARK: - 合併

final class PTMergeSession: PTSession {
    @Published var bookmarks = true
    @Published var duplex = false
    @Published var fileName = ""
    @Published var firstName = ""
    @Published var totalPages = 0

    override func filesDidChange(_ files: [URL]) {
        super.filesDidChange(files)
        firstName = files.first.map { CVFileNaming.baseName(of: $0) + "-已合併" } ?? "合併"
        totalPages = files.reduce(0) { $0 + (PTPDF.pageCount(of: $1) ?? 0) }
    }

    override func optionsView() -> AnyView { AnyView(PTMergeOptions(session: self)) }

    override func makeJob() -> ToolJob { Self.job(bookmarks: bookmarks, duplex: duplex, name: fileName) }

    nonisolated static func job(bookmarks: Bool, duplex: Bool, name: String) -> ToolJob {
        ToolJob { files, context in
            guard files.count >= 2 else { throw ConversionError.other("合併需要至少 2 個 PDF 檔案。") }
            var entries: [PTPageEntry] = []
            var outline: [PTOutlineNode] = []
            for (fi, file) in files.enumerated() {
                try context.checkCancellation()
                context.progress(Double(fi) / Double(files.count) * 0.3, "讀取「\(file.lastPathComponent)」（\(fi + 1)/\(files.count)）…")
                let doc = try PTPDF.open(file)
                let start = entries.count
                entries += (0..<doc.pageCount).map { PTPageEntry(doc: doc, index: $0) }
                let nested = PTOutline.nodes(from: doc, map: { start + $0 })
                if bookmarks {
                    outline.append(PTOutline.entry(CVFileNaming.baseName(of: file), page: start, doc: doc, sourceIndex: 0, children: nested))
                } else {
                    outline += nested
                }
                // 雙面列印：頁數為奇數時補一張空白頁，讓下一份從正面開始
                if duplex && fi < files.count - 1 && doc.pageCount % 2 == 1, let last = doc.page(at: doc.pageCount - 1) {
                    entries.append(PTPageEntry(doc: doc, index: nil, blankSize: PTPDF.visualSize(last)))
                }
            }
            let trimmed = name.trimmingCharacters(in: .whitespacesAndNewlines)
            var outName = trimmed.isEmpty ? context.outputName(for: files[0], tag: "已合併", ext: "pdf") : CVFileNaming.sanitize(trimmed)
            if !outName.lowercased().hasSuffix(".pdf") { outName += ".pdf" }
            context.progress(0.3, "寫入合併後的 PDF（共 \(entries.count) 頁）…")
            let tmp = try context.makeWorkDirectory().appendingPathComponent("merged.pdf")
            try PTAssemble.write(entries, outline: outline, info: ["Title": String(outName.dropLast(4))], to: tmp, context: context)
            return [try context.deliver(tmp, as: outName, for: files[0])]
        }
    }
}

private struct PTMergeOptions: View {
    @ObservedObject var session: PTMergeSession

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            if session.totalPages > 0 {
                OptionNote(text: "合併後共 \(session.totalPages) 頁。在左側拖曳檔案卡片即可調整順序。", systemImage: "doc.on.doc")
            }
            OptionGroup(title: "書籤與版面", systemImage: "bookmark") {
                VStack(alignment: .leading, spacing: 12) {
                    OptionToggle(title: "每個檔案加一個書籤", subtitle: "以檔名當書籤，方便在閱讀器中跳到各份文件；原有書籤會放在底下。", isOn: $session.bookmarks)
                    OptionToggle(title: "雙面列印對齊", subtitle: "頁數為奇數的檔案後面補一張空白頁，讓每份都從正面開始。", isOn: $session.duplex)
                }
            }
            OptionGroup(title: "輸出檔名", systemImage: "pencil.line") {
                OptionTextField(title: "檔名（可留白）", text: $session.fileName, placeholder: session.firstName)
            }
        }
    }
}

// MARK: - 拆分

enum PTSplitMode: String, Sendable {
    case ranges, everyN, everyPage, extract
}

final class PTSplitSession: PTSession {
    @Published var mode: PTSplitMode = .ranges
    @Published var ranges = ""
    @Published var chunk = 2
    @Published var extract = ""
    @Published var separate = false
    @Published var folder = true

    override func filesDidChange(_ files: [URL]) {
        super.filesDidChange(files)
        // 預先填入「前半、後半」的範圍，方便直接修改
        guard ranges.isEmpty || ranges == autoRanges else { return }
        if let n = pageCount, n >= 2 {
            let half = (n + 1) / 2
            ranges = half == 1 ? "1, 2" : "1-\(half), \(half + 1)\(half + 1 == n ? "" : "-\(n)")"
        } else {
            ranges = ""
        }
        autoRanges = ranges
    }

    private var autoRanges = ""

    override func validate(_ files: [URL]) -> String? {
        if let reason = super.validate(files) { return reason }
        switch mode {
        case .ranges:
            if ranges.trimmingCharacters(in: .whitespaces).isEmpty { return "請輸入要拆分的範圍，例如 1-3, 4-6" }
            if let count = pageCount, PTPDF.rangeGroups(ranges, count: count) == nil { return "範圍不正確（共 \(count) 頁）" }
        case .extract:
            if extract.trimmingCharacters(in: .whitespaces).isEmpty { return "請輸入要擷取的頁面" }
            if let count = pageCount, PageRange.parse(extract, pageCount: count) == nil { return "頁碼範圍不正確（共 \(count) 頁）" }
        case .everyN:
            if chunk < 1 { return "每個檔案至少 1 頁" }
        case .everyPage:
            break
        }
        return nil
    }

    override func optionsView() -> AnyView { AnyView(PTSplitOptions(session: self)) }

    override func makeJob() -> ToolJob {
        Self.job(mode: mode, ranges: ranges, chunk: chunk, extract: extract, separate: separate, folder: folder)
    }

    nonisolated static func job(mode: PTSplitMode, ranges: String, chunk: Int, extract: String, separate: Bool, folder: Bool) -> ToolJob {
        .perFile { file, context in
            let doc = try PTPDF.open(file)
            let n = doc.pageCount
            var groups: [[Int]]
            var single = false
            switch mode {
            case .ranges:
                guard let g = PTPDF.rangeGroups(ranges, count: n) else {
                    throw ConversionError.other("拆分範圍「\(ranges)」不正確：「\(file.lastPathComponent)」共 \(n) 頁。")
                }
                groups = g
            case .everyN:
                let size = max(1, chunk)
                groups = stride(from: 1, through: n, by: size).map { Array($0...min($0 + size - 1, n)) }
            case .everyPage:
                groups = (1...n).map { [$0] }
            case .extract:
                let pages = try PTPDF.pages(extract, count: n, file: file)
                groups = separate ? pages.map { [$0] } : [pages]
                single = !separate
            }
            if groups.count == 1 && groups[0].count == n && !single {
                throw ConversionError.other("「\(file.lastPathComponent)」只有 \(n) 頁，拆分後還是同一份檔案。")
            }
            let base = CVFileNaming.baseName(of: file)
            let work = try context.makeWorkDirectory()
            var parts: [URL] = []
            for (gi, group) in groups.enumerated() {
                try context.checkCancellation()
                context.progress(Double(gi) / Double(groups.count) * 0.9, "產生第 \(gi + 1) / \(groups.count) 個檔案…")
                var indexMap: [Int: Int] = [:]
                for (i, p) in group.enumerated() where indexMap[p - 1] == nil { indexMap[p - 1] = i }
                let entries = group.map { PTPageEntry(doc: doc, index: $0 - 1) }
                let outline = PTOutline.nodes(from: doc, map: { indexMap[$0] })
                let label = single ? "已擷取" : Self.label(group, total: n)
                let url = work.appendingPathComponent(CVFileNaming.fileName(CVFileNaming.sanitize("\(base)-\(label)"), "pdf"))
                try PTAssemble.write(entries, outline: outline, info: PTPDF.info(of: doc), to: url)
                parts.append(url)
            }
            if parts.count == 1 {
                return [try context.deliver(parts[0], as: parts[0].lastPathComponent, for: file)]
            }
            if folder {
                let dir = try PTPDF.makeFolder(in: work, named: "\(base)-已拆分")
                for p in parts { try FileManager.default.moveItem(at: p, to: dir.appendingPathComponent(p.lastPathComponent)) }
                context.progress(1, "已拆成 \(parts.count) 個檔案")
                return [try context.deliver(dir, as: dir.lastPathComponent, for: file)]
            }
            return try parts.map { try context.deliver($0, as: $0.lastPathComponent, for: file) }
        }
    }

    /// 檔名用的頁碼說明：「第03頁」「第01-05頁」「第02頁等3頁」
    nonisolated static func label(_ group: [Int], total: Int) -> String {
        guard let first = group.first, let last = group.last else { return "空白" }
        if group.count == 1 { return "第\(PTPDF.padded(first, total: total))頁" }
        if group == Array(first...max(first, last)) {
            return "第\(PTPDF.padded(first, total: total))-\(PTPDF.padded(last, total: total))頁"
        }
        return "第\(PTPDF.padded(first, total: total))頁等\(group.count)頁"
    }
}

private struct PTSplitOptions: View {
    @ObservedObject var session: PTSplitSession

    private var preview: String? {
        guard let n = session.pageCount else { return nil }
        switch session.mode {
        case .ranges:
            guard let g = PTPDF.rangeGroups(session.ranges, count: n) else { return nil }
            return "會產生 \(g.count) 個 PDF"
        case .everyN:
            let size = max(1, session.chunk)
            return "共 \(n) 頁，會產生 \((n + size - 1) / size) 個 PDF"
        case .everyPage:
            return "共 \(n) 頁，會產生 \(n) 個 PDF"
        case .extract:
            guard let p = PageRange.parse(session.extract, pageCount: n), !session.extract.isEmpty else { return nil }
            return session.separate ? "會產生 \(p.count) 個 PDF" : "擷取 \(p.count) 頁成一個 PDF"
        }
    }

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            OptionGroup(title: "拆分方式", systemImage: "scissors") {
                ChoiceCards(selection: $session.mode, choices: [
                    OptionChoice(value: .ranges, title: "依範圍拆分", subtitle: "每個範圍存成一個檔案", systemImage: "text.line.first.and.arrowtriangle.forward"),
                    OptionChoice(value: .everyN, title: "每 N 頁一個檔案", subtitle: "固定頁數平均切開", systemImage: "square.split.2x1"),
                    OptionChoice(value: .everyPage, title: "每一頁一個檔案", subtitle: "整份拆成單頁 PDF", systemImage: "square.grid.3x3"),
                    OptionChoice(value: .extract, title: "擷取指定頁面", subtitle: "只取出需要的頁面", systemImage: "doc.on.clipboard"),
                ])
            }
            OptionGroup(title: "設定", systemImage: "slider.horizontal.3") {
                VStack(alignment: .leading, spacing: 12) {
                    switch session.mode {
                    case .ranges:
                        VStack(alignment: .leading, spacing: 4) {
                            Text("範圍").font(.callout)
                            TextField("例如 1-3, 4-6, 7-", text: $session.ranges).textFieldStyle(.roundedBorder)
                            Text("以逗號分隔，每段各存成一個 PDF").font(.caption).foregroundStyle(.secondary)
                        }
                    case .everyN:
                        PTNumberField(title: "每個檔案的頁數", value: $session.chunk, range: 1...max(1, session.pageCount ?? 9999))
                    case .everyPage:
                        EmptyView()
                    case .extract:
                        PageRangeField(title: "要擷取的頁面", text: $session.extract, pageCount: session.pageCount)
                        OptionToggle(title: "每頁各存成一個檔案", subtitle: "關閉時合併成一個 PDF", isOn: $session.separate)
                    }
                    if session.mode != .extract || session.separate {
                        OptionToggle(title: "放進新資料夾", subtitle: "以原檔名建立資料夾收納拆分結果", isOn: $session.folder)
                    }
                    if let preview { OptionNote(text: preview, systemImage: "doc.on.doc") }
                }
            }
        }
    }
}

// MARK: - 刪除頁面

enum PTDeleteMode: String, Sendable {
    case custom, even, odd, blank
}

final class PTDeletePagesSession: PTSession {
    @Published var mode: PTDeleteMode = .custom
    @Published var pages = ""

    override func validate(_ files: [URL]) -> String? {
        if let reason = super.validate(files) { return reason }
        guard mode == .custom else { return nil }
        if pages.trimmingCharacters(in: .whitespaces).isEmpty { return "請輸入要刪除的頁碼" }
        if let count = pageCount {
            guard let list = PageRange.parse(pages, pageCount: count) else { return "頁碼範圍不正確（共 \(count) 頁）" }
            if Set(list).count >= count { return "不能刪除全部的頁面" }
        }
        return nil
    }

    override func optionsView() -> AnyView { AnyView(PTDeleteOptions(session: self)) }

    override func makeJob() -> ToolJob { Self.job(mode: mode, pages: pages) }

    nonisolated static func job(mode: PTDeleteMode, pages: String) -> ToolJob {
        ToolJob { files, context in
            var outputs: [URL] = []
            for (fi, file) in files.enumerated() {
                try context.checkCancellation()
                let sub = context.segment(fi, of: files.count)
                sub.progress(0, "分析「\(file.lastPathComponent)」…")
                let doc = try PTPDF.open(file)
                let n = doc.pageCount
                var remove: Set<Int>
                switch mode {
                case .custom:
                    if pages.trimmingCharacters(in: .whitespaces).isEmpty { throw ConversionError.other("請輸入要刪除的頁碼。") }
                    remove = Set(try PTPDF.pages(pages, count: n, file: file))
                case .even:
                    remove = Set(stride(from: 2, through: n, by: 2))
                case .odd:
                    remove = Set(stride(from: 1, through: n, by: 2))
                case .blank:
                    remove = []
                    for i in 0..<n {
                        try sub.checkCancellation()
                        sub.progress(Double(i) / Double(n) * 0.8, "檢查第 \(i + 1) / \(n) 頁是否空白…")
                        if let page = doc.page(at: i), PTBlankDetector.isBlank(page) { remove.insert(i + 1) }
                    }
                }
                if remove.isEmpty {
                    if files.count == 1 { throw ConversionError.other("「\(file.lastPathComponent)」沒有需要刪除的頁面（沒有偵測到空白頁）。") }
                    sub.progress(1, "「\(file.lastPathComponent)」沒有需要刪除的頁面，略過")
                    continue
                }
                if remove.count >= n {
                    throw ConversionError.other("「\(file.lastPathComponent)」共 \(n) 頁，不能把全部頁面都刪除。")
                }
                let kept = (0..<n).filter { !remove.contains($0 + 1) }
                var indexMap: [Int: Int] = [:]
                for (i, p) in kept.enumerated() { indexMap[p] = i }
                let tmp = try sub.makeWorkDirectory().appendingPathComponent("deleted.pdf")
                try PTAssemble.write(kept.map { PTPageEntry(doc: doc, index: $0) }, outline: PTOutline.nodes(from: doc, map: { indexMap[$0] }),
                                     info: PTPDF.info(of: doc), to: tmp, context: sub, span: 0.8...0.98)
                outputs.append(try sub.deliver(tmp, as: sub.outputName(for: file, tag: "已刪頁", ext: "pdf"), for: file))
                sub.progress(1, "已刪除 \(remove.count) 頁，剩下 \(n - remove.count) 頁")
            }
            return outputs
        }
    }
}

/// 空白頁偵測：沒有文字、沒有註解，且縮圖幾乎全白。
enum PTBlankDetector {
    static func isBlank(_ page: PDFPage) -> Bool {
        if let text = page.string, !text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty { return false }
        if page.annotations.contains(where: { $0.type != "Link" }) { return false }
        guard let ref = page.pageRef, let image = CVPDFConverter.render(page: ref, dpi: 40) else { return false }
        let w = image.width, h = image.height
        guard let ctx = CGContext(data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: w,
                                  space: CGColorSpaceCreateDeviceGray(), bitmapInfo: CGImageAlphaInfo.none.rawValue),
              let data = ctx.data else { return false }
        ctx.draw(image, in: CGRect(x: 0, y: 0, width: w, height: h))
        let bytes = data.bindMemory(to: UInt8.self, capacity: w * h)
        var dark = 0
        for i in 0..<(w * h) where bytes[i] < 235 { dark += 1 }
        return Double(dark) / Double(max(w * h, 1)) < 0.002
    }
}

private struct PTDeleteOptions: View {
    @ObservedObject var session: PTDeletePagesSession

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            OptionGroup(title: "要刪除哪些頁面", systemImage: "trash") {
                ChoiceCards(selection: $session.mode, choices: [
                    OptionChoice(value: .custom, title: "指定頁碼", subtitle: "輸入要移除的頁碼或範圍", systemImage: "number"),
                    OptionChoice(value: .blank, title: "所有空白頁", subtitle: "自動找出沒有內容的頁面", systemImage: "doc"),
                    OptionChoice(value: .even, title: "偶數頁", subtitle: "移除第 2、4、6… 頁", systemImage: "2.square"),
                    OptionChoice(value: .odd, title: "奇數頁", subtitle: "移除第 1、3、5… 頁", systemImage: "1.square"),
                ])
            }
            if session.mode == .custom {
                OptionGroup(title: "頁碼", systemImage: "doc.on.doc") {
                    VStack(alignment: .leading, spacing: 4) {
                        Text("要刪除的頁面").font(.callout)
                        TextField("例如 2, 5-7", text: $session.pages).textFieldStyle(.roundedBorder)
                        if let count = session.pageCount {
                            if !session.pages.isEmpty, PageRange.parse(session.pages, pageCount: count) == nil {
                                Label("範圍不正確（共 \(count) 頁）", systemImage: "exclamationmark.triangle.fill").font(.caption).foregroundStyle(.orange)
                            } else {
                                Text("共 \(count) 頁").font(.caption).foregroundStyle(.secondary)
                            }
                        }
                    }
                }
            }
            OptionNote(text: "原始檔案不會被修改，結果會另存成「檔名-已刪頁.pdf」。")
        }
    }
}
