import CoreImage
import SwiftUI

// MARK: - 旋轉／翻轉

struct ITRotateOptions: Sendable {
    /// 順時針角度：0、90、180、270
    var angle = 90
    var flipHorizontal = false
    var flipVertical = false
}

extension ImageToolCatalog {
    static let rotate = ToolDefinition(
        id: "image.rotate",
        category: .image,
        title: "旋轉／翻轉",
        subtitle: "把圖片轉 90°、180°，或水平、垂直翻轉（鏡像），可一次處理多張。",
        symbol: "rotate.right",
        tint: ToolPalette.slate,
        badge: "IMG",
        inputTypes: [.image],
        inputLabel: "圖片",
        actionTitle: "旋轉圖片",
        keywords: ["rotate", "flip", "mirror", "turn", "旋轉", "翻轉", "鏡像", "轉向"],
        quickActions: [
            "image.rotate-right": { ITRotateSession.job(ITRotateOptions(angle: 90)) },
        ],
        makeSession: { ITRotateSession() }
    )
}

final class ITRotateSession: ToolSession {
    @Published var angle = 90
    @Published var flipHorizontal = false
    @Published var flipVertical = false

    override func validate(_ files: [URL]) -> String? {
        angle == 0 && !flipHorizontal && !flipVertical ? "請選擇旋轉或翻轉方式" : nil
    }

    override var actionTitleOverride: String? {
        if angle == 0 { return "翻轉圖片" }
        return flipHorizontal || flipVertical ? "旋轉並翻轉" : "旋轉圖片"
    }

    override func optionsView() -> AnyView { AnyView(ITRotateOptionsView(session: self)) }

    override func makeJob() -> ToolJob {
        Self.job(ITRotateOptions(angle: angle, flipHorizontal: flipHorizontal, flipVertical: flipVertical))
    }

    nonisolated static func job(_ o: ITRotateOptions) -> ToolJob {
        .perFile { file, context in
            [try ITPipeline.run(file, context: context, tag: o.angle == 0 ? "已翻轉" : "已旋轉", output: { source in
                ITOutput(format: source.preferredOutputFormat, quality: 0.92, metadata: .keep, keepsAlpha: source.hasAlpha)
            }, transform: { image, _ in try transform(image, o) })]
        }
    }

    /// 90° 倍數的旋轉與翻轉：只搬移像素，不重新取樣、不轉換色彩。
    nonisolated static func transform(_ image: CGImage, _ o: ITRotateOptions) throws -> CGImage {
        var ci = CIImage(cgImage: image)
        if o.flipHorizontal { ci = ci.oriented(.upMirrored) }
        if o.flipVertical { ci = ci.oriented(.downMirrored) }
        switch (o.angle % 360 + 360) % 360 {
        case 90: ci = ci.oriented(.right)
        case 180: ci = ci.oriented(.down)
        case 270: ci = ci.oriented(.left)
        default: break
        }
        ci = ci.transformed(by: CGAffineTransform(translationX: -ci.extent.minX, y: -ci.extent.minY))
        return try ITRender.render(ci, rect: ci.extent, like: image, exact: true)
    }
}

private struct ITRotateOptionsView: View {
    @ObservedObject var session: ITRotateSession

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            OptionGroup(title: "旋轉", systemImage: "arrow.clockwise") {
                ChoiceCards(selection: $session.angle, choices: [
                    OptionChoice(value: 90, title: "向右轉 90°", subtitle: "順時針", systemImage: "rotate.right"),
                    OptionChoice(value: 270, title: "向左轉 90°", subtitle: "逆時針", systemImage: "rotate.left"),
                    OptionChoice(value: 180, title: "轉 180°", subtitle: "上下顛倒", systemImage: "arrow.up.arrow.down"),
                    OptionChoice(value: 0, title: "不旋轉", subtitle: "只翻轉", systemImage: "nosign"),
                ])
            }
            OptionGroup(title: "翻轉（鏡像）", systemImage: "arrow.left.and.right.righttriangle.left.righttriangle.right") {
                OptionToggle(title: "水平翻轉", subtitle: "左右對調，像照鏡子", isOn: $session.flipHorizontal)
                OptionToggle(title: "垂直翻轉", subtitle: "上下對調", isOn: $session.flipVertical)
            }
            OptionNote(text: "只搬移像素、不重新取樣，畫面不會變模糊（JPG 會以高品質重新儲存）；相片資訊與色彩描述檔會保留。")
        }
    }
}
