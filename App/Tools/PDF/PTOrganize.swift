import AppKit
import PDFKit
import SwiftUI
import UniformTypeIdentifiers

// 整理 PDF：以頁面縮圖拖曳排序、旋轉、刪除、插入空白頁，輸出新的 PDF。

extension PDFToolCatalog {
    static let organize = ToolDefinition(
        id: "pdf.organize",
        category: .pdf,
        title: "整理 PDF",
        subtitle: "拖曳頁面縮圖調整順序，也可以旋轉、刪除頁面或插入空白頁。",
        symbol: "square.grid.3x3.square",
        badge: "PDF",
        inputTypes: [.pdf],
        inputLabel: "PDF 檔案",
        allowsMultiple: false,
        actionTitle: "儲存整理後的 PDF",
        keywords: ["organize", "reorder", "sort pages", "move pages", "insert blank", "整理", "排序", "頁面順序", "重新排列"],
        makeSession: { PTOrganizeSession() }
    )
}

/// 整理後的一頁：來自原檔的第幾頁（nil＝空白頁）與額外旋轉角度。
struct PTOrgPage: Identifiable, Hashable, Sendable {
    let id: UUID
    var source: Int?
    var rotation: Int

    init(source: Int?, rotation: Int = 0) {
        self.id = UUID()
        self.source = source
        self.rotation = rotation
    }
}

/// 背景產生頁面縮圖（快取）。
final class PTThumbnailRenderer: @unchecked Sendable {
    private let document: CGPDFDocument?
    private let queue = DispatchQueue(label: "QuickSeemd.PDFTools.thumbnails", qos: .userInitiated)
    private let cache = NSCache<NSNumber, CGImage>()

    init(url: URL) {
        let doc = CGPDFDocument(url as CFURL)
        if let doc, doc.isEncrypted, !doc.isUnlocked { _ = doc.unlockWithPassword("") }
        document = doc
    }

    func thumbnail(_ index: Int, maxSide: CGFloat = 260) async -> CGImage? {
        if let cached = cache.object(forKey: NSNumber(value: index)) { return cached }
        return await withCheckedContinuation { (cont: CheckedContinuation<CGImage?, Never>) in
            queue.async { [self] in
                if let cached = cache.object(forKey: NSNumber(value: index)) { cont.resume(returning: cached); return }
                guard let doc = document, let page = doc.page(at: index + 1) else { cont.resume(returning: nil); return }
                let box = page.getBoxRect(.cropBox)
                let dpi = Double(maxSide / max(box.width, box.height, 1) * 72)
                let image = CVPDFConverter.render(page: page, dpi: dpi)
                if let image { cache.setObject(image, forKey: NSNumber(value: index)) }
                cont.resume(returning: image)
            }
        }
    }
}

final class PTOrganizeSession: PTSession {
    @Published var pages: [PTOrgPage] = []
    @Published var selection: Set<UUID> = []
    @Published var dragging: UUID?
    @Published private(set) var originalCount = 0
    /// 原檔各頁看起來的大小（含原本的旋轉），供縮圖與空白頁使用。
    private(set) var pageSizes: [CGSize] = []
    private(set) var renderer: PTThumbnailRenderer?
    private var file: URL?

    override func filesDidChange(_ files: [URL]) {
        super.filesDidChange(files)
        guard let first = files.first else {
            file = nil; pages = []; selection = []; renderer = nil; originalCount = 0; pageSizes = []
            return
        }
        guard first != file else { return }
        file = first
        selection = []
        guard let doc = PDFDocument(url: first), !doc.isLocked || doc.unlock(withPassword: "") else {
            pages = []; originalCount = 0; renderer = nil
            return
        }
        originalCount = doc.pageCount
        pageSizes = (0..<doc.pageCount).map { doc.page(at: $0).map(PTPDF.visualSize) ?? CGSize(width: 595, height: 842) }
        pages = (0..<doc.pageCount).map { PTOrgPage(source: $0) }
        renderer = PTThumbnailRenderer(url: first)
    }

    override func validate(_ files: [URL]) -> String? {
        if let reason = super.validate(files) { return reason }
        if !files.isEmpty && pages.isEmpty { return "至少要保留一頁" }
        return nil
    }

    override func optionsView() -> AnyView { AnyView(PTOrganizePanel(session: self)) }

    override func customFilesView(files: Binding<[URL]>) -> AnyView? {
        AnyView(PTOrganizeGrid(session: self))
    }

    override func makeJob() -> ToolJob { Self.job(pages: pages) }

    var isModified: Bool {
        pages.count != originalCount || pages.enumerated().contains { $0.element.source != $0.offset || $0.element.rotation % 360 != 0 }
    }

    // MARK: 編輯動作

    func size(of item: PTOrgPage) -> CGSize {
        if let s = item.source, s < pageSizes.count { return pageSizes[s] }
        // 空白頁：沿用前一頁的大小
        if let i = pages.firstIndex(of: item) {
            for j in stride(from: i - 1, through: 0, by: -1) { if let s = pages[j].source, s < pageSizes.count { return pageSizes[s] } }
        }
        return pageSizes.first ?? CGSize(width: 595, height: 842)
    }

    func rotate(_ ids: Set<UUID>, by degrees: Int) {
        for i in pages.indices where ids.contains(pages[i].id) {
            pages[i].rotation = ((pages[i].rotation + degrees) % 360 + 360) % 360
        }
    }

    func delete(_ ids: Set<UUID>) {
        withAnimation(.easeOut(duration: 0.15)) { pages.removeAll { ids.contains($0.id) } }
        selection.subtract(ids)
    }

    func insertBlank(after id: UUID?) {
        let index = id.flatMap { id in pages.firstIndex { $0.id == id } }.map { $0 + 1 } ?? pages.count
        withAnimation(.easeOut(duration: 0.15)) { pages.insert(PTOrgPage(source: nil), at: index) }
    }

    func move(_ id: UUID, to target: UUID) {
        guard id != target, let from = pages.firstIndex(where: { $0.id == id }), let to = pages.firstIndex(where: { $0.id == target }) else { return }
        withAnimation(.easeInOut(duration: 0.15)) {
            pages.move(fromOffsets: IndexSet(integer: from), toOffset: to > from ? to + 1 : to)
        }
    }

    func reverse() { withAnimation { pages.reverse() } }

    func reset() {
        withAnimation { pages = (0..<originalCount).map { PTOrgPage(source: $0) } }
        selection = []
    }

    func tap(_ id: UUID) {
        let flags = NSEvent.modifierFlags
        if flags.contains(.command) {
            if selection.contains(id) { selection.remove(id) } else { selection.insert(id) }
        } else if flags.contains(.shift), let anchor = pages.firstIndex(where: { selection.contains($0.id) }),
                  let target = pages.firstIndex(where: { $0.id == id }) {
            selection = Set(pages[min(anchor, target)...max(anchor, target)].map(\.id))
        } else {
            selection = selection == [id] ? [] : [id]
        }
    }

    // MARK: 背景工作

    nonisolated static func job(pages plan: [PTOrgPage]) -> ToolJob {
        ToolJob { files, context in
            guard let file = files.first else { throw ConversionError.other("請選擇一個 PDF 檔案。") }
            let doc = try PTPDF.open(file)
            let items = plan.isEmpty ? (0..<doc.pageCount).map { PTOrgPage(source: $0) } : plan
            var entries: [PTPageEntry] = []
            var reference = doc.page(at: 0).map(PTPDF.visualSize) ?? CGSize(width: 595, height: 842)
            var indexMap: [Int: Int] = [:]
            for (i, item) in items.enumerated() {
                try context.checkCancellation()
                if let s = item.source {
                    guard s >= 0, s < doc.pageCount, let page = doc.page(at: s) else {
                        throw ConversionError.other("「\(file.lastPathComponent)」已經變更（找不到第 \(s + 1) 頁），請重新選擇檔案。")
                    }
                    entries.append(PTPageEntry(doc: doc, index: s, rotation: ((page.rotation + item.rotation) % 360 + 360) % 360))
                    reference = PTPDF.visualSize(page)
                    if indexMap[s] == nil { indexMap[s] = i }
                } else {
                    entries.append(PTPageEntry(doc: doc, index: nil, blankSize: reference))
                }
            }
            guard !entries.isEmpty else { throw ConversionError.other("至少要保留一頁。") }
            context.progress(0.2, "寫出 PDF（共 \(entries.count) 頁）…")
            let tmp = try context.makeWorkDirectory().appendingPathComponent("organized.pdf")
            try PTAssemble.write(entries, outline: PTOutline.nodes(from: doc, map: { indexMap[$0] }), info: PTPDF.info(of: doc), to: tmp, context: context, span: 0.2...0.95)
            return [try context.deliver(tmp, as: context.outputName(for: file, tag: "已整理", ext: "pdf"), for: file)]
        }
    }
}

// MARK: - 頁面縮圖格線

private struct PTOrganizeGrid: View {
    @ObservedObject var session: PTOrganizeSession
    @Environment(\.toolTint) private var tint

    var body: some View {
        if session.pages.isEmpty {
            VStack(spacing: 10) {
                Image(systemName: session.originalCount == 0 ? "lock.doc" : "doc").font(.system(size: 34)).foregroundStyle(.secondary)
                Text(session.originalCount == 0 ? "無法讀取這份 PDF 的頁面" : "所有頁面都已刪除").font(.headline)
                if session.originalCount > 0 { Button("還原全部頁面") { session.reset() } }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity)
        } else {
            ScrollView {
                LazyVGrid(columns: [GridItem(.adaptive(minimum: 150, maximum: 180), spacing: 18)], spacing: 18) {
                    ForEach(Array(session.pages.enumerated()), id: \.element.id) { index, item in
                        PTPageCell(session: session, item: item, number: index + 1, selected: session.selection.contains(item.id))
                            .opacity(session.dragging == item.id ? 0.35 : 1)
                            .onDrag {
                                session.dragging = item.id
                                return NSItemProvider(object: item.id.uuidString as NSString)
                            }
                            .onDrop(of: [.text], delegate: PTPageDropDelegate(target: item.id, session: session))
                    }
                    PTAddBlankCell { session.insertBlank(after: nil) }
                }
                .padding(20)
            }
            .contentShape(Rectangle())
            .onTapGesture { session.selection = [] }
        }
    }
}

private struct PTPageDropDelegate: DropDelegate {
    let target: UUID
    let session: PTOrganizeSession

    func dropEntered(info: DropInfo) {
        guard let dragging = session.dragging, dragging != target else { return }
        session.move(dragging, to: target)
    }

    func dropUpdated(info: DropInfo) -> DropProposal? { DropProposal(operation: .move) }

    func validateDrop(info: DropInfo) -> Bool { session.dragging != nil }

    func performDrop(info: DropInfo) -> Bool {
        session.dragging = nil
        return true
    }
}

private struct PTPageCell: View {
    @ObservedObject var session: PTOrganizeSession
    let item: PTOrgPage
    let number: Int
    let selected: Bool
    @State private var image: CGImage?
    @State private var hovering = false
    @Environment(\.toolTint) private var tint

    private let box = CGSize(width: 132, height: 150)

    var body: some View {
        VStack(spacing: 6) {
            ZStack(alignment: .topTrailing) {
                pageView
                    .frame(width: box.width, height: box.height)
                    .padding(8)
                    .background(
                        RoundedRectangle(cornerRadius: 10, style: .continuous)
                            .fill(selected ? tint.opacity(0.12) : Color.clear)
                    )
                    .overlay(
                        RoundedRectangle(cornerRadius: 10, style: .continuous)
                            .strokeBorder(selected ? tint : (hovering ? Color.primary.opacity(0.18) : Color.clear), lineWidth: selected ? 2 : 1)
                    )
                if hovering || selected { toolbar.padding(4) }
            }
            HStack(spacing: 4) {
                Text("\(number)").font(.callout.weight(.semibold).monospacedDigit())
                if let s = item.source {
                    if s + 1 != number { Text("原第 \(s + 1) 頁").font(.caption).foregroundStyle(.secondary) }
                } else {
                    Text("空白頁").font(.caption).foregroundStyle(.secondary)
                }
                if item.rotation != 0 {
                    Image(systemName: "rotate.right").font(.caption).foregroundStyle(tint)
                }
            }
        }
        .contentShape(Rectangle())
        .onHover { hovering = $0 }
        .onTapGesture { session.tap(item.id) }
        .task(id: item.source) {
            guard let s = item.source, let renderer = session.renderer else { image = nil; return }
            image = await renderer.thumbnail(s)
        }
        .help(item.source.map { "原檔第 \($0 + 1) 頁" } ?? "空白頁")
    }

    /// 依旋轉後的比例縮放進方框，再旋轉顯示。
    private var pageView: some View {
        let natural = session.size(of: item)
        let quarter = item.rotation % 180 != 0
        let shown = quarter ? CGSize(width: natural.height, height: natural.width) : natural
        let scale = min(box.width / max(shown.width, 1), box.height / max(shown.height, 1))
        let fitted = CGSize(width: shown.width * scale, height: shown.height * scale)
        let unrotated = quarter ? CGSize(width: fitted.height, height: fitted.width) : fitted
        return ZStack {
            Rectangle().fill(Color.white)
            if let image {
                Image(decorative: image, scale: 1).resizable().interpolation(.high)
            } else if item.source == nil {
                VStack(spacing: 4) {
                    Image(systemName: "doc").font(.title2)
                    Text("空白頁").font(.caption)
                }
                .foregroundStyle(Color.black.opacity(0.3))
            } else {
                ProgressView().controlSize(.small)
            }
        }
        .frame(width: unrotated.width, height: unrotated.height)
        .rotationEffect(.degrees(Double(item.rotation)))
        .frame(width: fitted.width, height: fitted.height)
        .shadow(color: .black.opacity(0.18), radius: 3, y: 1)
        .animation(.easeInOut(duration: 0.2), value: item.rotation)
    }

    private var toolbar: some View {
        HStack(spacing: 2) {
            cellButton("rotate.left", "向左轉") { session.rotate([item.id], by: -90) }
            cellButton("rotate.right", "向右轉") { session.rotate([item.id], by: 90) }
            cellButton("doc.badge.plus", "在後面插入空白頁") { session.insertBlank(after: item.id) }
            cellButton("trash", "刪除這一頁") { session.delete([item.id]) }
        }
        .padding(3)
        .background(Capsule().fill(.regularMaterial))
        .overlay(Capsule().strokeBorder(Color.primary.opacity(0.12)))
        .shadow(color: .black.opacity(0.12), radius: 2, y: 1)
    }

    private func cellButton(_ symbol: String, _ help: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Image(systemName: symbol).font(.system(size: 11, weight: .semibold)).frame(width: 22, height: 20).contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .foregroundStyle(symbol == "trash" ? Color.red : Color.primary)
        .help(help)
    }
}

private struct PTAddBlankCell: View {
    let action: () -> Void
    @Environment(\.toolTint) private var tint
    @State private var hovering = false

    var body: some View {
        Button(action: action) {
            VStack(spacing: 8) {
                Image(systemName: "plus").font(.system(size: 22, weight: .medium))
                Text("加入空白頁").font(.callout)
            }
            .foregroundStyle(hovering ? tint : Color.secondary)
            .frame(width: 132, height: 150)
            .padding(8)
            .background(
                RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .strokeBorder(style: StrokeStyle(lineWidth: 1.5, dash: [6, 4]))
                    .foregroundStyle(hovering ? tint : Color.primary.opacity(0.2))
            )
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .onHover { hovering = $0 }
    }
}

// MARK: - 選項面板

private struct PTOrganizePanel: View {
    @ObservedObject var session: PTOrganizeSession

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            HStack(spacing: 12) {
                stat("\(session.originalCount)", "原本頁數")
                Image(systemName: "arrow.right").foregroundStyle(.secondary)
                stat("\(session.pages.count)", "整理後")
                Spacer()
            }
            if !session.selection.isEmpty {
                OptionGroup(title: "已選取 \(session.selection.count) 頁", systemImage: "checkmark.circle") {
                    VStack(alignment: .leading, spacing: 8) {
                        HStack(spacing: 8) {
                            actionButton("向左轉", "rotate.left") { session.rotate(session.selection, by: -90) }
                            actionButton("向右轉", "rotate.right") { session.rotate(session.selection, by: 90) }
                        }
                        HStack(spacing: 8) {
                            actionButton("刪除", "trash", role: .destructive) { session.delete(session.selection) }
                            actionButton("取消選取", "xmark") { session.selection = [] }
                        }
                    }
                }
            }
            OptionGroup(title: "所有頁面", systemImage: "doc.on.doc") {
                VStack(alignment: .leading, spacing: 8) {
                    HStack(spacing: 8) {
                        actionButton("全部向左轉", "rotate.left") { session.rotate(Set(session.pages.map(\.id)), by: -90) }
                        actionButton("全部向右轉", "rotate.right") { session.rotate(Set(session.pages.map(\.id)), by: 90) }
                    }
                    HStack(spacing: 8) {
                        actionButton("反轉順序", "arrow.up.arrow.down") { session.reverse() }
                        actionButton("加入空白頁", "doc.badge.plus") { session.insertBlank(after: nil) }
                    }
                    actionButton("還原所有變更", "arrow.uturn.backward") { session.reset() }
                        .disabled(!session.isModified)
                }
            }
            OptionNote(text: "拖曳縮圖可以調整順序；把滑鼠移到頁面上可旋轉、刪除或在後面插入空白頁。按住 ⌘ 或 ⇧ 點選可一次選取多頁。")
        }
    }

    private func stat(_ value: String, _ label: String) -> some View {
        VStack(alignment: .leading, spacing: 0) {
            Text(value).font(.title2.weight(.bold).monospacedDigit())
            Text(label).font(.caption).foregroundStyle(.secondary)
        }
    }

    private func actionButton(_ title: String, _ symbol: String, role: ButtonRole? = nil, action: @escaping () -> Void) -> some View {
        Button(role: role, action: action) {
            Label(title, systemImage: symbol).frame(maxWidth: .infinity)
        }
        .controlSize(.large)
        .buttonStyle(.bordered)
    }
}
