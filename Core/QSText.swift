import Foundation

/// 文字解碼：BOM、UTF-8、UTF-16 猜測，以及 Big5 / GB18030 / Shift-JIS 等舊編碼偵測。
struct QSDecodedText {
    var text: String          // 已統一為 LF 換行
    var encoding: String      // 顯示用名稱
    var lineEnding: String    // LF / CRLF / CR / 混合
    var truncated: Bool
}

enum QSText {
    static func cf(_ e: CFStringEncodings) -> String.Encoding {
        String.Encoding(rawValue: CFStringConvertEncodingToNSStringEncoding(CFStringEncoding(e.rawValue)))
    }

    static let big5 = cf(.big5)
    static let gb18030 = cf(.GB_18030_2000)
    static let eucKR = cf(.EUC_KR)

    /// 判斷資料看起來是否為二進位（含 NUL 或大量控制字元）。UTF-16 會另外處理。
    static func looksBinary(_ data: Data) -> Bool {
        if data.isEmpty { return false }
        if utf16Guess(data) != nil { return false }
        let sample = data.prefix(16384)
        var control = 0
        for b in sample {
            if b == 0 { return true }
            if b < 0x20 && b != 9 && b != 10 && b != 12 && b != 13 && b != 27 { control += 1 }
        }
        return control * 100 > sample.count * 2
    }

    private static func utf16Guess(_ data: Data) -> String.Encoding? {
        let bytes = [UInt8](data.prefix(2048))
        if bytes.count >= 2 {
            if bytes[0] == 0xFF && bytes[1] == 0xFE { return .utf16LittleEndian }
            if bytes[0] == 0xFE && bytes[1] == 0xFF { return .utf16BigEndian }
        }
        guard bytes.count >= 16 else { return nil }
        var evenZero = 0, oddZero = 0
        for (i, b) in bytes.enumerated() where b == 0 { if i % 2 == 0 { evenZero += 1 } else { oddZero += 1 } }
        let half = bytes.count / 2
        if oddZero > half * 7 / 10 && evenZero < half / 10 { return .utf16LittleEndian }
        if evenZero > half * 7 / 10 && oddZero < half / 10 { return .utf16BigEndian }
        return nil
    }

    static func decode(_ raw: Data, truncated: Bool) -> QSDecodedText? {
        var data = raw
        var text: String?
        var name = "UTF-8"

        if data.starts(with: [0xEF, 0xBB, 0xBF]) {
            data = data.dropFirst(3)
            text = decodeUTF8(data, allowTrim: truncated)
            name = "UTF-8（BOM）"
        } else if data.starts(with: [0xFF, 0xFE, 0, 0]) {
            text = String(data: data.dropFirst(4), encoding: .utf32LittleEndian); name = "UTF-32 LE"
        } else if data.starts(with: [0, 0, 0xFE, 0xFF]) {
            text = String(data: data.dropFirst(4), encoding: .utf32BigEndian); name = "UTF-32 BE"
        } else if let enc = utf16Guess(data) {
            var d = data
            if d.starts(with: [0xFF, 0xFE]) || d.starts(with: [0xFE, 0xFF]) { d = d.dropFirst(2) }
            if d.count % 2 == 1 { d = d.dropLast() }
            text = String(data: d, encoding: enc)
            name = enc == .utf16LittleEndian ? "UTF-16 LE" : "UTF-16 BE"
        } else if looksBinary(data) {
            return nil
        } else if let s = decodeUTF8(data, allowTrim: truncated) {
            text = s
            name = data.contains(where: { $0 >= 0x80 }) ? "UTF-8" : "ASCII"
        } else {
            var converted: NSString?
            var lossy: ObjCBool = false
            let suggested = [big5, gb18030, .shiftJIS, .japaneseEUC, eucKR, .windowsCP1252, .macOSRoman].map { NSNumber(value: $0.rawValue) }
            let enc = NSString.stringEncoding(for: data, encodingOptions: [
                .suggestedEncodingsKey: suggested,
                .allowLossyKey: false,
            ], convertedString: &converted, usedLossyConversion: &lossy)
            if enc != 0, let c = converted {
                text = c as String
                name = String.localizedName(of: String.Encoding(rawValue: enc))
            } else if let s = String(data: data, encoding: .isoLatin1) {
                text = s; name = "ISO Latin-1"
            }
        }
        guard let t = text else { return nil }
        let (normalized, ending) = normalizeNewlines(t)
        return QSDecodedText(text: normalized, encoding: name, lineEnding: ending, truncated: truncated)
    }

    /// UTF-8 解碼；檔案被截斷時，容許結尾有被切開的多位元組字元。
    private static func decodeUTF8(_ data: Data, allowTrim: Bool) -> String? {
        if let s = String(data: data, encoding: .utf8) { return s }
        guard allowTrim else { return nil }
        for cut in 1...3 where data.count > cut {
            if let s = String(data: data.dropLast(cut), encoding: .utf8) { return s }
        }
        return nil
    }

    static func normalizeNewlines(_ s: String) -> (String, String) {
        var crlf = 0, cr = 0, lf = 0
        let u = Array(s.utf8)
        var i = 0
        while i < u.count {
            if u[i] == 13 {
                if i + 1 < u.count && u[i + 1] == 10 { crlf += 1; i += 2; continue }
                cr += 1
            } else if u[i] == 10 {
                lf += 1
            }
            i += 1
        }
        let kinds = [crlf > 0, cr > 0, lf > 0].filter { $0 }.count
        let ending = kinds > 1 ? "混合" : crlf > 0 ? "CRLF" : cr > 0 ? "CR" : "LF"
        if crlf == 0 && cr == 0 { return (s, ending) }
        var out = s.replacingOccurrences(of: "\r\n", with: "\n")
        if cr > 0 { out = out.replacingOccurrences(of: "\r", with: "\n") }
        return (out, ending)
    }
}
