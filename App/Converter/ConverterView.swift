import AppKit
import SwiftUI
import UniformTypeIdentifiers

/// 「格式轉換」頁面。由 App 外殼放進視窗的一個分頁或窗格中。
struct ConverterView: View {
    @ObservedObject var model: ConverterModel
    @State private var isDropTargeted = false
    @State private var showBackends = false

    init(model: ConverterModel) {
        self.model = model
    }

    var body: some View {
        ZStack {
            CVVisualEffectBackground(material: .underWindowBackground, blending: .behindWindow)
                .ignoresSafeArea()
            VStack(spacing: 0) {
                header
                    .padding(.horizontal, 20)
                    .padding(.top, 16)
                    .padding(.bottom, 12)
                if model.jobs.isEmpty {
                    CVDropZone(isTargeted: isDropTargeted, compact: false) { model.openFiles() }
                        .padding(.horizontal, 20)
                        .padding(.bottom, 20)
                        .transition(.opacity.combined(with: .scale(scale: 0.98)))
                } else {
                    workspace
                        .transition(.opacity)
                }
                if !model.jobs.isEmpty {
                    CVFooterBar(model: model)
                }
            }
        }
        .frame(minWidth: 720, minHeight: 480)
        .dropDestination(for: URL.self) { urls, _ in
            let files = urls.filter(\.isFileURL)
            guard !files.isEmpty else { return false }
            withAnimation(.snappy) { model.add(urls: files) }
            return true
        } isTargeted: { targeted in
            isDropTargeted = targeted
        }
        .animation(.snappy(duration: 0.25), value: model.jobs.isEmpty)
    }

    // MARK: - 標題列

    private var header: some View {
        HStack(alignment: .center, spacing: 12) {
            ZStack {
                RoundedRectangle(cornerRadius: 10, style: .continuous)
                    .fill(LinearGradient(colors: [Color(red: 0.32, green: 0.55, blue: 1.0), Color(red: 0.55, green: 0.36, blue: 0.98)],
                                         startPoint: .topLeading, endPoint: .bottomTrailing))
                    .frame(width: 38, height: 38)
                    .shadow(color: Color(red: 0.4, green: 0.4, blue: 1).opacity(0.35), radius: 6, y: 3)
                Image(systemName: "arrow.triangle.2.circlepath")
                    .font(.system(size: 18, weight: .semibold))
                    .foregroundStyle(.white)
            }
            VStack(alignment: .leading, spacing: 2) {
                Text("格式轉換")
                    .font(.system(size: 19, weight: .bold))
                Text("把檔案轉成你需要的格式，全部在這台 Mac 上離線完成")
                    .font(.system(size: 12))
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
            }
            Spacer(minLength: 12)
            Button {
                showBackends.toggle()
            } label: {
                HStack(spacing: 6) {
                    Image(systemName: "cpu")
                    Text(model.availableBackendCount == 0 ? "macOS 內建引擎" : "內建 + \(model.availableBackendCount) 個外部引擎")
                }
                .font(.system(size: 12, weight: .medium))
                .padding(.horizontal, 10)
                .padding(.vertical, 6)
                .background(Capsule().fill(Color.primary.opacity(0.06)))
                .overlay(Capsule().strokeBorder(Color.primary.opacity(0.08)))
            }
            .buttonStyle(.plain)
            .help("查看可用的轉換引擎")
            .popover(isPresented: $showBackends, arrowEdge: .bottom) {
                CVBackendPopover(statuses: model.backendStatuses) { model.refreshBackends() }
            }
            if !model.jobs.isEmpty {
                Menu {
                    Button("全選") { model.selectAll() }
                    Button("清除已完成的項目") { withAnimation { model.clearFinished() } }
                        .disabled(model.finishedCount == 0)
                    Divider()
                    Button("全部移除", role: .destructive) { withAnimation { model.removeAll() } }
                        .disabled(model.isRunning)
                } label: {
                    Image(systemName: "ellipsis.circle")
                        .font(.system(size: 15))
                }
                .menuStyle(.borderlessButton)
                .menuIndicator(.hidden)
                .fixedSize()
                .help("更多動作")
            }
        }
    }

    // MARK: - 主要區域

    private var workspace: some View {
        GeometryReader { geo in
            let leftWidth = min(400, max(300, geo.size.width * 0.42))
            HStack(alignment: .top, spacing: 14) {
                fileColumn
                    .frame(width: leftWidth)
                CVTargetPanel(model: model)
                    .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
            }
            .padding(.horizontal, 20)
            .padding(.bottom, 14)
        }
    }

    private var fileColumn: some View {
        VStack(spacing: 10) {
            CVDropZone(isTargeted: isDropTargeted, compact: true) { model.openFiles() }
            HStack {
                Text("檔案")
                    .font(.system(size: 12, weight: .semibold))
                    .foregroundStyle(.secondary)
                Text("\(model.jobs.count)")
                    .font(.system(size: 11, weight: .semibold).monospacedDigit())
                    .padding(.horizontal, 6)
                    .padding(.vertical, 1)
                    .background(Capsule().fill(Color.primary.opacity(0.08)))
                    .foregroundStyle(.secondary)
                Spacer()
                if model.jobs.count > 1 {
                    Text("⌘ 點按可複選")
                        .font(.system(size: 10.5))
                        .foregroundStyle(.tertiary)
                }
            }
            .padding(.horizontal, 4)
            ScrollView {
                LazyVStack(spacing: 7) {
                    ForEach(model.jobs) { job in
                        CVJobRow(job: job,
                                 isSelected: model.selection.contains(job.id),
                                 onSelect: { extend in model.select(job.id, extend: extend) },
                                 onRemove: { withAnimation(.snappy) { model.remove([job.id]) } },
                                 onReveal: { model.reveal($0) },
                                 onOpen: { model.open($0) })
                            .transition(.asymmetric(insertion: .move(edge: .top).combined(with: .opacity), removal: .opacity))
                    }
                }
                .padding(.vertical, 2)
                .padding(.horizontal, 2)
                .animation(.snappy(duration: 0.25), value: model.jobs)
            }
            .scrollIndicators(.automatic)
            .focusable()
            .focusEffectDisabled()
            .onDeleteCommand { withAnimation { model.removeSelected() } }
        }
    }
}

// MARK: - 右側：格式與選項

struct CVTargetPanel: View {
    @ObservedObject var model: ConverterModel

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 12) {
                if let banner = model.banner {
                    CVBannerView(banner: banner, onReveal: { model.reveal($0) }, onClose: { model.banner = nil })
                }
                targetsCard
                let sections = model.relevantOptionSections
                if !sections.isEmpty {
                    CVOptionsCard(model: model, sections: sections)
                }
                if model.mergeableJobs.count >= 2 {
                    mergeCard
                }
                if !model.unavailableHints.isEmpty {
                    unavailableCard
                }
            }
            .padding(.bottom, 4)
            .padding(.horizontal, 2)
        }
        .scrollIndicators(.automatic)
    }

    private var targetsCard: some View {
        VStack(alignment: .leading, spacing: 14) {
            HStack(alignment: .firstTextBaseline) {
                Text("轉換成")
                    .font(.system(size: 15, weight: .semibold))
                Text(model.scopeDescription)
                    .font(.system(size: 12))
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.middle)
                Spacer()
                if model.isAnalyzingScope {
                    ProgressView().controlSize(.small)
                }
            }
            if model.scopeIsMixed {
                Label("選取的檔案類型不同，只列出它們都能轉換的格式。點選左側單一檔案可個別設定。", systemImage: "info.circle")
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            let groups = model.groupedTargets
            if groups.isEmpty && !model.isAnalyzingScope {
                HStack(spacing: 10) {
                    Image(systemName: "nosign")
                        .font(.system(size: 20))
                        .foregroundStyle(.secondary)
                    Text(model.scopeJobs.count > 1 ? "這些檔案沒有共同可轉換的格式。" : "這個檔案目前沒有可轉換的格式。")
                        .font(.system(size: 12.5))
                        .foregroundStyle(.secondary)
                }
                .padding(.vertical, 6)
            }
            ForEach(groups, id: \.category) { group in
                VStack(alignment: .leading, spacing: 7) {
                    Label(group.category.title, systemImage: group.category.symbol)
                        .font(.system(size: 11.5, weight: .semibold))
                        .foregroundStyle(.secondary)
                        .labelStyle(.titleAndIcon)
                    CVFlowLayout(spacing: 7, lineSpacing: 7) {
                        ForEach(group.formats) { format in
                            let state = model.selectionState(of: format)
                            CVFormatChip(format: format, isSelected: state.all, isPartial: state.some && !state.all) {
                                withAnimation(.snappy(duration: 0.2)) { model.choose(format) }
                            }
                        }
                    }
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .cvCardStyle()
    }

    private var mergeCard: some View {
        HStack(spacing: 12) {
            Image(systemName: "rectangle.stack.badge.plus")
                .font(.system(size: 20))
                .foregroundStyle(Color.accentColor)
                .frame(width: 28)
            VStack(alignment: .leading, spacing: 2) {
                Text("合併成一個 PDF")
                    .font(.system(size: 13, weight: .semibold))
                Text("依清單順序，把 \(model.mergeableJobs.count) 個圖片／PDF 合成一份文件")
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
            }
            Spacer()
            Button("合併") { model.mergeIntoPDF() }
                .disabled(model.isRunning)
        }
        .cvCardStyle(padding: 12)
    }

    private var unavailableCard: some View {
        VStack(alignment: .leading, spacing: 9) {
            Label("目前無法輸出", systemImage: "lock")
                .font(.system(size: 11.5, weight: .semibold))
                .foregroundStyle(.secondary)
            ForEach(model.unavailableHints) { hint in
                HStack(alignment: .firstTextBaseline, spacing: 8) {
                    Text(hint.formats)
                        .font(.system(size: 12, weight: .semibold))
                        .foregroundStyle(.secondary)
                    Text(hint.reason)
                        .font(.system(size: 11.5))
                        .foregroundStyle(.tertiary)
                        .fixedSize(horizontal: false, vertical: true)
                }
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .cvCardStyle(padding: 12)
        .opacity(0.9)
    }
}

// MARK: - 提示橫幅

struct CVBannerView: View {
    let banner: ConverterModel.Banner
    let onReveal: ([URL]) -> Void
    let onClose: () -> Void

    var body: some View {
        HStack(spacing: 10) {
            Image(systemName: banner.isError ? "exclamationmark.triangle.fill" : "checkmark.seal.fill")
                .font(.system(size: 18))
                .foregroundStyle(banner.isError ? Color.orange : Color.green)
            Text(banner.message)
                .font(.system(size: 12.5, weight: .medium))
                .fixedSize(horizontal: false, vertical: true)
                .textSelection(.enabled)
            Spacer(minLength: 8)
            if !banner.urls.isEmpty {
                Button("在 Finder 中顯示") { onReveal(banner.urls) }
                    .controlSize(.small)
            }
            Button(action: onClose) {
                Image(systemName: "xmark")
                    .font(.system(size: 10, weight: .bold))
            }
            .buttonStyle(.borderless)
            .help("關閉")
        }
        .cvCardStyle(padding: 11)
        .transition(.move(edge: .top).combined(with: .opacity))
    }
}

// MARK: - 底部列

struct CVFooterBar: View {
    @ObservedObject var model: ConverterModel

    var body: some View {
        VStack(spacing: 0) {
            Divider().opacity(0.6)
            HStack(spacing: 12) {
                Image(systemName: "folder")
                    .foregroundStyle(.secondary)
                Text("輸出位置")
                    .font(.system(size: 12, weight: .medium))
                    .foregroundStyle(.secondary)
                Picker("輸出位置", selection: $model.outputMode) {
                    ForEach(ConverterModel.OutputMode.allCases) { mode in
                        Text(mode.title).tag(mode)
                    }
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .fixedSize()
                .onChange(of: model.outputMode) { _, newValue in
                    if newValue == .custom && model.customOutputFolder == nil { model.chooseOutputFolder() }
                }
                if model.outputMode == .custom {
                    Button {
                        model.chooseOutputFolder()
                    } label: {
                        HStack(spacing: 4) {
                            if let folder = model.customOutputFolder {
                                Image(nsImage: NSWorkspace.shared.icon(forFile: folder.path))
                                    .resizable()
                                    .frame(width: 14, height: 14)
                                Text(FileManager.default.displayName(atPath: folder.path))
                                    .lineLimit(1)
                                    .truncationMode(.middle)
                            } else {
                                Text("選擇…")
                            }
                        }
                        .font(.system(size: 12))
                        .frame(maxWidth: 170)
                    }
                    .help(model.customOutputFolder?.path ?? "選擇輸出資料夾")
                }
                Spacer(minLength: 8)
                if let progress = model.overallProgress {
                    ProgressView(value: progress)
                        .progressViewStyle(.circular)
                        .controlSize(.small)
                }
                if model.isRunning {
                    Button(role: .cancel) {
                        model.cancel()
                    } label: {
                        Label("停止", systemImage: "stop.fill")
                    }
                    .controlSize(.large)
                } else {
                    Button {
                        model.convertAll()
                    } label: {
                        Label(convertTitle, systemImage: "arrow.triangle.2.circlepath")
                            .frame(minWidth: 120)
                    }
                    .buttonStyle(.borderedProminent)
                    .controlSize(.large)
                    .disabled(model.readyJobs.isEmpty)
                    .keyboardShortcut(.return, modifiers: [.command])
                    .help(model.readyJobs.isEmpty ? "先在右側選擇輸出格式" : "開始轉換（⌘↩）")
                }
            }
            .padding(.horizontal, 20)
            .padding(.vertical, 11)
            .background(.bar)
        }
    }

    private var convertTitle: String {
        let n = model.readyJobs.count
        return n <= 1 ? "轉換" : "轉換 \(n) 個檔案"
    }
}
