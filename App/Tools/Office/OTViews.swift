import SwiftUI

// Office 工具共用的選項元件。

/// 引擎選擇卡片：外觀同 ChoiceCards，但無法使用的引擎會變淡、不能點，並標示原因（例如「未安裝」）。
struct OTEngineCards: View {
    @Binding var selection: OTEngine
    let states: [OTEngineState]
    @Environment(\.toolTint) private var tint

    var body: some View {
        VStack(spacing: 8) {
            ForEach(states) { state in
                let selected = state.engine == selection && state.available
                Button { if state.available { selection = state.engine } } label: {
                    HStack(alignment: .center, spacing: 12) {
                        Image(systemName: state.engine.symbol)
                            .font(.system(size: 16, weight: .medium))
                            .foregroundStyle(selected ? tint : .secondary)
                            .frame(width: 24)
                        VStack(alignment: .leading, spacing: 2) {
                            HStack(spacing: 6) {
                                Text(state.engine.title).font(.callout.weight(.semibold)).foregroundStyle(.primary)
                                if let reason = state.reason {
                                    Text(reason).font(.caption2.weight(.bold)).foregroundStyle(.secondary)
                                        .padding(.horizontal, 6).padding(.vertical, 1)
                                        .background(Capsule().fill(Color.primary.opacity(0.08)))
                                }
                            }
                            Text(state.detail).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                        }
                        Spacer(minLength: 0)
                        Image(systemName: selected ? "checkmark.circle.fill" : "circle")
                            .font(.system(size: 16))
                            .foregroundStyle(selected ? tint : Color.secondary.opacity(0.5))
                    }
                    .padding(.horizontal, 12).padding(.vertical, 9)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(
                        RoundedRectangle(cornerRadius: 10, style: .continuous)
                            .fill(selected ? tint.opacity(0.10) : Color(nsColor: .controlBackgroundColor))
                    )
                    .overlay(
                        RoundedRectangle(cornerRadius: 10, style: .continuous)
                            .strokeBorder(selected ? tint : Color.primary.opacity(0.10), lineWidth: selected ? 1.5 : 1)
                    )
                    .opacity(state.available ? 1 : 0.5)
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
                .disabled(!state.available)
                .help(state.available ? state.detail : "\(state.engine.title)：\(state.reason ?? "無法使用")")
            }
        }
    }
}

/// 「轉換引擎」整組（含 Office 自動化權限的說明）。
struct OTEngineGroup: View {
    @Binding var selection: OTEngine
    let app: OTOfficeApp
    let support: OTSupport

    var body: some View {
        let states = OTEngines.states(app: app, support: support)
        let effective = OTEngines.candidates(selection, app: app, support: support).first
        OptionGroup(title: "轉換引擎", systemImage: "gearshape.2") {
            OTEngineCards(selection: $selection, states: states)
            if effective == .office {
                OptionNote(text: "會在背景開啟 \(app.appName) 轉換副本，不會動到你開著的文件。第一次使用時，macOS 會詢問是否允許 QUICK_SEEMD 控制 \(app.shortName)，請按「好」。",
                           systemImage: "lock.shield")
            } else if selection != .auto && !states.contains(where: { $0.engine == selection && $0.available }) {
                OptionNote(text: "目前選的引擎無法處理這些檔案，請改選其他引擎。", systemImage: "exclamationmark.triangle")
            }
        }
    }
}

/// 讓 ToolSession 的 @Published 在選擇不可用時自動回到「自動」。
enum OTEngineFix {
    static func valid(_ engine: OTEngine, app: OTOfficeApp, support: OTSupport) -> OTEngine {
        if engine == .auto { return .auto }
        return OTEngines.candidates(engine, app: app, support: support).isEmpty ? .auto : engine
    }
}
