import AppKit
import SwiftUI

// 圖片工具共用的選項元件（顏色、九宮格位置、數字欄位）。

// MARK: - 顏色選擇

enum ITColorChoice: String, CaseIterable, Sendable {
    case transparent, white, black, custom

    var title: String {
        switch self {
        case .transparent: return "透明"
        case .white: return "白色"
        case .black: return "黑色"
        case .custom: return "自訂"
        }
    }

    var symbol: String {
        switch self {
        case .transparent: return "checkerboard.rectangle"
        case .white: return "circle"
        case .black: return "circle.fill"
        case .custom: return "paintpalette"
        }
    }

    func resolve(custom: Color) -> ITRGBA {
        switch self {
        case .transparent: return .clear
        case .white: return .white
        case .black: return .black
        case .custom: return ITRGBA(swiftUI: custom)
        }
    }
}

extension ITRGBA {
    /// SwiftUI 顏色 → sRGB 數值。
    init(swiftUI color: Color) {
        let ns = NSColor(color).usingColorSpace(.sRGB) ?? .white
        self.init(red: Double(ns.redComponent), green: Double(ns.greenComponent), blue: Double(ns.blueComponent),
                  alpha: Double(ns.alphaComponent))
    }
}

/// 顏色膠囊＋自訂色盤。
struct ITColorPicker: View {
    @Binding var choice: ITColorChoice
    @Binding var custom: Color
    var options: [ITColorChoice] = [.white, .black, .custom]

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            ChoiceChips(selection: $choice, choices: options.map {
                OptionChoice(value: $0, title: $0.title, systemImage: $0.symbol)
            })
            if choice == .custom {
                ColorPicker(selection: $custom, supportsOpacity: false) {
                    Text("選擇顏色").font(.callout)
                }
            }
        }
    }
}

// MARK: - 九宮格位置

enum ITPosition: Int, CaseIterable, Sendable {
    case topLeft, top, topRight, left, center, right, bottomLeft, bottom, bottomRight

    /// 0 左、1 中、2 右
    var column: Int { rawValue % 3 }
    /// 0 上、1 中、2 下
    var row: Int { rawValue / 3 }

    var title: String {
        ["左上", "上方", "右上", "左側", "正中央", "右側", "左下", "下方", "右下"][rawValue]
    }
}

struct ITPositionPicker: View {
    @Binding var position: ITPosition
    @Environment(\.toolTint) private var tint

    var body: some View {
        Grid(horizontalSpacing: 4, verticalSpacing: 4) {
            ForEach(0..<3, id: \.self) { row in
                GridRow {
                    ForEach(0..<3, id: \.self) { column in
                        cell(ITPosition(rawValue: row * 3 + column) ?? .center)
                    }
                }
            }
        }
        .padding(6)
        .background(RoundedRectangle(cornerRadius: 9, style: .continuous).fill(Color.primary.opacity(0.05)))
    }

    private func cell(_ p: ITPosition) -> some View {
        let selected = p == position
        return Button { position = p } label: {
            RoundedRectangle(cornerRadius: 5, style: .continuous)
                .fill(selected ? tint : Color(nsColor: .controlBackgroundColor))
                .overlay(RoundedRectangle(cornerRadius: 5, style: .continuous)
                    .strokeBorder(selected ? Color.clear : Color.primary.opacity(0.12)))
                .overlay(Circle().fill(selected ? Color.white : Color.secondary.opacity(0.45)).frame(width: 6, height: 6))
                .frame(width: 38, height: 26)
                .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .help(p.title)
        .accessibilityLabel(p.title)
    }
}

// MARK: - 數字欄位

struct ITNumberField: View {
    let title: String
    @Binding var value: Int
    var unit: String = "px"

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title).font(.caption).foregroundStyle(.secondary)
            HStack(spacing: 4) {
                TextField(title, value: $value, format: .number.grouping(.never))
                    .labelsHidden()
                    .textFieldStyle(.roundedBorder)
                    .multilineTextAlignment(.trailing)
                Text(unit).font(.caption).foregroundStyle(.secondary)
            }
        }
    }
}

/// 一行「標題　數值」的小資訊列。
struct ITInfoRow: View {
    let title: String
    let value: String
    var systemImage: String? = nil

    var body: some View {
        HStack(spacing: 6) {
            if let systemImage { Image(systemName: systemImage).foregroundStyle(.secondary) }
            Text(title).foregroundStyle(.secondary)
            Spacer(minLength: 8)
            Text(value).monospacedDigit().fontWeight(.medium)
        }
        .font(.callout)
    }
}

enum ITOptionFormat {
    static func percent(_ v: Double) -> String { "\(Int(v.rounded()))%" }
    static func quality(_ v: Double) -> String { "\(Int((v * 100).rounded()))%" }
}
