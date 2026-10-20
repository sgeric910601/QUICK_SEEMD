import AppKit
import SwiftUI

// MARK: - 背景材質

/// 視窗後方模糊。
struct CVVisualEffectBackground: NSViewRepresentable {
    var material: NSVisualEffectView.Material = .underWindowBackground
    var blending: NSVisualEffectView.BlendingMode = .behindWindow

    func makeNSView(context: Context) -> NSVisualEffectView {
        let view = NSVisualEffectView()
        view.material = material
        view.blendingMode = blending
        view.state = .followsWindowActiveState
        view.isEmphasized = false
        return view
    }

    func updateNSView(_ view: NSVisualEffectView, context: Context) {
        view.material = material
        view.blendingMode = blending
    }
}

/// 半透明圓角卡片。
struct CVCardModifier: ViewModifier {
    var radius: CGFloat = 14
    var padding: CGFloat = 14
    @Environment(\.colorScheme) private var scheme

    func body(content: Content) -> some View {
        content
            .padding(padding)
            .background {
                RoundedRectangle(cornerRadius: radius, style: .continuous)
                    .fill(.regularMaterial)
                    .shadow(color: .black.opacity(scheme == .dark ? 0.25 : 0.06), radius: 10, y: 3)
            }
            .overlay {
                RoundedRectangle(cornerRadius: radius, style: .continuous)
                    .strokeBorder(scheme == .dark ? Color.white.opacity(0.08) : Color.black.opacity(0.06), lineWidth: 1)
            }
    }
}

extension View {
    func cvCardStyle(radius: CGFloat = 14, padding: CGFloat = 14) -> some View {
        modifier(CVCardModifier(radius: radius, padding: padding))
    }
}

// MARK: - 自動換行排列

/// 依寬度自動換行的排列方式（格式按鈕用）。
struct CVFlowLayout: Layout {
    var spacing: CGFloat = 8
    var lineSpacing: CGFloat = 8

    func sizeThatFits(proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) -> CGSize {
        let maxWidth = proposal.width ?? .infinity
        var x: CGFloat = 0, y: CGFloat = 0, lineHeight: CGFloat = 0, widest: CGFloat = 0
        for view in subviews {
            let size = view.sizeThatFits(.unspecified)
            if x > 0, x + size.width > maxWidth {
                y += lineHeight + lineSpacing
                x = 0
                lineHeight = 0
            }
            x += size.width + spacing
            widest = max(widest, x - spacing)
            lineHeight = max(lineHeight, size.height)
        }
        return CGSize(width: proposal.width ?? widest, height: y + lineHeight)
    }

    func placeSubviews(in bounds: CGRect, proposal: ProposedViewSize, subviews: Subviews, cache: inout ()) {
        var x = bounds.minX, y = bounds.minY, lineHeight: CGFloat = 0
        for view in subviews {
            let size = view.sizeThatFits(.unspecified)
            if x > bounds.minX, x + size.width > bounds.maxX {
                y += lineHeight + lineSpacing
                x = bounds.minX
                lineHeight = 0
            }
            view.place(at: CGPoint(x: x, y: y), proposal: ProposedViewSize(size))
            x += size.width + spacing
            lineHeight = max(lineHeight, size.height)
        }
    }
}

// MARK: - 格式按鈕

struct CVFormatChip: View {
    let format: ConversionFormat
    let isSelected: Bool
    let isPartial: Bool
    let action: () -> Void
    @State private var hovering = false
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        Button(action: action) {
            VStack(alignment: .leading, spacing: 1) {
                HStack(spacing: 5) {
                    Text(format.displayName)
                        .font(.system(size: 12.5, weight: .semibold))
                        .lineLimit(1)
                    if format.backend != .native {
                        Text(format.backend.displayName)
                            .font(.system(size: 9, weight: .bold))
                            .padding(.horizontal, 4)
                            .padding(.vertical, 1)
                            .background(Capsule().fill(isSelected ? Color.white.opacity(0.25) : Color.orange.opacity(0.18)))
                            .foregroundStyle(isSelected ? Color.white : Color.orange)
                    }
                }
                if let note = format.note {
                    Text(note)
                        .font(.system(size: 10))
                        .lineLimit(1)
                        .foregroundStyle(isSelected ? Color.white.opacity(0.85) : Color.secondary)
                }
            }
            .padding(.horizontal, 11)
            .padding(.vertical, format.note == nil ? 7 : 5)
            .frame(minWidth: 64, alignment: .leading)
            .foregroundStyle(isSelected ? Color.white : Color.primary)
            .background {
                RoundedRectangle(cornerRadius: 9, style: .continuous)
                    .fill(isSelected ? AnyShapeStyle(Color.accentColor.gradient)
                          : AnyShapeStyle(hovering ? Color.primary.opacity(scheme == .dark ? 0.14 : 0.08) : Color.primary.opacity(scheme == .dark ? 0.07 : 0.04)))
            }
            .overlay {
                RoundedRectangle(cornerRadius: 9, style: .continuous)
                    .strokeBorder(isPartial && !isSelected ? Color.accentColor : Color.primary.opacity(isSelected ? 0 : 0.08),
                                  style: StrokeStyle(lineWidth: isPartial && !isSelected ? 1.5 : 1, dash: isPartial && !isSelected ? [4, 3] : []))
            }
            .shadow(color: isSelected ? Color.accentColor.opacity(0.35) : .clear, radius: 5, y: 2)
            .contentShape(RoundedRectangle(cornerRadius: 9, style: .continuous))
        }
        .buttonStyle(.plain)
        .onHover { hovering = $0 }
        .animation(.easeOut(duration: 0.12), value: hovering)
        .help(format.backend == .native ? "轉換成 \(format.displayName)" : "轉換成 \(format.displayName)（使用 \(format.backend.displayName)）")
        .accessibilityAddTraits(isSelected ? .isSelected : [])
    }
}

// MARK: - 拖放區

struct CVDropZone: View {
    let isTargeted: Bool
    let compact: Bool
    let action: () -> Void
    @State private var hovering = false

    var body: some View {
        Button(action: action) {
            Group {
                if compact { compactBody } else { largeBody }
            }
            .frame(maxWidth: .infinity, maxHeight: compact ? nil : .infinity)
            .background {
                RoundedRectangle(cornerRadius: compact ? 14 : 22, style: .continuous)
                    .fill(Color.accentColor.opacity(isTargeted ? 0.16 : (hovering ? 0.08 : 0.04)))
            }
            .overlay {
                RoundedRectangle(cornerRadius: compact ? 14 : 22, style: .continuous)
                    .strokeBorder(isTargeted ? Color.accentColor : Color.secondary.opacity(hovering ? 0.55 : 0.35),
                                  style: StrokeStyle(lineWidth: isTargeted ? 2.5 : 1.5, dash: [7, 5]))
            }
            .scaleEffect(isTargeted ? 1.01 : 1)
            .contentShape(Rectangle())
        }
        .buttonStyle(.plain)
        .onHover { hovering = $0 }
        .animation(.spring(response: 0.3, dampingFraction: 0.75), value: isTargeted)
        .animation(.easeOut(duration: 0.15), value: hovering)
        .accessibilityLabel("加入檔案")
    }

    private var largeBody: some View {
        VStack(spacing: 18) {
            ZStack {
                Circle()
                    .fill(Color.accentColor.opacity(0.12))
                    .frame(width: 96, height: 96)
                Image(systemName: isTargeted ? "arrow.down.circle.fill" : "square.and.arrow.down.on.square")
                    .font(.system(size: 40, weight: .light))
                    .foregroundStyle(Color.accentColor)
                    .symbolRenderingMode(.hierarchical)
            }
            VStack(spacing: 6) {
                Text(isTargeted ? "放開以加入檔案" : "把檔案或資料夾拖到這裡")
                    .font(.system(size: 20, weight: .semibold))
                Text("或按一下選擇檔案。也可以把檔案拖到 Dock 上的 QUICK_SEEMD 圖示。")
                    .font(.system(size: 13))
                    .foregroundStyle(.secondary)
            }
            CVFlowLayout(spacing: 6, lineSpacing: 6) {
                ForEach(CVDropZone.highlights, id: \.0) { item in
                    Label(item.0, systemImage: item.1)
                        .font(.system(size: 11.5, weight: .medium))
                        .padding(.horizontal, 9)
                        .padding(.vertical, 5)
                        .background(Capsule().fill(Color.primary.opacity(0.06)))
                        .foregroundStyle(.secondary)
                }
            }
            .frame(maxWidth: 520)
            .fixedSize(horizontal: false, vertical: true)
        }
        .padding(32)
    }

    static let highlights: [(String, String)] = [
        ("圖片", "photo"), ("PDF", "doc.text"), ("Word／RTF", "doc.richtext"), ("Markdown", "text.badge.checkmark"),
        ("影片", "film"), ("音訊", "waveform"), ("JSON／CSV／Excel", "tablecells"), ("文字編碼", "doc.plaintext"),
        ("ZIP／7z／DMG", "archivebox"),
    ]

    private var compactBody: some View {
        HStack(spacing: 12) {
            Image(systemName: isTargeted ? "arrow.down.circle.fill" : "plus.circle.fill")
                .font(.system(size: 24))
                .foregroundStyle(Color.accentColor)
                .symbolRenderingMode(.hierarchical)
            VStack(alignment: .leading, spacing: 2) {
                Text(isTargeted ? "放開以加入" : "加入更多檔案")
                    .font(.system(size: 13, weight: .semibold))
                Text("拖放到這裡，或按一下選擇")
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
            }
            Spacer(minLength: 0)
        }
        .padding(.horizontal, 14)
        .padding(.vertical, 11)
    }
}

// MARK: - 檔案列

struct CVJobRow: View {
    let job: ConverterJob
    let isSelected: Bool
    let onSelect: (_ extend: Bool) -> Void
    let onRemove: () -> Void
    let onReveal: ([URL]) -> Void
    let onOpen: (URL) -> Void
    @State private var hovering = false
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        HStack(alignment: .top, spacing: 11) {
            Image(nsImage: job.icon)
                .resizable()
                .interpolation(.high)
                .aspectRatio(contentMode: .fit)
                .clipShape(RoundedRectangle(cornerRadius: job.hasThumbnail ? 5 : 0, style: .continuous))
                .shadow(color: .black.opacity(job.hasThumbnail ? 0.18 : 0), radius: 1.5, y: 0.5)
                .frame(width: 38, height: 38)
                .padding(.top, 1)
            VStack(alignment: .leading, spacing: 4) {
                HStack(spacing: 6) {
                    Text(job.name)
                        .font(.system(size: 13, weight: .medium))
                        .lineLimit(1)
                        .truncationMode(.middle)
                        .help(job.url.path)
                    Spacer(minLength: 4)
                    targetPill
                }
                Text(metaLine)
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
                    .lineLimit(1)
                    .truncationMode(.tail)
                statusLine
            }
        }
        .padding(.horizontal, 11)
        .padding(.vertical, 9)
        .background {
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .fill(isSelected ? Color.accentColor.opacity(scheme == .dark ? 0.22 : 0.13)
                      : (hovering ? Color.primary.opacity(0.05) : Color.primary.opacity(scheme == .dark ? 0.03 : 0.015)))
        }
        .overlay {
            RoundedRectangle(cornerRadius: 12, style: .continuous)
                .strokeBorder(isSelected ? Color.accentColor.opacity(0.55) : Color.primary.opacity(0.06), lineWidth: 1)
        }
        .overlay(alignment: .topTrailing) {
            if hovering && !isRunning {
                Button(action: onRemove) {
                    Image(systemName: "xmark.circle.fill")
                        .font(.system(size: 15))
                        .symbolRenderingMode(.palette)
                        .foregroundStyle(.white, Color.secondary.opacity(0.8))
                }
                .buttonStyle(.plain)
                .offset(x: 5, y: -5)
                .help("從清單移除")
                .transition(.opacity)
            }
        }
        .contentShape(RoundedRectangle(cornerRadius: 12, style: .continuous))
        .onTapGesture {
            let flags = NSEvent.modifierFlags
            onSelect(flags.contains(.command) || flags.contains(.shift))
        }
        .onHover { hovering = $0 }
        .animation(.easeOut(duration: 0.12), value: hovering)
        .contextMenu {
            if case .done(let urls) = job.status {
                Button("在 Finder 中顯示") { onReveal(urls) }
                if let first = urls.first { Button("打開") { onOpen(first) } }
                Divider()
            }
            Button("在 Finder 中顯示原始檔") { onReveal([job.url]) }
            Button("從清單移除", role: .destructive, action: onRemove).disabled(isRunning)
        }
    }

    private var isRunning: Bool {
        switch job.status {
        case .running, .queued: return true
        default: return false
        }
    }

    private var metaLine: String {
        var parts: [String] = []
        if !job.sizeText.isEmpty { parts.append(job.sizeText) }
        if !job.kind.isEmpty { parts.append(job.kind) }
        if let d = job.detail { parts.append(d) }
        return parts.joined(separator: " · ")
    }

    @ViewBuilder private var targetPill: some View {
        if job.isAnalyzing {
            ProgressView().controlSize(.mini)
        } else if let target = job.target {
            HStack(spacing: 3) {
                Image(systemName: "arrow.right")
                    .font(.system(size: 9, weight: .bold))
                Text(target.displayName)
                    .font(.system(size: 11, weight: .semibold))
                    .lineLimit(1)
            }
            .padding(.horizontal, 8)
            .padding(.vertical, 3)
            .background(Capsule().fill(Color.accentColor.opacity(0.15)))
            .foregroundStyle(Color.accentColor)
        } else if job.targets.isEmpty {
            Text("無法轉換")
                .font(.system(size: 11, weight: .medium))
                .padding(.horizontal, 8)
                .padding(.vertical, 3)
                .background(Capsule().fill(Color.secondary.opacity(0.12)))
                .foregroundStyle(.secondary)
        } else {
            Text("選擇格式")
                .font(.system(size: 11, weight: .medium))
                .padding(.horizontal, 8)
                .padding(.vertical, 3)
                .overlay(Capsule().strokeBorder(Color.secondary.opacity(0.45), style: StrokeStyle(lineWidth: 1, dash: [3, 2])))
                .foregroundStyle(.secondary)
        }
    }

    @ViewBuilder private var statusLine: some View {
        switch job.status {
        case .idle:
            EmptyView()
        case .queued:
            Label("等待中…", systemImage: "clock")
                .font(.system(size: 11))
                .foregroundStyle(.secondary)
        case .running(let p):
            HStack(spacing: 8) {
                ProgressView(value: max(0.02, p))
                    .progressViewStyle(.linear)
                    .controlSize(.small)
                Text(p > 0 ? "\(Int(p * 100))%" : "轉換中…")
                    .font(.system(size: 11).monospacedDigit())
                    .foregroundStyle(.secondary)
                    .frame(minWidth: 34, alignment: .trailing)
            }
        case .done(let urls):
            HStack(spacing: 6) {
                Image(systemName: "checkmark.circle.fill").foregroundStyle(.green)
                Text(urls.count == 1 ? "完成：\(urls[0].lastPathComponent)" : "完成：\(urls.count) 個項目")
                    .lineLimit(1)
                    .truncationMode(.middle)
                Spacer(minLength: 4)
                Button("在 Finder 中顯示") { onReveal(urls) }
                    .buttonStyle(.link)
                    .fixedSize()
            }
            .font(.system(size: 11))
        case .failed(let message):
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Image(systemName: "exclamationmark.triangle.fill").foregroundStyle(.orange)
                Text(message)
                    .foregroundStyle(.primary.opacity(0.85))
                    .lineLimit(4)
                    .fixedSize(horizontal: false, vertical: true)
                    .textSelection(.enabled)
            }
            .font(.system(size: 11))
        case .cancelled:
            Label("已取消", systemImage: "stop.circle")
                .font(.system(size: 11))
                .foregroundStyle(.secondary)
        }
    }
}

// MARK: - 外部引擎狀態

struct CVBackendPopover: View {
    let statuses: [ConversionBackendStatus]
    let onRefresh: () -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            HStack {
                Text("轉換引擎")
                    .font(.system(size: 14, weight: .semibold))
                Spacer()
                Button(action: onRefresh) {
                    Label("重新偵測", systemImage: "arrow.clockwise")
                }
                .controlSize(.small)
            }
            row(name: "macOS 內建", available: true, summary: ConversionBackendStatus(backend: .native, executable: nil).summary,
                detail: "ImageIO、PDFKit、AVFoundation、TextKit、afconvert、ditto、tar、hdiutil", hint: nil)
            Divider()
            Text("選用的外部工具（自行安裝後自動啟用）")
                .font(.system(size: 11))
                .foregroundStyle(.secondary)
            ForEach(statuses) { status in
                row(name: status.backend.displayName, available: status.isAvailable, summary: status.summary,
                    detail: status.executable?.path, hint: status.isAvailable ? nil : status.installHint)
            }
        }
        .padding(16)
        .frame(width: 380)
    }

    private func row(name: String, available: Bool, summary: String, detail: String?, hint: String?) -> some View {
        HStack(alignment: .top, spacing: 10) {
            Circle()
                .fill(available ? Color.green : Color.secondary.opacity(0.4))
                .frame(width: 8, height: 8)
                .padding(.top, 5)
            VStack(alignment: .leading, spacing: 3) {
                HStack(spacing: 6) {
                    Text(name).font(.system(size: 12.5, weight: .semibold))
                    Text(available ? "可用" : "未安裝")
                        .font(.system(size: 10, weight: .medium))
                        .foregroundStyle(available ? Color.green : Color.secondary)
                }
                Text(summary)
                    .font(.system(size: 11))
                    .foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                if let detail {
                    Text(detail)
                        .font(.system(size: 10).monospaced())
                        .foregroundStyle(.tertiary)
                        .lineLimit(2)
                        .textSelection(.enabled)
                }
                if let hint {
                    HStack(spacing: 6) {
                        Text(hint)
                            .font(.system(size: 10.5).monospaced())
                            .textSelection(.enabled)
                            .padding(.horizontal, 6)
                            .padding(.vertical, 2)
                            .background(RoundedRectangle(cornerRadius: 4).fill(Color.primary.opacity(0.06)))
                        Button {
                            NSPasteboard.general.clearContents()
                            NSPasteboard.general.setString(hint, forType: .string)
                        } label: {
                            Image(systemName: "doc.on.doc")
                        }
                        .buttonStyle(.borderless)
                        .controlSize(.small)
                        .help("拷貝安裝指令（需要 Homebrew）")
                    }
                }
            }
        }
    }
}
