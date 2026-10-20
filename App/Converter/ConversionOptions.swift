import Foundation

/// 影片輸出的解析度上限（以短邊計算，不會放大）。
enum ConversionVideoSize: Int, CaseIterable, Identifiable, Sendable {
    case original = 0
    case p480 = 480
    case p720 = 720
    case p1080 = 1080
    case p2160 = 2160

    var id: Int { rawValue }

    var title: String {
        switch self {
        case .original: return "原始大小"
        case .p480: return "480p"
        case .p720: return "720p"
        case .p1080: return "1080p"
        case .p2160: return "4K"
        }
    }
}

/// 來源文字編碼（自動偵測或手動指定）。
enum ConversionTextEncoding: String, CaseIterable, Identifiable, Sendable {
    case auto, utf8, utf16, big5, gb18030, shiftJIS, eucKR, windows1252, macRoman

    var id: String { rawValue }

    var title: String {
        switch self {
        case .auto: return "自動偵測"
        case .utf8: return "UTF-8"
        case .utf16: return "UTF-16"
        case .big5: return "Big5（繁體中文）"
        case .gb18030: return "GB18030（簡體中文）"
        case .shiftJIS: return "Shift-JIS（日文）"
        case .eucKR: return "EUC-KR（韓文）"
        case .windows1252: return "Windows-1252（西歐）"
        case .macRoman: return "Mac Roman"
        }
    }

    var encoding: String.Encoding? {
        switch self {
        case .auto: return nil
        case .utf8: return .utf8
        case .utf16: return .utf16
        case .big5: return CVTextCodec.big5
        case .gb18030: return CVTextCodec.gb18030
        case .shiftJIS: return .shiftJIS
        case .eucKR: return CVTextCodec.eucKR
        case .windows1252: return .windowsCP1252
        case .macRoman: return .macOSRoman
        }
    }
}

/// 轉換選項；UI 共用一份，各轉換器只讀取自己需要的欄位。
struct ConversionOptions: Sendable, Equatable {
    // 圖片
    /// 有損格式品質 0…1。
    var imageQuality: Double = 0.85
    /// 最長邊像素上限；nil 代表維持原始尺寸。
    var imageMaxPixelSize: Int? = nil
    /// PDF 轉圖片時的解析度（DPI）。
    var pdfRenderDPI: Double = 150

    // 影片
    var videoSize: ConversionVideoSize = .original
    /// 截取靜態畫面的時間點（秒）；nil 代表自動（第 1 秒或影片中點）。
    var frameTime: Double? = nil
    var gifFramesPerSecond: Int = 12
    var gifMaxWidth: Int = 480
    var gifMaxDuration: Double = 15

    // 音訊
    /// AAC 位元率（bps）。
    var audioBitrate: Int = 192_000

    // 文字
    var sourceEncoding: ConversionTextEncoding = .auto

    init() {}
}
