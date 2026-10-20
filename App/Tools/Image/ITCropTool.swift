import SwiftUI
import Vision

// MARK: - 裁剪圖片

enum ITAspect: String, CaseIterable, Sendable {
    case free, square, r4x3, r3x4, r3x2, r2x3, r16x9, r9x16, a4Portrait, a4Landscape

    var title: String {
        switch self {
        case .free: return "自由"
        case .square: return "1:1"
        case .r4x3: return "4:3"
        case .r3x4: return "3:4"
        case .r3x2: return "3:2"
        case .r2x3: return "2:3"
        case .r16x9: return "16:9"
        case .r9x16: return "9:16"
        case .a4Portrait: return "A4 直"
        case .a4Landscape: return "A4 橫"
        }
    }

    var hint: String {
        switch self {
        case .free: return "自由拖曳"
        case .square: return "大頭貼、IG 貼文"
        case .r4x3, .r3x4: return "一般相機、iPad"
        case .r3x2, .r2x3: return "單眼相機、沖印"
        case .r16x9: return "影片、簡報、桌布"
        case .r9x16: return "限時動態、短影音"
        case .a4Portrait, .a4Landscape: return "列印 A4 紙張"
        }
    }

    /// 寬 ÷ 高；自由時為 nil
    var ratio: Double? {
        switch self {
        case .free: return nil
        case .square: return 1
        case .r4x3: return 4.0 / 3
        case .r3x4: return 3.0 / 4
        case .r3x2: return 3.0 / 2
        case .r2x3: return 2.0 / 3
        case .r16x9: return 16.0 / 9
        case .r9x16: return 9.0 / 16
        case .a4Portrait: return 210.0 / 297
        case .a4Landscape: return 297.0 / 210
        }
    }
}

/// 依比例自動裁切（多張圖片）時，裁切框放在哪裡。
enum ITCropAnchor: String, Sendable {
    case center, top, smart
}

struct ITCropOptions: Sendable {
    var aspect: ITAspect = .free
    /// 單張圖片時使用者框選的範圍（0…1，左上為原點）；nil 代表依比例自動裁切
    var rect: CGRect?
    var anchor: ITCropAnchor = .center
}

extension ImageToolCatalog {
    static let crop = ToolDefinition(
        id: "image.crop",
        category: .image,
        title: "裁剪圖片",
        subtitle: "拖曳框選要保留的範圍，或依 1:1、16:9、A4 等比例一次裁切多張圖片。",
        symbol: "crop",
        tint: ToolPalette.teal,
        badge: "IMG",
        inputTypes: [.image],
        inputLabel: "圖片",
        actionTitle: "裁剪圖片",
        keywords: ["crop", "trim", "cut", "aspect", "裁切", "剪裁", "比例"],
        makeSession: { ITCropSession() }
    )
}

final class ITCropSession: ToolSession {
    @Published var aspect: ITAspect = .free {
        didSet { if aspect != oldValue && aspect != .free { resetRect() } }
    }
    /// 正規化的裁剪範圍（0…1，左上為原點）
    @Published var rect = CGRect(x: 0, y: 0, width: 1, height: 1)
    /// 單張圖片時的像素尺寸（已轉正）
    @Published var imageSize: CGSize?
    @Published var fileCount = 0
    @Published var anchor: ITCropAnchor = .smart

    override func filesDidChange(_ files: [URL]) {
        fileCount = files.count
        imageSize = files.count == 1 ? ITLoader.pixelSize(of: files[0]) : nil
        resetRect()
    }

    /// 依目前比例選出最大、置中的範圍。
    func resetRect() {
        guard let s = imageSize, s.width > 0, s.height > 0, let ratio = aspect.ratio else {
            rect = CGRect(x: 0, y: 0, width: 1, height: 1)
            return
        }
        rect = Self.centered(ratio: ratio, imageAspect: s.width / s.height)
    }

    nonisolated static func centered(ratio: Double, imageAspect: Double) -> CGRect {
        if ratio > imageAspect {
            let h = imageAspect / ratio
            return CGRect(x: 0, y: (1 - h) / 2, width: 1, height: h)
        }
        let w = ratio / imageAspect
        return CGRect(x: (1 - w) / 2, y: 0, width: w, height: 1)
    }

    var pixelSizeText: String? {
        guard let s = imageSize else { return nil }
        let r = Self.pixelRect(width: Int(s.width), height: Int(s.height), options: options)
        return ITText.pixels(Int(r.width), Int(r.height))
    }

    var options: ITCropOptions {
        ITCropOptions(aspect: aspect, rect: fileCount == 1 ? rect : nil, anchor: anchor)
    }

    override func validate(_ files: [URL]) -> String? {
        if files.count > 1 && aspect == .free { return "多張圖片請選擇裁剪比例（會從中間裁切）" }
        if files.count == 1 && aspect == .free && rect.width > 0.999 && rect.height > 0.999 { return "請拖曳裁剪框，選擇要保留的範圍" }
        return nil
    }

    override func optionsView() -> AnyView { AnyView(ITCropOptionsView(session: self)) }

    override func customFilesView(files: Binding<[URL]>) -> AnyView? {
        guard files.wrappedValue.count == 1, let url = files.wrappedValue.first else { return nil }
        return AnyView(ITCropEditor(session: self, url: url, onRemove: { files.wrappedValue = [] }))
    }

    override func makeJob() -> ToolJob { Self.job(options) }

    /// 實際裁剪的像素範圍（左上為原點）。
    /// - Parameter focus: 自動裁切時要保留在畫面中的重點（0…1，左上為原點），例如人臉
    nonisolated static func pixelRect(width w: Int, height h: Int, options o: ITCropOptions, focus: CGPoint? = nil) -> CGRect {
        let W = Double(w), H = Double(h)
        let bounds = CGRect(x: 0, y: 0, width: W, height: H)
        guard let n = o.rect else {
            guard let ratio = o.aspect.ratio else { return bounds }
            let c = centered(ratio: ratio, imageAspect: W / H)
            var r = CGRect(x: c.minX * W, y: c.minY * H, width: c.width * W, height: c.height * H)
            switch o.anchor {
            case .center: break
            case .top: r.origin.y = 0
            case .smart:
                if let f = focus {
                    r.origin.x = min(max(0, f.x * W - r.width / 2), W - r.width)
                    r.origin.y = min(max(0, f.y * H - r.height / 2), H - r.height)
                }
            }
            return r.integral.intersection(bounds)
        }
        var r = CGRect(x: n.minX * W, y: n.minY * H, width: n.width * W, height: n.height * H)
        if let ratio = o.aspect.ratio {
            // 換算成整數像素後仍維持正確比例
            let width = max(1, r.width.rounded())
            r = CGRect(x: r.minX, y: r.minY, width: width, height: max(1, (width / ratio).rounded()))
        }
        r = CGRect(x: r.minX.rounded(), y: r.minY.rounded(), width: max(1, r.width.rounded()), height: max(1, r.height.rounded()))
        if r.maxX > W { r.origin.x = max(0, W - r.width) }
        if r.maxY > H { r.origin.y = max(0, H - r.height) }
        return r.intersection(bounds)
    }

    /// 畫面重點：先找人臉，找不到再用「注意力」顯著區域（0…1，左上為原點）。
    nonisolated static func focusPoint(_ image: CGImage) -> CGPoint? {
        let handler = VNImageRequestHandler(cgImage: image, options: [:])
        let faces = VNDetectFaceRectanglesRequest()
        let saliency = VNGenerateAttentionBasedSaliencyImageRequest()
        try? handler.perform([faces, saliency])
        var boxes = (faces.results ?? []).map(\.boundingBox)
        if boxes.isEmpty { boxes = (saliency.results?.first?.salientObjects ?? []).map(\.boundingBox) }
        guard !boxes.isEmpty else { return nil }
        let union = boxes.reduce(CGRect.null) { $0.union($1) }
        return CGPoint(x: union.midX, y: 1 - union.midY)
    }

    nonisolated static func job(_ o: ITCropOptions) -> ToolJob {
        .perFile { file, context in
            [try ITPipeline.run(file, context: context, tag: "已裁剪", output: { source in
                ITOutput(format: source.preferredOutputFormat, quality: 0.92, metadata: .keep, keepsAlpha: source.hasAlpha)
            }, transform: { image, _ in
                let focus = o.rect == nil && o.anchor == .smart && o.aspect.ratio != nil ? focusPoint(image) : nil
                let r = pixelRect(width: image.width, height: image.height, options: o, focus: focus)
                guard r.width >= 1, r.height >= 1, let cropped = image.cropping(to: r) else {
                    throw ConversionError.other("裁剪範圍不正確。")
                }
                return cropped
            })]
        }
    }
}

private struct ITCropOptionsView: View {
    @ObservedObject var session: ITCropSession

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            OptionGroup(title: "比例", systemImage: "aspectratio") {
                ChoiceChips(selection: $session.aspect, choices: ITAspect.allCases.map {
                    OptionChoice(value: $0, title: $0.title, subtitle: $0.hint)
                })
                Text(session.aspect.hint).font(.caption).foregroundStyle(.secondary)
            }
            if session.fileCount == 1 {
                OptionGroup(title: "裁剪範圍", systemImage: "crop") {
                    if let text = session.pixelSizeText { ITInfoRow(title: "輸出尺寸", value: text) }
                    if let s = session.imageSize { ITInfoRow(title: "原始尺寸", value: ITText.pixels(Int(s.width), Int(s.height))) }
                    Button { session.resetRect() } label: {
                        Label("重設裁剪框", systemImage: "arrow.counterclockwise")
                    }
                    .controlSize(.small)
                }
                OptionNote(text: "在圖片上拖曳裁剪框移動位置，拖曳四角調整大小。", systemImage: "hand.draw")
            } else if session.fileCount > 1 {
                OptionGroup(title: "裁切位置", systemImage: "viewfinder") {
                    SegmentedOption(selection: $session.anchor, choices: [
                        OptionChoice(value: .smart, title: "智慧"),
                        OptionChoice(value: .center, title: "置中"),
                        OptionChoice(value: .top, title: "靠上"),
                    ])
                    Text(anchorHint).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                }
                OptionNote(text: "多張圖片時，每張都會依所選比例裁切出最大的範圍。", systemImage: "square.on.square")
            }
        }
    }

    private var anchorHint: String {
        switch session.anchor {
        case .smart: return "自動找出人臉或主體，讓它留在畫面中。"
        case .center: return "從正中央裁切。"
        case .top: return "保留上方（適合直式人像、文件）。"
        }
    }
}

// MARK: - 裁剪框編輯器

private enum ITCropHandle: CaseIterable {
    case topLeft, top, topRight, right, bottomRight, bottom, bottomLeft, left

    var isCorner: Bool { [.topLeft, .topRight, .bottomLeft, .bottomRight].contains(self) }

    func point(in r: CGRect) -> CGPoint {
        switch self {
        case .topLeft: return CGPoint(x: r.minX, y: r.minY)
        case .top: return CGPoint(x: r.midX, y: r.minY)
        case .topRight: return CGPoint(x: r.maxX, y: r.minY)
        case .right: return CGPoint(x: r.maxX, y: r.midY)
        case .bottomRight: return CGPoint(x: r.maxX, y: r.maxY)
        case .bottom: return CGPoint(x: r.midX, y: r.maxY)
        case .bottomLeft: return CGPoint(x: r.minX, y: r.maxY)
        case .left: return CGPoint(x: r.minX, y: r.midY)
        }
    }
}

struct ITCropEditor: View {
    @ObservedObject var session: ITCropSession
    let url: URL
    var onRemove: () -> Void
    @State private var preview: CGImage?
    @State private var failed = false
    @State private var dragStart: CGRect?
    @Environment(\.toolTint) private var tint

    private let minSide: CGFloat = 24

    var body: some View {
        VStack(spacing: 12) {
            GeometryReader { geo in
                let size = fitted(in: CGSize(width: geo.size.width - 24, height: geo.size.height - 24))
                ZStack {
                    if let preview, size.width > 0 {
                        editor(preview, size: size)
                    } else if failed {
                        Label("無法預覽這張圖片", systemImage: "exclamationmark.triangle").foregroundStyle(.secondary)
                    } else {
                        ProgressView()
                    }
                }
                .frame(width: geo.size.width, height: geo.size.height)
            }
            .frame(minHeight: 300)
            .background(RoundedRectangle(cornerRadius: 12, style: .continuous).fill(Color.primary.opacity(0.04)))

            HStack(spacing: 10) {
                Image(systemName: "photo").foregroundStyle(tint)
                Text(url.lastPathComponent).lineLimit(1).truncationMode(.middle)
                Spacer(minLength: 8)
                if let text = session.pixelSizeText {
                    Text(text).monospacedDigit().foregroundStyle(.secondary)
                }
                Button { session.resetRect() } label: { Image(systemName: "arrow.counterclockwise") }
                    .buttonStyle(.borderless).help("重設裁剪框")
                Button(role: .destructive, action: onRemove) { Image(systemName: "xmark.circle.fill") }
                    .buttonStyle(.borderless).foregroundStyle(.secondary).help("移除這張圖片")
            }
            .font(.callout)
        }
        .task(id: url) {
            let url = self.url
            let image = await Task.detached(priority: .userInitiated) { ITLoader.preview(of: url, maxPixelSize: 1800) }.value
            preview = image
            failed = image == nil
        }
    }

    private func fitted(in box: CGSize) -> CGSize {
        let s = session.imageSize ?? preview.map { CGSize(width: $0.width, height: $0.height) } ?? .zero
        guard s.width > 0, s.height > 0, box.width > 0, box.height > 0 else { return .zero }
        let scale = min(box.width / s.width, box.height / s.height)
        return CGSize(width: (s.width * scale).rounded(), height: (s.height * scale).rounded())
    }

    private func editor(_ image: CGImage, size: CGSize) -> some View {
        let n = session.rect
        let frame = CGRect(x: n.minX * size.width, y: n.minY * size.height, width: n.width * size.width, height: n.height * size.height)
        return ZStack(alignment: .topLeading) {
            Image(decorative: image, scale: 1)
                .resizable()
                .interpolation(.high)
                .frame(width: size.width, height: size.height)
            Path { p in
                p.addRect(CGRect(origin: .zero, size: size))
                p.addRect(frame)
            }
            .fill(Color.black.opacity(0.55), style: FillStyle(eoFill: true))
            .allowsHitTesting(false)
            Path { p in
                for i in 1...2 {
                    let x = frame.minX + frame.width * CGFloat(i) / 3
                    let y = frame.minY + frame.height * CGFloat(i) / 3
                    p.move(to: CGPoint(x: x, y: frame.minY)); p.addLine(to: CGPoint(x: x, y: frame.maxY))
                    p.move(to: CGPoint(x: frame.minX, y: y)); p.addLine(to: CGPoint(x: frame.maxX, y: y))
                }
            }
            .stroke(Color.white.opacity(0.45), lineWidth: 0.75)
            .allowsHitTesting(false)
            Rectangle()
                .strokeBorder(Color.white, lineWidth: 1.5)
                .background(Color.white.opacity(0.001))
                .frame(width: frame.width, height: frame.height)
                .position(x: frame.midX, y: frame.midY)
                .gesture(moveGesture(size: size))
                .help("拖曳移動裁剪框")
            ForEach(ITCropHandle.allCases.filter { $0.isCorner || session.aspect == .free }, id: \.self) { handle in
                handleView(handle)
                    .position(handle.point(in: frame))
                    .gesture(resizeGesture(handle, size: size))
            }
        }
        .frame(width: size.width, height: size.height)
        .shadow(color: .black.opacity(0.15), radius: 6, y: 2)
    }

    private func handleView(_ handle: ITCropHandle) -> some View {
        let w: CGFloat = handle.isCorner ? 14 : (handle == .top || handle == .bottom ? 22 : 6)
        let h: CGFloat = handle.isCorner ? 14 : (handle == .left || handle == .right ? 22 : 6)
        return RoundedRectangle(cornerRadius: 3, style: .continuous)
            .fill(Color.white)
            .overlay(RoundedRectangle(cornerRadius: 3, style: .continuous).strokeBorder(tint, lineWidth: 1.5))
            .frame(width: w, height: h)
            .shadow(color: .black.opacity(0.35), radius: 2)
            .frame(width: 30, height: 30)
            .contentShape(Rectangle())
    }

    private func moveGesture(size: CGSize) -> some Gesture {
        DragGesture(minimumDistance: 1)
            .onChanged { value in
                let start = dragStart ?? session.rect
                if dragStart == nil { dragStart = start }
                var r = start
                r.origin.x = min(max(0, start.minX + value.translation.width / size.width), 1 - start.width)
                r.origin.y = min(max(0, start.minY + value.translation.height / size.height), 1 - start.height)
                session.rect = r
            }
            .onEnded { _ in dragStart = nil }
    }

    private func resizeGesture(_ handle: ITCropHandle, size: CGSize) -> some Gesture {
        DragGesture(minimumDistance: 0)
            .onChanged { value in
                let start = dragStart ?? session.rect
                if dragStart == nil { dragStart = start }
                let s = CGRect(x: start.minX * size.width, y: start.minY * size.height,
                               width: start.width * size.width, height: start.height * size.height)
                let r = resized(s, handle: handle, by: value.translation, bounds: size)
                session.rect = CGRect(x: r.minX / size.width, y: r.minY / size.height, width: r.width / size.width, height: r.height / size.height)
            }
            .onEnded { _ in dragStart = nil }
    }

    /// 以對角（或對邊）為固定點調整大小；有固定比例時維持比例。
    private func resized(_ s: CGRect, handle: ITCropHandle, by t: CGSize, bounds: CGSize) -> CGRect {
        func clampX(_ x: CGFloat) -> CGFloat { min(max(0, x), bounds.width) }
        func clampY(_ y: CGFloat) -> CGFloat { min(max(0, y), bounds.height) }
        switch handle {
        case .top:
            let y = min(clampY(s.minY + t.height), s.maxY - minSide)
            return CGRect(x: s.minX, y: y, width: s.width, height: s.maxY - y)
        case .bottom:
            let y = max(clampY(s.maxY + t.height), s.minY + minSide)
            return CGRect(x: s.minX, y: s.minY, width: s.width, height: y - s.minY)
        case .left:
            let x = min(clampX(s.minX + t.width), s.maxX - minSide)
            return CGRect(x: x, y: s.minY, width: s.maxX - x, height: s.height)
        case .right:
            let x = max(clampX(s.maxX + t.width), s.minX + minSide)
            return CGRect(x: s.minX, y: s.minY, width: x - s.minX, height: s.height)
        default:
            break
        }
        let moving = handle.point(in: s)
        let anchor: CGPoint
        switch handle {
        case .topLeft: anchor = CGPoint(x: s.maxX, y: s.maxY)
        case .topRight: anchor = CGPoint(x: s.minX, y: s.maxY)
        case .bottomLeft: anchor = CGPoint(x: s.maxX, y: s.minY)
        default: anchor = CGPoint(x: s.minX, y: s.minY)
        }
        let dirX: CGFloat = moving.x >= anchor.x ? 1 : -1
        let dirY: CGFloat = moving.y >= anchor.y ? 1 : -1
        let p = CGPoint(x: clampX(moving.x + t.width), y: clampY(moving.y + t.height))
        var w = max(minSide, (p.x - anchor.x) * dirX)
        var h = max(minSide, (p.y - anchor.y) * dirY)
        w = min(w, dirX > 0 ? bounds.width - anchor.x : anchor.x)
        h = min(h, dirY > 0 ? bounds.height - anchor.y : anchor.y)
        if let ratio = session.aspect.ratio {
            if w / h > ratio { w = h * ratio } else { h = w / ratio }
        }
        return CGRect(x: dirX > 0 ? anchor.x : anchor.x - w, y: dirY > 0 ? anchor.y : anchor.y - h, width: w, height: h)
    }
}
