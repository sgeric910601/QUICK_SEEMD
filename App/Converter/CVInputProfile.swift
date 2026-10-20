import AppKit
import AVFoundation
import Foundation
import ImageIO
import UniformTypeIdentifiers

/// 對一個輸入檔案的分析結果：它「是什麼」，以及哪些引擎讀得懂它。
/// 同一個檔案可能同時有多種身分（例如 CSV 也是純文字、GIF 也可以是動畫）。
struct CVInputProfile: Sendable {
    enum DocumentKind: String, Sendable {
        case plain, rtf, rtfd, html, doc, docx, odt, webArchive
    }

    enum DataKind: String, Sendable {
        case json, plist, csv, tsv, xlsx, yaml
    }

    enum ArchiveKind: String, Sendable {
        case zip, tar, sevenZip, rar, xar, cpio, iso, gzip, bzip2, xz
    }

    enum OfficeKind: String, Sendable {
        case word, spreadsheet, presentation, iWork
    }

    let url: URL
    let type: UTType?
    let ext: String
    let isDirectory: Bool
    let isPackage: Bool

    var isImage = false
    var isSVG = false
    var isAnimatedImage = false
    var imageFrameCount = 0
    var isPDF = false
    var documentKind: DocumentKind?
    var isMarkdown = false
    var isPlainText = false
    var isSourceCode = false
    var dataKind: DataKind?
    var isAudio = false
    var isVideo = false
    /// AVFoundation 能直接解碼。
    var isAVReadable = false
    /// 影片是否含音軌（analyze 時才確認；預設視為有）。
    var hasAudioTrack = true
    var archiveKind: ArchiveKind?
    var officeKind: OfficeKind?
    /// pandoc 的讀取格式名稱（pandoc -f）。
    var pandocReader: String?
    var isFFmpegCandidate = false
    var isMagickCandidate = false

    var fileName: String { url.lastPathComponent }

    /// 一般資料夾（不是 .rtfd／.app 這類套件）。
    var isPlainFolder: Bool { isDirectory && !isPackage }

    /// 主要身分的簡短說明（UI 用）。
    var kindLabel: String {
        if isPlainFolder { return "資料夾" }
        if let desc = type?.localizedDescription, !desc.isEmpty, type?.isDynamic == false { return desc }
        return ext.isEmpty ? "檔案" : ext.uppercased() + " 檔案"
    }
}

enum CVInputInspector {
    private static let imageReadable: Set<String> = Set((CGImageSourceCopyTypeIdentifiers() as? [String]) ?? [])
    private static let nsImageReadable: Set<String> = Set(NSImage.imageTypes)
    private static let avTypes: [UTType] = AVURLAsset.audiovisualTypes().compactMap { UTType($0.rawValue) }

    static let markdownExtensions: Set<String> = ["md", "markdown", "mdown", "mkd", "mkdn", "mdwn", "mdtxt"]
    static let ffmpegAudioExtensions: Set<String> = ["wma", "ogg", "oga", "opus", "ape", "mka", "dts", "ac3", "amr", "wv", "tta", "spx"]
    static let ffmpegVideoExtensions: Set<String> = [
        "mkv", "webm", "flv", "wmv", "ogv", "avi", "mpg", "mpeg", "ts", "m2ts", "mts", "3gp", "vob", "rm", "rmvb",
        "asf", "divx", "f4v", "mod",
    ]
    static let magickOnlyExtensions: Set<String> = ["xcf", "eps", "ps", "ai", "jxr", "wdp", "pcx", "ppm", "pgm", "xpm", "xbm", "qoi"]
    /// 這些副檔名本質上是 zip，但不該當成一般壓縮檔。
    private static let zipBasedDocuments: Set<String> = ["docx", "xlsx", "pptx", "odt", "ods", "odp", "epub", "ipa", "jar", "apk", "pages", "numbers", "key"]

    /// 分析檔案；只看類型與少量檔頭，速度快。
    static func inspect(_ url: URL) -> CVInputProfile {
        let values = try? url.resourceValues(forKeys: [.contentTypeKey, .isDirectoryKey, .isPackageKey])
        let isDir = values?.isDirectory ?? CVFileNaming.isDirectory(url)
        let isPackage = values?.isPackage ?? false
        let ext = url.pathExtension.lowercased()
        var type = values?.contentType
        if type == nil || type?.isDynamic == true, let byExt = UTType(filenameExtension: ext), !byExt.isDynamic {
            type = byExt
        }
        var p = CVInputProfile(url: url, type: type, ext: ext, isDirectory: isDir, isPackage: isPackage)
        if p.isPlainFolder { return p }

        let typeID = type?.identifier ?? ""
        func conforms(_ t: UTType) -> Bool { type?.conforms(to: t) ?? false }
        func conforms(_ id: String) -> Bool { UTType(id).map(conforms) ?? false }

        // PDF
        p.isPDF = conforms(.pdf) || ext == "pdf"

        // 圖片（交給 ImageIO 實際開檔確認）
        if !p.isPDF && !isDir {
            if conforms(.image) || imageReadable.contains(typeID) {
                if conforms(.svg) || ext == "svg" {
                    p.isSVG = nsImageReadable.contains(UTType.svg.identifier)
                    p.isImage = p.isSVG
                } else if let src = CGImageSourceCreateWithURL(url as CFURL, [kCGImageSourceShouldCache: false] as CFDictionary),
                          CGImageSourceGetCount(src) > 0, CGImageSourceGetType(src) != nil {
                    p.isImage = true
                    let count = CGImageSourceGetCount(src)
                    p.imageFrameCount = count
                    let animatable: Set<String> = ["com.compuserve.gif", "public.png", "org.webmproject.webp", "public.heics", "public.avis"]
                    p.isAnimatedImage = count > 1 && animatable.contains((CGImageSourceGetType(src) as String?) ?? "")
                }
                p.isMagickCandidate = !p.isSVG
            }
            if magickOnlyExtensions.contains(ext) { p.isMagickCandidate = true }
        }

        // 文件（NSAttributedString 讀得懂的）
        if conforms(.rtfd) || ext == "rtfd" { p.documentKind = .rtfd }
        else if conforms(.rtf) || ext == "rtf" { p.documentKind = .rtf }
        else if conforms(.html) || ["html", "htm", "xhtml"].contains(ext) { p.documentKind = .html }
        else if conforms(.webArchive) || ext == "webarchive" { p.documentKind = .webArchive }
        else if ["docx", "docm", "dotx"].contains(ext) || conforms("org.openxmlformats.wordprocessingml.document") { p.documentKind = .docx }
        else if ["doc", "dot"].contains(ext) || conforms("com.microsoft.word.doc") { p.documentKind = .doc }
        else if ["odt", "ott"].contains(ext) || conforms("org.oasis-open.opendocument.text") { p.documentKind = .odt }

        // 結構化資料
        if conforms(.json) || ["json", "jsonc", "json5", "geojson", "ipynb"].contains(ext) { p.dataKind = .json }
        else if ext == "plist" || conforms(.propertyList) { p.dataKind = .plist }
        else if ext == "csv" || conforms(.commaSeparatedText) { p.dataKind = .csv }
        else if ext == "tsv" || conforms(.tabSeparatedText) { p.dataKind = .tsv }
        else if ["xlsx", "xlsm"].contains(ext) || conforms("org.openxmlformats.spreadsheetml.sheet") { p.dataKind = .xlsx }
        else if ["yaml", "yml"].contains(ext) || conforms(.yaml) { p.dataKind = .yaml }

        p.isMarkdown = markdownExtensions.contains(ext) || typeID == "net.daringfireball.markdown"

        // 純文字（含程式碼、CSV、JSON、Markdown…）
        if p.documentKind == nil && !isDir && !p.isImage {
            let textual = conforms(.plainText) || conforms(.sourceCode) || conforms(.delimitedText) || conforms(.json)
                || conforms(.xml) || conforms(.yaml) || conforms(.script) || p.isMarkdown
            if textual {
                p.isPlainText = true
            } else if (type == nil || type?.isDynamic == true || type == .data || ext.isEmpty || conforms(.log))
                        && !ffmpegVideoExtensions.contains(ext) && !ffmpegAudioExtensions.contains(ext)
                        && !conforms(.audiovisualContent) && !conforms(.archive) {
                p.isPlainText = looksLikeText(url)
            }
            if p.dataKind == .plist, let head = try? readHead(url, count: 8), head.starts(with: Array("bplist".utf8)) {
                p.isPlainText = false
            }
            if p.isPlainText { p.documentKind = .plain }
        }
        p.isSourceCode = conforms(.sourceCode) || conforms(.script)

        // 影音
        let avReadable = type.map { t in avTypes.contains { t.conforms(to: $0) } } ?? false
        if conforms(.audio) || ffmpegAudioExtensions.contains(ext) {
            p.isAudio = true
        }
        if conforms(.movie) || conforms(.video) || ffmpegVideoExtensions.contains(ext) {
            p.isVideo = true
            p.isAudio = false
        }
        if p.isAudio || p.isVideo {
            p.isAVReadable = avReadable && !["mkv", "webm", "flv", "wmv", "ogv", "ogg", "oga", "opus", "wma", "ape"].contains(ext)
            p.isFFmpegCandidate = true
        }

        // 封存檔
        let lowerName = url.lastPathComponent.lowercased()
        if !isDir && !zipBasedDocuments.contains(ext) {
            if ext == "zip" || conforms(.zip) { p.archiveKind = .zip }
            else if ["tar", "tgz", "tbz", "tbz2", "txz"].contains(ext)
                        || CVFileNaming.compoundExtensions.contains(where: { lowerName.hasSuffix("." + $0) }) { p.archiveKind = .tar }
            else if ext == "7z" { p.archiveKind = .sevenZip }
            else if ext == "rar" { p.archiveKind = .rar }
            else if ext == "xar" || ext == "pkg" { p.archiveKind = .xar }
            else if ext == "cpio" { p.archiveKind = .cpio }
            else if ext == "iso" { p.archiveKind = .iso }
            else if ext == "gz" { p.archiveKind = .gzip }
            else if ext == "bz2" { p.archiveKind = .bzip2 }
            else if ext == "xz" { p.archiveKind = .xz }
        }

        // Office／iWork（LibreOffice 可處理）
        switch ext {
        case "doc", "docx", "docm", "odt", "rtf", "wpd": p.officeKind = .word
        case "xls", "xlsx", "xlsm", "ods", "csv": p.officeKind = .spreadsheet
        case "ppt", "pptx", "pps", "ppsx", "odp": p.officeKind = .presentation
        case "pages", "numbers", "key": p.officeKind = .iWork
        default: break
        }

        // pandoc
        if p.isMarkdown { p.pandocReader = "markdown" }
        else {
            let readers: [String: String] = [
                "docx": "docx", "odt": "odt", "html": "html", "htm": "html", "xhtml": "html", "epub": "epub",
                "tex": "latex", "latex": "latex", "rst": "rst", "org": "org", "ipynb": "ipynb", "rtf": "rtf",
                "textile": "textile", "fb2": "fb2", "opml": "opml", "dbk": "docbook", "typ": "typst",
            ]
            p.pandocReader = readers[ext]
        }
        return p
    }

    /// JSON 頂層是否為陣列（才能轉成表格）。
    static func jsonIsArray(_ url: URL) -> Bool {
        guard let head = try? readHead(url, count: 4096) else { return false }
        for byte in head {
            switch byte {
            case 0x20, 0x09, 0x0A, 0x0D, 0xEF, 0xBB, 0xBF: continue
            case UInt8(ascii: "["): return true
            default: return false
            }
        }
        return false
    }

    static func readHead(_ url: URL, count: Int) throws -> Data {
        let handle = try FileHandle(forReadingFrom: url)
        defer { try? handle.close() }
        return try handle.read(upToCount: count) ?? Data()
    }

    /// 以檔頭判斷是否為文字檔（沒有 NUL 位元組，或有 UTF-16 BOM）。
    static func looksLikeText(_ url: URL) -> Bool {
        guard let head = try? readHead(url, count: 8192) else { return false }
        if head.isEmpty { return true }
        if head.starts(with: [0xFF, 0xFE]) || head.starts(with: [0xFE, 0xFF]) { return true }
        if head.contains(0) { return false }
        var control = 0
        for b in head where b < 0x09 || (b > 0x0D && b < 0x20 && b != 0x1B) { control += 1 }
        return control * 100 < head.count
    }
}
