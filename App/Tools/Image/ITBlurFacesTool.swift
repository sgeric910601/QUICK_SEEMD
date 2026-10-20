import CoreImage
import SwiftUI
import Vision

// MARK: - 模糊臉部

enum ITFaceStyle: String, Sendable {
    case blur, pixelate, blackout
}

enum ITFaceShape: String, Sendable {
    case ellipse, rectangle
}

struct ITBlurFacesOptions: Sendable {
    var style: ITFaceStyle = .blur
    /// 1…10
    var strength: Double = 6
    var shape: ITFaceShape = .ellipse
    /// 遮蓋範圍比偵測到的臉大多少（0.1＝10%）
    var expand: Double = 0.25
}

extension ImageToolCatalog {
    static let blurFaces = ToolDefinition(
        id: "image.blur-faces",
        category: .image,
        title: "模糊臉部",
        subtitle: "自動找出照片中的每一張臉，以模糊、馬賽克或黑色色塊遮住，保護隱私後再分享。",
        symbol: "face.dashed",
        tint: ToolPalette.slate,
        badge: "IMG",
        inputTypes: [.image],
        inputLabel: "圖片",
        actionTitle: "模糊臉部",
        keywords: ["blur face", "anonymize", "pixelate", "mosaic", "privacy", "模糊", "馬賽克", "打碼", "臉", "隱私"],
        makeSession: { ITBlurFacesSession() }
    )
}

final class ITBlurFacesSession: ToolSession {
    @Published var style: ITFaceStyle = .blur
    @Published var strength: Double = 6
    @Published var shape: ITFaceShape = .ellipse
    @Published var expand: Double = 0.25

    override var actionTitleOverride: String? {
        switch style {
        case .blur: return "模糊臉部"
        case .pixelate: return "加上馬賽克"
        case .blackout: return "遮住臉部"
        }
    }

    override func optionsView() -> AnyView { AnyView(ITBlurFacesOptionsView(session: self)) }

    override func makeJob() -> ToolJob {
        Self.job(ITBlurFacesOptions(style: style, strength: strength, shape: shape, expand: expand))
    }

    nonisolated static func job(_ o: ITBlurFacesOptions) -> ToolJob {
        ToolJob { files, context in
            var faces = 0
            let result = try await ITPipeline.eachFile(files, context: context) { file, context in
                context.progress(0.02, "讀取：\(file.lastPathComponent)")
                let source = try ITLoader.load(file)
                context.progress(0.1, "尋找臉孔：\(file.lastPathComponent)")
                let rects = try detectFaces(source.image)
                guard !rects.isEmpty else { throw ITSkip(reason: "在「\(file.lastPathComponent)」裡找不到臉孔，未產生檔案。") }
                faces += rects.count
                try context.checkCancellation()
                context.progress(0.5, "找到 \(rects.count) 張臉，處理中…")
                let image = try apply(source.image, faces: rects, o)
                let output = ITOutput(format: source.preferredOutputFormat, quality: 0.92, metadata: .keep, keepsAlpha: source.hasAlpha)
                let url = try ITPipeline.deliver([ITFrame(image: image, delay: 0)], source: source, output: output, context: context,
                                                 tag: [.blur: "模糊臉部", .pixelate: "馬賽克", .blackout: "遮蓋臉部"][o.style] ?? "模糊臉部")
                context.progress(1, "已處理 \(rects.count) 張臉")
                return [url]
            }
            var message = "共處理 \(faces) 張臉"
            if !result.skipped.isEmpty { message += "；\(result.skipped.count) 張圖片沒有找到臉孔，已略過" }
            context.progress(1, message)
            return result.outputs
        }
    }

    /// 臉孔範圍（Core Image 座標：左下為原點、像素）。
    nonisolated static func detectFaces(_ image: CGImage) throws -> [CGRect] {
        let request = VNDetectFaceRectanglesRequest()
        let handler = VNImageRequestHandler(cgImage: image, options: [:])
        do {
            try handler.perform([request])
        } catch {
            throw ConversionError.other("臉孔偵測失敗：\(error.localizedDescription)")
        }
        let W = CGFloat(image.width), H = CGFloat(image.height)
        return (request.results ?? []).map { face in
            let b = face.boundingBox
            return CGRect(x: b.minX * W, y: b.minY * H, width: b.width * W, height: b.height * H)
        }
    }

    nonisolated static func apply(_ image: CGImage, faces: [CGRect], _ o: ITBlurFacesOptions) throws -> CGImage {
        let base = CIImage(cgImage: image)
        let extent = base.extent
        let strength = max(1, min(10, o.strength))
        var result = base
        for face in faces {
            // 往外擴張（頭髮、下巴、耳朵也要遮住），上方多留一點
            var r = face.insetBy(dx: -face.width * o.expand / 2, dy: -face.height * o.expand / 2)
            r.size.height += face.height * o.expand * 0.3
            r = r.intersection(extent)
            guard !r.isEmpty else { continue }
            let size = max(r.width, r.height)
            let effect: CIImage
            switch o.style {
            case .blur:
                let sigma = Double(size) * (0.02 + 0.012 * strength)
                effect = base.clampedToExtent().applyingGaussianBlur(sigma: sigma).cropped(to: extent)
            case .pixelate:
                let blocks = max(3, 24 - 2 * strength)
                effect = base.clampedToExtent().applyingFilter("CIPixellate", parameters: [
                    kCIInputScaleKey: Double(size) / blocks,
                    kCIInputCenterKey: CIVector(x: r.midX, y: r.midY),
                ]).cropped(to: extent)
            case .blackout:
                effect = CIImage(color: CIColor(red: 0, green: 0, blue: 0)).cropped(to: extent)
            }
            let mask = faceMask(r, shape: o.shape, soft: o.style == .blur)
            result = effect.applyingFilter("CIBlendWithMask", parameters: [
                kCIInputBackgroundImageKey: result,
                kCIInputMaskImageKey: mask,
            ]).cropped(to: extent)
        }
        return try ITRender.render(result, rect: extent, like: image)
    }

    /// 白色＝要遮的地方。
    nonisolated static func faceMask(_ r: CGRect, shape: ITFaceShape, soft: Bool) -> CIImage {
        let white = CIColor(red: 1, green: 1, blue: 1)
        let black = CIColor(red: 0, green: 0, blue: 0)
        switch shape {
        case .rectangle:
            let rect = CIImage(color: white).cropped(to: r)
            let mask = rect.composited(over: CIImage(color: black))
            return soft ? mask.applyingGaussianBlur(sigma: Double(min(r.width, r.height)) * 0.04) : mask
        case .ellipse:
            // 單位圓的放射漸層 → 縮放成橢圓
            let gradient = CIFilter(name: "CIRadialGradient", parameters: [
                "inputCenter": CIVector(x: 0, y: 0),
                "inputRadius0": soft ? 80 : 99,
                "inputRadius1": 100,
                "inputColor0": white,
                "inputColor1": black,
            ])?.outputImage ?? CIImage(color: white)
            return gradient
                .transformed(by: CGAffineTransform(scaleX: r.width / 200, y: r.height / 200))
                .transformed(by: CGAffineTransform(translationX: r.midX, y: r.midY))
        }
    }
}

private struct ITBlurFacesOptionsView: View {
    @ObservedObject var session: ITBlurFacesSession

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            OptionGroup(title: "遮蓋方式", systemImage: "face.dashed") {
                ChoiceCards(selection: $session.style, choices: [
                    OptionChoice(value: .blur, title: "模糊", subtitle: "柔和自然，看得出是人但認不出是誰", systemImage: "drop"),
                    OptionChoice(value: .pixelate, title: "馬賽克", subtitle: "新聞畫面常見的方塊效果", systemImage: "square.grid.3x3.fill"),
                    OptionChoice(value: .blackout, title: "黑色色塊", subtitle: "完全遮住，最安全", systemImage: "rectangle.fill"),
                ])
            }
            if session.style != .blackout {
                OptionGroup(title: "強度", systemImage: "dial.medium") {
                    OptionSlider(title: session.style == .blur ? "模糊程度" : "方塊大小", value: $session.strength, range: 1...10, step: 1)
                }
            }
            OptionGroup(title: "形狀與範圍", systemImage: "oval") {
                SegmentedOption(selection: $session.shape, choices: [
                    OptionChoice(value: .ellipse, title: "橢圓"),
                    OptionChoice(value: .rectangle, title: "方形"),
                ])
                OptionSlider(title: "遮蓋範圍", value: $session.expand, range: 0...0.8, step: 0.05, format: { "+\(Int(($0 * 100).rounded()))%" })
                Text("比偵測到的臉再大一些，把頭髮與輪廓也遮住。").font(.caption).foregroundStyle(.secondary)
            }
            OptionNote(text: "使用 macOS 內建的臉孔偵測，完全離線處理。沒有找到臉孔的圖片會略過；請在分享前再確認一次結果。",
                       systemImage: "lock.shield")
        }
    }
}
