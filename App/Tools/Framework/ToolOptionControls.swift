import SwiftUI

// 各工具右側選項面板共用的元件：大塊、好點、清楚標示目前選擇。

private struct ToolTintKey: EnvironmentKey { static let defaultValue: Color = .accentColor }

extension EnvironmentValues {
    /// 目前工具的主題色（工具頁會設定）。
    var toolTint: Color {
        get { self[ToolTintKey.self] }
        set { self[ToolTintKey.self] = newValue }
    }
}

/// 一組選項：小標題＋內容。
struct OptionGroup<Content: View>: View {
    let title: String
    var systemImage: String? = nil
    @ViewBuilder var content: Content

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 6) {
                if let systemImage { Image(systemName: systemImage).foregroundStyle(.secondary) }
                Text(title).font(.subheadline.weight(.semibold)).foregroundStyle(.secondary)
            }
            content
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }
}

/// 一個選擇項目。
struct OptionChoice<Value: Hashable>: Identifiable {
    let value: Value
    let title: String
    var subtitle: String? = nil
    var systemImage: String? = nil
    var badge: String? = nil
    var id: Value { value }
}

/// 直式選項卡片（例如壓縮程度：極度 / 建議 / 低度）。
struct ChoiceCards<Value: Hashable>: View {
    @Binding var selection: Value
    let choices: [OptionChoice<Value>]
    @Environment(\.toolTint) private var tint

    var body: some View {
        VStack(spacing: 8) {
            ForEach(choices) { choice in
                let selected = choice.value == selection
                Button { selection = choice.value } label: {
                    HStack(alignment: .center, spacing: 12) {
                        if let icon = choice.systemImage {
                            Image(systemName: icon)
                                .font(.system(size: 17, weight: .medium))
                                .foregroundStyle(selected ? tint : .secondary)
                                .frame(width: 26)
                        }
                        VStack(alignment: .leading, spacing: 2) {
                            HStack(spacing: 6) {
                                Text(choice.title).font(.callout.weight(.semibold)).foregroundStyle(.primary)
                                if let badge = choice.badge {
                                    Text(badge).font(.caption2.weight(.bold)).foregroundStyle(.white)
                                        .padding(.horizontal, 6).padding(.vertical, 1)
                                        .background(Capsule().fill(tint))
                                }
                            }
                            if let sub = choice.subtitle {
                                Text(sub).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                            }
                        }
                        Spacer(minLength: 0)
                        Image(systemName: selected ? "checkmark.circle.fill" : "circle")
                            .font(.system(size: 16))
                            .foregroundStyle(selected ? tint : Color.secondary.opacity(0.5))
                    }
                    .padding(.horizontal, 12).padding(.vertical, 10)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(
                        RoundedRectangle(cornerRadius: 10, style: .continuous)
                            .fill(selected ? tint.opacity(0.10) : Color(nsColor: .controlBackgroundColor))
                    )
                    .overlay(
                        RoundedRectangle(cornerRadius: 10, style: .continuous)
                            .strokeBorder(selected ? tint : Color.primary.opacity(0.10), lineWidth: selected ? 1.5 : 1)
                    )
                    .contentShape(Rectangle())
                }
                .buttonStyle(.plain)
            }
        }
    }
}

/// 橫向可換行的小膠囊選項（例如輸出格式）。
struct ChoiceChips<Value: Hashable>: View {
    @Binding var selection: Value
    let choices: [OptionChoice<Value>]
    @Environment(\.toolTint) private var tint

    var body: some View {
        FlowLayout(spacing: 6) {
            ForEach(choices) { choice in
                let selected = choice.value == selection
                Button { selection = choice.value } label: {
                    HStack(spacing: 4) {
                        if let icon = choice.systemImage { Image(systemName: icon) }
                        Text(choice.title)
                    }
                    .font(.callout.weight(selected ? .semibold : .regular))
                    .padding(.horizontal, 11).padding(.vertical, 5)
                    .foregroundStyle(selected ? Color.white : Color.primary)
                    .background(Capsule().fill(selected ? tint : Color(nsColor: .controlBackgroundColor)))
                    .overlay(Capsule().strokeBorder(selected ? Color.clear : Color.primary.opacity(0.12)))
                    .contentShape(Capsule())
                }
                .buttonStyle(.plain)
                .help(choice.subtitle ?? "")
            }
        }
    }
}

/// 分段選擇（2–4 個選項時使用）。
struct SegmentedOption<Value: Hashable>: View {
    @Binding var selection: Value
    let choices: [OptionChoice<Value>]

    var body: some View {
        Picker("", selection: $selection) {
            ForEach(choices) { Text($0.title).tag($0.value) }
        }
        .pickerStyle(.segmented)
        .labelsHidden()
    }
}

/// 有標題與數值顯示的滑桿。
struct OptionSlider: View {
    let title: String
    @Binding var value: Double
    let range: ClosedRange<Double>
    var step: Double = 1
    var format: (Double) -> String = { "\(Int($0))" }
    @Environment(\.toolTint) private var tint

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(title).font(.callout)
                Spacer()
                Text(format(value)).font(.callout.monospacedDigit().weight(.semibold)).foregroundStyle(tint)
            }
            Slider(value: $value, in: range, step: step).tint(tint)
        }
    }
}

/// 開關＋說明。
struct OptionToggle: View {
    let title: String
    var subtitle: String? = nil
    @Binding var isOn: Bool
    @Environment(\.toolTint) private var tint

    var body: some View {
        Toggle(isOn: $isOn) {
            VStack(alignment: .leading, spacing: 2) {
                Text(title).font(.callout)
                if let subtitle { Text(subtitle).font(.caption).foregroundStyle(.secondary) }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
        .toggleStyle(.switch)
        .tint(tint)
    }
}

/// 單行文字輸入（浮水印文字、密碼…）。
struct OptionTextField: View {
    let title: String
    @Binding var text: String
    var placeholder: String = ""
    var secure = false

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title).font(.callout)
            Group {
                if secure { SecureField(placeholder, text: $text) } else { TextField(placeholder, text: $text) }
            }
            .textFieldStyle(.roundedBorder)
        }
    }
}

/// 頁碼範圍輸入：例如「1-3, 5, 8-」，空白代表全部。
struct PageRangeField: View {
    let title: String
    @Binding var text: String
    var pageCount: Int?

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title).font(.callout)
            TextField("全部頁面", text: $text).textFieldStyle(.roundedBorder)
            HStack(spacing: 4) {
                if let count = pageCount {
                    if PageRange.parse(text, pageCount: count) == nil {
                        Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.orange)
                        Text("範圍不正確（共 \(count) 頁）").foregroundStyle(.orange)
                    } else {
                        Text("例如：1-3, 5, 8-　共 \(count) 頁").foregroundStyle(.secondary)
                    }
                } else {
                    Text("例如：1-3, 5, 8-").foregroundStyle(.secondary)
                }
            }
            .font(.caption)
        }
    }
}

/// 小提示框。
struct OptionNote: View {
    let text: String
    var systemImage = "info.circle"

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            Image(systemName: systemImage).foregroundStyle(.secondary)
            Text(text).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
        }
        .padding(10)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(RoundedRectangle(cornerRadius: 8).fill(Color.primary.opacity(0.05)))
    }
}

/// 會自動換行的水平排列（可置中）。
struct FlowLayout: Layout {
    var spacing: CGFloat = 6
    var centered = false

    private func rows(_ subviews: Subviews, width: CGFloat) -> [(indices: [Int], width: CGFloat, height: CGFloat)] {
        var rows: [(indices: [Int], width: CGFloat, height: CGFloat)] = []
        var current: [Int] = []
        var x: CGFloat = 0, h: CGFloat = 0
        for (i, v) in subviews.enumerated() {
            let s = v.sizeThatFits(.unspecified)
            if !current.isEmpty && x + s.width > width {
                rows.append((current, x - spacing, h))
                current = []; x = 0; h = 0
            }
            current.append(i)
            x += s.width + spacing
            h = max(h, s.height)
        }
        if !current.isEmpty { rows.append((current, x - spacing, h)) }
        return rows
    }

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let width = proposal.width ?? 320
        let r = rows(subviews, width: width)
        let height = r.reduce(0) { $0 + $1.height } + spacing * CGFloat(max(r.count - 1, 0))
        return CGSize(width: proposal.width ?? (r.map(\.width).max() ?? 0), height: height)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var y = bounds.minY
        for row in rows(subviews, width: bounds.width) {
            var x = bounds.minX + (centered ? max(0, (bounds.width - row.width) / 2) : 0)
            for i in row.indices {
                let s = subviews[i].sizeThatFits(.unspecified)
                subviews[i].place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(s))
                x += s.width + spacing
            }
            y += row.height + spacing
        }
    }
}
