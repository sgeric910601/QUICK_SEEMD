import CoreImage
import SwiftUI
import Vision

// MARK: - 移除背景

enum ITBackdrop: String, Sendable {
    case transparent, white, custom, blur
}

struct ITRemoveBackgroundOptions: Sendable {
    var backdrop: ITBackdrop = .transparent
    var color: ITRGBA = .white
    var format: ITFormat = .png
    var cropToSubject = false
}

extension ImageToolCatalog {
    static let removeBackground = ToolDefinition(
        id: "image.remove-bg",
        category: .image,
        title: "移除背景",
        subtitle: "自動辨識人物、動物或商品，一鍵去背成透明 PNG，或換成白色、自訂顏色、模糊背景。",
        symbol: "person.and.background.dotted",
        tint: ToolPalette.video,
        badge: "PNG",
        inputTypes: [.image],
        inputLabel: "圖片",
        actionTitle: "移除背景",
        keywords: ["remove background", "background", "cutout", "transparent", "去背", "背景", "透明", "摳圖", "商品照"],
        quickActions: [
            "image.remove-bg-transparent": { ITRemoveBackgroundSession.job(ITRemoveBackgroundOptions()) },
        ],
        makeSession: { ITRemoveBackgroundSession() }
    )
}

final class ITRemoveBackgroundSession: ToolSession {
    @Published var backdrop: ITBackdrop = .transparent {
        didSet { if backdrop == .transparent && format == .jpeg { format = .png } }
    }
    @Published var customColor = Color(red: 0.85, green: 0.92, blue: 1)
    @Published var format: ITFormat = .png
    @Published var cropToSubject = false

    override func optionsView() -> AnyView { AnyView(ITRemoveBackgroundOptionsView(session: self)) }

    override func makeJob() -> ToolJob {
        Self.job(ITRemoveBackgroundOptions(backdrop: backdrop, color: ITRGBA(swiftUI: customColor), format: format,
                                           cropToSubject: cropToSubject))
    }

    nonisolated static func job(_ o: ITRemoveBackgroundOptions) -> ToolJob {
        ToolJob { files, context in
            let result = try await ITPipeline.eachFile(files, context: context) { file, context in
                [try process(file, o, context: context)]
            }
            if !result.skipped.isEmpty {
                context.progress(1, "已略過 \(result.skipped.count) 張：" + result.skipped.joined(separator: "；"))
            }
            return result.outputs
        }
    }

    nonisolated static func process(_ file: URL, _ o: ITRemoveBackgroundOptions, context: ToolContext) throws -> URL {
        context.progress(0.02, "讀取：\(file.lastPathComponent)")
        let source = try ITLoader.load(file)
        try context.checkCancellation()
        context.progress(0.1, "辨識主體：\(file.lastPathComponent)")
        let subject = try subjectMask(source.image, name: file.lastPathComponent)
        try context.checkCancellation()
        context.progress(0.6, "合成背景…")
        var image = try composite(source.image, mask: subject.mask, o)
        if o.cropToSubject {
            let pad = Double(max(subject.bounds.width, subject.bounds.height)) * 0.04
            let r = subject.bounds.insetBy(dx: -pad, dy: -pad).integral
                .intersection(CGRect(x: 0, y: 0, width: image.width, height: image.height))
            if let cropped = image.cropping(to: r) { image = cropped }
        }
        try context.checkCancellation()
        let transparent = o.backdrop == .transparent
        let format: ITFormat = transparent && !o.format.supportsAlpha ? .png : (o.format.isWritable ? o.format : .png)
        let output = ITOutput(format: format, quality: 0.9, metadata: .keep, background: .white, keepsAlpha: transparent)
        return try ITPipeline.deliver([ITFrame(image: image, delay: 0)], source: source, output: output, context: context, tag: "去背")
    }

    /// Vision 前景遮罩（與原圖同尺寸）以及主體範圍（像素，左上為原點）。
    nonisolated static func subjectMask(_ image: CGImage, name: String) throws -> (mask: CIImage, bounds: CGRect) {
        let handler = VNImageRequestHandler(cgImage: image, options: [:])
        let request = VNGenerateForegroundInstanceMaskRequest()
        do {
            try handler.perform([request])
        } catch {
            throw ConversionError.other("無法辨識主體：\(error.localizedDescription)")
        }
        guard let observation = request.results?.first, !observation.allInstances.isEmpty else {
            throw ITSkip(reason: "在「\(name)」裡找不到明顯的主體（人物、動物或物品）。")
        }
        let buffer: CVPixelBuffer
        do {
            buffer = try observation.generateScaledMaskForImage(forInstances: observation.allInstances, from: handler)
        } catch {
            throw ConversionError.other("無法產生去背遮罩：\(error.localizedDescription)")
        }
        var mask = CIImage(cvPixelBuffer: buffer, options: [.colorSpace: NSNull()])
        let bw = CVPixelBufferGetWidth(buffer), bh = CVPixelBufferGetHeight(buffer)
        if bw != image.width || bh != image.height {
            mask = mask.transformed(by: CGAffineTransform(scaleX: CGFloat(image.width) / CGFloat(bw), y: CGFloat(image.height) / CGFloat(bh)))
        }
        let bounds = maskBounds(buffer).applying(CGAffineTransform(scaleX: CGFloat(image.width) / CGFloat(bw),
                                                                   y: CGFloat(image.height) / CGFloat(bh)))
        return (mask, bounds.isEmpty ? CGRect(x: 0, y: 0, width: image.width, height: image.height) : bounds)
    }

    /// 遮罩中「是主體」的範圍（左上為原點）。
    nonisolated static func maskBounds(_ buffer: CVPixelBuffer) -> CGRect {
        CVPixelBufferLockBaseAddress(buffer, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(buffer, .readOnly) }
        guard CVPixelBufferGetPixelFormatType(buffer) == kCVPixelFormatType_OneComponent32Float,
              let base = CVPixelBufferGetBaseAddress(buffer) else { return .null }
        let w = CVPixelBufferGetWidth(buffer), h = CVPixelBufferGetHeight(buffer)
        let rowBytes = CVPixelBufferGetBytesPerRow(buffer)
        var minX = w, minY = h, maxX = -1, maxY = -1
        for y in 0..<h {
            let row = base.advanced(by: y * rowBytes).assumingMemoryBound(to: Float32.self)
            for x in 0..<w where row[x] > 0.5 {
                if x < minX { minX = x }
                if x > maxX { maxX = x }
                if y < minY { minY = y }
                if y > maxY { maxY = y }
            }
        }
        guard maxX >= minX, maxY >= minY else { return .null }
        return CGRect(x: minX, y: minY, width: maxX - minX + 1, height: maxY - minY + 1)
    }

    nonisolated static func composite(_ image: CGImage, mask: CIImage, _ o: ITRemoveBackgroundOptions) throws -> CGImage {
        let input = CIImage(cgImage: image)
        let extent = input.extent
        let background: CIImage
        switch o.backdrop {
        case .transparent: background = CIImage(color: .clear).cropped(to: extent)
        case .white: background = CIImage(color: ITRGBA.white.ciColor).cropped(to: extent)
        case .custom: background = CIImage(color: o.color.ciColor).cropped(to: extent)
        case .blur:
            let sigma = Double(max(image.width, image.height)) * 0.012
            background = input.clampedToExtent().applyingGaussianBlur(sigma: sigma).cropped(to: extent)
        }
        let out = input.applyingFilter("CIBlendWithMask", parameters: [
            kCIInputBackgroundImageKey: background,
            kCIInputMaskImageKey: mask,
        ])
        return try ITRender.render(out.cropped(to: extent), rect: extent, like: image)
    }
}

private struct ITRemoveBackgroundOptionsView: View {
    @ObservedObject var session: ITRemoveBackgroundSession

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            OptionGroup(title: "新的背景", systemImage: "person.and.background.dotted") {
                ChoiceCards(selection: $session.backdrop, choices: [
                    OptionChoice(value: .transparent, title: "透明", subtitle: "去背後可疊在任何設計上", systemImage: "checkerboard.rectangle", badge: "PNG"),
                    OptionChoice(value: .white, title: "白色", subtitle: "商品照、證件照", systemImage: "square"),
                    OptionChoice(value: .custom, title: "自訂顏色", subtitle: "換成任何單色背景", systemImage: "paintpalette"),
                    OptionChoice(value: .blur, title: "模糊背景", subtitle: "像人像模式一樣淡化背景", systemImage: "camera.aperture"),
                ])
                if session.backdrop == .custom {
                    ColorPicker(selection: $session.customColor, supportsOpacity: false) { Text("背景顏色").font(.callout) }
                }
            }
            OptionGroup(title: "輸出格式", systemImage: "doc.badge.gearshape") {
                ChoiceChips(selection: $session.format, choices: formats.map { OptionChoice(value: $0, title: $0.title) })
                Text(session.backdrop == .transparent
                     ? "PNG 相容性最好；HEIC 檔案較小。兩者都保留透明。"
                     : "背景不透明時也可以存成 JPG。")
                    .font(.caption).foregroundStyle(.secondary)
            }
            OptionGroup(title: "選項", systemImage: "slider.horizontal.3") {
                OptionToggle(title: "裁切到主體", subtitle: "去掉多餘的空白，只留主體與一點邊距", isOn: $session.cropToSubject)
            }
            OptionNote(text: "使用 macOS 內建的主體辨識，完全在這台 Mac 上處理、不會上傳。人物、動物、商品照效果最好。",
                       systemImage: "lock.shield")
        }
    }

    private var formats: [ITFormat] {
        let list: [ITFormat] = session.backdrop == .transparent ? [.png, .heic] : [.png, .heic, .jpeg]
        return list.filter(\.isWritable)
    }
}
