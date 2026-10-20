import ImageIO
import SwiftUI

// MARK: - 製作圖示

enum ITIconOutput: String, Sendable {
    case icns, ico, iconset, pngSet
}

enum ITIconShape: String, Sendable {
    case original, rounded, circle
}

struct ITIconOptions: Sendable {
    var output: ITIconOutput = .icns
    var shape: ITIconShape = .rounded
    /// 四周留白（0…0.3，佔邊長的比例）
    var padding: Double = 0.1
    var background: ITRGBA = .clear
}

extension ImageToolCatalog {
    static let makeIcon = ToolDefinition(
        id: "image.make-icon",
        category: .image,
        title: "製作圖示",
        subtitle: "把圖片做成 macOS 圖示（.icns）、Windows／網站圖示（.ico）或各種尺寸的 PNG 圖示組。",
        symbol: "app.dashed",
        badge: "ICNS",
        inputTypes: [.image],
        inputLabel: "圖片",
        actionTitle: "製作圖示",
        keywords: ["icon", "icns", "ico", "favicon", "iconset", "app icon", "圖示", "圖標", "應用程式圖示"],
        makeSession: { ITMakeIconSession() }
    )
}

final class ITMakeIconSession: ToolSession {
    @Published var output: ITIconOutput = .icns
    @Published var shape: ITIconShape = .rounded {
        didSet { if shape != oldValue { padding = shape == .rounded ? 0.1 : 0 } }
    }
    @Published var padding: Double = 0.1
    @Published var background: ITColorChoice = .transparent
    @Published var customColor = Color.white

    override func optionsView() -> AnyView { AnyView(ITMakeIconOptionsView(session: self)) }

    override func makeJob() -> ToolJob {
        Self.job(ITIconOptions(output: output, shape: shape, padding: padding, background: background.resolve(custom: customColor)))
    }

    nonisolated static let icnsSides = [16, 32, 64, 128, 256, 512, 1024]
    nonisolated static let icoSides = [16, 32, 48, 64, 128, 256]
    nonisolated static let pngSides = [16, 32, 48, 64, 128, 180, 192, 256, 512, 1024]

    nonisolated static func job(_ o: ITIconOptions) -> ToolJob {
        .perFile { file, context in [try await make(file, o, context: context)] }
    }

    /// 產生一個圖示檔（或圖示資料夾）並交付。「轉換圖片格式」轉 ICO／ICNS 時也用這裡（各尺寸以 Lanczos 縮放）。
    nonisolated static func make(_ file: URL, _ o: ITIconOptions, context: ToolContext) async throws -> URL {
        context.progress(0.02, "讀取：\(file.lastPathComponent)")
        let source = try ITLoader.load(file)
        let base = try master(source.image, o)
        try context.checkCancellation()
        let work = try context.makeWorkDirectory()
        defer { try? FileManager.default.removeItem(at: work) }

        switch o.output {
        case .icns:
            let iconset = work.appendingPathComponent("icon.iconset", isDirectory: true)
            try writeIconset(base, to: iconset, context: context, range: 0.1...0.7)
            let tmp = work.appendingPathComponent("icon.icns")
            context.progress(0.75, "產生 ICNS…")
            // iconutil 會寫入完整的 16–1024 px（含 Retina @2x）；ImageIO 的 ICNS 只支援部分尺寸，當作備援
            let iconutil = "/usr/bin/iconutil"
            var built = false
            if FileManager.default.isExecutableFile(atPath: iconutil),
               let result = try? await CVProcess.run(iconutil, ["-c", "icns", "-o", tmp.path, iconset.path]), result.succeeded {
                built = FileManager.default.fileExists(atPath: tmp.path)
            }
            try context.checkCancellation()
            if !built { try writeContainer(base, sides: icnsSides, uti: ITFormat.icns.typeIdentifier, to: tmp, context: context) }
            return try ITPipeline.deliver(file: tmp, for: file, context: context, tag: nil, ext: "icns")
        case .ico:
            let tmp = work.appendingPathComponent("icon.ico")
            try writeContainer(base, sides: icoSides, uti: ITFormat.ico.typeIdentifier, to: tmp, context: context)
            return try ITPipeline.deliver(file: tmp, for: file, context: context, tag: nil, ext: "ico")
        case .iconset:
            let folder = work.appendingPathComponent("icon.iconset", isDirectory: true)
            try writeIconset(base, to: folder, context: context, range: 0.1...0.9)
            return try ITPipeline.deliver(file: folder, for: file, context: context, tag: nil, ext: "iconset")
        case .pngSet:
            let folder = work.appendingPathComponent("icons", isDirectory: true)
            try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
            for (i, side) in pngSides.enumerated() {
                try context.checkCancellation()
                let name = side == 180 ? "apple-touch-icon-180x180.png" : "icon-\(side)x\(side).png"
                try ITWriter.write(try ITScale.resample(base, width: side, height: side), source: nil,
                                   to: folder.appendingPathComponent(name), output: ITOutput(format: .png, metadata: .removeAll))
                context.progress(0.1 + 0.8 * Double(i + 1) / Double(pngSides.count), "產生 \(side)×\(side)…")
            }
            return try ITPipeline.deliver(file: folder, for: file, context: context, tag: "圖示", ext: "")
        }
    }

    /// iconutil 用的 .iconset（16–512 pt，各含 @2x）。
    nonisolated static func writeIconset(_ base: CGImage, to folder: URL, context: ToolContext, range: ClosedRange<Double>) throws {
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let entries: [(String, Int)] = [16, 32, 128, 256, 512].flatMap { s in
            [("icon_\(s)x\(s).png", s), ("icon_\(s)x\(s)@2x.png", s * 2)]
        }
        for (i, entry) in entries.enumerated() {
            try context.checkCancellation()
            try ITWriter.write(try ITScale.resample(base, width: entry.1, height: entry.1), source: nil,
                               to: folder.appendingPathComponent(entry.0), output: ITOutput(format: .png, metadata: .removeAll))
            context.progress(range.lowerBound + (range.upperBound - range.lowerBound) * Double(i + 1) / Double(entries.count),
                             "產生 \(entry.1)×\(entry.1)…")
        }
    }

    /// 以 ImageIO 寫出多尺寸的 ICO／ICNS。
    nonisolated static func writeContainer(_ base: CGImage, sides: [Int], uti: String, to url: URL, context: ToolContext) throws {
        guard let dest = CGImageDestinationCreateWithURL(url as CFURL, uti as CFString, sides.count, nil) else {
            throw ConversionError.writeFailed("這台 Mac 無法寫入這種圖示格式。")
        }
        for (i, side) in sides.enumerated() {
            try context.checkCancellation()
            CGImageDestinationAddImage(dest, try ITScale.resample(base, width: side, height: side), nil)
            context.progress(0.1 + 0.8 * Double(i + 1) / Double(sides.count), "產生 \(side)×\(side)…")
        }
        guard CGImageDestinationFinalize(dest) else { throw ConversionError.writeFailed("圖示編碼失敗。") }
    }

    /// 1024×1024 的母版：依形狀裁切、留白、補底色。
    nonisolated static func master(_ image: CGImage, _ o: ITIconOptions) throws -> CGImage {
        let side: CGFloat = 1024
        let ctx = try ITRender.canvas(width: Int(side), height: Int(side), like: image.bitsPerComponent > 8 ? nil : image)
        let inset = side * max(0, min(0.3, o.padding))
        let box = CGRect(x: inset, y: inset, width: side - inset * 2, height: side - inset * 2)
        let path: CGPath
        switch o.shape {
        case .original: path = CGPath(rect: box, transform: nil)
        case .rounded: path = CGPath(roundedRect: box, cornerWidth: box.width * 0.225, cornerHeight: box.height * 0.225, transform: nil)
        case .circle: path = CGPath(ellipseIn: box, transform: nil)
        }
        ctx.addPath(path)
        ctx.clip()
        if o.background.alpha > 0 {
            ctx.setFillColor(o.background.cgColor)
            ctx.fill(box)
        }
        let iw = CGFloat(image.width), ih = CGFloat(image.height)
        // 原樣：完整放入；圓角／圓形：填滿
        let scale = o.shape == .original ? min(box.width / iw, box.height / ih) : max(box.width / iw, box.height / ih)
        let w = iw * scale, h = ih * scale
        ctx.interpolationQuality = .high
        ctx.draw(image, in: CGRect(x: box.midX - w / 2, y: box.midY - h / 2, width: w, height: h))
        guard let out = ctx.makeImage() else { throw ConversionError.other("無法產生圖示。") }
        return out
    }
}

private struct ITMakeIconOptionsView: View {
    @ObservedObject var session: ITMakeIconSession

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            OptionGroup(title: "輸出", systemImage: "app.badge") {
                ChoiceCards(selection: $session.output, choices: [
                    OptionChoice(value: .icns, title: "macOS 圖示", subtitle: ".icns，16–1024 px", systemImage: "macwindow", badge: "ICNS"),
                    OptionChoice(value: .ico, title: "Windows／網站圖示", subtitle: ".ico，16–256 px（favicon）", systemImage: "globe", badge: "ICO"),
                    OptionChoice(value: .iconset, title: "圖示組 .iconset", subtitle: ".iconset 資料夾，可用 iconutil 轉換", systemImage: "hammer"),
                    OptionChoice(value: .pngSet, title: "PNG 圖示組", subtitle: "16–1024 px 各尺寸 PNG（含 Apple Touch）", systemImage: "square.stack.3d.up"),
                ])
            }
            OptionGroup(title: "形狀", systemImage: "app") {
                SegmentedOption(selection: $session.shape, choices: [
                    OptionChoice(value: .original, title: "原樣"),
                    OptionChoice(value: .rounded, title: "圓角方形"),
                    OptionChoice(value: .circle, title: "圓形"),
                ])
                OptionSlider(title: "四周留白", value: $session.padding, range: 0...0.3, step: 0.01, format: ITOptionFormat.quality)
                Text(session.shape == .rounded ? "macOS 圖示建議留白約 10%。" : "非正方形的圖片會置中。")
                    .font(.caption).foregroundStyle(.secondary)
            }
            OptionGroup(title: "背景", systemImage: "square.fill") {
                ITColorPicker(choice: $session.background, custom: $session.customColor, options: [.transparent, .white, .black, .custom])
            }
        }
    }
}
