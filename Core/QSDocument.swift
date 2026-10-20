import AppKit
import CoreGraphics
import ImageIO
import UniformTypeIdentifiers

/// 預覽產出的 HTML 與附件（附件在 HTML 內以 `cid:名稱` 引用）。
struct QSDocument {
    var html: String
    var attachments: [String: QSAttachment] = [:]
    var preferredSize: CGSize
}

struct QSAttachment {
    var data: Data
    var contentType: UTType
}

/// 組合一個預覽頁面：工具列 + 提示 + 內容。
struct QSPage {
    var kind: String
    var chips: [QSChip] = []
    var notices: [QSNotice] = []
    var body: String = ""
    var bodyClass: String = ""
    var showIcon = true
    var size = CGSize(width: 900, height: 680)
    var attachments: [String: QSAttachment] = [:]

    func build(for file: QSFile) -> QSDocument {
        var atts = attachments
        var html = "<!DOCTYPE html><html><head><meta charset=\"utf-8\"><meta name=\"generator\" content=\"QUICK_SEEMD\"><style>"
        html += QSStyle.css
        html += "</style></head><body class=\"\(bodyClass)\">"
        html += "<header class=\"qs-bar\">"
        if showIcon, let icon = QSIcons.png(forFile: file.url, size: 36) {
            atts["file-icon"] = QSAttachment(data: icon, contentType: .png)
            html += "<img class=\"ico\" src=\"cid:file-icon\" alt=\"\">"
        }
        html += "<span class=\"kind\">\(QSHTML.escape(kind))</span>"
        for chip in chips { html += chip.html }
        html += "<span class=\"spacer\"></span><span class=\"chip\">\(QSFormat.bytes(file.size))</span>"
        html += "</header>"
        for n in notices { html += n.html }
        html += body
        html += "</body></html>"
        return QSDocument(html: html, attachments: atts, preferredSize: size)
    }
}

struct QSChip {
    enum Tone: String { case plain = "", ok, warn, err }
    var text: String
    var tone: Tone = .plain
    init(_ text: String, _ tone: Tone = .plain) { self.text = text; self.tone = tone }
    var html: String { "<span class=\"chip \(tone.rawValue)\">\(QSHTML.escape(text))</span>" }
}

struct QSNotice {
    var text: String
    var warn = false
    init(_ text: String, warn: Bool = false) { self.text = text; self.warn = warn }
    var html: String { "<div class=\"notice\(warn ? " warn" : "")\">\(QSHTML.escape(text))</div>" }
}

/// 被預覽檔案的基本資訊。
struct QSFile {
    let url: URL
    let name: String
    let ext: String
    let size: Int64
    let contentType: UTType?
    let created: Date?
    let modified: Date?
    let posixPermissions: Int?

    init(url: URL) {
        self.url = url
        name = url.lastPathComponent
        ext = url.pathExtension.lowercased()
        let values = try? url.resourceValues(forKeys: [.fileSizeKey, .contentTypeKey, .creationDateKey, .contentModificationDateKey])
        let attrs = try? FileManager.default.attributesOfItem(atPath: url.path)
        size = Int64(values?.fileSize ?? (attrs?[.size] as? Int) ?? 0)
        contentType = values?.contentType
        created = values?.creationDate
        modified = values?.contentModificationDate
        posixPermissions = (attrs?[.posixPermissions] as? NSNumber)?.intValue
    }

    /// 讀取檔案開頭最多 `limit` 位元組。
    func head(_ limit: Int) -> Data {
        guard let fh = try? FileHandle(forReadingFrom: url) else { return Data() }
        defer { try? fh.close() }
        return (try? fh.read(upToCount: limit)) ?? Data()
    }

    var kindDescription: String {
        if let d = contentType?.localizedDescription, !(contentType?.isDynamic ?? true) { return d }
        return ext.isEmpty ? "文件" : "\(ext.uppercased()) 檔案"
    }
}

enum QSHTML {
    static func escape(_ s: String) -> String {
        var out = ""
        out.reserveCapacity(s.utf8.count + 16)
        for ch in s.unicodeScalars {
            switch ch {
            case "&": out += "&amp;"
            case "<": out += "&lt;"
            case ">": out += "&gt;"
            case "\"": out += "&quot;"
            default: out.unicodeScalars.append(ch)
            }
        }
        return out
    }
}

enum QSFormat {
    static func bytes(_ n: Int64) -> String {
        if n < 1024 { return "\(n) 位元組" }
        let f = ByteCountFormatter()
        f.countStyle = .file
        return f.string(fromByteCount: n)
    }

    static func number(_ n: Int) -> String {
        let f = NumberFormatter()
        f.numberStyle = .decimal
        return f.string(from: NSNumber(value: n)) ?? "\(n)"
    }

    static func date(_ d: Date?) -> String {
        guard let d else { return "—" }
        let f = DateFormatter()
        f.locale = Locale(identifier: "zh_TW")
        f.dateFormat = "yyyy/MM/dd HH:mm"
        return f.string(from: d)
    }

    static func dateLong(_ d: Date?) -> String {
        guard let d else { return "—" }
        let f = DateFormatter()
        f.locale = Locale(identifier: "zh_TW")
        f.dateStyle = .long
        f.timeStyle = .medium
        return f.string(from: d)
    }

    static func permissions(_ p: Int?) -> String {
        guard let p else { return "—" }
        let chars = Array("rwxrwxrwx")
        var s = ""
        for i in 0..<9 { s.append((p >> (8 - i)) & 1 == 1 ? chars[i] : "-") }
        return s + String(format: "（%o）", p & 0o777)
    }
}

/// 從 Finder 取得檔案 / 類型圖示並轉成 PNG（可在背景執行緒使用）。
enum QSIcons {
    private static var cache: [String: Data] = [:]
    private static let lock = NSLock()

    static func png(forFile url: URL, size: Int) -> Data? {
        render(NSWorkspace.shared.icon(forFile: url.path), size: size)
    }

    static func png(forType type: UTType, size: Int) -> Data? {
        let key = "\(type.identifier)@\(size)"
        lock.lock(); if let d = cache[key] { lock.unlock(); return d }; lock.unlock()
        let d = render(NSWorkspace.shared.icon(for: type), size: size)
        lock.lock(); cache[key] = d; lock.unlock()
        return d
    }

    static func render(_ image: NSImage, size: Int) -> Data? {
        var rect = CGRect(x: 0, y: 0, width: size, height: size)
        guard let cg = image.cgImage(forProposedRect: &rect, context: nil, hints: nil),
              let ctx = CGContext(data: nil, width: size, height: size, bitsPerComponent: 8, bytesPerRow: 0,
                                  space: CGColorSpace(name: CGColorSpace.sRGB)!,
                                  bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue) else { return nil }
        ctx.interpolationQuality = .high
        ctx.draw(cg, in: CGRect(x: 0, y: 0, width: size, height: size))
        guard let out = ctx.makeImage() else { return nil }
        let data = NSMutableData()
        guard let dest = CGImageDestinationCreateWithData(data, UTType.png.identifier as CFString, 1, nil) else { return nil }
        CGImageDestinationAddImage(dest, out, nil)
        guard CGImageDestinationFinalize(dest) else { return nil }
        return data as Data
    }
}
