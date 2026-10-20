import SwiftUI

// MARK: - 調整大小

enum ITResizeMode: String, Sendable {
    case pixels, percent, preset
}

/// 固定框（寬×高）時的放法。
enum ITResizeFit: String, Sendable {
    /// 填滿並裁掉多餘部分
    case fill
    /// 完整放入，空白處補底色
    case fit
    /// 拉伸成剛好的尺寸（會變形）
    case stretch
}

struct ITResizePreset: Identifiable, Hashable, Sendable {
    let id: String
    let title: String
    let subtitle: String
    let width: Int?
    let height: Int?

    static let all: [ITResizePreset] = [
        ITResizePreset(id: "w1920", title: "1920 寬", subtitle: "Full HD、簡報、桌布", width: 1920, height: nil),
        ITResizePreset(id: "w1280", title: "1280 寬", subtitle: "網頁、部落格、Email", width: 1280, height: nil),
        ITResizePreset(id: "ig1080", title: "1080 × 1080", subtitle: "Instagram 貼文（正方形）", width: 1080, height: 1080),
        ITResizePreset(id: "ig1350", title: "1080 × 1350", subtitle: "Instagram 直式貼文（4:5）", width: 1080, height: 1350),
        ITResizePreset(id: "story", title: "1080 × 1920", subtitle: "限時動態、Reels（9:16）", width: 1080, height: 1920),
        ITResizePreset(id: "og1200", title: "1200 × 630", subtitle: "社群分享預覽（Facebook、LINE、X）", width: 1200, height: 630),
        ITResizePreset(id: "icon512", title: "512 × 512", subtitle: "App 圖示、頭像", width: 512, height: 512),
    ]

    static func named(_ id: String) -> ITResizePreset { all.first { $0.id == id } ?? all[0] }
}

struct ITResizeOptions: Sendable {
    var mode: ITResizeMode = .percent
    var width = 1920
    var height = 1080
    var lockAspect = true
    var percent: Double = 50
    var preset = "w1920"
    var fit: ITResizeFit = .fill
    var padColor: ITRGBA = .white
    var noUpscale = true
    var smooth = true
}

/// 計算好的縮放結果：先把原圖縮放成 scaled，再放到 canvas 的 offset（左上為原點，可為負＝裁掉）。
struct ITResizePlan: Equatable {
    var canvasWidth: Int
    var canvasHeight: Int
    var scaledWidth: Int
    var scaledHeight: Int
    var offsetX: Int = 0
    var offsetY: Int = 0
    /// true：縮放後從中間裁出畫布（填滿）；false：放在畫布上補邊（完整放入）
    var crops = false

    var isIdentity: Bool { offsetX == 0 && offsetY == 0 && canvasWidth == scaledWidth && canvasHeight == scaledHeight }

    static func make(width w: Int, height h: Int, options o: ITResizeOptions) -> ITResizePlan {
        let W = Double(w), H = Double(h)
        func proportional(_ s: Double) -> ITResizePlan {
            let s2 = o.noUpscale ? min(s, 1) : s
            let sw = max(1, Int((W * s2).rounded())), sh = max(1, Int((H * s2).rounded()))
            return ITResizePlan(canvasWidth: sw, canvasHeight: sh, scaledWidth: sw, scaledHeight: sh)
        }
        func box(_ bw: Int, _ bh: Int, _ fit: ITResizeFit) -> ITResizePlan {
            let BW = Double(bw), BH = Double(bh)
            switch fit {
            case .stretch:
                let tw = o.noUpscale ? min(bw, w) : bw, th = o.noUpscale ? min(bh, h) : bh
                return ITResizePlan(canvasWidth: tw, canvasHeight: th, scaledWidth: tw, scaledHeight: th)
            case .fill:
                // 原圖中最大的、符合目標比例的區域
                let cropW = min(W, H * BW / BH), cropH = cropW * BH / BW
                var outW = BW, outH = BH
                if o.noUpscale && cropW < BW { outW = cropW; outH = cropH }
                let s = outW / cropW
                let sw = max(1, Int((W * s).rounded())), sh = max(1, Int((H * s).rounded()))
                let cw = max(1, Int(outW.rounded())), ch = max(1, Int(outH.rounded()))
                return ITResizePlan(canvasWidth: min(cw, sw), canvasHeight: min(ch, sh), scaledWidth: sw, scaledHeight: sh,
                                    offsetX: (min(cw, sw) - sw) / 2, offsetY: (min(ch, sh) - sh) / 2, crops: true)
            case .fit:
                var s = min(BW / W, BH / H)
                var cw = BW, ch = BH
                if o.noUpscale && s > 1 {
                    s = 1
                    // 不放大：畫布縮成剛好容納原圖、且維持目標比例
                    if W / H > BW / BH { cw = W; ch = W * BH / BW } else { ch = H; cw = H * BW / BH }
                }
                let sw = max(1, Int((W * s).rounded())), sh = max(1, Int((H * s).rounded()))
                let icw = max(sw, Int(cw.rounded())), ich = max(sh, Int(ch.rounded()))
                return ITResizePlan(canvasWidth: icw, canvasHeight: ich, scaledWidth: sw, scaledHeight: sh,
                                    offsetX: (icw - sw) / 2, offsetY: (ich - sh) / 2)
            }
        }
        switch o.mode {
        case .percent:
            return proportional(max(o.percent, 1) / 100)
        case .pixels:
            let bw = max(o.width, 0), bh = max(o.height, 0)
            if o.lockAspect || bw == 0 || bh == 0 {
                if bw > 0 && bh > 0 { return proportional(min(Double(bw) / W, Double(bh) / H)) }
                if bw > 0 { return proportional(Double(bw) / W) }
                if bh > 0 { return proportional(Double(bh) / H) }
                return proportional(1)
            }
            return box(bw, bh, .stretch)
        case .preset:
            let p = ITResizePreset.named(o.preset)
            if let pw = p.width, let ph = p.height { return box(pw, ph, o.fit) }
            if let pw = p.width { return proportional(Double(pw) / W) }
            if let ph = p.height { return proportional(Double(ph) / H) }
            return proportional(1)
        }
    }
}

extension ImageToolCatalog {
    static let resize = ToolDefinition(
        id: "image.resize",
        category: .image,
        title: "調整大小",
        subtitle: "依像素、百分比或社群常用尺寸（IG、限動、分享預覽）調整圖片大小，可批次處理。",
        symbol: "arrow.up.left.and.arrow.down.right",
        tint: ToolPalette.word,
        badge: "IMG",
        inputTypes: [.image],
        inputLabel: "圖片",
        actionTitle: "調整大小",
        keywords: ["resize", "scale", "dimension", "instagram", "縮小", "放大", "尺寸", "解析度", "縮圖"],
        quickActions: [
            "image.resize-half": { ITResizeSession.job(ITResizeOptions(mode: .percent, percent: 50)) },
        ],
        makeSession: { ITResizeSession() }
    )
}

final class ITResizeSession: ToolSession {
    @Published var mode: ITResizeMode = .percent
    @Published var width = 1920 { didSet { syncHeight() } }
    @Published var height = 1080 { didSet { syncWidth() } }
    @Published var lockAspect = true { didSet { if lockAspect { syncHeight() } } }
    @Published var percentChoice = 50
    @Published var customPercent: Double = 60
    @Published var preset = "w1920"
    @Published var fit: ITResizeFit = .fill
    @Published var padChoice: ITColorChoice = .white
    @Published var padCustom = Color.gray
    @Published var noUpscale = true
    @Published var smooth = true
    /// 第一張圖的像素尺寸（已轉正）
    @Published var firstSize: CGSize?
    @Published var fileCount = 0
    private var syncing = false

    override func filesDidChange(_ files: [URL]) {
        fileCount = files.count
        firstSize = files.first.flatMap { ITLoader.pixelSize(of: $0) }
        if let s = firstSize {
            syncing = true
            width = Int(s.width)
            height = Int(s.height)
            syncing = false
        }
    }

    private func syncHeight() {
        guard lockAspect, !syncing, let s = firstSize, s.width > 0 else { return }
        syncing = true
        height = max(1, Int((Double(width) * s.height / s.width).rounded()))
        syncing = false
    }

    private func syncWidth() {
        guard lockAspect, !syncing, let s = firstSize, s.height > 0 else { return }
        syncing = true
        width = max(1, Int((Double(height) * s.width / s.height).rounded()))
        syncing = false
    }

    var options: ITResizeOptions {
        ITResizeOptions(mode: mode, width: width, height: height, lockAspect: lockAspect,
                        percent: percentChoice == 0 ? customPercent : Double(percentChoice),
                        preset: preset, fit: fit, padColor: padChoice.resolve(custom: padCustom), noUpscale: noUpscale, smooth: smooth)
    }

    /// 第一張圖的輸出尺寸預覽
    var previewText: String? {
        guard let s = firstSize else { return nil }
        let plan = ITResizePlan.make(width: Int(s.width), height: Int(s.height), options: options)
        return "\(Int(s.width)) × \(Int(s.height)) → \(plan.canvasWidth) × \(plan.canvasHeight) px"
    }

    override func validate(_ files: [URL]) -> String? {
        if mode == .pixels {
            if width <= 0 && height <= 0 { return "請輸入寬度或高度" }
            if width > 32_768 || height > 32_768 { return "尺寸上限為 32768 px" }
            if !lockAspect && (width <= 0 || height <= 0) { return "不鎖定比例時請輸入寬度與高度" }
        }
        return nil
    }

    override func optionsView() -> AnyView { AnyView(ITResizeOptionsView(session: self)) }

    override func makeJob() -> ToolJob { Self.job(options) }

    nonisolated static func job(_ o: ITResizeOptions) -> ToolJob {
        .perFile { file, context in
            [try ITPipeline.run(file, context: context, tag: "已調整大小", output: { source in
                let plan = ITResizePlan.make(width: source.width, height: source.height, options: o)
                let padded = !plan.crops && (plan.canvasWidth > plan.scaledWidth || plan.canvasHeight > plan.scaledHeight)
                let format = source.preferredOutputFormat
                return ITOutput(format: format, quality: 0.92, metadata: .keep, background: o.padColor.isOpaque ? o.padColor : .white,
                                keepsAlpha: source.hasAlpha || (padded && !o.padColor.isOpaque))
            }, transform: { image, _ in
                try resize(image, plan: ITResizePlan.make(width: image.width, height: image.height, options: o), options: o)
            })]
        }
    }

    nonisolated static func resize(_ image: CGImage, plan: ITResizePlan, options o: ITResizeOptions) throws -> CGImage {
        let scaled = try ITScale.resample(image, width: plan.scaledWidth, height: plan.scaledHeight, smooth: o.smooth)
        if plan.isIdentity { return scaled }
        if plan.crops {
            // 填滿：從中間裁出畫布大小
            let rect = CGRect(x: -plan.offsetX, y: -plan.offsetY, width: plan.canvasWidth, height: plan.canvasHeight)
            guard let cropped = scaled.cropping(to: rect) else { throw ConversionError.other("裁切失敗。") }
            return cropped
        }
        // 完整放入：補邊
        let opaque = o.padColor.isOpaque && !CVImageConverter.hasAlpha(image)
        let ctx = try ITRender.canvas(width: plan.canvasWidth, height: plan.canvasHeight, like: image, opaque: opaque)
        if o.padColor.alpha > 0 {
            ctx.setFillColor(o.padColor.cgColor)
            ctx.fill(CGRect(x: 0, y: 0, width: plan.canvasWidth, height: plan.canvasHeight))
        }
        let y = plan.canvasHeight - plan.offsetY - plan.scaledHeight
        ctx.draw(scaled, in: CGRect(x: plan.offsetX, y: y, width: plan.scaledWidth, height: plan.scaledHeight))
        guard let out = ctx.makeImage() else { throw ConversionError.other("無法產生圖片。") }
        return out
    }
}

private struct ITResizeOptionsView: View {
    @ObservedObject var session: ITResizeSession

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            OptionGroup(title: "調整方式", systemImage: "arrow.up.left.and.arrow.down.right") {
                ChoiceCards(selection: $session.mode, choices: [
                    OptionChoice(value: .pixels, title: "依像素", subtitle: "輸入寬度、高度", systemImage: "ruler"),
                    OptionChoice(value: .percent, title: "依百分比", subtitle: "等比例縮放", systemImage: "percent"),
                    OptionChoice(value: .preset, title: "常用尺寸", subtitle: "IG、限動、分享預覽、圖示", systemImage: "square.grid.2x2"),
                ])
            }
            switch session.mode {
            case .pixels: pixels
            case .percent: percent
            case .preset: presets
            }
            OptionGroup(title: "選項", systemImage: "slider.horizontal.3") {
                OptionToggle(title: "不放大", subtitle: "比原圖小的尺寸才縮放，避免變模糊", isOn: $session.noUpscale)
                OptionToggle(title: "平滑縮放（高品質）", subtitle: "關閉時用最近鄰取樣，適合像素風圖片", isOn: $session.smooth)
            }
            if let preview = session.previewText {
                OptionNote(text: (session.fileCount > 1 ? "第一張：" : "") + preview, systemImage: "aspectratio")
            }
        }
    }

    private var pixels: some View {
        OptionGroup(title: "尺寸", systemImage: "ruler") {
            HStack(alignment: .bottom, spacing: 8) {
                ITNumberField(title: "寬度", value: $session.width)
                Button { session.lockAspect.toggle() } label: {
                    Image(systemName: session.lockAspect ? "lock.fill" : "lock.open")
                        .frame(width: 22, height: 22)
                }
                .buttonStyle(.borderless)
                .help(session.lockAspect ? "已鎖定比例" : "未鎖定比例")
                .padding(.bottom, 2)
                ITNumberField(title: "高度", value: $session.height)
            }
            OptionToggle(title: "鎖定長寬比例", isOn: $session.lockAspect)
            if session.fileCount > 1 && session.lockAspect {
                Text("每張圖片都會等比例縮放到這個範圍內。").font(.caption).foregroundStyle(.secondary)
            }
        }
    }

    private var percent: some View {
        OptionGroup(title: "縮放比例", systemImage: "percent") {
            ChoiceChips(selection: $session.percentChoice, choices: [
                OptionChoice(value: 25, title: "25%"),
                OptionChoice(value: 50, title: "50%"),
                OptionChoice(value: 75, title: "75%"),
                OptionChoice(value: 0, title: "自訂"),
            ])
            if session.percentChoice == 0 {
                OptionSlider(title: "自訂比例", value: $session.customPercent, range: 5...400, step: 5, format: ITOptionFormat.percent)
            }
        }
    }

    private var presets: some View {
        let selected = ITResizePreset.named(session.preset)
        return VStack(alignment: .leading, spacing: 20) {
            OptionGroup(title: "常用尺寸", systemImage: "square.grid.2x2") {
                ChoiceCards(selection: $session.preset, choices: ITResizePreset.all.map {
                    OptionChoice(value: $0.id, title: $0.title, subtitle: $0.subtitle)
                })
            }
            if selected.width != nil && selected.height != nil {
                OptionGroup(title: "比例不同時", systemImage: "crop") {
                    SegmentedOption(selection: $session.fit, choices: [
                        OptionChoice(value: .fill, title: "裁切填滿"),
                        OptionChoice(value: .fit, title: "完整放入"),
                        OptionChoice(value: .stretch, title: "拉伸"),
                    ])
                    Text(fitHint).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                    if session.fit == .fit {
                        ITColorPicker(choice: $session.padChoice, custom: $session.padCustom, options: [.white, .black, .transparent, .custom])
                    }
                }
            }
        }
    }

    private var fitHint: String {
        switch session.fit {
        case .fill: return "從中間裁掉多出來的部分，畫面填滿不留白。"
        case .fit: return "完整保留整張圖，空白處補上底色。"
        case .stretch: return "強制變成這個尺寸，圖片可能變形。"
        }
    }
}
