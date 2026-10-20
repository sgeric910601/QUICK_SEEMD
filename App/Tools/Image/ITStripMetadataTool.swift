import ImageIO
import SwiftUI

// MARK: - 移除相片資訊

enum ITStripMode: String, Sendable {
    /// 只移除 GPS 與地點
    case location
    /// 移除全部（保留方向與色彩描述檔，畫面不會變）
    case all
}

extension ImageToolCatalog {
    static let stripMetadata = ToolDefinition(
        id: "image.strip-metadata",
        category: .image,
        title: "移除相片資訊",
        subtitle: "清除 EXIF、GPS 位置、相機型號、拍攝時間等隱私資訊；JPG 等格式直接移除、不重新壓縮。",
        symbol: "location.slash",
        tint: ToolPalette.teal,
        badge: "EXIF",
        inputTypes: [.image],
        inputLabel: "圖片",
        actionTitle: "移除相片資訊",
        keywords: ["exif", "metadata", "gps", "location", "privacy", "隱私", "定位", "位置", "中繼資料"],
        quickActions: [
            "image.strip-location": { ITStripMetadataSession.job(.location) },
        ],
        makeSession: { ITStripMetadataSession() }
    )
}

final class ITStripMetadataSession: ToolSession {
    @Published var mode: ITStripMode = .all
    /// 第一個檔案裡找到的資訊（顯示在選項面板）
    @Published var found: [String] = []
    @Published var fileCount = 0

    override func filesDidChange(_ files: [URL]) {
        fileCount = files.count
        found = files.first.map { ITMetadataStripper.categories(of: $0, mode: .all) } ?? []
    }

    override var actionTitleOverride: String? { mode == .location ? "移除位置資訊" : "移除相片資訊" }

    override func optionsView() -> AnyView { AnyView(ITStripMetadataOptionsView(session: self)) }

    override func makeJob() -> ToolJob { Self.job(mode) }

    nonisolated static func job(_ mode: ITStripMode) -> ToolJob {
        .perFile { file, context in
            context.progress(0.1, "讀取：\(file.lastPathComponent)")
            let work = try context.makeWorkDirectory()
            defer { try? FileManager.default.removeItem(at: work) }
            let tmp = work.appendingPathComponent("stripped")
            let report = try ITMetadataStripper.strip(file, mode: mode, to: tmp)
            try context.checkCancellation()
            let message = report.removed.isEmpty
                ? (mode == .location ? "沒有找到位置資訊，已輸出副本" : "沒有找到相片資訊，已輸出副本")
                : "已移除：" + report.removed.joined(separator: "、") + (report.lossless ? "（未重新壓縮）" : "")
            context.progress(0.95, message)
            let url = try ITPipeline.deliver(file: report.file, for: file, context: context,
                                             tag: mode == .location ? "已移除位置" : "已移除資訊", ext: report.ext)
            context.progress(1, message)
            return [url]
        }
    }
}

enum ITMetadataStripper {
    struct Report {
        let file: URL
        let ext: String
        let removed: [String]
        let lossless: Bool
    }

    /// 移除相片資訊。JPG／PNG／TIFF／HEIC 直接複製影像資料（不重新壓縮），其他格式才重新編碼。
    /// - Parameter out: 輸出路徑（不含副檔名，會依格式補上）
    static func strip(_ input: URL, mode: ITStripMode, to out: URL) throws -> Report {
        guard FileManager.default.fileExists(atPath: input.path) else { throw ConversionError.fileNotFound(input.lastPathComponent) }
        let removed = categories(of: input, mode: mode)
        if let src = CGImageSourceCreateWithURL(input as CFURL, nil), let type = CGImageSourceGetType(src),
           let format = ITFormat.of(typeIdentifier: type as String), [.jpeg, .png, .tiff, .heic].contains(format) {
            let ext = input.pathExtension.isEmpty ? format.fileExtension : input.pathExtension.lowercased()
            let target = out.appendingPathExtension(ext)
            if copyWithoutMetadata(src, type: type, mode: mode, to: target), verify(target, mode: mode) {
                return Report(file: target, ext: ext, removed: removed, lossless: true)
            }
            try? FileManager.default.removeItem(at: target)
        }
        // 其他格式：重新編碼（像素轉正，不寫入任何相片資訊）
        let source = try ITLoader.load(input, allFrames: ToolFiles.contentType(of: input).conforms(to: .gif))
        let format = source.preferredOutputFormat
        let ext = source.outputExtension(for: format)
        let target = out.appendingPathExtension(ext)
        // 有損格式重新編碼時，找出「不比原檔大」的最高品質（HEIC 的品質與大小不成線性，用二分搜尋）
        let output = { (q: Double) in
            ITOutput(format: format, quality: q, metadata: mode == .all ? .removeAll : .removeLocation, keepsAlpha: source.hasAlpha)
        }
        let budget = source.fileSize + source.fileSize / 20
        try ITWriter.write(source.frames, source: source, to: target, output: output(0.92))
        if format.isLossy && ITText.fileSize(target) > budget {
            var lo = 0.4, hi = 0.92, best: Double?
            for _ in 0..<5 {
                let q = (lo + hi) / 2
                try ITWriter.write(source.frames, source: source, to: target, output: output(q))
                if ITText.fileSize(target) <= budget { best = q; lo = q } else { hi = q }
            }
            try ITWriter.write(source.frames, source: source, to: target, output: output(best ?? lo))
        }
        return Report(file: target, ext: ext, removed: removed, lossless: false)
    }

    private static func copyWithoutMetadata(_ src: CGImageSource, type: CFString, mode: ITStripMode, to url: URL) -> Bool {
        let index = CGImageSourceGetPrimaryImageIndex(src)
        let meta = CGImageSourceCopyMetadataAtIndex(src, index, nil)
        let props = (CGImageSourceCopyPropertiesAtIndex(src, index, nil) as? [CFString: Any]) ?? [:]
        let newMeta: CGImageMetadata
        switch mode {
        case .location:
            newMeta = meta.map { ITMetadata.removingLocation($0) } ?? CGImageMetadataCreateMutable()
        case .all:
            // 只留下方向，畫面才不會轉錯（HEIC 收到空的資訊時會沿用原本的 EXIF，所以一定要寫入方向）
            let m = CGImageMetadataCreateMutable()
            let orientation = (props[kCGImagePropertyOrientation] as? Int) ?? 1
            CGImageMetadataSetValueMatchingImageProperty(m, kCGImagePropertyTIFFDictionary, kCGImagePropertyTIFFOrientation,
                                                         orientation as CFNumber)
            newMeta = m
        }
        guard let dest = CGImageDestinationCreateWithURL(url as CFURL, type, CGImageSourceGetCount(src), nil) else { return false }
        var opts: [CFString: Any] = [kCGImageDestinationMetadata: newMeta, kCGImageDestinationMergeMetadata: false]
        if mode == .all { opts[kCGImageMetadataShouldExcludeXMP] = true }
        var error: Unmanaged<CFError>?
        let ok = CGImageDestinationCopyImageSource(dest, src, opts as CFDictionary, &error)
        error?.release()
        return ok
    }

    /// 確認輸出真的沒有該移除的資訊（不同格式的 ImageIO 行為不一，保險起見再讀一次）。
    private static func verify(_ url: URL, mode: ITStripMode) -> Bool {
        guard let src = CGImageSourceCreateWithURL(url as CFURL, nil), CGImageSourceGetCount(src) > 0 else { return false }
        let index = CGImageSourceGetPrimaryImageIndex(src)
        let props = (CGImageSourceCopyPropertiesAtIndex(src, index, nil) as? [CFString: Any]) ?? [:]
        if props[kCGImagePropertyGPSDictionary] != nil { return false }
        if ITMetadata.locationTagCount(CGImageSourceCopyMetadataAtIndex(src, index, nil)) > 0 { return false }
        if mode == .all {
            let tiff = (props[kCGImagePropertyTIFFDictionary] as? [CFString: Any]) ?? [:]
            if tiff[kCGImagePropertyTIFFMake] != nil || tiff[kCGImagePropertyTIFFModel] != nil { return false }
            let exif = (props[kCGImagePropertyExifDictionary] as? [CFString: Any]) ?? [:]
            if exif[kCGImagePropertyExifDateTimeOriginal] != nil || exif[kCGImagePropertyExifLensModel] != nil { return false }
            let personal: Set<String> = ["photoshop:DateCreated", "xmp:CreateDate", "xmp:CreatorTool", "dc:creator", "tiff:Make", "tiff:Model"]
            let tags = CGImageSourceCopyMetadataAtIndex(src, index, nil).map { ITMetadata.tags($0) } ?? []
            if tags.contains(where: { personal.contains("\($0.prefix):\($0.name)") }) { return false }
        }
        return true
    }

    /// 檔案裡有哪些類別的資訊（依模式，給使用者看的文字）。
    static func categories(of url: URL, mode: ITStripMode) -> [String] {
        guard let src = CGImageSourceCreateWithURL(url as CFURL, nil), CGImageSourceGetCount(src) > 0 else { return [] }
        let index = CGImageSourceGetPrimaryImageIndex(src)
        let props = (CGImageSourceCopyPropertiesAtIndex(src, index, nil) as? [CFString: Any]) ?? [:]
        let tags = CGImageSourceCopyMetadataAtIndex(src, index, nil).map { ITMetadata.tags($0) } ?? []
        let names = Set(tags.map { "\($0.prefix):\($0.name)" })
        let hasLocation = props[kCGImagePropertyGPSDictionary] != nil || tags.contains { ITMetadata.isLocation(prefix: $0.prefix, name: $0.name) }
        if mode == .location { return hasLocation ? ["位置（GPS）"] : [] }
        var list: [String] = []
        if hasLocation { list.append("位置（GPS）") }
        if !names.isDisjoint(with: ["tiff:Make", "tiff:Model", "exifEX:LensModel", "exifEX:LensMake", "exifEX:BodySerialNumber", "exifAux:SerialNumber"]) {
            list.append("相機與鏡頭")
        }
        if !names.isDisjoint(with: ["exif:DateTimeOriginal", "exif:DateTimeDigitized", "tiff:DateTime", "xmp:CreateDate", "photoshop:DateCreated", "xmp:ModifyDate"]) {
            list.append("拍攝時間")
        }
        if !names.isDisjoint(with: ["exif:ExposureTime", "exif:FNumber", "exif:ISOSpeedRatings", "exif:FocalLength", "exif:Flash"]) {
            list.append("拍攝參數")
        }
        if !names.isDisjoint(with: ["dc:creator", "tiff:Artist", "tiff:Copyright", "dc:rights"]) { list.append("作者與版權") }
        if !names.isDisjoint(with: ["tiff:Software", "xmp:CreatorTool"]) { list.append("編輯軟體") }
        if list.isEmpty, tags.contains(where: { !["tiff:Orientation", "exif:PixelXDimension", "exif:PixelYDimension", "exif:ColorSpace"].contains("\($0.prefix):\($0.name)") }) {
            list.append("其他中繼資料")
        }
        return list
    }
}

private struct ITStripMetadataOptionsView: View {
    @ObservedObject var session: ITStripMetadataSession

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            OptionGroup(title: "要移除的資訊", systemImage: "eye.slash") {
                ChoiceCards(selection: $session.mode, choices: [
                    OptionChoice(value: .location, title: "只移除位置資訊", subtitle: "清掉 GPS 座標與地點，保留拍攝時間、相機等資訊。",
                                 systemImage: "location.slash"),
                    OptionChoice(value: .all, title: "移除全部", subtitle: "清掉 EXIF、GPS、相機、拍攝時間、作者等所有資訊。",
                                 systemImage: "trash.slash", badge: "最安全"),
                ])
            }
            if session.fileCount == 1 {
                OptionGroup(title: "這張圖片裡有", systemImage: "doc.text.magnifyingglass") {
                    if session.found.isEmpty {
                        Text("沒有找到相片資訊。").font(.callout).foregroundStyle(.secondary)
                    } else {
                        FlowLayout(spacing: 6) {
                            ForEach(session.found, id: \.self) { item in
                                Text(item).font(.caption.weight(.medium))
                                    .padding(.horizontal, 8).padding(.vertical, 3)
                                    .background(Capsule().fill(Color.primary.opacity(0.07)))
                            }
                        }
                    }
                }
            }
            OptionNote(text: "JPG、TIFF 直接移除資訊、不重新壓縮；PNG 以無損方式重新儲存，畫質都完全不變。HEIC 只移除位置時不重新壓縮。圖片方向與色彩描述檔都會保留。")
        }
    }
}
