import SwiftUI

struct MainView: View {
    @EnvironmentObject var state: AppState

    var body: some View {
        NavigationSplitView {
            List(selection: Binding(get: { state.openToolID == nil ? state.sidebar : nil },
                                    set: { if let item = $0 { state.select(item) } })) {
                Section("工具") {
                    sidebarRow("所有工具", "square.grid.2x2.fill", .accentColor).tag(SidebarItem.allTools)
                    ForEach(ToolCategory.allCases) { c in
                        sidebarRow(c.title, c.symbol, c.tint).tag(SidebarItem.category(c))
                    }
                }
                Section("轉換") {
                    sidebarRow("萬用格式轉換", "arrow.triangle.2.circlepath", ToolPalette.teal).tag(SidebarItem.converter)
                }
                Section("預覽與整合") {
                    sidebarRow("預覽測試", "doc.viewfinder", ToolPalette.slate).tag(SidebarItem.preview)
                    sidebarRow("右鍵、拖曳與空白鍵預覽", "cursorarrow.click.2", ToolPalette.slate).tag(SidebarItem.integration)
                }
            }
            .listStyle(.sidebar)
            .navigationSplitViewColumnWidth(min: 200, ideal: 220, max: 260)
            .safeAreaInset(edge: .bottom) {
                HStack(spacing: 10) {
                    Image(nsImage: NSApp.applicationIconImage).resizable().frame(width: 30, height: 30)
                    VStack(alignment: .leading, spacing: 1) {
                        Text("QUICK_SEEMD").font(.headline)
                        Text("萬用預覽・轉檔・工具箱").font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer()
                }
                .padding(14)
            }
        } detail: {
            Group {
                if let tool = state.openTool {
                    if let custom = tool.customPage {
                        CustomToolPage(tool: tool, content: custom())
                    } else {
                        ToolPageView(tool: tool, files: state.pendingFiles).id(state.pageToken)
                    }
                } else {
                    switch state.sidebar ?? .allTools {
                    case .allTools: ToolHubView(category: nil)
                    case .category(let c): ToolHubView(category: c).id(c)
                    case .converter: ConverterView(model: state.converter)
                    case .preview: PreviewTestView()
                    case .integration: IntegrationView()
                    }
                }
            }
            .frame(minWidth: 720, minHeight: 560)
        }
    }

    private func sidebarRow(_ title: String, _ symbol: String, _ tint: Color) -> some View {
        Label {
            Text(title)
        } icon: {
            Image(systemName: symbol).foregroundStyle(tint)
        }
    }
}

/// 自訂頁面的工具（例如萬用格式轉換）：加上返回列。
private struct CustomToolPage: View {
    let tool: ToolDefinition
    let content: AnyView
    @EnvironmentObject var state: AppState

    var body: some View {
        VStack(spacing: 0) {
            HStack(spacing: 10) {
                Button { state.closeTool() } label: { Label("所有工具", systemImage: "chevron.left").font(.callout.weight(.medium)) }
                    .buttonStyle(.plain)
                    .foregroundStyle(.secondary)
                Divider().frame(height: 16)
                ToolIcon(tool: tool, size: 22)
                Text(tool.title).font(.headline)
                Spacer()
            }
            .padding(.horizontal, 18)
            .frame(height: 46)
            .background(.bar)
            Divider()
            content
        }
    }
}
