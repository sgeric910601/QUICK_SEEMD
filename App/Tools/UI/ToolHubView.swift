import SwiftUI

/// 工具大廳：大標題、搜尋、分類篩選、工具卡片。
struct ToolHubView: View {
    @EnvironmentObject var state: AppState
    var category: ToolCategory?
    @State private var search = ""
    @State private var dropTargeted = false

    private var categories: [ToolCategory] { category.map { [$0] } ?? ToolCategory.allCases }

    private func tools(in c: ToolCategory) -> [ToolDefinition] {
        ToolRegistry.tools(in: c).filter { tool in
            tool.matches(search: search) && (state.inbox.isEmpty || state.inboxFits(tool))
        }
    }

    var body: some View {
        ScrollView {
            VStack(spacing: 26) {
                header
                if !state.inbox.isEmpty { InboxBanner() }
                filterBar
                let sections = categories.map { ($0, tools(in: $0)) }.filter { !$0.1.isEmpty }
                if sections.isEmpty {
                    emptyResult
                }
                ForEach(sections, id: \.0) { c, list in
                    VStack(alignment: .leading, spacing: 12) {
                        if category == nil {
                            HStack(spacing: 8) {
                                Image(systemName: c.symbol).foregroundStyle(c.tint)
                                Text(c.title).font(.title3.weight(.bold))
                                Text("\(list.count)").font(.callout.weight(.semibold)).foregroundStyle(.secondary)
                                    .padding(.horizontal, 7).padding(.vertical, 1)
                                    .background(Capsule().fill(Color.primary.opacity(0.07)))
                            }
                        }
                        LazyVGrid(columns: [GridItem(.adaptive(minimum: 215, maximum: 360), spacing: 14)], spacing: 14) {
                            ForEach(list) { tool in
                                ToolCard(tool: tool) { state.open(tool, files: state.inboxFiles(for: tool)) }
                            }
                        }
                    }
                }
            }
            .padding(.horizontal, 32)
            .padding(.top, 26)
            .padding(.bottom, 40)
            .frame(maxWidth: 1240)
            .frame(maxWidth: .infinity)
        }
        .background(HubBackground(tint: category?.tint ?? ToolPalette.pdf))
        .onDrop(of: [.fileURL], isTargeted: $dropTargeted) { providers in
            ToolDrop.loadURLs(providers) { state.receive($0) }
            return true
        }
        .overlay {
            if dropTargeted {
                RoundedRectangle(cornerRadius: 18)
                    .strokeBorder(style: StrokeStyle(lineWidth: 3, dash: [10, 7]))
                    .foregroundStyle(Color.accentColor)
                    .background(RoundedRectangle(cornerRadius: 18).fill(Color.accentColor.opacity(0.06)))
                    .overlay(Label("放開即可選擇要使用的工具", systemImage: "tray.and.arrow.down.fill").font(.title2.weight(.semibold)))
                    .padding(14)
                    .allowsHitTesting(false)
            }
        }
    }

    private var header: some View {
        VStack(spacing: 10) {
            Text(category == nil ? "你需要的檔案工具，全都在這裡" : category!.title)
                .font(.system(size: 30, weight: .heavy))
                .multilineTextAlignment(.center)
            Text(subtitle)
                .font(.title3)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)
                .frame(maxWidth: 680)
            HStack(spacing: 8) {
                Image(systemName: "magnifyingglass").foregroundStyle(.secondary)
                TextField("搜尋工具，例如「壓縮」「Word」「GIF」", text: $search)
                    .textFieldStyle(.plain)
                    .font(.body)
                if !search.isEmpty {
                    Button { search = "" } label: { Image(systemName: "xmark.circle.fill").foregroundStyle(.secondary) }
                        .buttonStyle(.plain)
                }
            }
            .padding(.horizontal, 14).padding(.vertical, 9)
            .frame(maxWidth: 460)
            .background(Capsule().fill(Color(nsColor: .controlBackgroundColor)))
            .overlay(Capsule().strokeBorder(Color.primary.opacity(0.12)))
            .padding(.top, 6)
        }
        .padding(.top, 4)
    }

    private var subtitle: String {
        switch category {
        case nil: return "PDF、Word、Excel、PowerPoint、圖片、影片——合併、壓縮、轉檔一次搞定，全部在這台 Mac 上離線完成。"
        case .pdf: return "合併、拆分、壓縮、轉換、加浮水印、保護……處理 PDF 需要的一切。"
        case .office: return "Word、Excel、PowerPoint 與 PDF 互轉，可選 Microsoft Office 或 macOS 內建引擎。"
        case .image: return "轉檔、壓縮、調整大小、裁剪、去背、浮水印，批次處理大量圖片。"
        case .media: return "轉換、壓縮、剪輯影片，做 GIF、擷取音訊，音訊格式互轉。"
        case .other: return "萬用格式轉換、壓縮檔與文字編碼等實用工具。"
        }
    }

    private var filterBar: some View {
        HStack(spacing: 8) {
            FilterPill(title: "全部", systemImage: "square.grid.2x2", tint: .accentColor, selected: category == nil) {
                state.select(.allTools)
            }
            ForEach(ToolCategory.allCases) { c in
                if !ToolRegistry.tools(in: c).isEmpty {
                    FilterPill(title: c.shortTitle, systemImage: c.symbol, tint: c.tint, selected: category == c) {
                        state.select(.category(c))
                    }
                }
            }
        }
    }

    private var emptyResult: some View {
        VStack(spacing: 10) {
            Image(systemName: "magnifyingglass").font(.system(size: 34)).foregroundStyle(.secondary)
            Text(state.inbox.isEmpty ? "找不到符合「\(search)」的工具" : "沒有工具能同時處理這些檔案")
                .font(.title3.weight(.semibold))
            if !state.inbox.isEmpty {
                Button("清除選取的檔案") { state.inbox = [] }.buttonStyle(ToolSecondaryButtonStyle())
            }
        }
        .padding(.top, 50)
    }
}

private struct FilterPill: View {
    let title: String
    let systemImage: String
    let tint: Color
    let selected: Bool
    let action: () -> Void
    @State private var hover = false

    var body: some View {
        Button(action: action) {
            Label(title, systemImage: systemImage)
                .font(.callout.weight(.semibold))
                .padding(.horizontal, 14).padding(.vertical, 7)
                .foregroundStyle(selected ? Color.white : Color.primary)
                .background(Capsule().fill(selected ? tint : (hover ? Color.primary.opacity(0.08) : Color(nsColor: .controlBackgroundColor))))
                .overlay(Capsule().strokeBorder(selected ? Color.clear : Color.primary.opacity(0.12)))
                .contentShape(Capsule())
        }
        .buttonStyle(.plain)
        .onHover { hover = $0 }
    }
}

/// 工具卡片：圖示、名稱、說明；滑過時浮起並以工具顏色描邊。
struct ToolCard: View {
    let tool: ToolDefinition
    let action: () -> Void
    @State private var hover = false

    var body: some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 12) {
                ToolIcon(tool: tool, size: 46)
                Text(tool.title)
                    .font(.title3.weight(.bold))
                    .foregroundStyle(.primary)
                Text(tool.subtitle)
                    .font(.callout)
                    .foregroundStyle(.secondary)
                    .lineLimit(3)
                    .fixedSize(horizontal: false, vertical: true)
                Spacer(minLength: 0)
            }
            .padding(18)
            .frame(maxWidth: .infinity, minHeight: 168, alignment: .topLeading)
            .background(
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .fill(Color(nsColor: .controlBackgroundColor))
            )
            .overlay(
                RoundedRectangle(cornerRadius: 14, style: .continuous)
                    .strokeBorder(hover ? tool.tint.opacity(0.7) : Color.primary.opacity(0.08), lineWidth: hover ? 1.5 : 1)
            )
            .shadow(color: .black.opacity(hover ? 0.13 : 0.05), radius: hover ? 14 : 4, y: hover ? 7 : 2)
            .offset(y: hover ? -3 : 0)
            .contentShape(RoundedRectangle(cornerRadius: 14))
        }
        .buttonStyle(.plain)
        .onHover { h in withAnimation(.easeOut(duration: 0.15)) { hover = h } }
        .help(tool.subtitle)
    }
}

/// 從 Dock、Finder 服務或拖放收到的檔案：請使用者選擇工具。
private struct InboxBanner: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        HStack(spacing: 14) {
            HStack(spacing: -16) {
                ForEach(Array(state.inbox.prefix(3).enumerated()), id: \.offset) { _, url in
                    FileThumbnail(url: url, maxSize: CGSize(width: 44, height: 52))
                }
            }
            VStack(alignment: .leading, spacing: 3) {
                Text("已選取 \(state.inbox.count) 個檔案：要對它們做什麼？").font(.headline)
                Text(state.inbox.map(\.lastPathComponent).joined(separator: "、"))
                    .font(.callout).foregroundStyle(.secondary).lineLimit(1).truncationMode(.tail)
            }
            Spacer()
            Button("清除") { state.inbox = [] }.buttonStyle(ToolSecondaryButtonStyle())
        }
        .padding(14)
        .background(RoundedRectangle(cornerRadius: 14).fill(Color.accentColor.opacity(0.09)))
        .overlay(RoundedRectangle(cornerRadius: 14).strokeBorder(Color.accentColor.opacity(0.35)))
    }
}

struct HubBackground: View {
    let tint: Color
    var body: some View {
        ZStack(alignment: .top) {
            Color(nsColor: .windowBackgroundColor)
            LinearGradient(colors: [tint.opacity(0.07), .clear], startPoint: .top, endPoint: .bottom)
                .frame(height: 320)
        }
        .ignoresSafeArea()
    }
}
