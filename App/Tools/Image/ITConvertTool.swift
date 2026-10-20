import ImageIO
import SwiftUI

// MARK: - 轉換圖片格式

struct ITConvertOptions: Sendable {
    var format: ITFormat = .jpeg
    var quality: Double = 0.9
    var background: ITRGBA = .white
    var keepMetadata = true
}

extension ImageToolCatalog {
    static let convert = ToolDefinition(
        id: "image.convert",
        category: .image,
        title: "轉換圖片格式",
        subtitle: "在 JPG、PNG、HEIC、AVIF、TIFF、GIF、ICO、PDF 等格式間互轉，可調品質與透明背景顏色。",
        symbol: "arrow.triangle.2.circlepath",
        badge: "JPG",
        inputTypes: [.image],
        inputLabel: "圖片",
        actionTitle: "轉換圖片",
        keywords: ["convert", "format", "jpg", "jpeg", "png", "heic", "avif", "webp", "tiff", "gif", "bmp", "ico", "icns", "pdf", "格式", "轉檔"],
        quickActions: [
            "image.to-jpg": { ITConvertSession.job(ITConvertOptions(format: .jpeg)) },
            "image.to-png": { ITConvertSession.job(ITConvertOptions(format: .png)) },
            "image.to-heic": { ITConvertSession.job(ITConvertOptions(format: .heic, quality: 0.85)) },
        ],
        makeSession: { ITConvertSession() }
    )
}

final class ITConvertSession: ToolSession {
    @Published var format: ITFormat = .jpeg
    @Published var quality = 0.9
    @Published var background: ITColorChoice = .white
    @Published var customColor = Color(red: 0.96, green: 0.94, blue: 0.88)
    @Published var keepMetadata = true
    /// 選取的檔案裡有透明背景的圖片
    @Published var hasTransparency = false

    /// 這台 Mac 能寫出的格式（WebP 只有系統支援時才出現）。
    static let formats: [ITFormat] = [.jpeg, .png, .heic, .avif, .tiff, .gif, .bmp, .webp, .ico, .icns, .pdf].filter(\.isWritable)

    override func filesDidChange(_ files: [URL]) {
        hasTransparency = files.prefix(50).contains { url in
            guard let src = CGImageSourceCreateWithURL(url as CFURL, nil),
                  let p = CGImageSourceCopyPropertiesAtIndex(src, CGImageSourceGetPrimaryImageIndex(src), nil) as? [CFString: Any] else { return false }
            return (p[kCGImagePropertyHasAlpha] as? Bool) ?? false
        }
    }

    override var actionTitleOverride: String? { "轉成 \(format.title)" }

    override func optionsView() -> AnyView { AnyView(ITConvertOptionsView(session: self)) }

    override func makeJob() -> ToolJob {
        Self.job(ITConvertOptions(format: format, quality: quality, background: background.resolve(custom: customColor),
                                  keepMetadata: keepMetadata))
    }

    nonisolated static func job(_ o: ITConvertOptions) -> ToolJob {
        .perFile { file, context in
            if o.format == .ico || o.format == .icns {
                // 圖示：每個尺寸各自以 Lanczos 縮放，ICNS 含 Retina 尺寸
                let icon = ITIconOptions(output: o.format == .ico ? .ico : .icns, shape: .original, padding: 0, background: .clear)
                return [try await ITMakeIconSession.make(file, icon, context: context)]
            }
            return [try ITPipeline.run(file, context: context, tag: nil, output: { source in
                ITOutput(format: o.format, quality: o.quality, metadata: o.keepMetadata ? .keep : .removeAll,
                         background: o.background, keepsAlpha: source.hasAlpha)
            }, transform: { image, _ in image })]
        }
    }

    static func hint(for f: ITFormat) -> String {
        switch f {
        case .jpeg: return "最通用的相片格式，檔案小，不支援透明。"
        case .png: return "無損、支援透明，適合截圖、圖表與標誌。"
        case .heic: return "Apple 裝置的高效率格式，同畫質約為 JPG 的一半大小。"
        case .avif: return "新一代網頁格式，壓縮率很高，較新的瀏覽器才支援。"
        case .tiff: return "無損（LZW 壓縮），適合印刷與後製。"
        case .gif: return "最多 256 色；動畫 GIF 會保留動畫。"
        case .bmp: return "未壓縮的點陣圖，給舊程式使用。"
        case .webp: return "網頁常用格式。"
        case .ico: return "Windows／網站圖示，自動產生 16–256 px 多種尺寸。"
        case .icns: return "macOS 圖示，自動產生 16–1024 px 多種尺寸。"
        case .pdf: return "每張圖片一頁，依圖片解析度決定頁面大小。"
        }
    }
}

private struct ITConvertOptionsView: View {
    @ObservedObject var session: ITConvertSession

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            OptionGroup(title: "轉成", systemImage: "photo.on.rectangle.angled") {
                ChoiceChips(selection: $session.format, choices: ITConvertSession.formats.map {
                    OptionChoice(value: $0, title: $0.title, subtitle: ITConvertSession.hint(for: $0))
                })
                OptionNote(text: ITConvertSession.hint(for: session.format), systemImage: "lightbulb")
            }
            if session.format.isLossy {
                OptionGroup(title: "品質", systemImage: "dial.medium") {
                    OptionSlider(title: "壓縮品質", value: $session.quality, range: 0.3...1, step: 0.05, format: ITOptionFormat.quality)
                    Text(session.quality >= 0.9 ? "幾乎看不出差異，檔案較大" : session.quality >= 0.7 ? "畫質與大小的平衡點" : "檔案最小，細節會變模糊")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }
            if !session.format.supportsAlpha {
                OptionGroup(title: "透明部分的底色", systemImage: "square.on.square.dashed") {
                    ITColorPicker(choice: $session.background, custom: $session.customColor)
                    if !session.hasTransparency {
                        Text("目前的圖片沒有透明背景，不會用到這個設定。").font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
            OptionGroup(title: "相片資訊", systemImage: "info.circle") {
                OptionToggle(title: "保留 EXIF 等相片資訊", subtitle: "拍攝日期、相機、位置等；關閉可保護隱私", isOn: $session.keepMetadata)
                if !session.format.supportsMetadata {
                    Text("\(session.format.title) 格式無法保存相片資訊。").font(.caption).foregroundStyle(.secondary)
                }
            }
        }
    }
}
