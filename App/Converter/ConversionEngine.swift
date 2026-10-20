import Foundation

/// 說明某些格式為什麼現在不能用（UI 會以淡色、鎖頭顯示）。
struct ConversionUnavailable: Identifiable, Hashable, Sendable {
    let formats: String
    let reason: String
    let backend: ConversionBackend?
    var id: String { formats + reason }
}

/// 對單一輸入檔案的分析摘要（UI 顯示用）。
struct ConversionInputSummary: Sendable {
    let kind: String
    let detail: String?
}

/// 轉換引擎：與 UI 無關，CLI 與 App 共用。
enum ConversionEngine {
    enum Route: Sendable {
        case image, imageToPDF, ocr, animatedToVideo
        case pdfToImages, pdfToText
        case document
        case data
        case text
        case audio, video, videoToGIF, videoFrame
        case archive
        case external(ConversionBackend)
    }

    struct Target: Sendable {
        let format: ConversionFormat
        let route: Route
    }

    // MARK: - 分析

    /// 快速分析（同步，只看類型與檔頭）。
    static func profile(for url: URL) -> CVInputProfile {
        CVInputInspector.inspect(url)
    }

    /// 完整分析：額外確認影片是否真的有影像軌（.mp4 也可能只有聲音）。
    static func analyze(_ url: URL) async -> CVInputProfile {
        var p = CVInputInspector.inspect(url)
        if p.isVideo && p.isAVReadable {
            if !(await CVMediaConverter.hasVideoTrack(url)) {
                p.isVideo = false
                p.isAudio = true
            } else {
                p.hasAudioTrack = await CVMediaConverter.hasAudioTrack(url)
            }
        }
        return p
    }

    // MARK: - 可用格式

    /// 這個檔案在這台 Mac 上實際能轉成的格式（同步版本）。
    static func targets(for url: URL) -> [ConversionFormat] {
        plan(for: profile(for: url)).map(\.format)
    }

    /// 這個檔案在這台 Mac 上實際能轉成的格式（含影音軌偵測）。
    static func availableTargets(for url: URL) async -> [ConversionFormat] {
        plan(for: await analyze(url)).map(\.format)
    }

    /// 多個檔案共同可用的格式（交集，依第一個檔案的順序）。
    static func commonTargets(_ lists: [[ConversionFormat]]) -> [ConversionFormat] {
        guard var result = lists.first else { return [] }
        for list in lists.dropFirst() {
            let ids = Set(list.map(\.id))
            result = result.filter { ids.contains($0.id) }
        }
        return result
    }

    static func summary(for profile: CVInputProfile) -> ConversionInputSummary {
        var detail: String?
        if profile.isPlainText, let size = fileSize(profile.url), size <= 8_000_000,
           let decoded = try? CVTextCodec.decode(profile.url) {
            let ending = decoded.lineEnding
            detail = decoded.encodingName + (decoded.hadBOM ? "（含 BOM）" : "") + (ending == .none ? "" : " · \(ending.rawValue)")
        } else if profile.isPlainFolder {
            let count = (try? FileManager.default.contentsOfDirectory(atPath: profile.url.path).filter { !$0.hasPrefix(".") }.count) ?? 0
            detail = "\(count) 個項目"
        } else if profile.isImage, profile.isAnimatedImage {
            detail = "動畫 · \(profile.imageFrameCount) 格"
        }
        return ConversionInputSummary(kind: profile.kindLabel, detail: detail)
    }

    private static func fileSize(_ url: URL) -> Int? {
        (try? url.resourceValues(forKeys: [.fileSizeKey]))?.fileSize
    }

    /// 依檔案身分規劃所有可行的「格式 + 路線」，同 id 只保留第一個（原生優先）。
    static func plan(for p: CVInputProfile) -> [Target] {
        var targets: [Target] = []
        var seen = Set<String>()
        func add(_ format: ConversionFormat, _ route: Route, note: String? = nil) {
            guard !seen.contains(format.id) else { return }
            seen.insert(format.id)
            var f = format
            if let note { f.note = note }
            if case .external(let backend) = route { f.backend = backend }
            targets.append(Target(format: f, route: route))
        }
        let ext = p.ext
        let ffmpeg = CVExternalTools.isAvailable(.ffmpeg)
        let pandoc = CVExternalTools.isAvailable(.pandoc)
        let soffice = CVExternalTools.isAvailable(.libreOffice)
        let magick = CVExternalTools.isAvailable(.imageMagick)

        // 圖片
        if p.isImage {
            let sourceUTI = p.type?.identifier
            for f in ConversionFormats.nativeWritableImageFormats {
                if ConversionFormats.imageUTIs[f.id] == sourceUTI && !(p.isAnimatedImage && f.id == "gif") {
                    add(f, .image, note: "重新壓縮或縮小")
                } else if p.isAnimatedImage && f.id == "gif" {
                    add(f, .image, note: "保留動畫")
                } else {
                    add(f, .image, note: p.isAnimatedImage && f.id != "tiff" ? "只取第一格" : nil)
                }
            }
            if p.isAnimatedImage {
                add(ConversionFormats.mp4, .animatedToVideo, note: "動畫轉影片")
                add(ConversionFormats.mov, .animatedToVideo, note: "動畫轉影片")
            }
            add(ConversionFormats.pdf, .imageToPDF)
            add(ConversionFormats.ocrText, .ocr, note: "辨識圖片中的文字")
            if magick {
                for f in [ConversionFormats.webp, ConversionFormats.jxl] { add(f, .external(.imageMagick)) }
            }
        } else if p.isMagickCandidate && magick {
            for f in [ConversionFormats.png, ConversionFormats.jpeg, ConversionFormats.tiff, ConversionFormats.webp] {
                add(f, .external(.imageMagick))
            }
        }

        // PDF
        if p.isPDF {
            for f in [ConversionFormats.png, ConversionFormats.jpeg, ConversionFormats.tiff, ConversionFormats.heic] {
                if f.id == "heic" && !ConversionFormats.nativeWritableImageFormats.contains(f) { continue }
                add(f, .pdfToImages, note: "每頁一張")
            }
            add(ConversionFormats.txt, .pdfToText, note: "掃描檔自動 OCR")
            for f in [ConversionFormats.docx, ConversionFormats.rtf, ConversionFormats.html, ConversionFormats.odt] {
                add(f, .document, note: "僅文字與基本格式")
            }
        }

        // Markdown
        if p.isMarkdown {
            add(ConversionFormats.html, .document)
            for f in [ConversionFormats.pdf, ConversionFormats.docx, ConversionFormats.rtf, ConversionFormats.odt,
                      ConversionFormats.doc, ConversionFormats.rtfd] {
                add(f, .document)
            }
            if pandoc {
                add(ConversionFormats.epub, .external(.pandoc))
                add(ConversionFormats.latex, .external(.pandoc))
            }
        }

        // 文件（RTF、Word、HTML…）與純文字
        if let kind = p.documentKind, !p.isMarkdown, p.dataKind == nil || kind != .plain {
            let sourceID: String? = {
                switch kind {
                case .plain: return "txt"
                case .rtf: return "rtf"
                case .rtfd: return "rtfd"
                case .html: return "html"
                case .doc: return "doc"
                case .docx: return "docx"
                case .odt: return "odt"
                case .webArchive: return "webarchive"
                }
            }()
            for f in CVDocumentConverter.attributedOutputs where f.id != sourceID {
                if kind == .plain && ["webarchive", "rtfd", "txt"].contains(f.id) { continue }
                add(f, .document)
            }
            if pandoc, p.pandocReader != nil {
                add(ConversionFormats.markdown, .external(.pandoc))
                add(ConversionFormats.epub, .external(.pandoc))
                add(ConversionFormats.latex, .external(.pandoc))
            }
        } else if pandoc, let _ = p.pandocReader, !p.isMarkdown {
            // pandoc 才讀得懂的格式（EPUB、LaTeX、rst…）
            for f in [ConversionFormats.docx, ConversionFormats.html, ConversionFormats.odt, ConversionFormats.markdown,
                      ConversionFormats.epub, ConversionFormats.latex] where f.fileExtension != ext {
                add(f, .external(.pandoc))
            }
        }

        // 結構化資料
        if let kind = p.dataKind {
            switch kind {
            case .json:
                for f in [ConversionFormats.jsonPretty, ConversionFormats.jsonMin, ConversionFormats.yaml,
                          ConversionFormats.plistXML, ConversionFormats.plistBinary] { add(f, .data) }
                if CVInputInspector.jsonIsArray(p.url) {
                    for f in [ConversionFormats.csv, ConversionFormats.xlsx, ConversionFormats.tsv, ConversionFormats.htmlTable] { add(f, .data) }
                }
            case .plist:
                for f in [ConversionFormats.json, ConversionFormats.yaml, ConversionFormats.plistXML, ConversionFormats.plistBinary] { add(f, .data) }
            case .csv, .tsv:
                for f in [ConversionFormats.xlsx, ConversionFormats.json, ConversionFormats.yaml, ConversionFormats.htmlTable] { add(f, .data) }
                add(kind == .csv ? ConversionFormats.tsv : ConversionFormats.csv, .data)
            case .xlsx:
                for f in [ConversionFormats.csv, ConversionFormats.json, ConversionFormats.tsv, ConversionFormats.htmlTable, ConversionFormats.yaml] {
                    add(f, .data, note: f.id == "csv" || f.id == "tsv" ? "多個工作表各存一檔" : nil)
                }
            case .yaml:
                break
            }
        }

        // 文字編碼與換行
        if p.isPlainText {
            var current: CVTextCodec.Decoded?
            if let size = fileSize(p.url), size <= 8_000_000 { current = try? CVTextCodec.decode(p.url) }
            let enc = current?.encoding
            let isUTF8 = enc == .utf8
            if !(isUTF8 && current?.hadBOM == false) { add(ConversionFormats.utf8, .text) }
            if !(isUTF8 && current?.hadBOM == true) {
                add(ConversionFormats.utf8BOM, .text, note: p.dataKind == .csv || p.dataKind == .tsv ? "Windows Excel 開啟不亂碼" : nil)
            }
            if !(enc == .utf16 || enc == .utf16LittleEndian) { add(ConversionFormats.utf16, .text) }
            if !(enc == CVTextCodec.big5 || enc == CVTextCodec.big5CP950) { add(ConversionFormats.big5, .text) }
            let ending = current?.lineEnding
            if ending != .lf && ending != CVTextCodec.LineEnding.none { add(ConversionFormats.lf, .text) }
            if ending != .crlf { add(ConversionFormats.crlf, .text) }
        }

        // 影片
        if p.isVideo {
            if p.isAVReadable {
                for f in CVMediaConverter.nativeVideoFormats { add(f, .video) }
                add(ConversionFormats.animatedGIF, .videoToGIF)
                add(ConversionFormats.png, .videoFrame, note: "擷取單一畫面")
                add(ConversionFormats.jpeg, .videoFrame, note: "擷取單一畫面")
                if p.hasAudioTrack {
                    for f in CVMediaConverter.nativeAudioFormats { add(f, .audio, note: "擷取音軌") }
                }
            }
            if ffmpeg {
                for f in [ConversionFormats.webm, ConversionFormats.mkv, ConversionFormats.avi, ConversionFormats.mp4,
                          ConversionFormats.mov, ConversionFormats.mp4HEVC, ConversionFormats.animatedGIF] where f.fileExtension != ext || f.id == "anim-gif" {
                    add(f, .external(.ffmpeg))
                }
                for f in [ConversionFormats.mp3, ConversionFormats.ogg, ConversionFormats.opus, ConversionFormats.m4a,
                          ConversionFormats.wav, ConversionFormats.flac] where p.hasAudioTrack {
                    add(f, .external(.ffmpeg), note: "擷取音軌")
                }
            }
        }

        // 音訊
        if p.isAudio {
            let afReadable = p.isAVReadable || ["wav", "aif", "aiff", "caf", "m4a", "mp3", "aac", "flac", "au", "snd", "amr", "ac3"].contains(ext)
            if afReadable {
                for f in CVMediaConverter.nativeAudioFormats {
                    if f.fileExtension == ext && f.id != "m4a" && f.id != "alac" { continue }
                    if ext == "aif" && f.id == "aiff" { continue }
                    add(f, .audio)
                }
            }
            if ffmpeg {
                for f in [ConversionFormats.mp3, ConversionFormats.ogg, ConversionFormats.opus, ConversionFormats.m4a,
                          ConversionFormats.flac, ConversionFormats.wav, ConversionFormats.aiff] where f.fileExtension != ext {
                    add(f, .external(.ffmpeg))
                }
            }
        }

        // LibreOffice
        if soffice, let office = p.officeKind {
            switch office {
            case .word:
                for f in [ConversionFormats.pdf, ConversionFormats.docx, ConversionFormats.odt] where f.fileExtension != ext { add(f, .external(.libreOffice)) }
            case .spreadsheet:
                for f in [ConversionFormats.pdf, ConversionFormats.xlsx, ConversionFormats.ods, ConversionFormats.csv] where f.fileExtension != ext {
                    add(f, .external(.libreOffice))
                }
            case .presentation:
                for f in [ConversionFormats.pdf, ConversionFormats.pptx, ConversionFormats.odp] where f.fileExtension != ext { add(f, .external(.libreOffice)) }
            case .iWork:
                add(ConversionFormats.pdf, .external(.libreOffice))
                switch ext {
                case "pages": add(ConversionFormats.docx, .external(.libreOffice))
                case "numbers": add(ConversionFormats.xlsx, .external(.libreOffice))
                case "key": add(ConversionFormats.pptx, .external(.libreOffice))
                default: break
                }
            }
        }

        // 壓縮與封存
        if let archive = p.archiveKind {
            if archive != .xz {
                add(ConversionFormats.extract, .archive)
                if archive != .gzip && archive != .bzip2 {
                    if archive != .zip { add(ConversionFormats.zip, .archive, note: "重新封裝") }
                    if archive != .tar { add(ConversionFormats.tarGz, .archive, note: "重新封裝") }
                    if archive != .sevenZip { add(ConversionFormats.sevenZip, .archive, note: "重新封裝") }
                }
            }
        } else if FileManager.default.fileExists(atPath: p.url.path) {
            add(ConversionFormats.zip, .archive)
            add(ConversionFormats.tarGz, .archive)
            add(ConversionFormats.sevenZip, .archive)
            if p.isDirectory { add(ConversionFormats.dmg, .archive) }
        }
        return targets
    }

    /// 這個檔案「想轉但目前不行」的格式與原因（誠實告知使用者）。
    static func unavailable(for p: CVInputProfile) -> [ConversionUnavailable] {
        var list: [ConversionUnavailable] = []
        let ffmpeg = CVExternalTools.isAvailable(.ffmpeg)
        if p.isVideo && !p.isAVReadable && !ffmpeg {
            list.append(.init(formats: "所有影片與音訊格式", reason: "macOS 無法解碼 .\(p.ext) 影片，需要安裝 ffmpeg。", backend: .ffmpeg))
        } else if p.isAudio && !p.isAVReadable && !ffmpeg {
            list.append(.init(formats: "所有音訊格式", reason: "macOS 無法解碼 .\(p.ext) 音訊，需要安裝 ffmpeg。", backend: .ffmpeg))
        } else if (p.isAudio || (p.isVideo && p.hasAudioTrack)) && !ffmpeg {
            list.append(.init(formats: "MP3、OGG、Opus", reason: "macOS 內建沒有這些編碼器，需要安裝 ffmpeg。", backend: .ffmpeg))
        }
        if p.isVideo && p.isAVReadable && !ffmpeg {
            list.append(.init(formats: "WebM、MKV、AVI", reason: "macOS 沒有這些影片封裝的編碼器，需要安裝 ffmpeg。", backend: .ffmpeg))
        }
        if p.isImage || p.isPDF {
            let native = Set(ConversionFormats.nativeWritableImageFormats.map(\.id))
            let missing = [ConversionFormats.webp, ConversionFormats.jxl].filter { !native.contains($0.id) }
            if p.isImage, !missing.isEmpty, !CVExternalTools.isAvailable(.imageMagick) {
                list.append(.init(formats: missing.map(\.displayName).joined(separator: "、"),
                                  reason: "這台 Mac 的 ImageIO 只能讀取、不能寫入，需要安裝 ImageMagick。", backend: .imageMagick))
            }
        }
        if p.isMagickCandidate && !p.isImage && !CVExternalTools.isAvailable(.imageMagick) {
            list.append(.init(formats: "所有圖片格式", reason: "macOS 無法讀取 .\(p.ext)，需要安裝 ImageMagick。", backend: .imageMagick))
        }
        if p.isPDF {
            list.append(.init(formats: "可編輯的 Word 版面", reason: "PDF 轉 Word 只能保留文字與基本格式，無法重建原始版面。", backend: nil))
        }
        if (p.isMarkdown || p.pandocReader != nil) && !CVExternalTools.isAvailable(.pandoc) {
            let what = p.isMarkdown ? "EPUB、LaTeX" : "Markdown、EPUB、LaTeX"
            list.append(.init(formats: what, reason: "需要安裝 Pandoc。", backend: .pandoc))
        }
        if let office = p.officeKind, !CVExternalTools.isAvailable(.libreOffice) {
            switch office {
            case .spreadsheet where p.dataKind != .csv:
                list.append(.init(formats: p.dataKind == .xlsx ? "PDF、ODS" : "PDF、XLSX、CSV",
                                  reason: "試算表排版需要 LibreOffice。", backend: .libreOffice))
            case .presentation:
                list.append(.init(formats: "PDF、PPTX、ODP", reason: "簡報需要 LibreOffice 才能轉換。", backend: .libreOffice))
            case .iWork:
                list.append(.init(formats: "PDF、Office 格式", reason: "Pages／Numbers／Keynote 需要 LibreOffice（或用原 App 的「輸出」）。", backend: .libreOffice))
            default: break
            }
        }
        if p.dataKind == .yaml {
            list.append(.init(formats: "JSON、Plist", reason: "目前不支援讀取 YAML（只能輸出 YAML）。", backend: nil))
        }
        if p.archiveKind == .xz {
            list.append(.init(formats: "解壓縮", reason: "macOS 沒有內建單一 .xz 檔的解壓工具（.tar.xz 可以）。", backend: nil))
        }
        return list
    }

    // MARK: - 轉換

    /// 轉換單一檔案。輸出先寫到暫存區，完成後才搬到 `outputDirectory`，同名時自動改成「名稱 (1).副檔名」。
    /// - Returns: 實際產生的檔案或資料夾。
    static func convert(_ input: URL, to format: ConversionFormat, options: ConversionOptions = ConversionOptions(),
                        outputDirectory: URL, progress: @escaping @Sendable (Double) -> Void = { _ in }) async throws -> [URL] {
        guard FileManager.default.fileExists(atPath: input.path) else { throw ConversionError.fileNotFound(input.lastPathComponent) }
        let profile = await analyze(input)
        guard let target = plan(for: profile).first(where: { $0.format.id == format.id }) else {
            throw ConversionError.unsupportedTarget(source: input.lastPathComponent, target: format.displayName)
        }
        try ensureWritable(outputDirectory)
        let work = try CVFileNaming.makeWorkDirectory(near: outputDirectory)
        defer { try? FileManager.default.removeItem(at: work) }
        progress(0)
        let produced: [URL]
        do {
            produced = try await run(target, input: input, profile: profile, options: options, workDir: work, progress: progress)
        } catch is CancellationError {
            throw ConversionError.cancelled
        }
        try Task.checkCancellation()
        var results: [URL] = []
        for item in produced {
            CVPDFPostProcess.apply(to: item)
            results.append(try CVFileNaming.commit(item, into: outputDirectory))
        }
        progress(1)
        return results
    }

    /// 依 id 轉換（CLI 用）。
    static func convert(_ input: URL, toFormatID id: String, options: ConversionOptions = ConversionOptions(),
                        outputDirectory: URL, progress: @escaping @Sendable (Double) -> Void = { _ in }) async throws -> [URL] {
        guard let format = ConversionFormats.format(id: id) else {
            throw ConversionError.other("未知的格式代碼「\(id)」。")
        }
        return try await convert(input, to: format, options: options, outputDirectory: outputDirectory, progress: progress)
    }

    /// 將多張圖片與 PDF 依序合併成一份 PDF。
    static func mergeToPDF(_ inputs: [URL], options: ConversionOptions = ConversionOptions(), outputDirectory: URL,
                           fileName: String = "合併文件.pdf", progress: @escaping @Sendable (Double) -> Void = { _ in }) async throws -> URL {
        let usable = inputs.filter { url in
            let p = CVInputInspector.inspect(url)
            return p.isImage || p.isPDF
        }
        guard !usable.isEmpty else { throw ConversionError.emptyContent("沒有可合併的圖片或 PDF。") }
        try ensureWritable(outputDirectory)
        let work = try CVFileNaming.makeWorkDirectory(near: outputDirectory)
        defer { try? FileManager.default.removeItem(at: work) }
        let out = work.appendingPathComponent(CVFileNaming.sanitize(fileName))
        try await Task.detached(priority: .userInitiated) {
            try CVImageConverter.writePDF(from: usable, to: out, options: options, progress: progress)
        }.value
        return try CVFileNaming.commit(out, into: outputDirectory)
    }

    static func ensureWritable(_ directory: URL) throws {
        var isDir: ObjCBool = false
        guard FileManager.default.fileExists(atPath: directory.path, isDirectory: &isDir), isDir.boolValue,
              FileManager.default.isWritableFile(atPath: directory.path) else {
            throw ConversionError.outputFolderUnavailable(directory.path)
        }
    }

    private static func run(_ target: Target, input: URL, profile: CVInputProfile, options: ConversionOptions, workDir: URL,
                            progress: @escaping @Sendable (Double) -> Void) async throws -> [URL] {
        let format = target.format
        // 同步、吃 CPU 的工作放到背景執行緒，避免卡住呼叫端
        func background<T: Sendable>(_ body: @escaping @Sendable () throws -> T) async throws -> T {
            try await Task.detached(priority: .userInitiated, operation: body).value
        }
        switch target.route {
        case .image:
            return try await background { try CVImageConverter.convert(input, profile: profile, to: format, options: options, workDir: workDir, progress: progress) }
        case .imageToPDF:
            let out = workDir.appendingPathComponent(CVFileNaming.fileName(CVFileNaming.baseName(of: input), "pdf"))
            try await background { try CVImageConverter.writePDF(from: [input], to: out, options: options, progress: progress) }
            return [out]
        case .ocr:
            let out = workDir.appendingPathComponent(CVFileNaming.fileName(CVFileNaming.baseName(of: input), "txt"))
            try await background { try CVImageConverter.ocr(input, to: out) }
            return [out]
        case .animatedToVideo:
            return try await CVMediaConverter.animatedImageToVideo(input, to: format, options: options, workDir: workDir, progress: progress)
        case .pdfToImages:
            return try await background { try CVPDFConverter.toImages(input, format: format, options: options, workDir: workDir, progress: progress) }
        case .pdfToText:
            return try await background { try CVPDFConverter.toText(input, forceOCR: false, workDir: workDir, progress: progress) }
        case .document:
            return try await CVDocumentConverter.convert(input, profile: profile, to: format, options: options, workDir: workDir)
        case .data:
            return try await CVDataConverter.convert(input, profile: profile, to: format, options: options, workDir: workDir)
        case .text:
            return try await background { try CVTextConverter.convert(input, to: format, options: options, workDir: workDir) }
        case .audio:
            return try await CVMediaConverter.convertAudio(input, profile: profile, to: format, options: options, workDir: workDir, progress: progress)
        case .video:
            return try await CVMediaConverter.convertVideo(input, to: format, options: options, workDir: workDir, progress: progress)
        case .videoToGIF:
            return try await CVMediaConverter.videoToGIF(input, options: options, workDir: workDir, progress: progress)
        case .videoFrame:
            return try await CVMediaConverter.videoFrame(input, format: format, options: options, workDir: workDir)
        case .archive:
            return try await CVArchiveConverter.convert(input, profile: profile, to: format, workDir: workDir, progress: progress)
        case .external(let backend):
            return try await CVExternalTools.run(backend, input: input, profile: profile, format: format, options: options,
                                                 workDir: workDir, progress: progress)
        }
    }

    /// 外部引擎狀態（UI 顯示用）。
    static var backendStatuses: [ConversionBackendStatus] { CVExternalTools.statuses }

    static func refreshBackends() { CVExternalTools.refresh() }
}
