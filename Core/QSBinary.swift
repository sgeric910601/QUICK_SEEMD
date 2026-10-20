import CryptoKit
import Foundation

/// 二進位檔：辨識格式（Mach-O / ELF / PE …）、資訊面板、彩色十六進位傾印。
enum QSBinary {
    static let hexLimit = 8 * 1024

    private static let magics: [(bytes: [UInt8], offset: Int, name: String)] = [
        ([0x7F, 0x45, 0x4C, 0x46], 0, "ELF 執行檔"),
        ([0x00, 0x61, 0x73, 0x6D], 0, "WebAssembly 模組"),
        ([0x50, 0x4B, 0x03, 0x04], 0, "ZIP 壓縮資料"),
        ([0x1F, 0x8B], 0, "gzip 壓縮資料"),
        ([0xFD, 0x37, 0x7A, 0x58, 0x5A, 0x00], 0, "xz 壓縮資料"),
        ([0x42, 0x5A, 0x68], 0, "bzip2 壓縮資料"),
        ([0x28, 0xB5, 0x2F, 0xFD], 0, "Zstandard 壓縮資料"),
        ([0x37, 0x7A, 0xBC, 0xAF, 0x27, 0x1C], 0, "7-Zip 壓縮檔"),
        ([0x52, 0x61, 0x72, 0x21, 0x1A, 0x07], 0, "RAR 壓縮檔"),
        ([0x04, 0x22, 0x4D, 0x18], 0, "LZ4 壓縮資料"),
        (Array("bplist00".utf8), 0, "二進位屬性列表 (bplist)"),
        (Array("SQLite format 3".utf8), 0, "SQLite 資料庫"),
        (Array("%PDF-".utf8), 0, "PDF 文件"),
        ([0x00, 0x00, 0x00, 0x01, 0x42, 0x75, 0x64, 0x31], 0, "Finder .DS_Store 中繼資料"),
        (Array("PACK".utf8), 0, "Git pack 檔"),
        ([0x00, 0x01, 0x00, 0x00, 0x00], 0, "TrueType 字型"),
        (Array("OTTO".utf8), 0, "OpenType 字型"),
        (Array("wOFF".utf8), 0, "WOFF 網頁字型"),
        (Array("wOF2".utf8), 0, "WOFF2 網頁字型"),
        (Array("ID3".utf8), 0, "MP3 音訊（ID3）"),
        (Array("OggS".utf8), 0, "Ogg 媒體"),
        (Array("fLaC".utf8), 0, "FLAC 音訊"),
        ([0x1A, 0x45, 0xDF, 0xA3], 0, "Matroska / WebM 影片"),
        (Array("ftyp".utf8), 4, "ISO 媒體（MP4 / MOV / HEIC）"),
        (Array("RIFF".utf8), 0, "RIFF 容器（WAV / AVI / WebP）"),
        ([0x89, 0x50, 0x4E, 0x47], 0, "PNG 圖片"),
        ([0xFF, 0xD8, 0xFF], 0, "JPEG 圖片"),
        (Array("GIF8".utf8), 0, "GIF 圖片"),
        (Array("8BPS".utf8), 0, "PSD 影像"),
        ([0xD0, 0xCF, 0x11, 0xE0, 0xA1, 0xB1, 0x1A, 0xE1], 0, "Microsoft Office 舊版文件（OLE2）"),
        (Array("koly".utf8), -512, "磁碟映像檔 (DMG)"),
        (Array("xar!".utf8), 0, "XAR 封存（.pkg / .xip）"),
        ([0xED, 0xAB, 0xEE, 0xDB], 0, "RPM 套件"),
        (Array("!<arch>\n".utf8), 0, "ar 封存（.a / .deb）"),
        (Array("dex\n".utf8), 0, "Android DEX"),
        ([0x4C, 0x00, 0x00, 0x00, 0x01, 0x14, 0x02, 0x00], 0, "Windows 捷徑 (.lnk)"),
        (Array("-----BEGIN".utf8), 0, "PEM 憑證 / 金鑰"),
    ]

    static func identify(_ head: [UInt8]) -> (format: String?, details: [(String, String)]) {
        var details: [(String, String)] = []
        func u32le(_ o: Int) -> UInt32 { o + 3 < head.count ? UInt32(head[o]) | UInt32(head[o + 1]) << 8 | UInt32(head[o + 2]) << 16 | UInt32(head[o + 3]) << 24 : 0 }
        func u32be(_ o: Int) -> UInt32 { o + 3 < head.count ? UInt32(head[o]) << 24 | UInt32(head[o + 1]) << 16 | UInt32(head[o + 2]) << 8 | UInt32(head[o + 3]) : 0 }
        func u16le(_ o: Int) -> UInt16 { o + 1 < head.count ? UInt16(head[o]) | UInt16(head[o + 1]) << 8 : 0 }

        func cpuName(_ t: UInt32, _ sub: UInt32 = 0) -> String {
            switch t {
            case 0x0100_000C: return (sub & 0xFF) == 2 ? "arm64e" : "arm64"
            case 0x0200_000C: return "arm64_32"
            case 0x0100_0007: return "x86_64"
            case 7: return "i386"
            case 12: return "arm"
            case 18: return "ppc"
            case 0x0100_0012: return "ppc64"
            default: return String(format: "0x%x", t)
            }
        }
        let fileTypes: [UInt32: String] = [1: "目的檔 (.o)", 2: "執行檔", 3: "固定 VM 共用程式庫", 4: "核心傾印", 5: "預先載入執行檔", 6: "動態程式庫 (.dylib)", 7: "動態連結器", 8: "套件 (bundle)", 9: "動態程式庫 Stub", 10: "除錯符號 (dSYM)", 11: "核心延伸功能 (kext)"]

        let m = u32le(0)
        if m == 0xFEED_FACF || m == 0xFEED_FACE {
            details.append(("架構", cpuName(u32le(4), u32le(8))))
            details.append(("類型", fileTypes[u32le(12)] ?? "未知"))
            details.append(("載入命令", "\(u32le(16)) 個"))
            return ("Mach-O \(m == 0xFEED_FACF ? "64" : "32") 位元", details)
        }
        let mb = u32be(0)
        if mb == 0xCAFE_BABE || mb == 0xCAFE_BABF {
            let n = u32be(4)
            if n > 0 && n < 30 {
                let size = mb == 0xCAFE_BABF ? 32 : 20
                let archs = (0..<Int(n)).map { cpuName(u32be(8 + $0 * size), u32be(12 + $0 * size)) }
                details.append(("架構", archs.joined(separator: "、")))
                return ("Mach-O 通用二進位（Universal）", details)
            }
            let major = Int(head.count > 7 ? UInt16(head[6]) << 8 | UInt16(head[7]) : 0)
            if major >= 45 { details.append(("版本", "Java \(major - 44)")); return ("Java 類別檔", details) }
        }
        if head.starts(with: [0x7F, 0x45, 0x4C, 0x46]), head.count > 20 {
            let machines: [UInt16: String] = [0x3E: "x86-64", 0xB7: "AArch64", 0x28: "ARM", 0x03: "x86", 0xF3: "RISC-V", 0x08: "MIPS"]
            let types: [UInt16: String] = [1: "可重定位", 2: "執行檔", 3: "共用物件", 4: "核心傾印"]
            details.append(("位元", head[4] == 2 ? "64 位元" : "32 位元"))
            details.append(("架構", machines[u16le(18)] ?? String(format: "0x%x", u16le(18))))
            details.append(("類型", types[u16le(16)] ?? "未知"))
            return ("ELF（Linux）", details)
        }
        if head.starts(with: [0x4D, 0x5A]) {
            let pe = Int(u32le(0x3C))
            if pe > 0 && pe + 6 < head.count && head[pe] == 0x50 && head[pe + 1] == 0x45 {
                let machines: [UInt16: String] = [0x8664: "x64", 0x14C: "x86", 0xAA64: "ARM64", 0x1C0: "ARM"]
                details.append(("架構", machines[u16le(pe + 4)] ?? String(format: "0x%x", u16le(pe + 4))))
                return ("Windows PE 執行檔", details)
            }
            return ("DOS / Windows 執行檔", details)
        }
        for (bytes, offset, name) in magics where offset >= 0 {
            if head.count >= offset + bytes.count && Array(head[offset..<(offset + bytes.count)]) == bytes { return (name, details) }
        }
        return (nil, details)
    }

    static func sha256(_ url: URL, size: Int64) -> String? {
        guard size <= 64 * 1024 * 1024, let fh = try? FileHandle(forReadingFrom: url) else { return nil }
        defer { try? fh.close() }
        var hasher = SHA256()
        while let chunk = try? fh.read(upToCount: 1 << 20), !chunk.isEmpty { hasher.update(data: chunk) }
        return hasher.finalize().map { String(format: "%02x", $0) }.joined()
    }

    static func hexDump(_ data: Data, baseOffset: Int = 0) -> String {
        let b = [UInt8](data)
        var s = ""
        s.reserveCapacity(b.count * 40)
        func cls(_ c: UInt8) -> String {
            if c == 0 { return "z" }
            if c == 9 || c == 10 || c == 13 || c == 32 { return "w" }
            if c > 32 && c < 127 { return "p" }
            if c >= 128 { return "h" }
            return "x"
        }
        let hexDigits = Array("0123456789abcdef")
        var row = 0
        while row < b.count {
            let end = min(row + 16, b.count)
            s += "<span class=\"o\">\(String(format: "%08x", baseOffset + row))</span>  "
            for i in row..<(row + 16) {
                if i == row + 8 { s += " " }
                if i < end {
                    let c = b[i]
                    s += "<span class=\"\(cls(c))\">\(hexDigits[Int(c >> 4)])\(hexDigits[Int(c & 15)])</span> "
                } else {
                    s += "   "
                }
            }
            s += " <span class=\"sep\">│</span>"
            var lastCls = ""
            for i in row..<end {
                let c = b[i]
                let k = cls(c)
                if k != lastCls { if !lastCls.isEmpty { s += "</span>" }; s += "<span class=\"\(k)\">"; lastCls = k }
                switch c {
                case 38: s += "&amp;"
                case 60: s += "&lt;"
                case 62: s += "&gt;"
                case 33..<127: s.unicodeScalars.append(UnicodeScalar(c))
                case 32: s += " "
                default: s += "·"
                }
            }
            if !lastCls.isEmpty { s += "</span>" }
            s += "<span class=\"sep\">│</span>\n"
            row += 16
        }
        return s
    }

    /// 資訊面板：大圖示 + 檔名 + 屬性表。
    static func infoPanel(file: QSFile, rows: [(String, String)], subtitle: String) -> (html: String, attachments: [String: QSAttachment]) {
        var atts: [String: QSAttachment] = [:]
        var h = "<section class=\"info\">"
        if let png = QSIcons.png(forFile: file.url, size: 256) {
            atts["big-icon"] = QSAttachment(data: png, contentType: .png)
            h += "<img class=\"big\" src=\"cid:big-icon\" alt=\"\">"
        }
        h += "<div><div class=\"name\">\(QSHTML.escape(file.name))</div><div class=\"sub\">\(QSHTML.escape(subtitle))</div><dl class=\"kv\">"
        for (k, v) in rows {
            let mono = k == "SHA-256" || k == "權限"
            h += "<dt>\(QSHTML.escape(k))</dt><dd\(mono ? " class=\"mono\"" : "")>\(QSHTML.escape(v))</dd>"
        }
        h += "</dl></div></section>"
        return (h, atts)
    }

    static func standardRows(_ file: QSFile) -> [(String, String)] {
        [
            ("大小", "\(QSFormat.bytes(file.size))（\(QSFormat.number(Int(file.size))) 位元組）"),
            ("建立時間", QSFormat.dateLong(file.created)),
            ("修改時間", QSFormat.dateLong(file.modified)),
            ("權限", QSFormat.permissions(file.posixPermissions)),
        ]
    }

    static func render(file: QSFile, head: Data) -> QSPage {
        let (format, details) = identify([UInt8](head.prefix(4096)))
        var rows: [(String, String)] = []
        if let format { rows.append(("偵測格式", format)) }
        rows += details
        rows += standardRows(file)
        if let h = sha256(file.url, size: file.size) { rows.append(("SHA-256", h)) }

        let info = infoPanel(file: file, rows: rows, subtitle: file.kindDescription)
        var page = QSPage(kind: format ?? "二進位檔")
        page.showIcon = false
        page.size = CGSize(width: 820, height: 640)
        page.chips = [QSChip(file.kindDescription)]
        var body = info.html
        if !head.isEmpty {
            let shown = head.prefix(hexLimit)
            body += "<div class=\"section-title\">十六進位內容\(file.size > Int64(shown.count) ? "（前 \(QSFormat.bytes(Int64(shown.count)))）" : "")</div>"
            body += "<div class=\"card\"><pre class=\"hex\">\(hexDump(shown))</pre></div>"
        }
        page.body = body
        page.attachments = info.attachments
        return page
    }
}
