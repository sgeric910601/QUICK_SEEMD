import AppKit
import CoreText
import SwiftUI
import UniformTypeIdentifiers

// MARK: - 加入浮水印

enum ITWatermarkKind: String, Sendable {
    case text, image
}

struct ITWatermarkOptions: Sendable {
    var kind: ITWatermarkKind = .text
    var text = "© 版權所有"
    /// 文字高度＝圖片短邊的百分比
    var textSize: Double = 5
    var bold = true
    var color: ITRGBA = .white
    var opacity: Double = 0.6
    /// 角度；正值＝逆時針（往右上斜）
    var rotation: Double = 0
    var position: ITPosition = .bottomRight
    var tiled = false
    var shadow = true
    var logo: URL?
    /// 標誌寬度＝圖片寬度的百分比
    var logoScale: Double = 20
}

extension ImageToolCatalog {
    static let watermark = ToolDefinition(
        id: "image.watermark",
        category: .image,
        title: "加入浮水印",
        subtitle: "在圖片加上文字或標誌浮水印：可調大小、顏色、透明度、角度，放在九宮格任一位置或整張平鋪。",
        symbol: "signature",
        tint: ToolPalette.audio,
        badge: "IMG",
        inputTypes: [.image],
        inputLabel: "圖片",
        actionTitle: "加入浮水印",
        keywords: ["watermark", "logo", "copyright", "stamp", "浮水印", "版權", "標誌", "商標"],
        makeSession: { ITWatermarkSession() }
    )
}

final class ITWatermarkSession: ToolSession {
    @Published var kind: ITWatermarkKind = .text
    @Published var text = "© 版權所有"
    @Published var textSize: Double = 5
    @Published var bold = true
    @Published var colorChoice: ITColorChoice = .white
    @Published var customColor = Color(red: 0.89, green: 0.2, blue: 0.18)
    @Published var opacity: Double = 0.6
    @Published var rotation: Double = 0
    @Published var position: ITPosition = .bottomRight
    @Published var tiled = false
    @Published var shadow = true
    @Published var logo: URL?
    @Published var logoScale: Double = 20

    override func validate(_ files: [URL]) -> String? {
        switch kind {
        case .text: return text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? "請輸入浮水印文字" : nil
        case .image: return logo == nil ? "請先選擇標誌圖片" : nil
        }
    }

    override func optionsView() -> AnyView { AnyView(ITWatermarkOptionsView(session: self)) }

    override func makeJob() -> ToolJob {
        Self.job(ITWatermarkOptions(kind: kind, text: text, textSize: textSize, bold: bold, color: colorChoice.resolve(custom: customColor),
                                    opacity: opacity, rotation: rotation, position: position, tiled: tiled, shadow: shadow,
                                    logo: logo, logoScale: logoScale))
    }

    func chooseLogo() {
        let panel = NSOpenPanel()
        panel.title = "選擇標誌圖片"
        panel.message = "建議使用背景透明的 PNG。"
        panel.prompt = "選擇"
        panel.allowedContentTypes = [.image]
        panel.allowsMultipleSelection = false
        if panel.runModal() == .OK, let url = panel.url { logo = url }
    }

    nonisolated static func job(_ o: ITWatermarkOptions) -> ToolJob {
        ToolJob { files, context in
            var logo: CGImage?
            if o.kind == .image {
                guard let url = o.logo else { throw ConversionError.other("請先選擇標誌圖片。") }
                logo = try ITLoader.load(url).image
            }
            var outputs: [URL] = []
            for (i, file) in files.enumerated() {
                try context.checkCancellation()
                let sub = context.segment(i, of: files.count)
                outputs.append(try ITPipeline.run(file, context: sub, tag: "浮水印", output: { source in
                    ITOutput(format: source.preferredOutputFormat, quality: 0.92, metadata: .keep, keepsAlpha: source.hasAlpha)
                }, transform: { image, _ in try apply(image, o, logo: logo) }))
                sub.progress(1)
            }
            return outputs
        }
    }

    /// 要蓋上去的「印章」：尺寸與繪製方式（以原點為中心）。
    private struct Stamp {
        let size: CGSize
        let draw: (CGContext) -> Void
    }

    nonisolated static func apply(_ image: CGImage, _ o: ITWatermarkOptions, logo: CGImage?) throws -> CGImage {
        let W = CGFloat(image.width), H = CGFloat(image.height)
        let short = min(W, H)
        let ctx = try ITRender.canvas(width: image.width, height: image.height, like: image, opaque: !CVImageConverter.hasAlpha(image))
        ctx.draw(image, in: CGRect(x: 0, y: 0, width: W, height: H))

        let stamp: Stamp
        var shadowScale = short * 0.02
        if o.kind == .image, let logo {
            let lw = max(4, W * o.logoScale / 100)
            let lh = lw * CGFloat(logo.height) / CGFloat(max(logo.width, 1))
            stamp = Stamp(size: CGSize(width: lw, height: lh)) { c in
                c.interpolationQuality = .high
                c.draw(logo, in: CGRect(x: -lw / 2, y: -lh / 2, width: lw, height: lh))
            }
        } else {
            let fontSize = max(6, short * o.textSize / 100)
            shadowScale = fontSize
            let font = CTFontCreateUIFontForLanguage(o.bold ? .emphasizedSystem : .system, fontSize, nil)
                ?? CTFontCreateWithName("Helvetica" as CFString, fontSize, nil)
            let attrs: [NSAttributedString.Key: Any] = [
                NSAttributedString.Key(kCTFontAttributeName as String): font,
                NSAttributedString.Key(kCTForegroundColorAttributeName as String): o.color.opacity(1).cgColor,
            ]
            let line = CTLineCreateWithAttributedString(NSAttributedString(string: o.text, attributes: attrs))
            var ascent: CGFloat = 0, descent: CGFloat = 0, leading: CGFloat = 0
            let width = CGFloat(CTLineGetTypographicBounds(line, &ascent, &descent, &leading))
            let height = ascent + descent
            stamp = Stamp(size: CGSize(width: width, height: height)) { c in
                c.textMatrix = .identity
                c.textPosition = CGPoint(x: -width / 2, y: -height / 2 + descent)
                CTLineDraw(line, c)
            }
        }
        guard stamp.size.width > 0, stamp.size.height > 0 else { return image }

        ctx.setAlpha(max(0.05, min(1, o.opacity)))
        if o.shadow && o.kind == .text {
            ctx.setShadow(offset: CGSize(width: 0, height: -shadowScale * 0.05), blur: shadowScale * 0.18,
                          color: CGColor(gray: o.color.red + o.color.green + o.color.blue > 1.5 ? 0 : 1, alpha: 0.5))
        }
        let angle = o.rotation * .pi / 180
        if o.tiled {
            let gapX = max(stamp.size.width * 0.6, short * 0.08)
            let stepX = stamp.size.width + gapX
            let stepY = stamp.size.height + max(stamp.size.height * 2.2, short * 0.08)
            let reach = (W * W + H * H).squareRoot() / 2 + max(stepX, stepY)
            ctx.translateBy(x: W / 2, y: H / 2)
            ctx.rotate(by: angle)
            var row = 0
            var y = -reach
            while y <= reach {
                var x = -reach + (row % 2 == 0 ? 0 : stepX / 2)
                while x <= reach {
                    ctx.saveGState()
                    ctx.translateBy(x: x, y: y)
                    stamp.draw(ctx)
                    ctx.restoreGState()
                    x += stepX
                }
                y += stepY
                row += 1
            }
        } else {
            let c = abs(cos(angle)), s = abs(sin(angle))
            let bw = stamp.size.width * c + stamp.size.height * s
            let bh = stamp.size.width * s + stamp.size.height * c
            let margin = short * 0.04
            let cx: CGFloat = [margin + bw / 2, W / 2, W - margin - bw / 2][o.position.column]
            let cy: CGFloat = [H - margin - bh / 2, H / 2, margin + bh / 2][o.position.row]
            ctx.translateBy(x: cx, y: cy)
            ctx.rotate(by: angle)
            stamp.draw(ctx)
        }
        guard let out = ctx.makeImage() else { throw ConversionError.other("無法合成浮水印。") }
        return out
    }
}

private struct ITWatermarkOptionsView: View {
    @ObservedObject var session: ITWatermarkSession

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            OptionGroup(title: "浮水印類型", systemImage: "signature") {
                SegmentedOption(selection: $session.kind, choices: [
                    OptionChoice(value: .text, title: "文字"),
                    OptionChoice(value: .image, title: "標誌圖片"),
                ])
            }
            if session.kind == .text { textOptions } else { logoOptions }
            OptionGroup(title: "外觀", systemImage: "circle.lefthalf.filled") {
                OptionSlider(title: "不透明度", value: $session.opacity, range: 0.1...1, step: 0.05, format: ITOptionFormat.quality)
                OptionSlider(title: "旋轉角度", value: $session.rotation, range: -90...90, step: 5, format: { "\(Int($0))°" })
                ChoiceChips(selection: $session.rotation, choices: [
                    OptionChoice(value: 0, title: "水平"),
                    OptionChoice(value: 45, title: "斜 45°"),
                    OptionChoice(value: -45, title: "斜 −45°"),
                    OptionChoice(value: 90, title: "直立"),
                ])
                if session.kind == .text {
                    OptionToggle(title: "加上陰影", subtitle: "在任何背景上都清楚可見", isOn: $session.shadow)
                }
            }
            OptionGroup(title: "位置", systemImage: "square.grid.3x3") {
                SegmentedOption(selection: $session.tiled, choices: [
                    OptionChoice(value: false, title: "單一位置"),
                    OptionChoice(value: true, title: "整張平鋪"),
                ])
                if session.tiled {
                    Text("重複鋪滿整張圖片，較難被裁掉。").font(.caption).foregroundStyle(.secondary)
                } else {
                    HStack(alignment: .center, spacing: 12) {
                        ITPositionPicker(position: $session.position)
                        Text(session.position.title).font(.callout).foregroundStyle(.secondary)
                    }
                }
            }
        }
    }

    private var textOptions: some View {
        OptionGroup(title: "文字", systemImage: "pencil.line") {
            OptionTextField(title: "浮水印文字", text: $session.text, placeholder: "例如：© 2026 我的工作室")
            OptionSlider(title: "文字大小", value: $session.textSize, range: 1...25, step: 0.5, format: { "\(String(format: "%.1f", $0))%" })
            Text("以圖片短邊的百分比計算，大小不同的圖片看起來一致。").font(.caption).foregroundStyle(.secondary)
            OptionToggle(title: "粗體", isOn: $session.bold)
            Text("顏色").font(.callout)
            ITColorPicker(choice: $session.colorChoice, custom: $session.customColor)
        }
    }

    private var logoOptions: some View {
        OptionGroup(title: "標誌圖片", systemImage: "photo") {
            HStack(spacing: 10) {
                if let url = session.logo, let image = NSImage(contentsOf: url) {
                    Image(nsImage: image).resizable().scaledToFit().frame(width: 44, height: 44)
                        .background(RoundedRectangle(cornerRadius: 6).fill(Color.primary.opacity(0.06)))
                    Text(url.lastPathComponent).font(.callout).lineLimit(1).truncationMode(.middle)
                } else {
                    Image(systemName: "photo.badge.plus").font(.title2).foregroundStyle(.secondary).frame(width: 44, height: 44)
                    Text("尚未選擇").font(.callout).foregroundStyle(.secondary)
                }
                Spacer(minLength: 0)
            }
            Button { session.chooseLogo() } label: {
                Label(session.logo == nil ? "選擇標誌圖片…" : "更換…", systemImage: "folder")
            }
            OptionSlider(title: "標誌大小", value: $session.logoScale, range: 3...80, step: 1, format: { "圖寬 \(Int($0))%" })
            Text("建議使用背景透明的 PNG 標誌。").font(.caption).foregroundStyle(.secondary)
        }
    }
}
