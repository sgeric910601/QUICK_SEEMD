import SwiftUI
import UniformTypeIdentifiers

/// 單一工具的工作頁：選檔（空狀態）→ 檔案卡片＋右側選項 → 處理中 → 完成。
struct ToolPageView: View {
    let tool: ToolDefinition
    @EnvironmentObject var state: AppState
    @StateObject private var session: ToolSession
    @State private var files: [URL]
    @State private var phase: Phase = .editing
    @State private var progress: Double = 0
    @State private var message = ""
    @State private var task: Task<Void, Never>?
    @State private var note: String?
    @State private var dropTargeted = false
    @State private var dragging: URL?

    enum Phase: Equatable {
        case editing, running
        case done([URL])
        case failed(String)
    }

    init(tool: ToolDefinition, files: [URL]) {
        self.tool = tool
        _session = StateObject(wrappedValue: tool.makeSession())
        var accepted = files.filter { tool.accepts($0) }
        if !tool.allowsMultiple { accepted = Array(accepted.prefix(1)) }
        _files = State(initialValue: accepted)
    }

    var body: some View {
        VStack(spacing: 0) {
            topBar
            Divider()
            ZStack {
                switch phase {
                case .editing, .running:
                    if files.isEmpty { landing } else { workspace }
                case .done(let outputs):
                    ToolResultView(tool: tool, inputs: files, outputs: outputs, summary: ToolResultView.summary(from: message, files: files),
                                   onBack: { phase = .editing },
                                   onRestart: { files = []; phase = .editing })
                case .failed(let reason):
                    failure(reason)
                }
                if phase == .running { runningOverlay }
            }
        }
        .environment(\.toolTint, tool.tint)
        .onAppear { session.filesDidChange(files) }
        .onChange(of: files) { _, newValue in session.filesDidChange(newValue) }
    }

    // MARK: 上方列

    private var topBar: some View {
        HStack(spacing: 10) {
            Button { state.closeTool() } label: {
                Label("所有工具", systemImage: "chevron.left").font(.callout.weight(.medium))
            }
            .buttonStyle(.plain)
            .foregroundStyle(.secondary)
            .disabled(phase == .running)
            Divider().frame(height: 16)
            ToolIcon(tool: tool, size: 22)
            Text(tool.title).font(.headline)
            Spacer()
            if !files.isEmpty && phase == .editing {
                Button { pickFiles() } label: { Label("加入檔案", systemImage: "plus") }
                    .buttonStyle(ToolSecondaryButtonStyle())
                    .disabled(!tool.allowsMultiple && files.count >= 1)
            }
        }
        .padding(.horizontal, 18)
        .frame(height: 46)
        .background(.bar)
    }

    // MARK: 空狀態（選擇檔案）

    private var landing: some View {
        VStack(spacing: 16) {
            Spacer(minLength: 20)
            ToolIcon(tool: tool, size: 78)
                .padding(.bottom, 6)
            Text(tool.title).font(.system(size: 34, weight: .heavy))
            Text(tool.subtitle)
                .font(.title3)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 560)
            Button { pickFiles() } label: {
                Label("選擇\(tool.inputLabel)", systemImage: "plus.circle.fill")
                    .frame(minWidth: 280)
            }
            .buttonStyle(ToolPrimaryButtonStyle(tint: tool.tint))
            .padding(.top, 10)
            Text(tool.allowsMultiple ? "或把檔案拖到這裡，可以一次選多個" : "或把檔案拖到這裡")
                .foregroundStyle(.secondary)
            if let note { Label(note, systemImage: "exclamationmark.triangle.fill").foregroundStyle(.orange).font(.callout) }
            Spacer(minLength: 20)
        }
        .padding(30)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
        .background(HubBackground(tint: tool.tint))
        .overlay { dropHighlight }
        .onDrop(of: [.fileURL], isTargeted: $dropTargeted) { providers in
            ToolDrop.loadURLs(providers) { add($0) }
            return true
        }
    }

    @ViewBuilder private var dropHighlight: some View {
        if dropTargeted {
            RoundedRectangle(cornerRadius: 18)
                .strokeBorder(style: StrokeStyle(lineWidth: 3, dash: [10, 7]))
                .foregroundStyle(tool.tint)
                .background(RoundedRectangle(cornerRadius: 18).fill(tool.tint.opacity(0.06)))
                .padding(14)
                .allowsHitTesting(false)
        }
    }

    // MARK: 工作區

    private var workspace: some View {
        HStack(spacing: 0) {
            filesArea
            Divider()
            optionsPanel.frame(width: 340)
        }
    }

    private var filesArea: some View {
        VStack(spacing: 0) {
            HStack(spacing: 10) {
                Text("\(files.count) 個檔案").font(.headline)
                if tool.reorderable && files.count > 1 {
                    Text("拖曳卡片可調整順序").font(.callout).foregroundStyle(.secondary)
                    Menu {
                        Button("依名稱 A → Z") { files.sort { $0.lastPathComponent.localizedStandardCompare($1.lastPathComponent) == .orderedAscending } }
                        Button("依名稱 Z → A") { files.sort { $0.lastPathComponent.localizedStandardCompare($1.lastPathComponent) == .orderedDescending } }
                        Button("反轉順序") { files.reverse() }
                    } label: { Label("排序", systemImage: "arrow.up.arrow.down") }
                        .menuStyle(.borderlessButton)
                        .fixedSize()
                }
                if let note { Label(note, systemImage: "exclamationmark.triangle.fill").font(.callout).foregroundStyle(.orange).lineLimit(1) }
                Spacer()
                Button("全部移除") { files = [] }
                    .buttonStyle(.plain)
                    .foregroundStyle(.secondary)
            }
            .padding(.horizontal, 20)
            .frame(height: 44)

            if let custom = session.customFilesView(files: $files) {
                custom
            } else {
                ScrollView {
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 158, maximum: 190), spacing: 16)], spacing: 16) {
                        ForEach(Array(files.enumerated()), id: \.element) { index, url in
                            FileCard(url: url, order: tool.reorderable && files.count > 1 ? index + 1 : nil, tint: tool.tint) {
                                withAnimation(.easeOut(duration: 0.15)) { files.removeAll { $0 == url } }
                            }
                            .opacity(dragging == url ? 0.35 : 1)
                            .onDrag {
                                dragging = url
                                return NSItemProvider(object: url.path as NSString)
                            }
                            .onDrop(of: [.text], delegate: ReorderDelegate(item: url, files: $files, dragging: $dragging, enabled: tool.reorderable))
                        }
                        if tool.allowsMultiple {
                            AddMoreCard(tint: tool.tint) { pickFiles() }
                        }
                    }
                    .padding(20)
                }
            }
        }
        .background(Color(nsColor: .windowBackgroundColor).overlay(Color.primary.opacity(0.035)))
        .overlay { dropHighlight }
        .onDrop(of: [.fileURL], isTargeted: $dropTargeted) { providers in
            ToolDrop.loadURLs(providers) { add($0) }
            return true
        }
    }

    private var validation: String? {
        if files.count < tool.minimumFiles { return "至少需要 \(tool.minimumFiles) 個檔案" }
        if !tool.allowsMultiple && files.count > 1 { return "這個工具一次只能處理 1 個檔案" }
        return session.validate(files)
    }

    private var optionsPanel: some View {
        VStack(spacing: 0) {
            ScrollView {
                VStack(alignment: .leading, spacing: 18) {
                    HStack(spacing: 10) {
                        ToolIcon(tool: tool, size: 34)
                        Text(tool.title).font(.title2.weight(.bold))
                    }
                    Text(tool.subtitle).font(.callout).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                    Divider()
                    session.optionsView()
                }
                .padding(20)
            }
            Divider()
            VStack(alignment: .leading, spacing: 10) {
                OutputLocationPicker()
                if let reason = validation {
                    Label(reason, systemImage: "exclamationmark.triangle.fill")
                        .font(.callout)
                        .foregroundStyle(.orange)
                }
                Button(action: run) {
                    HStack {
                        Text(session.actionTitleOverride ?? tool.actionTitle)
                        Spacer()
                        Image(systemName: "arrow.right.circle.fill")
                    }
                    .frame(maxWidth: .infinity)
                }
                .buttonStyle(ToolPrimaryButtonStyle(tint: tool.tint))
                .disabled(validation != nil || phase == .running)
                .keyboardShortcut(.return, modifiers: .command)
            }
            .padding(16)
        }
        .background(Color(nsColor: .windowBackgroundColor))
    }

    // MARK: 執行

    private func run() {
        guard validation == nil else { return }
        let job = session.makeJob()
        let input = files
        let output = ToolOutputSettings.shared.output
        progress = 0
        message = "準備中…"
        phase = .running
        let context = ToolContext(output: output) { p, msg in
            Task { @MainActor in
                progress = max(progress, p)
                if let msg { message = msg }
            }
        }
        task = Task.detached(priority: .userInitiated) {
            do {
                let outputs = try await job.run(input, context)
                await MainActor.run {
                    if Task.isCancelled { phase = .editing } else { phase = .done(outputs) }
                }
            } catch {
                let cancelled = Task.isCancelled
                await MainActor.run { phase = cancelled ? .editing : .failed(error.localizedDescription) }
            }
        }
    }

    private var runningOverlay: some View {
        ZStack {
            Color.black.opacity(0.16).ignoresSafeArea()
            VStack(spacing: 14) {
                ToolIcon(tool: tool, size: 52)
                Text("\(Int(progress * 100))%")
                    .font(.system(size: 36, weight: .heavy).monospacedDigit())
                ProgressView(value: progress)
                    .progressViewStyle(.linear)
                    .tint(tool.tint)
                    .frame(width: 320)
                Text(message.isEmpty ? "處理中…" : message)
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .lineLimit(2)
                    .frame(width: 320)
                Button("取消") { task?.cancel() }
                    .buttonStyle(ToolSecondaryButtonStyle())
                    .keyboardShortcut(.cancelAction)
            }
            .padding(30)
            .background(RoundedRectangle(cornerRadius: 20, style: .continuous).fill(.regularMaterial))
            .shadow(color: .black.opacity(0.2), radius: 30, y: 10)
        }
        .transition(.opacity)
    }

    private func failure(_ reason: String) -> some View {
        VStack(spacing: 16) {
            Image(systemName: "xmark.octagon.fill").font(.system(size: 56)).foregroundStyle(.red)
            Text("無法完成").font(.system(size: 28, weight: .heavy))
            Text(reason).font(.title3).foregroundStyle(.secondary).multilineTextAlignment(.center).frame(maxWidth: 560)
                .textSelection(.enabled)
            Button { phase = .editing } label: { Label("返回修改", systemImage: "chevron.left").frame(minWidth: 200) }
                .buttonStyle(ToolPrimaryButtonStyle(tint: tool.tint))
                .padding(.top, 8)
        }
        .padding(40)
        .frame(maxWidth: .infinity, maxHeight: .infinity)
    }

    // MARK: 檔案

    private func pickFiles() {
        let panel = NSOpenPanel()
        panel.allowsMultipleSelection = tool.allowsMultiple
        panel.canChooseDirectories = tool.inputTypes.contains(.folder) || tool.inputTypes.contains(.directory)
        panel.canChooseFiles = true
        let types = tool.inputTypes.filter { $0 != .folder && $0 != .directory }
        if !types.isEmpty && !tool.inputTypes.contains(.item) { panel.allowedContentTypes = types }
        panel.prompt = "選擇"
        panel.message = "選擇要用「\(tool.title)」處理的\(tool.inputLabel)"
        if panel.runModal() == .OK { add(panel.urls) }
    }

    private func add(_ urls: [URL]) {
        let accepted = urls.filter { tool.accepts($0) }
        let skipped = urls.count - accepted.count
        note = skipped > 0 ? "已略過 \(skipped) 個不支援的檔案（需要\(tool.inputLabel)）" : nil
        var merged = files
        for u in accepted where !merged.contains(u) { merged.append(u) }
        if !tool.allowsMultiple, let last = accepted.last { merged = [last] }
        withAnimation(.easeOut(duration: 0.15)) { files = merged }
    }
}

/// 檔案卡片：縮圖、名稱、大小；滑過顯示移除鈕；可排序時顯示順序號碼。
struct FileCard: View {
    let url: URL
    var order: Int?
    var tint: Color = .accentColor
    var onRemove: (() -> Void)?
    @State private var hover = false

    var body: some View {
        VStack(spacing: 8) {
            ZStack(alignment: .topLeading) {
                FileThumbnail(url: url, maxSize: CGSize(width: 128, height: 146))
                    .frame(maxWidth: .infinity)
                    .frame(height: 150)
                if let order {
                    Text("\(order)")
                        .font(.caption.weight(.bold).monospacedDigit())
                        .foregroundStyle(.white)
                        .frame(minWidth: 20, minHeight: 20)
                        .background(Circle().fill(tint))
                }
            }
            .overlay(alignment: .topTrailing) {
                if hover, let onRemove {
                    Button(action: onRemove) {
                        Image(systemName: "xmark.circle.fill")
                            .font(.system(size: 20))
                            .symbolRenderingMode(.palette)
                            .foregroundStyle(.white, Color.black.opacity(0.65))
                    }
                    .buttonStyle(.plain)
                    .help("移除")
                }
            }
            Text(url.lastPathComponent)
                .font(.callout.weight(.medium))
                .lineLimit(2)
                .multilineTextAlignment(.center)
                .truncationMode(.middle)
                .frame(height: 36, alignment: .top)
            Text("\(ToolFiles.formattedSize(ToolFiles.size(of: url))) · \(ToolFiles.kind(of: url))")
                .font(.caption)
                .foregroundStyle(.secondary)
                .lineLimit(1)
        }
        .padding(10)
        .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Color(nsColor: .controlBackgroundColor)))
        .overlay(RoundedRectangle(cornerRadius: 12, style: .continuous).strokeBorder(hover ? tint.opacity(0.6) : Color.primary.opacity(0.08)))
        .shadow(color: .black.opacity(hover ? 0.12 : 0.05), radius: hover ? 10 : 3, y: hover ? 4 : 1)
        .onHover { h in withAnimation(.easeOut(duration: 0.12)) { hover = h } }
        .help(url.path)
    }
}

private struct AddMoreCard: View {
    let tint: Color
    let action: () -> Void
    @State private var hover = false

    var body: some View {
        Button(action: action) {
            VStack(spacing: 10) {
                Image(systemName: "plus")
                    .font(.system(size: 26, weight: .semibold))
                    .foregroundStyle(.white)
                    .frame(width: 54, height: 54)
                    .background(Circle().fill(tint))
                    .shadow(color: tint.opacity(0.4), radius: 8, y: 3)
                Text("加入更多檔案").font(.callout.weight(.medium)).foregroundStyle(.secondary)
            }
            .frame(maxWidth: .infinity, minHeight: 236)
            .background(
                RoundedRectangle(cornerRadius: 12, style: .continuous)
                    .strokeBorder(style: StrokeStyle(lineWidth: 1.5, dash: [7, 5]))
                    .foregroundStyle(hover ? tint : Color.primary.opacity(0.2))
            )
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .onHover { hover = $0 }
    }
}

private struct ReorderDelegate: DropDelegate {
    let item: URL
    @Binding var files: [URL]
    @Binding var dragging: URL?
    let enabled: Bool

    func validateDrop(info: DropInfo) -> Bool { enabled && dragging != nil }

    func dropEntered(info: DropInfo) {
        guard enabled, let dragging, dragging != item,
              let from = files.firstIndex(of: dragging), let to = files.firstIndex(of: item) else { return }
        withAnimation(.easeOut(duration: 0.18)) {
            files.move(fromOffsets: IndexSet(integer: from), toOffset: to > from ? to + 1 : to)
        }
    }

    func dropUpdated(info: DropInfo) -> DropProposal? { DropProposal(operation: .move) }

    func performDrop(info: DropInfo) -> Bool {
        dragging = nil
        return true
    }
}
