import CoreImage
import SwiftUI

// MARK: - 放大圖片

enum ITSharpen: String, Sendable {
    case soft, standard, strong

    var amount: Double {
        switch self {
        case .soft: return 0.25
        case .standard: return 0.55
        case .strong: return 0.95
        }
    }
}

struct ITUpscaleOptions: Sendable {
    var factor = 2
    var sharpen: ITSharpen = .standard
    var denoise = false
}

extension ImageToolCatalog {
    static let upscale = ToolDefinition(
        id: "image.upscale",
        category: .image,
        title: "放大圖片",
        subtitle: "以高品質 Lanczos 演算法把小圖放大 2 倍或 4 倍，並銳利化讓邊緣更清楚。",
        symbol: "arrow.up.backward.and.arrow.down.forward.square",
        tint: ToolPalette.video,
        badge: "2×",
        inputTypes: [.image],
        inputLabel: "圖片",
        actionTitle: "放大圖片",
        keywords: ["upscale", "enlarge", "super resolution", "enhance", "放大", "提高解析度", "變清楚"],
        makeSession: { ITUpscaleSession() }
    )
}

final class ITUpscaleSession: ToolSession {
    nonisolated static let maxSide = 16_384

    @Published var factor = 2
    @Published var sharpen: ITSharpen = .standard
    @Published var denoise = false
    @Published var firstSize: CGSize?
    @Published var fileCount = 0

    override func filesDidChange(_ files: [URL]) {
        fileCount = files.count
        firstSize = files.first.flatMap { ITLoader.pixelSize(of: $0) }
    }

    override func validate(_ files: [URL]) -> String? {
        if files.count == 1, let s = firstSize, Int(max(s.width, s.height)) * factor > Self.maxSide {
            return "放大後超過 \(Self.maxSide) px，請改用較小的倍數"
        }
        return nil
    }

    func sizeText(_ f: Int) -> String? {
        guard let s = firstSize else { return nil }
        return "\(Int(s.width) * f) × \(Int(s.height) * f) px"
    }

    override func optionsView() -> AnyView { AnyView(ITUpscaleOptionsView(session: self)) }

    override func makeJob() -> ToolJob { Self.job(ITUpscaleOptions(factor: factor, sharpen: sharpen, denoise: denoise)) }

    nonisolated static func job(_ o: ITUpscaleOptions) -> ToolJob {
        ToolJob { files, context in
            let result = try await ITPipeline.eachFile(files, context: context) { file, context in
                [try ITPipeline.run(file, context: context, tag: "放大\(o.factor)倍", animated: false, output: { source in
                    ITOutput(format: source.preferredOutputFormat == .gif ? .png : source.preferredOutputFormat,
                             quality: 0.92, metadata: .keep, keepsAlpha: source.hasAlpha)
                }, transform: { image, _ in try upscale(image, o) })]
            }
            if !result.skipped.isEmpty { context.progress(1, "已略過：" + result.skipped.joined(separator: "；")) }
            return result.outputs
        }
    }

    nonisolated static func upscale(_ image: CGImage, _ o: ITUpscaleOptions) throws -> CGImage {
        let w = image.width * o.factor, h = image.height * o.factor
        guard max(w, h) <= maxSide else {
            throw ITSkip(reason: "放大後為 \(w) × \(h) px，超過上限 \(maxSide) px，請改用較小的倍數")
        }
        var ci = CIImage(cgImage: image)
        let extent = ci.extent
        if o.denoise {
            ci = ci.clampedToExtent().applyingFilter("CINoiseReduction", parameters: [
                "inputNoiseLevel": 0.02,
                "inputSharpness": 0.4,
            ]).cropped(to: extent)
        }
        let scale = Double(o.factor)
        ci = ci.clampedToExtent().applyingFilter("CILanczosScaleTransform", parameters: [
            kCIInputScaleKey: scale,
            kCIInputAspectRatioKey: 1.0,
        ])
        ci = ci.applyingFilter("CIUnsharpMask", parameters: [
            kCIInputRadiusKey: 1.2 * scale,
            kCIInputIntensityKey: o.sharpen.amount,
        ])
        let rect = CGRect(x: 0, y: 0, width: w, height: h)
        return try ITRender.render(ci.cropped(to: rect), rect: rect, like: image)
    }
}

private struct ITUpscaleOptionsView: View {
    @ObservedObject var session: ITUpscaleSession

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            OptionGroup(title: "放大倍數", systemImage: "arrow.up.backward.and.arrow.down.forward") {
                ChoiceCards(selection: $session.factor, choices: [
                    OptionChoice(value: 2, title: "2 倍", subtitle: session.sizeText(2) ?? "寬高各放大 2 倍", systemImage: "2.square"),
                    OptionChoice(value: 4, title: "4 倍", subtitle: session.sizeText(4) ?? "寬高各放大 4 倍", systemImage: "4.square"),
                ])
            }
            OptionGroup(title: "銳利度", systemImage: "wand.and.rays") {
                SegmentedOption(selection: $session.sharpen, choices: [
                    OptionChoice(value: .soft, title: "柔和"),
                    OptionChoice(value: .standard, title: "標準"),
                    OptionChoice(value: .strong, title: "銳利"),
                ])
                OptionToggle(title: "降低雜訊", subtitle: "適合手機照片或壓縮過的 JPG", isOn: $session.denoise)
            }
            OptionNote(text: "放大無法憑空產生細節，但能讓小圖在大尺寸顯示或列印時更平滑清楚。上限為 16384 px。")
        }
    }
}
