import AppKit
import CryptoKit
import SwiftUI
import UniformTypeIdentifiers
import Vision

/// 其他工具：萬用格式轉換、壓縮檔、文字編碼、字幕、QR Code、校驗碼。
enum OtherToolCatalog {
    static let converterID = "other.convert"

    static let tools: [ToolDefinition] = [converter, compress, extract, encoding, subtitles, readQR, checksum]

    static let converter = ToolDefinition(
        id: converterID, category: .other, title: "萬用格式轉換",
        subtitle: "任何檔案丟進來，列出這台 Mac 能轉的所有格式：圖片、文件、影音、資料、編碼、壓縮檔。",
        symbol: "arrow.triangle.2.circlepath", tint: ToolPalette.teal, badge: "ALL",
        inputTypes: [.item], inputLabel: "檔案", actionTitle: "轉換",
        keywords: ["convert", "any", "格式", "轉檔", "萬用"],
        customPage: { AnyView(ConverterView(model: AppState.shared.converter)) },
        makeSession: { ToolSession() })

    static let compress = ToolDefinition(
        id: "other.compress", category: .other, title: "壓縮檔案",
        subtitle: "把檔案或資料夾壓成 ZIP 或 TAR.GZ，可各自壓縮或全部打包成一個。",
        symbol: "doc.zipper", tint: ToolPalette.slate, badge: "ZIP",
        inputTypes: [.item], inputLabel: "檔案或資料夾", actionTitle: "壓縮",
        keywords: ["zip", "compress", "archive", "tar", "打包"],
        quickActions: ["other.zip": { OTCompressSession.job(format: .zip, together: true, name: "") }],
        makeSession: { OTCompressSession() })

    static let extract = ToolDefinition(
        id: "other.extract", category: .other, title: "解壓縮",
        subtitle: "解開 ZIP、TAR、TAR.GZ、7z、GZ 等壓縮檔到同名資料夾。",
        symbol: "archivebox", tint: ToolPalette.slate, badge: "UNZIP",
        inputTypes: [.archive, .zip, .gzip], inputLabel: "壓縮檔", actionTitle: "解壓縮",
        keywords: ["unzip", "extract", "解壓", "rar", "7z"],
        quickActions: ["other.unzip": { OTConvertJob.make(formatID: "extract") }],
        makeSession: { OTPlainSession(job: OTConvertJob.make(formatID: "extract")) })

    static let encoding = ToolDefinition(
        id: "other.text-encoding", category: .other, title: "文字編碼轉換",
        subtitle: "亂碼救星：Big5、GB18030、UTF-16 等編碼與 Windows / Mac 換行互轉。",
        symbol: "character.textbox", tint: ToolPalette.teal, badge: "TXT",
        inputTypes: [.plainText, .commaSeparatedText, .sourceCode], inputLabel: "文字檔", actionTitle: "轉換編碼",
        keywords: ["encoding", "utf8", "big5", "亂碼", "編碼", "換行", "crlf"],
        quickActions: ["other.to-utf8": { OTConvertJob.make(formatID: "utf8") }],
        makeSession: { OTEncodingSession() })

    static let subtitles = ToolDefinition(
        id: "other.subtitles", category: .other, title: "字幕轉換",
        subtitle: "SRT 與 WebVTT 字幕互轉，也能整體提前或延後時間軸。",
        symbol: "captions.bubble", tint: ToolPalette.video, badge: "SRT",
        inputTypes: [UTType(filenameExtension: "srt"), UTType(filenameExtension: "vtt"), UTType("org.w3.webvtt")].compactMap { $0 },
        inputLabel: "字幕檔", actionTitle: "轉換字幕",
        keywords: ["srt", "vtt", "subtitle", "字幕"],
        makeSession: { OTSubtitleSession() })

    static let readQR = ToolDefinition(
        id: "other.read-qr", category: .other, title: "讀取 QR Code",
        subtitle: "從圖片或螢幕截圖找出 QR Code 與條碼，內容存成文字並複製到剪貼簿。",
        symbol: "qrcode.viewfinder", tint: ToolPalette.image, badge: "QR",
        inputTypes: [.image, .pdf], inputLabel: "圖片", actionTitle: "讀取",
        keywords: ["qr", "barcode", "條碼", "掃描"],
        quickActions: ["other.read-qr": { OTQRJob.make() }],
        makeSession: { OTPlainSession(job: OTQRJob.make()) })

    static let checksum = ToolDefinition(
        id: "other.checksum", category: .other, title: "計算校驗碼",
        subtitle: "算出 SHA-256、SHA-1、MD5，確認下載的檔案是否完整、有沒有被竄改。",
        symbol: "number.square", tint: ToolPalette.slate, badge: "SHA",
        inputTypes: [.item], inputLabel: "檔案", actionTitle: "計算",
        keywords: ["hash", "sha256", "md5", "checksum", "雜湊"],
        makeSession: { OTChecksumSession() })
}

// MARK: - 共用

/// 沒有選項的工具。
final class OTPlainSession: ToolSession {
    private let job: ToolJob
    init(job: ToolJob) { self.job = job }
    override func makeJob() -> ToolJob { job }
}

/// 用萬用轉換引擎把每個檔案轉成指定格式。
enum OTConvertJob {
    static func make(formatID: String) -> ToolJob {
        .perFile { file, context in
            try await ConversionEngine.convert(file, toFormatID: formatID, outputDirectory: context.outputDirectory(for: file),
                                               progress: { context.progress($0) })
        }
    }
}

// MARK: - 壓縮

enum OTArchiveFormat: String, Hashable { case zip, tgz }

final class OTCompressSession: ToolSession {
    @Published var format: OTArchiveFormat = .zip
    @Published var together = true
    @Published var name = ""

    override func optionsView() -> AnyView { AnyView(OTCompressOptions(session: self)) }
    override func makeJob() -> ToolJob { Self.job(format: format, together: together, name: name) }

    nonisolated static func job(format: OTArchiveFormat, together: Bool, name: String) -> ToolJob {
        ToolJob { files, context in
            guard !files.isEmpty else { return [] }
            if !together || files.count == 1 {
                return try await OTConvertJob.make(formatID: format == .zip ? "zip" : "tgz").run(files, context)
            }
            let ext = format == .zip ? "zip" : "tar.gz"
            let base = name.trimmingCharacters(in: .whitespaces).isEmpty ? "封存" : name
            let work = try context.makeWorkDirectory()
            let out = work.appendingPathComponent("archive.\(ext)")
            // 全部放到同一個暫存資料夾（用硬連結／複製），再一次壓縮，壓縮檔裡就是使用者選的那些項目
            let stage = work.appendingPathComponent("stage", isDirectory: true)
            try FileManager.default.createDirectory(at: stage, withIntermediateDirectories: true)
            var names: [String] = []
            for (i, f) in files.enumerated() {
                try context.checkCancellation()
                let dest = CVFileNaming.uniqueURL(in: stage, fileName: f.lastPathComponent)
                do { try FileManager.default.linkItem(at: f, to: dest) } catch { try FileManager.default.copyItem(at: f, to: dest) }
                names.append(dest.lastPathComponent)
                context.progress(Double(i + 1) / Double(files.count) * 0.3, "準備：\(f.lastPathComponent)")
            }
            context.progress(0.35, "壓縮中…")
            let result: CVProcess.Output
            if format == .zip {
                result = try await CVProcess.run("/usr/bin/zip", ["-r", "-X", "-y", "-q", out.path] + names + ["-x", "*.DS_Store", "__MACOSX/*"],
                                                 currentDirectory: stage)
            } else {
                result = try await CVProcess.run("/usr/bin/tar", ["-czf", out.path, "--exclude", ".DS_Store"] + names,
                                                 environment: ["COPYFILE_DISABLE": "1"], currentDirectory: stage)
            }
            guard result.succeeded else { throw ConversionError.toolFailed(tool: format == .zip ? "zip" : "tar", message: result.stderrText) }
            context.progress(0.95)
            return [try context.deliver(out, as: CVFileNaming.fileName(CVFileNaming.sanitize(base), ext), for: files[0])]
        }
    }
}

private struct OTCompressOptions: View {
    @ObservedObject var session: OTCompressSession
    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            OptionGroup(title: "壓縮格式", systemImage: "doc.zipper") {
                ChoiceCards(selection: $session.format, choices: [
                    OptionChoice(value: .zip, title: "ZIP", subtitle: "最通用，Windows、Mac、手機都能直接打開", systemImage: "doc.zipper"),
                    OptionChoice(value: .tgz, title: "TAR.GZ", subtitle: "Linux / 開發常用，保留檔案權限", systemImage: "shippingbox"),
                ])
            }
            OptionGroup(title: "多個項目時", systemImage: "square.stack.3d.up") {
                ChoiceCards(selection: $session.together, choices: [
                    OptionChoice(value: true, title: "全部打包成一個", subtitle: "所有選取的項目放在同一個壓縮檔", systemImage: "archivebox"),
                    OptionChoice(value: false, title: "各自壓縮", subtitle: "每個項目各產生一個壓縮檔", systemImage: "square.grid.2x2"),
                ])
                if session.together {
                    OptionTextField(title: "壓縮檔名稱", text: $session.name, placeholder: "封存")
                }
            }
        }
    }
}

// MARK: - 文字編碼

final class OTEncodingSession: ToolSession {
    @Published var target = "utf8"

    static let choices: [OptionChoice<String>] = [
        OptionChoice(value: "utf8", title: "UTF-8", subtitle: "現代標準，任何地方都通用", systemImage: "globe"),
        OptionChoice(value: "utf8-bom", title: "UTF-8（含 BOM）", subtitle: "讓舊版 Excel、記事本正確顯示中文", systemImage: "tablecells"),
        OptionChoice(value: "big5", title: "Big5", subtitle: "舊版繁體中文 Windows 程式", systemImage: "character"),
        OptionChoice(value: "gb18030", title: "GB18030", subtitle: "簡體中文系統", systemImage: "character"),
        OptionChoice(value: "utf16", title: "UTF-16", subtitle: "部分 Windows 程式使用", systemImage: "textformat"),
        OptionChoice(value: "crlf", title: "Windows 換行（CRLF）", subtitle: "只改換行符號，編碼不變", systemImage: "return"),
        OptionChoice(value: "lf", title: "Mac / Linux 換行（LF）", subtitle: "只改換行符號，編碼不變", systemImage: "return"),
    ].filter { ConversionFormats.format(id: $0.value) != nil }

    override func optionsView() -> AnyView {
        AnyView(OptionGroup(title: "轉成", systemImage: "character.textbox") {
            ChoiceCards(selection: Binding(get: { self.target }, set: { self.target = $0 }), choices: Self.choices)
            OptionNote(text: "來源編碼會自動偵測（UTF-8、Big5、GB18030、Shift-JIS、UTF-16…）。")
        })
    }

    override func makeJob() -> ToolJob { OTConvertJob.make(formatID: target) }
}

// MARK: - 字幕

final class OTSubtitleSession: ToolSession {
    @Published var target = "vtt"
    @Published var shift: Double = 0

    override func optionsView() -> AnyView { AnyView(OTSubtitleOptions(session: self)) }
    override func makeJob() -> ToolJob { Self.job(target: target, shiftSeconds: shift) }

    nonisolated static func job(target: String, shiftSeconds: Double) -> ToolJob {
        .perFile { file, context in
            let data = try Data(contentsOf: file)
            guard let decoded = QSText.decode(data, truncated: false) else { throw ConversionError.unreadable(file.lastPathComponent) }
            let text = OTSubtitles.convert(decoded.text, toVTT: target == "vtt", shift: shiftSeconds)
            let tmp = try context.makeWorkDirectory().appendingPathComponent("sub.\(target)")
            try text.write(to: tmp, atomically: true, encoding: .utf8)
            let tag = file.pathExtension.lowercased() == target ? "已調整" : nil
            return [try context.deliver(tmp, as: context.outputName(for: file, tag: tag, ext: target), for: file)]
        }
    }
}

private struct OTSubtitleOptions: View {
    @ObservedObject var session: OTSubtitleSession
    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            OptionGroup(title: "轉成", systemImage: "captions.bubble") {
                ChoiceCards(selection: $session.target, choices: [
                    OptionChoice(value: "vtt", title: "WebVTT（.vtt）", subtitle: "網頁播放器、YouTube、HTML5 影片", systemImage: "globe"),
                    OptionChoice(value: "srt", title: "SubRip（.srt）", subtitle: "最常見，播放器與剪輯軟體都支援", systemImage: "film"),
                ])
            }
            OptionGroup(title: "時間軸", systemImage: "clock.arrow.2.circlepath") {
                OptionSlider(title: "整體提前 / 延後", value: $session.shift, range: -10...10, step: 0.1) {
                    $0 == 0 ? "不調整" : String(format: "%+.1f 秒", $0)
                }
            }
        }
    }
}

enum OTSubtitles {
    /// SRT ↔ VTT，並可整體位移時間。
    static func convert(_ text: String, toVTT: Bool, shift: Double) -> String {
        let pattern = #"(\d{1,2}:)?(\d{1,2}):(\d{2})[,.](\d{3})"#
        let regex = try! NSRegularExpression(pattern: pattern)
        var blocks: [String] = []
        let normalized = text.replacingOccurrences(of: "\r\n", with: "\n")
        var cueIndex = 0
        for raw in normalized.components(separatedBy: "\n\n") {
            var lines = raw.components(separatedBy: "\n").filter { !$0.isEmpty }
            if lines.first?.hasPrefix("WEBVTT") == true { lines.removeFirst(); if lines.isEmpty { continue } }
            guard let timeIdx = lines.firstIndex(where: { $0.contains("-->") }) else { continue }
            cueIndex += 1
            var timeLine = lines[timeIdx]
            let ns = timeLine as NSString
            let matches = regex.matches(in: timeLine, range: NSRange(location: 0, length: ns.length)).reversed()
            for m in matches {
                let h = m.range(at: 1).location != NSNotFound ? Double(ns.substring(with: m.range(at: 1)).dropLast()) ?? 0 : 0
                let mi = Double(ns.substring(with: m.range(at: 2))) ?? 0
                let s = Double(ns.substring(with: m.range(at: 3))) ?? 0
                let ms = Double(ns.substring(with: m.range(at: 4))) ?? 0
                var t = max(0, h * 3600 + mi * 60 + s + ms / 1000 + shift)
                let hh = Int(t / 3600); t -= Double(hh) * 3600
                let mm = Int(t / 60); t -= Double(mm) * 60
                let ss = Int(t); let mss = Int(((t - Double(ss)) * 1000).rounded())
                let stamp = String(format: "%02d:%02d:%02d%@%03d", hh, mm, ss, toVTT ? "." : ",", min(mss, 999))
                timeLine = (timeLine as NSString).replacingCharacters(in: m.range, with: stamp)
            }
            if !toVTT, let arrow = timeLine.range(of: "-->") {
                // SRT 不支援 VTT 的位置設定，只保留「開始 --> 結束」
                let after = timeLine[arrow.upperBound...].trimmingCharacters(in: .whitespaces)
                let end = after.split(separator: " ").first.map(String.init) ?? after
                timeLine = timeLine[..<arrow.lowerBound].trimmingCharacters(in: .whitespaces) + " --> " + end
            }
            let content = lines[(timeIdx + 1)...].joined(separator: "\n")
            blocks.append(toVTT ? "\(timeLine)\n\(content)" : "\(cueIndex)\n\(timeLine)\n\(content)")
        }
        return (toVTT ? "WEBVTT\n\n" : "") + blocks.joined(separator: "\n\n") + "\n"
    }
}

// MARK: - QR Code

enum OTQRJob {
    static func make() -> ToolJob {
        ToolJob { files, context in
            var found: [(String, [String])] = []
            for (i, file) in files.enumerated() {
                try context.checkCancellation()
                let sub = context.segment(i, of: files.count)
                sub.progress(0.1, "掃描：\(file.lastPathComponent)")
                found.append((file.lastPathComponent, try scan(file)))
            }
            let codes = found.flatMap(\.1)
            guard !codes.isEmpty else { throw ConversionError.emptyContent("在圖片中找不到 QR Code 或條碼。") }
            var text = ""
            for (name, list) in found where !list.isEmpty {
                if files.count > 1 { text += "# \(name)\n" }
                text += list.joined(separator: "\n") + "\n\n"
            }
            await MainActor.run {
                NSPasteboard.general.clearContents()
                NSPasteboard.general.setString(codes.joined(separator: "\n"), forType: .string)
            }
            let tmp = try context.makeWorkDirectory().appendingPathComponent("qr.txt")
            try text.trimmingCharacters(in: .whitespacesAndNewlines).appending("\n").write(to: tmp, atomically: true, encoding: .utf8)
            context.progress(1, "找到 \(codes.count) 個，已複製到剪貼簿")
            return [try context.deliver(tmp, as: context.outputName(for: files[0], tag: "QR內容", ext: "txt"), for: files[0])]
        }
    }

    static func scan(_ url: URL) throws -> [String] {
        var images: [CGImage] = []
        if ToolFiles.contentType(of: url).conforms(to: .pdf), let doc = CGPDFDocument(url as CFURL) {
            for n in 1...min(doc.numberOfPages, 20) {
                guard let page = doc.page(at: n) else { continue }
                let box = page.getBoxRect(.mediaBox)
                let scale: CGFloat = 2
                guard let ctx = CGContext(data: nil, width: Int(box.width * scale), height: Int(box.height * scale), bitsPerComponent: 8, bytesPerRow: 0,
                                          space: CGColorSpaceCreateDeviceRGB(), bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { continue }
                ctx.setFillColor(.white); ctx.fill(CGRect(x: 0, y: 0, width: box.width * scale, height: box.height * scale))
                ctx.scaleBy(x: scale, y: scale)
                ctx.drawPDFPage(page)
                if let img = ctx.makeImage() { images.append(img) }
            }
        } else if let src = CGImageSourceCreateWithURL(url as CFURL, nil), let img = CGImageSourceCreateImageAtIndex(src, 0, nil) {
            images.append(img)
        } else {
            throw ConversionError.unreadable(url.lastPathComponent)
        }
        var results: [String] = []
        for img in images {
            let request = VNDetectBarcodesRequest()
            try VNImageRequestHandler(cgImage: img).perform([request])
            for obs in request.results ?? [] {
                if let payload = obs.payloadStringValue, !results.contains(payload) { results.append(payload) }
            }
        }
        return results
    }
}

// MARK: - 校驗碼

final class OTChecksumSession: ToolSession {
    @Published var sha256 = true
    @Published var sha1 = false
    @Published var md5 = true

    override func optionsView() -> AnyView { AnyView(OTChecksumOptions(session: self)) }
    override func validate(_ files: [URL]) -> String? { (sha256 || sha1 || md5) ? nil : "至少選一種演算法" }
    override func makeJob() -> ToolJob {
        let (a, b, c) = (sha256, sha1, md5)
        return ToolJob { files, context in
            var report = ""
            for (i, file) in files.enumerated() where !CVFileNaming.isDirectory(file) {
                try context.checkCancellation()
                let sub = context.segment(i, of: files.count)
                var h256 = SHA256(), h1 = Insecure.SHA1(), h5 = Insecure.MD5()
                let fh = try FileHandle(forReadingFrom: file)
                defer { try? fh.close() }
                let total = Double(max(ToolFiles.size(of: file), 1))
                var done = 0.0
                while let chunk = try fh.read(upToCount: 4 << 20), !chunk.isEmpty {
                    try sub.checkCancellation()
                    if a { h256.update(data: chunk) }
                    if b { h1.update(data: chunk) }
                    if c { h5.update(data: chunk) }
                    done += Double(chunk.count)
                    sub.progress(done / total, file.lastPathComponent)
                }
                func hex<D: Sequence>(_ d: D) -> String where D.Element == UInt8 { d.map { String(format: "%02x", $0) }.joined() }
                report += "\(file.lastPathComponent)\n"
                if a { report += "  SHA-256  \(hex(h256.finalize()))\n" }
                if b { report += "  SHA-1    \(hex(h1.finalize()))\n" }
                if c { report += "  MD5      \(hex(h5.finalize()))\n" }
                report += "\n"
            }
            let tmp = try context.makeWorkDirectory().appendingPathComponent("checksum.txt")
            try report.write(to: tmp, atomically: true, encoding: .utf8)
            return [try context.deliver(tmp, as: context.outputName(for: files[0], tag: "校驗碼", ext: "txt"), for: files[0])]
        }
    }
}

private struct OTChecksumOptions: View {
    @ObservedObject var session: OTChecksumSession
    var body: some View {
        OptionGroup(title: "演算法", systemImage: "number.square") {
            OptionToggle(title: "SHA-256", subtitle: "最常用於驗證下載檔案", isOn: $session.sha256)
            OptionToggle(title: "SHA-1", subtitle: "Git 與部分舊系統", isOn: $session.sha1)
            OptionToggle(title: "MD5", subtitle: "快速比對（不適合安全用途）", isOn: $session.md5)
            OptionNote(text: "結果會存成文字檔，放在第一個檔案旁邊。")
        }
    }
}
