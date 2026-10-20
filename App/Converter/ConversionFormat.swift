import Foundation
import ImageIO

/// 輸出格式的分類（決定 UI 中的分組與選項面板）。
enum ConversionCategory: String, CaseIterable, Identifiable, Sendable {
    case image, document, audio, video, data, text, archive

    var id: String { rawValue }

    var title: String {
        switch self {
        case .image: return "圖片"
        case .document: return "文件"
        case .audio: return "音訊"
        case .video: return "影片"
        case .data: return "資料"
        case .text: return "文字編碼與換行"
        case .archive: return "壓縮與封存"
        }
    }

    var symbol: String {
        switch self {
        case .image: return "photo"
        case .document: return "doc.richtext"
        case .audio: return "waveform"
        case .video: return "film"
        case .data: return "curlybraces.square"
        case .text: return "doc.plaintext"
        case .archive: return "archivebox"
        }
    }

    /// 排序用。
    var order: Int {
        switch self {
        case .image: return 0
        case .document: return 1
        case .video: return 2
        case .audio: return 3
        case .data: return 4
        case .text: return 5
        case .archive: return 6
        }
    }
}

/// 實際執行轉換的引擎。
enum ConversionBackend: String, CaseIterable, Identifiable, Sendable {
    case native, ffmpeg, pandoc, libreOffice, imageMagick

    var id: String { rawValue }

    var displayName: String {
        switch self {
        case .native: return "macOS 內建"
        case .ffmpeg: return "ffmpeg"
        case .pandoc: return "Pandoc"
        case .libreOffice: return "LibreOffice"
        case .imageMagick: return "ImageMagick"
        }
    }
}

/// 一個可輸出的格式。`id` 是穩定的識別碼（例如 "png"、"mp4-hevc"），CLI 與 UI 都用它。
struct ConversionFormat: Identifiable, Hashable, Sendable {
    let id: String
    let displayName: String
    /// 輸出副檔名；空字串代表「沿用來源副檔名」（文字編碼轉換）或輸出為資料夾（解壓縮）。
    let fileExtension: String
    let category: ConversionCategory
    /// 由哪個引擎完成（外部引擎在 UI 上會加註標記）。
    var backend: ConversionBackend = .native
    /// 依輸入而定的補充說明，例如「每頁輸出一張」。
    var note: String? = nil

    static func == (lhs: ConversionFormat, rhs: ConversionFormat) -> Bool { lhs.id == rhs.id }
    func hash(into hasher: inout Hasher) { hasher.combine(id) }

    func with(note: String?) -> ConversionFormat {
        var copy = self
        copy.note = note
        return copy
    }

    func via(_ backend: ConversionBackend) -> ConversionFormat {
        var copy = self
        copy.backend = backend
        return copy
    }

    /// 是否為有損壓縮（顯示品質滑桿用）。
    var isLossyImage: Bool { ["jpeg", "heic", "avif", "webp", "jxl", "jp2"].contains(id) }
}

/// 所有格式的目錄。
enum ConversionFormats {
    // MARK: 圖片
    static let png = ConversionFormat(id: "png", displayName: "PNG", fileExtension: "png", category: .image)
    static let jpeg = ConversionFormat(id: "jpeg", displayName: "JPEG", fileExtension: "jpg", category: .image)
    static let heic = ConversionFormat(id: "heic", displayName: "HEIC", fileExtension: "heic", category: .image)
    static let avif = ConversionFormat(id: "avif", displayName: "AVIF", fileExtension: "avif", category: .image)
    static let webp = ConversionFormat(id: "webp", displayName: "WebP", fileExtension: "webp", category: .image)
    static let jxl = ConversionFormat(id: "jxl", displayName: "JPEG XL", fileExtension: "jxl", category: .image)
    static let tiff = ConversionFormat(id: "tiff", displayName: "TIFF", fileExtension: "tiff", category: .image)
    static let gif = ConversionFormat(id: "gif", displayName: "GIF", fileExtension: "gif", category: .image)
    static let bmp = ConversionFormat(id: "bmp", displayName: "BMP", fileExtension: "bmp", category: .image)
    static let jp2 = ConversionFormat(id: "jp2", displayName: "JPEG 2000", fileExtension: "jp2", category: .image)
    static let ico = ConversionFormat(id: "ico", displayName: "ICO 圖示", fileExtension: "ico", category: .image)
    static let icns = ConversionFormat(id: "icns", displayName: "ICNS 圖示", fileExtension: "icns", category: .image)
    static let psd = ConversionFormat(id: "psd", displayName: "PSD", fileExtension: "psd", category: .image)
    static let tga = ConversionFormat(id: "tga", displayName: "TGA", fileExtension: "tga", category: .image)
    static let animatedGIF = ConversionFormat(id: "anim-gif", displayName: "GIF 動畫", fileExtension: "gif", category: .image)

    // MARK: 文件
    static let pdf = ConversionFormat(id: "pdf", displayName: "PDF", fileExtension: "pdf", category: .document)
    static let docx = ConversionFormat(id: "docx", displayName: "Word (DOCX)", fileExtension: "docx", category: .document)
    static let doc = ConversionFormat(id: "doc", displayName: "Word 97 (DOC)", fileExtension: "doc", category: .document)
    static let odt = ConversionFormat(id: "odt", displayName: "OpenDocument (ODT)", fileExtension: "odt", category: .document)
    static let rtf = ConversionFormat(id: "rtf", displayName: "RTF", fileExtension: "rtf", category: .document)
    static let rtfd = ConversionFormat(id: "rtfd", displayName: "RTFD（含圖片）", fileExtension: "rtfd", category: .document)
    static let html = ConversionFormat(id: "html", displayName: "HTML", fileExtension: "html", category: .document)
    static let webarchive = ConversionFormat(id: "webarchive", displayName: "Web 封存檔", fileExtension: "webarchive", category: .document)
    static let txt = ConversionFormat(id: "txt", displayName: "純文字 (TXT)", fileExtension: "txt", category: .document)
    static let ocrText = ConversionFormat(id: "ocr-txt", displayName: "文字辨識 (OCR)", fileExtension: "txt", category: .document)
    static let markdown = ConversionFormat(id: "md", displayName: "Markdown", fileExtension: "md", category: .document)
    static let epub = ConversionFormat(id: "epub", displayName: "EPUB 電子書", fileExtension: "epub", category: .document)
    static let latex = ConversionFormat(id: "tex", displayName: "LaTeX", fileExtension: "tex", category: .document)
    static let pptx = ConversionFormat(id: "pptx", displayName: "PowerPoint (PPTX)", fileExtension: "pptx", category: .document)
    static let odp = ConversionFormat(id: "odp", displayName: "簡報 (ODP)", fileExtension: "odp", category: .document)

    // MARK: 影片
    static let mp4 = ConversionFormat(id: "mp4", displayName: "MP4 (H.264)", fileExtension: "mp4", category: .video)
    static let mp4HEVC = ConversionFormat(id: "mp4-hevc", displayName: "MP4 (HEVC)", fileExtension: "mp4", category: .video)
    static let mov = ConversionFormat(id: "mov", displayName: "MOV (H.264)", fileExtension: "mov", category: .video)
    static let movHEVC = ConversionFormat(id: "mov-hevc", displayName: "MOV (HEVC)", fileExtension: "mov", category: .video)
    static let prores = ConversionFormat(id: "prores", displayName: "MOV (ProRes 422)", fileExtension: "mov", category: .video)
    static let m4v = ConversionFormat(id: "m4v", displayName: "M4V", fileExtension: "m4v", category: .video)
    static let webm = ConversionFormat(id: "webm", displayName: "WebM (VP9)", fileExtension: "webm", category: .video)
    static let mkv = ConversionFormat(id: "mkv", displayName: "MKV", fileExtension: "mkv", category: .video)
    static let avi = ConversionFormat(id: "avi", displayName: "AVI", fileExtension: "avi", category: .video)

    // MARK: 音訊
    static let m4a = ConversionFormat(id: "m4a", displayName: "M4A (AAC)", fileExtension: "m4a", category: .audio)
    static let alac = ConversionFormat(id: "alac", displayName: "M4A (ALAC 無損)", fileExtension: "m4a", category: .audio)
    static let aac = ConversionFormat(id: "aac", displayName: "AAC (ADTS)", fileExtension: "aac", category: .audio)
    static let wav = ConversionFormat(id: "wav", displayName: "WAV", fileExtension: "wav", category: .audio)
    static let aiff = ConversionFormat(id: "aiff", displayName: "AIFF", fileExtension: "aiff", category: .audio)
    static let caf = ConversionFormat(id: "caf", displayName: "CAF", fileExtension: "caf", category: .audio)
    static let flac = ConversionFormat(id: "flac", displayName: "FLAC", fileExtension: "flac", category: .audio)
    static let mp3 = ConversionFormat(id: "mp3", displayName: "MP3", fileExtension: "mp3", category: .audio)
    static let ogg = ConversionFormat(id: "ogg", displayName: "OGG Vorbis", fileExtension: "ogg", category: .audio)
    static let opus = ConversionFormat(id: "opus", displayName: "Opus", fileExtension: "opus", category: .audio)

    // MARK: 資料
    static let json = ConversionFormat(id: "json", displayName: "JSON", fileExtension: "json", category: .data)
    static let jsonPretty = ConversionFormat(id: "json-pretty", displayName: "JSON（排版）", fileExtension: "json", category: .data)
    static let jsonMin = ConversionFormat(id: "json-min", displayName: "JSON（壓縮）", fileExtension: "json", category: .data)
    static let plistXML = ConversionFormat(id: "plist", displayName: "Plist (XML)", fileExtension: "plist", category: .data)
    static let plistBinary = ConversionFormat(id: "bplist", displayName: "Plist（二進位）", fileExtension: "plist", category: .data)
    static let yaml = ConversionFormat(id: "yaml", displayName: "YAML", fileExtension: "yaml", category: .data)
    static let csv = ConversionFormat(id: "csv", displayName: "CSV", fileExtension: "csv", category: .data)
    static let tsv = ConversionFormat(id: "tsv", displayName: "TSV", fileExtension: "tsv", category: .data)
    static let xlsx = ConversionFormat(id: "xlsx", displayName: "Excel (XLSX)", fileExtension: "xlsx", category: .data)
    static let ods = ConversionFormat(id: "ods", displayName: "試算表 (ODS)", fileExtension: "ods", category: .data)
    static let htmlTable = ConversionFormat(id: "html-table", displayName: "HTML 表格", fileExtension: "html", category: .data)

    // MARK: 文字編碼
    static let utf8 = ConversionFormat(id: "utf8", displayName: "UTF-8", fileExtension: "", category: .text)
    static let utf8BOM = ConversionFormat(id: "utf8-bom", displayName: "UTF-8（含 BOM）", fileExtension: "", category: .text)
    static let utf16 = ConversionFormat(id: "utf16", displayName: "UTF-16", fileExtension: "", category: .text)
    static let big5 = ConversionFormat(id: "big5", displayName: "Big5", fileExtension: "", category: .text)
    static let lf = ConversionFormat(id: "lf", displayName: "LF 換行（macOS／Linux）", fileExtension: "", category: .text)
    static let crlf = ConversionFormat(id: "crlf", displayName: "CRLF 換行（Windows）", fileExtension: "", category: .text)

    // MARK: 封存
    static let zip = ConversionFormat(id: "zip", displayName: "ZIP", fileExtension: "zip", category: .archive)
    static let tarGz = ConversionFormat(id: "tgz", displayName: "TAR.GZ", fileExtension: "tar.gz", category: .archive)
    static let sevenZip = ConversionFormat(id: "7z", displayName: "7z", fileExtension: "7z", category: .archive)
    static let dmg = ConversionFormat(id: "dmg", displayName: "DMG 磁碟映像", fileExtension: "dmg", category: .archive)
    static let extract = ConversionFormat(id: "extract", displayName: "解壓縮到資料夾", fileExtension: "", category: .archive)

    static let all: [ConversionFormat] = [
        png, jpeg, heic, avif, webp, jxl, tiff, gif, bmp, jp2, ico, icns, psd, tga, animatedGIF,
        pdf, docx, doc, odt, rtf, rtfd, html, webarchive, txt, ocrText, markdown, epub, latex, pptx, odp,
        mp4, mp4HEVC, mov, movHEVC, prores, m4v, webm, mkv, avi,
        m4a, alac, aac, wav, aiff, caf, flac, mp3, ogg, opus,
        json, jsonPretty, jsonMin, plistXML, plistBinary, yaml, csv, tsv, xlsx, ods, htmlTable,
        utf8, utf8BOM, utf16, big5, lf, crlf,
        zip, tarGz, sevenZip, dmg, extract,
    ]

    private static let byID: [String: ConversionFormat] = {
        var map: [String: ConversionFormat] = [:]
        for f in all { map[f.id] = f }
        return map
    }()

    /// 依 id 取得格式；也接受常見別名（jpg、tif、htm、yml、gz…）。
    static func format(id raw: String) -> ConversionFormat? {
        let key = raw.lowercased()
        let aliases: [String: String] = [
            "jpg": "jpeg", "tif": "tiff", "htm": "html", "yml": "yaml", "tar.gz": "tgz", "targz": "tgz",
            "gz": "tgz", "aif": "aiff", "j2k": "jp2", "text": "txt", "markdown": "md", "ocr": "ocr-txt",
            "unzip": "extract", "folder": "extract", "hevc": "mp4-hevc", "gif-anim": "anim-gif", "xml-plist": "plist",
        ]
        return byID[aliases[key] ?? key]
    }

    // MARK: ImageIO 對照

    /// 圖片格式 id → ImageIO 型別識別碼。
    static let imageUTIs: [String: String] = [
        "png": "public.png", "jpeg": "public.jpeg", "heic": "public.heic", "avif": "public.avif",
        "webp": "org.webmproject.webp", "jxl": "public.jpeg-xl", "tiff": "public.tiff",
        "gif": "com.compuserve.gif", "bmp": "com.microsoft.bmp", "jp2": "public.jpeg-2000",
        "ico": "com.microsoft.ico", "icns": "com.apple.icns", "psd": "com.adobe.photoshop-image",
        "tga": "com.truevision.tga-image",
    ]

    /// 本機 ImageIO 實際可寫入的圖片格式（執行時查詢）。
    static let nativeWritableImageFormats: [ConversionFormat] = {
        let writable = Set((CGImageDestinationCopyTypeIdentifiers() as? [String]) ?? [])
        let order: [ConversionFormat] = [png, jpeg, heic, avif, webp, jxl, tiff, gif, bmp, jp2, ico, icns, psd, tga]
        return order.filter { f in
            guard let uti = imageUTIs[f.id] else { return false }
            return writable.contains(uti)
        }
    }()
}
