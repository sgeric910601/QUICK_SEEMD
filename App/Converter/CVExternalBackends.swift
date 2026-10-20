import Foundation

/// 外部引擎（若使用者自行安裝）的偵測與指令組合。QUICK_SEEMD 不會自動安裝任何東西。
struct ConversionBackendStatus: Identifiable, Sendable {
    let backend: ConversionBackend
    let executable: URL?
    var id: String { backend.rawValue }
    var isAvailable: Bool { executable != nil }

    var summary: String {
        switch backend {
        case .native: return "圖片、PDF、文件、音訊、影片、資料、壓縮檔"
        case .ffmpeg: return "MP3、OGG、Opus、WebM、MKV、AVI，以及 macOS 無法解碼的影音檔"
        case .pandoc: return "EPUB、LaTeX、Markdown（從 Word／HTML 轉回）"
        case .libreOffice: return "Excel、PowerPoint、Pages／Numbers／Keynote → PDF 與 Office 格式"
        case .imageMagick: return "WebP、JPEG XL 輸出，以及 PSD 圖層、XCF、EPS 等特殊圖檔"
        }
    }

    var installHint: String {
        switch backend {
        case .native: return ""
        case .ffmpeg: return "brew install ffmpeg"
        case .pandoc: return "brew install pandoc"
        case .libreOffice: return "brew install --cask libreoffice"
        case .imageMagick: return "brew install imagemagick"
        }
    }
}

enum CVExternalTools {
    /// 搜尋路徑。環境變數 QUICKSEEMD_CONVERTER_TOOLS_DIR 可額外指定一個資料夾（僅供測試）。
    static var searchDirectories: [String] {
        var dirs = ["/opt/homebrew/bin", "/usr/local/bin", "/usr/bin", "/opt/local/bin"]
        if let extra = ProcessInfo.processInfo.environment["QUICKSEEMD_CONVERTER_TOOLS_DIR"], !extra.isEmpty { dirs.insert(extra, at: 0) }
        return dirs
    }

    private static let lock = NSLock()
    nonisolated(unsafe) private static var cache: [ConversionBackend: URL?] = [:]

    /// 重新偵測（使用者剛安裝完時）。
    static func refresh() {
        lock.lock()
        cache.removeAll()
        lock.unlock()
    }

    static func executable(for backend: ConversionBackend) -> URL? {
        lock.lock()
        if let cached = cache[backend] { lock.unlock(); return cached }
        lock.unlock()
        let found = locate(backend)
        lock.lock()
        cache.updateValue(found, forKey: backend)
        lock.unlock()
        return found
    }

    private static func locate(_ backend: ConversionBackend) -> URL? {
        let fm = FileManager.default
        var candidates: [String] = []
        switch backend {
        case .native: return nil
        case .ffmpeg: candidates = searchDirectories.map { $0 + "/ffmpeg" }
        case .pandoc: candidates = searchDirectories.map { $0 + "/pandoc" }
        case .imageMagick: candidates = searchDirectories.map { $0 + "/magick" }
        case .libreOffice:
            candidates = searchDirectories.prefix(ProcessInfo.processInfo.environment["QUICKSEEMD_CONVERTER_TOOLS_DIR"] == nil ? 0 : 1).map { $0 + "/soffice" }
                + ["/Applications/LibreOffice.app/Contents/MacOS/soffice",
                   NSHomeDirectory() + "/Applications/LibreOffice.app/Contents/MacOS/soffice"]
                + searchDirectories.map { $0 + "/soffice" }
        }
        for path in candidates where fm.isExecutableFile(atPath: path) {
            return URL(fileURLWithPath: path).resolvingSymlinksInPath()
        }
        return nil
    }

    static func isAvailable(_ backend: ConversionBackend) -> Bool { executable(for: backend) != nil }

    static var statuses: [ConversionBackendStatus] {
        [ConversionBackend.ffmpeg, .pandoc, .libreOffice, .imageMagick].map {
            ConversionBackendStatus(backend: $0, executable: executable(for: $0))
        }
    }

    // MARK: - 指令組合（可單獨測試）

    /// ffmpeg 參數。輸出路徑由呼叫端提供；使用 -n 確保絕不覆蓋。
    static func ffmpegArguments(input: String, output: String, formatID: String, options: ConversionOptions) -> [String] {
        var args = ["-hide_banner", "-nostdin", "-n", "-i", input]
        let kbps = "\(max(64, options.audioBitrate / 1000))k"
        var scale: [String] = []
        if options.videoSize != .original {
            let cap = options.videoSize.rawValue
            // 短邊縮到上限（不放大），長邊等比例且為偶數
            scale = ["-vf", "scale='if(gt(iw,ih),-2,min(iw,\(cap)))':'if(gt(iw,ih),min(ih,\(cap)),-2)'"]
        }
        switch formatID {
        case "mp3": args += ["-vn", "-c:a", "libmp3lame", "-b:a", kbps]
        case "ogg": args += ["-vn", "-c:a", "libvorbis", "-q:a", "6"]
        case "opus": args += ["-vn", "-c:a", "libopus", "-b:a", "\(max(64, min(256, options.audioBitrate / 1000)))k"]
        case "flac": args += ["-vn", "-c:a", "flac"]
        case "wav": args += ["-vn", "-c:a", "pcm_s16le"]
        case "aiff": args += ["-vn", "-c:a", "pcm_s16be"]
        case "m4a", "aac": args += ["-vn", "-c:a", "aac", "-b:a", kbps]
        case "alac": args += ["-vn", "-c:a", "alac"]
        case "caf": args += ["-vn", "-c:a", "pcm_s16le"]
        case "webm": args += scale + ["-c:v", "libvpx-vp9", "-crf", "32", "-b:v", "0", "-row-mt", "1", "-c:a", "libopus", "-b:a", "128k"]
        case "mkv": args += scale.isEmpty ? ["-map", "0", "-c", "copy"] : scale + ["-c:v", "libx264", "-crf", "20", "-preset", "medium", "-c:a", "copy"]
        case "avi": args += scale + ["-c:v", "mpeg4", "-q:v", "3", "-c:a", "libmp3lame", "-b:a", "192k"]
        case "mp4", "m4v": args += scale + ["-c:v", "libx264", "-crf", "20", "-preset", "medium", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", kbps, "-movflags", "+faststart"]
        case "mp4-hevc": args += scale + ["-c:v", "libx265", "-crf", "24", "-tag:v", "hvc1", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", kbps, "-movflags", "+faststart"]
        case "mov": args += scale + ["-c:v", "libx264", "-crf", "20", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", kbps]
        case "mov-hevc": args += scale + ["-c:v", "libx265", "-crf", "24", "-tag:v", "hvc1", "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", kbps]
        case "prores": args += scale + ["-c:v", "prores_ks", "-profile:v", "2", "-c:a", "pcm_s16le"]
        case "anim-gif":
            let fps = max(1, min(30, options.gifFramesPerSecond))
            args += ["-t", String(options.gifMaxDuration),
                     "-vf", "fps=\(fps),scale=\(options.gifMaxWidth):-1:flags=lanczos,split[a][b];[a]palettegen[p];[b][p]paletteuse",
                     "-loop", "0"]
        default: break
        }
        args += ["-progress", "pipe:1", "-nostats", output]
        return args
    }

    /// pandoc 參數（輸出格式由副檔名推斷；Markdown 輸出採 GFM）。
    static func pandocArguments(input: String, output: String, reader: String?, formatID: String) -> [String] {
        var args = [input]
        if let reader { args += ["-f", reader] }
        switch formatID {
        case "md": args += ["-t", "gfm", "--wrap=none"]
        case "tex", "html": args += ["-s"]
        case "epub": args += ["--toc"]
        default: break
        }
        args += ["-o", output]
        return args
    }

    /// LibreOffice 的 --convert-to 篩選器名稱。
    static func sofficeFilter(for formatID: String) -> String {
        switch formatID {
        case "csv": return "csv:Text - txt - csv (StarCalc):44,34,76,1"
        default: return formatID
        }
    }

    /// LibreOffice 參數；使用獨立的設定檔目錄，避免與使用者開著的 LibreOffice 衝突。
    static func sofficeArguments(input: String, outDir: String, formatID: String, profileDir: String) -> [String] {
        ["-env:UserInstallation=file://" + profileDir, "--headless", "--norestore", "--nologo", "--nodefault",
         "--convert-to", sofficeFilter(for: formatID), "--outdir", outDir, input]
    }

    static func magickArguments(input: String, output: String, options: ConversionOptions, keepFrames: Bool) -> [String] {
        var args = [keepFrames ? input : input + "[0]", "-auto-orient"]
        if let maxSide = options.imageMaxPixelSize { args += ["-resize", "\(maxSide)x\(maxSide)>"] }
        args += ["-quality", String(Int((options.imageQuality * 100).rounded())), output]
        return args
    }

    // MARK: - 執行

    static func run(_ backend: ConversionBackend, input: URL, profile: CVInputProfile, format: ConversionFormat,
                    options: ConversionOptions, workDir: URL, progress: @escaping @Sendable (Double) -> Void) async throws -> [URL] {
        guard let exe = executable(for: backend) else { throw ConversionError.toolMissing(backend.displayName) }
        let base = CVFileNaming.baseName(of: input)
        let out = workDir.appendingPathComponent(CVFileNaming.fileName(base, format.fileExtension))
        switch backend {
        case .ffmpeg:
            let duration = await CVMediaConverter.duration(input)
            let args = ffmpegArguments(input: input.path, output: out.path, formatID: format.id, options: options)
            try await CVProcess.runChecked(exe.path, args, toolName: "ffmpeg") { line in
                // out_time_us=1234567（新版）或 out_time_ms=（舊版，其實也是微秒）
                guard let duration, line.hasPrefix("out_time_us=") || line.hasPrefix("out_time_ms="),
                      let micro = Double(line.split(separator: "=").last ?? "") else { return }
                progress(min(0.99, micro / 1_000_000 / duration))
            }
            return [out]
        case .pandoc:
            let args = pandocArguments(input: input.path, output: out.path, reader: profile.pandocReader, formatID: format.id)
            try await CVProcess.runChecked(exe.path, args, toolName: "pandoc", currentDirectory: input.deletingLastPathComponent())
            return [out]
        case .libreOffice:
            let scratch = try CVFileNaming.makeScratchDirectory()
            defer { try? FileManager.default.removeItem(at: scratch) }
            let outDir = scratch.appendingPathComponent("out", isDirectory: true)
            let profileDir = scratch.appendingPathComponent("profile", isDirectory: true)
            try FileManager.default.createDirectory(at: outDir, withIntermediateDirectories: true)
            let args = sofficeArguments(input: input.path, outDir: outDir.path, formatID: format.id, profileDir: profileDir.path)
            try await CVProcess.runChecked(exe.path, args, toolName: "LibreOffice")
            let produced = (try? FileManager.default.contentsOfDirectory(at: outDir, includingPropertiesForKeys: nil)) ?? []
            guard let file = produced.first else { throw ConversionError.toolFailed(tool: "LibreOffice", message: "沒有產生輸出檔。") }
            try FileManager.default.moveItem(at: file, to: out)
            return [out]
        case .imageMagick:
            let keepFrames = ["gif", "webp", "tiff"].contains(format.id)
            try await CVProcess.runChecked(exe.path, magickArguments(input: input.path, output: out.path, options: options, keepFrames: keepFrames),
                                           toolName: "ImageMagick")
            return [out]
        case .native:
            throw ConversionError.other("內部錯誤：原生格式不應交給外部引擎。")
        }
    }
}
