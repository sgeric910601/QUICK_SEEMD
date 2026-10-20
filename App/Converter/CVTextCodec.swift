import Foundation

/// 文字解碼／編碼：自動偵測 Big5、GB18030、Shift-JIS、UTF-16…，並處理換行字元。
enum CVTextCodec {
    static let big5 = String.Encoding(rawValue: CFStringConvertEncodingToNSStringEncoding(CFStringEncoding(CFStringEncodings.big5_HKSCS_1999.rawValue)))
    static let gb18030 = String.Encoding(rawValue: CFStringConvertEncodingToNSStringEncoding(CFStringEncoding(CFStringEncodings.GB_18030_2000.rawValue)))
    static let eucKR = String.Encoding(rawValue: CFStringConvertEncodingToNSStringEncoding(CFStringEncoding(CFStringEncodings.EUC_KR.rawValue)))
    /// 輸出 Big5 時使用 Windows 的 CP950（台灣最常見的 Big5 變體）。
    static let big5CP950 = String.Encoding(rawValue: CFStringConvertEncodingToNSStringEncoding(CFStringEncoding(CFStringEncodings.dosChineseTrad.rawValue)))

    enum LineEnding: String, Sendable {
        case lf = "LF", crlf = "CRLF", cr = "CR", mixed = "混合", none = "—"
    }

    struct Decoded: Sendable {
        let text: String
        let encoding: String.Encoding
        let hadBOM: Bool

        var encodingName: String { CVTextCodec.displayName(of: encoding) }
        var lineEnding: LineEnding { CVTextCodec.detectLineEnding(text) }
    }

    static func displayName(of encoding: String.Encoding) -> String {
        switch encoding {
        case .utf8: return "UTF-8"
        case .utf16, .utf16LittleEndian, .utf16BigEndian: return "UTF-16"
        case .utf32, .utf32LittleEndian, .utf32BigEndian: return "UTF-32"
        case big5, big5CP950: return "Big5"
        case gb18030: return "GB18030"
        case .shiftJIS: return "Shift-JIS"
        case eucKR: return "EUC-KR"
        case .windowsCP1252: return "Windows-1252"
        case .macOSRoman: return "Mac Roman"
        case .isoLatin1: return "ISO-8859-1"
        case .ascii: return "ASCII"
        default:
            let cf = CFStringConvertNSStringEncodingToEncoding(encoding.rawValue)
            if let name = CFStringConvertEncodingToIANACharSetName(cf) { return (name as String).uppercased() }
            return String.localizedName(of: encoding)
        }
    }

    static func decode(_ url: URL, forced: String.Encoding? = nil) throws -> Decoded {
        let data: Data
        do { data = try Data(contentsOf: url) } catch { throw ConversionError.unreadable(error.localizedDescription) }
        return try decode(data, forced: forced)
    }

    static func decode(_ data: Data, forced: String.Encoding? = nil) throws -> Decoded {
        // BOM 優先
        if data.starts(with: [0xEF, 0xBB, 0xBF]) {
            let body = data.dropFirst(3)
            if let s = String(data: body, encoding: .utf8) { return Decoded(text: s, encoding: .utf8, hadBOM: true) }
        }
        if data.starts(with: [0xFF, 0xFE, 0x00, 0x00]) || data.starts(with: [0x00, 0x00, 0xFE, 0xFF]) {
            if let s = String(data: data, encoding: .utf32) { return Decoded(text: s, encoding: .utf32, hadBOM: true) }
        }
        if data.starts(with: [0xFF, 0xFE]) || data.starts(with: [0xFE, 0xFF]) {
            if let s = String(data: data, encoding: .utf16) { return Decoded(text: s, encoding: .utf16, hadBOM: true) }
        }
        if let forced {
            guard let s = String(data: data, encoding: forced) else {
                throw ConversionError.unreadable("無法以 \(displayName(of: forced)) 解讀這個檔案，請改用其他來源編碼。")
            }
            return Decoded(text: s, encoding: forced, hadBOM: false)
        }
        if data.isEmpty { return Decoded(text: "", encoding: .utf8, hadBOM: false) }
        if let s = String(data: data, encoding: .utf8) { return Decoded(text: s, encoding: .utf8, hadBOM: false) }

        // 沒有 BOM 的 UTF-16（大量 0x00 交錯）
        if data.count >= 4 {
            let sample = data.prefix(4096)
            var evenZeros = 0, oddZeros = 0
            for (i, b) in sample.enumerated() where b == 0 { if i % 2 == 0 { evenZeros += 1 } else { oddZeros += 1 } }
            if oddZeros > sample.count / 4, evenZeros < sample.count / 50, let s = String(data: data, encoding: .utf16LittleEndian) {
                return Decoded(text: s, encoding: .utf16LittleEndian, hadBOM: false)
            }
            if evenZeros > sample.count / 4, oddZeros < sample.count / 50, let s = String(data: data, encoding: .utf16BigEndian) {
                return Decoded(text: s, encoding: .utf16BigEndian, hadBOM: false)
            }
        }

        // 交給 Foundation 的偵測器，偏好繁體中文
        var converted: NSString?
        var lossy = ObjCBool(false)
        let suggestions: [UInt] = [big5.rawValue, gb18030.rawValue, String.Encoding.shiftJIS.rawValue, eucKR.rawValue,
                                   String.Encoding.windowsCP1252.rawValue]
        let detected = NSString.stringEncoding(
            for: data,
            encodingOptions: [.suggestedEncodingsKey: suggestions, .allowLossyKey: false, .likelyLanguageKey: "zh-Hant"],
            convertedString: &converted, usedLossyConversion: &lossy)
        if detected != 0, let converted, !lossy.boolValue, detected != String.Encoding.utf8.rawValue {
            return Decoded(text: converted as String, encoding: String.Encoding(rawValue: detected), hadBOM: false)
        }
        // 後備：依序嘗試
        for enc in [big5, gb18030, .shiftJIS, eucKR, .windowsCP1252] {
            if let s = String(data: data, encoding: enc) { return Decoded(text: s, encoding: enc, hadBOM: false) }
        }
        throw ConversionError.unreadable("無法辨識文字編碼，請在選項中手動指定來源編碼。")
    }

    static func detectLineEnding(_ text: String) -> LineEnding {
        var crlf = 0, lf = 0, cr = 0
        let scalars = Array(text.unicodeScalars.prefix(2_000_000))
        var i = 0
        while i < scalars.count {
            let c = scalars[i]
            if c == "\r" {
                if i + 1 < scalars.count, scalars[i + 1] == "\n" { crlf += 1; i += 2; continue }
                cr += 1
            } else if c == "\n" {
                lf += 1
            }
            i += 1
        }
        let kinds = [crlf > 0, lf > 0, cr > 0].filter { $0 }.count
        if kinds == 0 { return .none }
        if kinds > 1 { return .mixed }
        return crlf > 0 ? .crlf : (lf > 0 ? .lf : .cr)
    }

    static func normalizeLineEndings(_ text: String, to ending: LineEnding) -> String {
        let lf = text.replacingOccurrences(of: "\r\n", with: "\n").replacingOccurrences(of: "\r", with: "\n")
        switch ending {
        case .crlf: return lf.replacingOccurrences(of: "\n", with: "\r\n")
        case .cr: return lf.replacingOccurrences(of: "\n", with: "\r")
        default: return lf
        }
    }

    /// 將字串編碼成指定編碼；無法表示的字元會丟出錯誤並指出第一個字元。
    static func encode(_ text: String, as encoding: String.Encoding, bom: Bool) throws -> Data {
        if encoding == .utf8 {
            var data = Data(text.utf8)
            if bom { data.insert(contentsOf: [0xEF, 0xBB, 0xBF], at: 0) }
            return data
        }
        if encoding == .utf16 || encoding == .utf16LittleEndian {
            // Windows 習慣的 UTF-16 LE + BOM
            var data = Data([0xFF, 0xFE])
            for unit in text.utf16 {
                data.append(UInt8(unit & 0xFF))
                data.append(UInt8(unit >> 8))
            }
            return data
        }
        if let data = text.data(using: encoding, allowLossyConversion: false) { return data }
        // 找出第一個無法編碼的字元
        var sample = "?"
        for ch in text where String(ch).data(using: encoding, allowLossyConversion: false) == nil {
            sample = String(ch)
            break
        }
        throw ConversionError.encodingFailed(encoding: displayName(of: encoding), sample: sample)
    }
}
