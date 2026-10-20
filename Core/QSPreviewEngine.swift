import Compression
import Foundation
import UniformTypeIdentifiers

/// 預覽入口：依檔案內容與類型挑選合適的檢視方式，產生 HTML。
enum QSPreviewEngine {
    static let textLimit = 2 * 1024 * 1024
    static let markdownExts: Set<String> = ["md", "markdown", "mdown", "mkd", "mkdn", "mdx", "mdwn"]

    static func render(url: URL) -> QSDocument {
        let file = QSFile(url: url)
        return makePage(file).build(for: file)
    }

    static func makePage(_ file: QSFile) -> QSPage {
        let head = file.head(64 * 1024)
        let ext = file.ext
        let type = file.contentType

        if markdownExts.contains(ext) || type?.conforms(to: UTType("net.daringfireball.markdown") ?? .plainText) == true && type?.identifier != UTType.plainText.identifier,
           let decoded = readText(file, head: head) {
            return markdownPage(file, decoded)
        }
        if ext == "ipynb", let decoded = readText(file, head: head), let page = QSNotebook.render(file: file, text: decoded.text) {
            return page
        }
        if ["csv", "tsv", "tab"].contains(ext) || type?.conforms(to: .commaSeparatedText) == true || type?.conforms(to: .tabSeparatedText) == true,
           let decoded = readText(file, head: head) {
            var page = QSCSV.render(text: decoded.text, file: file, truncatedFile: decoded.truncated)
            page.chips.append(QSChip(decoded.encoding))
            return page
        }
        if QSSQLite.isSQLite(head), let page = QSSQLite.render(file: file) { return page }
        if head.starts(with: [0x50, 0x4B, 0x03, 0x04]) || head.starts(with: [0x50, 0x4B, 0x05, 0x06]),
           let listing = QSArchive.readZip(file.url, fileSize: file.size) {
            var extra: [QSChip] = []
            if !["zip"].contains(ext) && !ext.isEmpty { extra.append(QSChip("\(ext.uppercased()) 使用 ZIP 格式")) }
            return QSArchiveView.render(listing, file: file, extraChips: extra)
        }
        if head.starts(with: [0x1F, 0x8B]), let page = gzipPage(file) { return page }
        if head.starts(with: [0xFD, 0x37, 0x7A, 0x58, 0x5A, 0x00]), let page = xzPage(file) { return page }
        if QSArchive.looksLikeTar(head) || ext == "tar", let listing = QSArchive.readTar(file.url) {
            return QSArchiveView.render(listing, file: file)
        }
        if head.starts(with: Array("bplist00".utf8)), let page = binaryPlistPage(file) { return page }
        if let decoded = readText(file, head: head) {
            return codePage(file, decoded)
        }
        return QSBinary.render(file: file, head: head)
    }

    // MARK: 讀取文字

    static func readText(_ file: QSFile, head: Data) -> QSDecodedText? {
        if QSText.looksBinary(head) { return nil }
        let truncated = file.size > Int64(textLimit)
        let data = Int64(head.count) >= file.size ? head : file.head(textLimit)
        return QSText.decode(data, truncated: truncated)
    }

    static func sizeNotice(_ file: QSFile, _ decoded: QSDecodedText) -> QSNotice? {
        guard decoded.truncated else { return nil }
        return QSNotice("檔案很大（\(QSFormat.bytes(file.size))），只顯示前 \(QSFormat.bytes(Int64(textLimit)))", warn: true)
    }

    // MARK: 程式碼 / 文字

    static func codePage(_ file: QSFile, _ decoded: QSDecodedText, language: QSLanguage? = nil, extraChips: [QSChip] = []) -> QSPage {
        var text = decoded.text
        let lang = language ?? QSLanguages.detect(file: file, text: text)
        var page = QSPage(kind: lang.name)
        var chips: [QSChip] = []

        if lang.id == "json" || lang.id == "jsonc" {
            var valid = true
            if !decoded.truncated && lang.id == "json" {
                let (summary, error) = QSJSON.inspect(text)
                if let summary { chips.append(QSChip("✓ 有效 JSON", .ok)); chips.append(QSChip(summary)) }
                if let error {
                    valid = false
                    chips.append(QSChip("JSON 格式錯誤", .err))
                    page.notices.append(QSNotice(error, warn: true))
                }
            }
            if valid && QSJSON.looksMinified(text) {
                text = QSJSON.prettyPrint(text)
                chips.append(QSChip("已自動排版"))
            }
        }
        let (html, lines) = QSHighlighter.codeView(text, language: lang)
        page.chips = chips + extraChips + [QSChip("\(QSFormat.number(lines)) 行"), QSChip(decoded.encoding), QSChip(decoded.lineEnding)]
        if text.isEmpty { page.notices.append(QSNotice("這是空白檔案")) }
        if let n = sizeNotice(file, decoded) { page.notices.append(n) }
        page.body = html
        page.size = lang.wrap ? CGSize(width: 820, height: 680) : CGSize(width: 920, height: 700)
        return page
    }

    // MARK: Markdown

    static func markdownPage(_ file: QSFile, _ decoded: QSDecodedText) -> QSPage {
        let (html, atts) = QSMarkdown.render(decoded.text, baseURL: file.url.deletingLastPathComponent())
        var page = QSPage(kind: "Markdown")
        let words = decoded.text.unicodeScalars.filter { CharacterSet.letters.contains($0) || CharacterSet.decimalDigits.contains($0) }.count
        page.chips = [QSChip("約 \(QSFormat.number(words)) 字"), QSChip(decoded.encoding)]
        if let n = sizeNotice(file, decoded) { page.notices.append(n) }
        page.body = "<article class=\"md\">\(html)</article>"
        page.attachments = atts
        page.size = CGSize(width: 900, height: 780)
        return page
    }

    // MARK: 二進位 plist

    static func binaryPlistPage(_ file: QSFile) -> QSPage? {
        guard file.size < 32 * 1024 * 1024, let data = try? Data(contentsOf: file.url),
              let obj = try? PropertyListSerialization.propertyList(from: data, format: nil),
              let xml = try? PropertyListSerialization.data(fromPropertyList: obj, format: .xml, options: 0) else { return nil }
        let text = String(decoding: xml, as: UTF8.self)
        let decoded = QSDecodedText(text: text, encoding: "UTF-8", lineEnding: "LF", truncated: false)
        var page = codePage(file, decoded, language: QSLanguages.byID["xml"], extraChips: [QSChip("二進位 plist，已轉為 XML 顯示")])
        page.kind = "屬性列表 (plist)"
        return page
    }

    // MARK: gzip / xz

    private static func virtualFile(named name: String) -> QSFile {
        QSFile(url: URL(fileURLWithPath: "/.quickseemd-virtual/").appendingPathComponent(name))
    }

    static func gzipPage(_ file: QSFile) -> QSPage? {
        guard let info = QSArchive.gzipInfo(file.url, fileSize: file.size) else { return nil }
        let inner = QSArchive.decompressHead(file.url, algorithm: COMPRESSION_ZLIB, skip: info.headerLength, limit: textLimit)
        if QSArchive.looksLikeTar(inner),
           let listing = QSArchive.readCompressedTar(file.url, algorithm: COMPRESSION_ZLIB, skip: info.headerLength, format: "TAR.GZ") {
            return QSArchiveView.render(listing, file: file)
        }
        let innerName = info.originalName ?? (file.name as NSString).deletingPathExtension
        var rows: [(String, String)] = [("壓縮格式", "gzip"), ("原始檔名", innerName)]
        if let s = info.uncompressedSize {
            rows.append(("原始大小", QSFormat.bytes(s)))
            if s > 0 { rows.append(("壓縮率", "\(max(0, Int((1 - Double(file.size) / Double(s)) * 100)))%")) }
        }
        return compressedSinglePage(file, inner: inner, innerName: innerName, rows: rows, format: "gzip 壓縮檔")
    }

    static func xzPage(_ file: QSFile) -> QSPage? {
        let inner = QSArchive.decompressHead(file.url, algorithm: COMPRESSION_LZMA, skip: 0, limit: textLimit)
        guard !inner.isEmpty else { return nil }
        if QSArchive.looksLikeTar(inner), let listing = QSArchive.readCompressedTar(file.url, algorithm: COMPRESSION_LZMA, skip: 0, format: "TAR.XZ") {
            return QSArchiveView.render(listing, file: file)
        }
        let innerName = (file.name as NSString).deletingPathExtension
        return compressedSinglePage(file, inner: inner, innerName: innerName, rows: [("壓縮格式", "xz"), ("原始檔名", innerName)], format: "xz 壓縮檔")
    }

    private static func compressedSinglePage(_ file: QSFile, inner: Data, innerName: String, rows: [(String, String)], format: String) -> QSPage {
        let info = QSBinary.infoPanel(file: file, rows: rows + QSBinary.standardRows(file), subtitle: format)
        var page = QSPage(kind: format)
        page.showIcon = false
        page.attachments = info.attachments
        var body = info.html
        if let decoded = QSText.looksBinary(inner) ? nil : QSText.decode(inner, truncated: inner.count >= textLimit) {
            let lang = QSLanguages.detect(file: virtualFile(named: innerName), text: decoded.text)
            body += "<div class=\"section-title\">解壓縮後的內容 · \(QSHTML.escape(lang.name))</div>"
            body += QSHighlighter.codeView(decoded.text, language: lang).html
        } else if !inner.isEmpty {
            body += "<div class=\"section-title\">解壓縮後的內容（十六進位）</div>"
            body += "<div class=\"card\"><pre class=\"hex\">\(QSBinary.hexDump(inner.prefix(QSBinary.hexLimit)))</pre></div>"
        }
        page.body = body
        page.size = CGSize(width: 860, height: 680)
        return page
    }
}

/// Jupyter Notebook (.ipynb)：Markdown 儲存格、程式碼、輸出（文字、圖片、HTML 表格、錯誤）。
enum QSNotebook {
    static func render(file: QSFile, text: String) -> QSPage? {
        guard let data = text.data(using: .utf8),
              let root = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
              let cells = root["cells"] as? [[String: Any]] else { return nil }
        let meta = root["metadata"] as? [String: Any]
        let langName = ((meta?["language_info"] as? [String: Any])?["name"] as? String)
            ?? ((meta?["kernelspec"] as? [String: Any])?["language"] as? String) ?? "python"
        let lang = QSLanguages.forHint(langName) ?? QSLanguages.byID["python"]!
        var atts: [String: QSAttachment] = [:]
        var imageCount = 0

        func joined(_ v: Any?) -> String {
            if let s = v as? String { return s }
            if let a = v as? [String] { return a.joined() }
            return ""
        }
        func stripANSI(_ s: String) -> String {
            s.replacingOccurrences(of: "\u{1B}\\[[0-9;]*[A-Za-z]", with: "", options: .regularExpression)
        }

        var html = "<div class=\"nb\">"
        var codeCells = 0
        for cell in cells {
            let src = joined(cell["source"])
            switch cell["cell_type"] as? String {
            case "markdown":
                let r = QSMarkdown.render(src, baseURL: file.url.deletingLastPathComponent())
                atts.merge(r.attachments) { a, _ in a }
                html += "<div class=\"cell\"><div class=\"prompt\"></div><div class=\"md\">\(r.html)</div></div>"
            case "code":
                codeCells += 1
                let n = (cell["execution_count"] as? Int).map(String.init) ?? " "
                html += "<div class=\"cell\"><div class=\"prompt in\">In [\(n)]:</div><div class=\"cellsrc\">"
                html += QSHighlighter.codeView(src, language: lang, wrap: false, gutter: false).html
                html += "</div></div>"
                for out in (cell["outputs"] as? [[String: Any]]) ?? [] {
                    var body = ""
                    switch out["output_type"] as? String {
                    case "stream":
                        let err = (out["name"] as? String) == "stderr"
                        body = "<pre\(err ? " class=\"err\"" : "")>\(QSHTML.escape(stripANSI(joined(out["text"]))))</pre>"
                    case "error":
                        let tb = ((out["traceback"] as? [String]) ?? []).map(stripANSI).joined(separator: "\n")
                        body = "<pre class=\"err\">\(QSHTML.escape(tb.isEmpty ? "\(out["ename"] ?? ""): \(out["evalue"] ?? "")" : tb))</pre>"
                    default:
                        let d = (out["data"] as? [String: Any]) ?? [:]
                        if let png = d["image/png"].map(joined), let img = Data(base64Encoded: png, options: .ignoreUnknownCharacters) {
                            imageCount += 1
                            atts["nb-img-\(imageCount)"] = QSAttachment(data: img, contentType: .png)
                            body = "<img src=\"cid:nb-img-\(imageCount)\" alt=\"\">"
                        } else if let jpg = d["image/jpeg"].map(joined), let img = Data(base64Encoded: jpg, options: .ignoreUnknownCharacters) {
                            imageCount += 1
                            atts["nb-img-\(imageCount)"] = QSAttachment(data: img, contentType: .jpeg)
                            body = "<img src=\"cid:nb-img-\(imageCount)\" alt=\"\">"
                        } else if let svg = d["image/svg+xml"].map(joined) {
                            body = svg
                        } else if let h = d["text/html"].map(joined) {
                            body = h
                        } else if let md = d["text/markdown"].map(joined) {
                            body = "<div class=\"md\">\(QSMarkdown.render(md, baseURL: nil).html)</div>"
                        } else if let t = d["text/plain"].map(joined) {
                            body = "<pre>\(QSHTML.escape(stripANSI(t)))</pre>"
                        }
                    }
                    if body.isEmpty { continue }
                    let n = (out["execution_count"] as? Int).map { "Out [\($0)]:" } ?? ""
                    html += "<div class=\"cell\"><div class=\"prompt\">\(n)</div><div class=\"out\">\(body)</div></div>"
                }
            default:
                html += "<div class=\"cell\"><div class=\"prompt\"></div><div class=\"out\"><pre>\(QSHTML.escape(src))</pre></div></div>"
            }
        }
        html += "</div>"
        var page = QSPage(kind: "Jupyter Notebook")
        page.chips = [QSChip(lang.name), QSChip("\(cells.count) 個儲存格（\(codeCells) 個程式碼）")]
        page.body = html
        page.attachments = atts
        page.size = CGSize(width: 980, height: 800)
        return page
    }
}
