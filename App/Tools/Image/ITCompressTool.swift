import SwiftUI

// MARK: - 壓縮圖片

enum ITCompressLevel: String, Sendable, CaseIterable {
    case maximum, recommended, light
}

enum ITCompressTarget: String, Sendable {
    case keep, jpeg, heic
}

struct ITCompressOptions: Sendable {
    var level: ITCompressLevel = .recommended
    var target: ITCompressTarget = .keep
    var stripMetadata = false
}

extension ImageToolCatalog {
    static let compress = ToolDefinition(
        id: "image.compress",
        category: .image,
        title: "壓縮圖片",
        subtitle: "在畫質與檔案大小間取得最佳平衡，JPG、PNG、HEIC 都能大幅縮小；絕不輸出比原檔更大的檔案。",
        symbol: "arrow.down.right.and.arrow.up.left",
        tint: ToolPalette.excel,
        badge: "IMG",
        inputTypes: [.image],
        inputLabel: "圖片",
        actionTitle: "壓縮圖片",
        keywords: ["compress", "optimize", "reduce", "smaller", "shrink", "壓縮", "縮小檔案", "最佳化", "減肥"],
        quickActions: [
            "image.compress-recommended": { ITCompressSession.job(ITCompressOptions()) },
        ],
        makeSession: { ITCompressSession() }
    )
}

final class ITCompressSession: ToolSession {
    @Published var level: ITCompressLevel = .recommended
    @Published var target: ITCompressTarget = .keep
    @Published var stripMetadata = false
    @Published var totalSize: Int64 = 0
    @Published var hasPNG = false

    override func filesDidChange(_ files: [URL]) {
        totalSize = files.reduce(0) { $0 + ToolFiles.size(of: $1) }
        hasPNG = files.contains { ToolFiles.contentType(of: $0).conforms(to: .png) }
    }

    override func optionsView() -> AnyView { AnyView(ITCompressOptionsView(session: self)) }

    override func makeJob() -> ToolJob {
        Self.job(ITCompressOptions(level: level, target: target, stripMetadata: stripMetadata))
    }

    nonisolated static func job(_ o: ITCompressOptions) -> ToolJob {
        ToolJob { files, context in
            var before: Int64 = 0, after: Int64 = 0
            var outputs: [URL] = []
            for (i, file) in files.enumerated() {
                try context.checkCancellation()
                let r = try compress(file, o, context: context.segment(i, of: files.count))
                outputs.append(r.url)
                before += r.before
                after += r.after
            }
            if files.count > 1 { context.progress(1, summary(before: before, after: after, total: true)) }
            return outputs
        }
    }

    nonisolated static func summary(before: Int64, after: Int64, total: Bool = false) -> String {
        let prefix = total ? "共" : ""
        guard before > 0, after < before else { return "已是最精簡的大小，保留原始內容" }
        let saved = Double(before - after) / Double(before)
        return "\(prefix)省下 \(ITText.percent(saved))（\(ITText.size(before)) → \(ITText.size(after))）"
    }

    /// 各格式在各壓縮程度的品質。
    nonisolated static func quality(_ format: ITFormat, _ level: ITCompressLevel) -> Double {
        switch (format, level) {
        case (.jpeg, .maximum): return 0.5
        case (.jpeg, .recommended): return 0.7
        case (.jpeg, .light): return 0.85
        case (.heic, .maximum), (.avif, .maximum), (.webp, .maximum): return 0.4
        case (.heic, .recommended), (.avif, .recommended), (.webp, .recommended): return 0.55
        default: return 0.72
        }
    }

    nonisolated static func compress(_ file: URL, _ o: ITCompressOptions, context: ToolContext) throws -> (url: URL, before: Int64, after: Int64) {
        context.progress(0.02, "讀取：\(file.lastPathComponent)")
        let source = try ITLoader.load(file, allFrames: ToolFiles.contentType(of: file).conforms(to: .gif))
        let original = source.fileSize
        try context.checkCancellation()

        let format: ITFormat
        switch o.target {
        case .jpeg: format = .jpeg
        case .heic: format = ITFormat.heic.isWritable ? .heic : .jpeg
        case .keep:
            switch source.format {
            case .some(let f) where [.jpeg, .png, .heic, .avif, .tiff, .gif].contains(f) && f.isWritable: format = f
            case .some(.bmp): format = .png
            default: format = source.hasAlpha ? .png : .jpeg
            }
        }
        let work = try context.makeWorkDirectory()
        defer { try? FileManager.default.removeItem(at: work) }
        let ext = source.outputExtension(for: format)
        let tmp = work.appendingPathComponent("compressed.\(ext)")
        let metadata: ITMetadataMode = o.stripMetadata ? .removeAll : .keep
        context.progress(0.15, "壓縮中：\(file.lastPathComponent)")

        if format == .png {
            let colors = o.level == .maximum ? 128 : 256
            let palette = try ITPalettePNG.write(source.image, maxColors: colors, dither: true, losslessOnly: o.level == .light,
                                                 dpi: source.dpi, to: tmp, checkCancellation: { try context.checkCancellation() })
            if palette == nil {
                try ITWriter.write(source.image, source: source, to: tmp,
                                   output: ITOutput(format: .png, metadata: metadata, keepsAlpha: source.hasAlpha))
            }
        } else {
            let frames = format == .gif ? source.frames : [source.frames[0]]
            try ITWriter.write(frames, source: source, to: tmp,
                               output: ITOutput(format: format, quality: quality(format, o.level), metadata: metadata,
                                                background: .white, keepsAlpha: source.hasAlpha))
        }
        try context.checkCancellation()
        context.progress(0.85)

        let compressed = ITText.fileSize(tmp)
        if compressed > 0 && compressed < original {
            let message = summary(before: original, after: compressed)
            let url = try ITPipeline.deliver(file: tmp, for: file, context: context, tag: "已壓縮", ext: ext)
            context.progress(1, message)
            return (url, original, compressed)
        }

        // 壓縮後沒有比較小：保留原始內容（需要時只移除相片資訊，不重新壓縮）
        let originalExt = file.pathExtension.isEmpty ? (source.format?.fileExtension ?? "img") : file.pathExtension.lowercased()
        var keep = work.appendingPathComponent("original.\(originalExt)")
        var keptSize = original
        if o.stripMetadata, let r = try? ITMetadataStripper.strip(file, mode: .all, to: work.appendingPathComponent("stripped")),
           r.ext == originalExt, ITText.fileSize(r.file) <= original {
            keep = r.file
            keptSize = ITText.fileSize(r.file)
        } else {
            try FileManager.default.copyItem(at: file, to: keep)
        }
        let url = try ITPipeline.deliver(file: keep, for: file, context: context, tag: "已壓縮", ext: originalExt)
        let message = keptSize < original ? summary(before: original, after: keptSize) : "已是最精簡的大小，保留原始內容（\(ITText.size(original))）"
        context.progress(1, message)
        return (url, original, keptSize)
    }
}

private struct ITCompressOptionsView: View {
    @ObservedObject var session: ITCompressSession

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            OptionGroup(title: "壓縮程度", systemImage: "gauge.with.dots.needle.67percent") {
                ChoiceCards(selection: $session.level, choices: [
                    OptionChoice(value: .maximum, title: "最高壓縮", subtitle: "檔案最小，畫質略降。適合傳訊息、上傳網頁或寄信。",
                                 systemImage: "arrow.down.to.line.compact"),
                    OptionChoice(value: .recommended, title: "建議", subtitle: "畫質與大小兼顧，肉眼幾乎看不出差別。",
                                 systemImage: "hand.thumbsup", badge: "推薦"),
                    OptionChoice(value: .light, title: "保留品質", subtitle: "只做輕微、近乎無損的最佳化，縮小幅度較少。",
                                 systemImage: "sparkles"),
                ])
            }
            OptionGroup(title: "轉成更省空間的格式", systemImage: "arrow.triangle.2.circlepath") {
                SegmentedOption(selection: $session.target, choices: [
                    OptionChoice(value: .keep, title: "維持原格式"),
                    OptionChoice(value: .jpeg, title: "JPG"),
                    OptionChoice(value: .heic, title: "HEIC"),
                ])
                Text(targetHint).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            OptionGroup(title: "相片資訊", systemImage: "info.circle") {
                OptionToggle(title: "移除相片資訊", subtitle: "EXIF、GPS 位置等，可再省一點空間並保護隱私", isOn: $session.stripMetadata)
            }
            if session.hasPNG && session.target == .keep {
                OptionNote(text: session.level == .light
                           ? "PNG：顏色不超過 256 色時改存成調色盤 PNG（完全無損）。"
                           : "PNG 會減色成調色盤（最多 \(session.level == .maximum ? 128 : 256) 色）並保留透明，截圖與圖表效果最好。",
                           systemImage: "paintpalette")
            }
            OptionNote(text: "如果壓縮後反而變大，會保留原始內容，絕不輸出比原檔更大的檔案。"
                       + (session.totalSize > 0 ? "\n目前共 \(ITText.size(session.totalSize))。" : ""),
                       systemImage: "checkmark.shield")
        }
    }

    private var targetHint: String {
        switch session.target {
        case .keep: return "JPG 維持 JPG、PNG 維持 PNG；BMP 會改存成 PNG。"
        case .jpeg: return "相容性最好；透明的部分會變成白色。"
        case .heic: return "同畫質約為 JPG 的一半大小，Apple 裝置與新版 Windows 可直接開啟。"
        }
    }
}
