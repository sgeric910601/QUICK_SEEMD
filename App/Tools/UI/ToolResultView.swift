import SwiftUI

/// 完成頁：「你的檔案已準備好」——結果檔、開啟／在 Finder 中顯示、接著可以用的工具。
struct ToolResultView: View {
    let tool: ToolDefinition
    let inputs: [URL]
    let outputs: [URL]
    /// 工具最後回報的狀態文字（例如「10.6 MB → 528 KB（減少 95%）」「略過 1 張」）
    var summary: String? = nil
    var onBack: () -> Void
    var onRestart: () -> Void
    @EnvironmentObject var state: AppState
    @State private var appeared = false

    private var totalOut: Int64 { outputs.reduce(0) { $0 + ToolFiles.size(of: $1) } }
    private var totalIn: Int64 { inputs.reduce(0) { $0 + ToolFiles.size(of: $1) } }

    /// 同格式輸出（壓縮類）才顯示節省比例。
    private var savings: Int? {
        guard !outputs.isEmpty, outputs.count == inputs.count, totalIn > 0, totalOut < totalIn,
              Set(outputs.map { $0.pathExtension.lowercased() }) == Set(inputs.map { $0.pathExtension.lowercased() }) else { return nil }
        return Int((1 - Double(totalOut) / Double(totalIn)) * 100)
    }

    /// 只保留有意義的摘要（略過「處理中：檔名」這類過程訊息）。
    static func summary(from message: String, files: [URL]) -> String? {
        let m = message.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !m.isEmpty else { return nil }
        let generic = ["處理中", "準備", "正在", "讀取", "寫入", "掃描："]
        if generic.contains(where: { m.hasPrefix($0) }) { return nil }
        if files.contains(where: { $0.lastPathComponent == m }) { return nil }
        return m
    }

    private var nextTools: [ToolDefinition] {
        guard !outputs.isEmpty else { return [] }
        return Array(ToolRegistry.tools(accepting: outputs).filter { $0.id != tool.id && $0.customPage == nil }.prefix(8))
    }

    var body: some View {
        ScrollView {
            VStack(spacing: 20) {
                Image(systemName: "checkmark.circle.fill")
                    .font(.system(size: 64))
                    .foregroundStyle(.white, .green)
                    .scaleEffect(appeared ? 1 : 0.5)
                    .opacity(appeared ? 1 : 0)
                    .padding(.top, 10)
                Text(outputs.isEmpty ? "完成！" : (outputs.count == 1 ? "檔案已經準備好了！" : "\(outputs.count) 個檔案已經準備好了！"))
                    .font(.system(size: 30, weight: .heavy))
                VStack(spacing: 4) {
                    if let summary {
                        Text(summary)
                            .font(.title3.weight(.semibold))
                            .foregroundStyle(summary.contains("省下") || summary.contains("減少") ? Color.green : Color.secondary)
                            .multilineTextAlignment(.center)
                    } else if let savings, savings > 0 {
                        Text("從 \(ToolFiles.formattedSize(totalIn)) 縮小到 \(ToolFiles.formattedSize(totalOut))，省下 \(savings)%")
                            .font(.title3.weight(.semibold))
                            .foregroundStyle(.green)
                    } else if !outputs.isEmpty {
                        Text("共 \(ToolFiles.formattedSize(totalOut))").font(.title3).foregroundStyle(.secondary)
                    }
                    if let dir = outputs.first?.deletingLastPathComponent() {
                        Text("儲存在「\(dir.lastPathComponent)」資料夾").font(.callout).foregroundStyle(.secondary)
                    }
                }

                if !outputs.isEmpty {
                    FlowLayout(spacing: 16, centered: true) {
                        ForEach(outputs.prefix(12), id: \.self) { url in
                            FileCard(url: url, tint: tool.tint)
                                .frame(width: 176)
                                .onTapGesture(count: 2) { NSWorkspace.shared.open(url) }
                                .contextMenu {
                                    Button("開啟") { NSWorkspace.shared.open(url) }
                                    Button("在 Finder 中顯示") { NSWorkspace.shared.activateFileViewerSelecting([url]) }
                                }
                        }
                    }
                    .frame(maxWidth: 820)
                    if outputs.count > 12 {
                        Text("還有 \(outputs.count - 12) 個檔案").foregroundStyle(.secondary)
                    }
                }

                HStack(spacing: 12) {
                    Button {
                        NSWorkspace.shared.activateFileViewerSelecting(outputs)
                    } label: {
                        Label("在 Finder 中顯示", systemImage: "folder").frame(minWidth: 200)
                    }
                    .buttonStyle(ToolPrimaryButtonStyle(tint: tool.tint))
                    .disabled(outputs.isEmpty)
                    if outputs.count >= 1 && outputs.count <= 5 {
                        Button { outputs.forEach { NSWorkspace.shared.open($0) } } label: {
                            Label(outputs.count == 1 ? "開啟檔案" : "全部開啟", systemImage: "arrow.up.forward.app")
                        }
                        .buttonStyle(ToolSecondaryButtonStyle())
                    }
                }
                .padding(.top, 4)

                HStack(spacing: 18) {
                    Button(action: onBack) { Label("返回修改選項", systemImage: "chevron.left") }
                    Button(action: onRestart) { Label("處理其他檔案", systemImage: "arrow.counterclockwise") }
                }
                .buttonStyle(.plain)
                .foregroundStyle(.secondary)

                if !nextTools.isEmpty {
                    VStack(spacing: 10) {
                        Text("接著可以…").font(.headline).foregroundStyle(.secondary)
                        FlowLayout(spacing: 8, centered: true) {
                            ForEach(nextTools) { t in
                                Button { state.open(t, files: outputs) } label: {
                                    HStack(spacing: 8) {
                                        ToolIcon(tool: t, size: 22)
                                        Text(t.title).font(.callout.weight(.medium))
                                    }
                                    .padding(.leading, 6).padding(.trailing, 12).padding(.vertical, 6)
                                    .background(Capsule().fill(Color(nsColor: .controlBackgroundColor)))
                                    .overlay(Capsule().strokeBorder(Color.primary.opacity(0.1)))
                                    .contentShape(Capsule())
                                }
                                .buttonStyle(.plain)
                            }
                        }
                        .frame(maxWidth: 720)
                    }
                    .padding(.top, 14)
                }
            }
            .padding(36)
            .frame(maxWidth: .infinity)
        }
        .background(HubBackground(tint: .green))
        .onAppear { withAnimation(.spring(response: 0.45, dampingFraction: 0.6)) { appeared = true } }
    }
}
