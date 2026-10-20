import Compression
import Foundation
import UniformTypeIdentifiers

struct QSArchiveEntry {
    var path: String
    var isDir: Bool
    var size: Int64?
    var compressedSize: Int64?
    var modified: Date?
    var encrypted = false
    var isLink = false
}

struct QSArchiveListing {
    var format: String
    var entries: [QSArchiveEntry] = []
    var truncated = false
    var hiddenMacMetadata = 0
    var comment: String?
}

/// 不解壓縮即可列出 ZIP / TAR / TAR.GZ / TAR.XZ 內容。
enum QSArchive {
    static let maxEntries = 20000

    // MARK: 小工具

    @inline(__always) static func u16(_ b: [UInt8], _ o: Int) -> Int { o + 1 < b.count ? Int(b[o]) | Int(b[o + 1]) << 8 : 0 }
    @inline(__always) static func u32(_ b: [UInt8], _ o: Int) -> Int64 { o + 3 < b.count ? Int64(u16(b, o)) | Int64(u16(b, o + 2)) << 16 : 0 }
    @inline(__always) static func u64(_ b: [UInt8], _ o: Int) -> Int64 { u32(b, o) | u32(b, o + 4) << 32 }

    static func read(_ fh: FileHandle, at offset: UInt64, count: Int) -> [UInt8] {
        do {
            try fh.seek(toOffset: offset)
            return [UInt8](try fh.read(upToCount: count) ?? Data())
        } catch { return [] }
    }

    static func decodeName(_ bytes: [UInt8], utf8Flag: Bool) -> String {
        if utf8Flag || !bytes.contains(where: { $0 >= 0x80 }) { return String(decoding: bytes, as: UTF8.self) }
        let data = Data(bytes)
        if let s = String(data: data, encoding: .utf8) { return s }
        if let s = String(data: data, encoding: QSText.big5) { return s }
        if let s = String(data: data, encoding: QSText.gb18030) { return s }
        let cp437 = String.Encoding(rawValue: CFStringConvertEncodingToNSStringEncoding(CFStringEncoding(CFStringEncodings.dosLatinUS.rawValue)))
        return String(data: data, encoding: cp437) ?? String(decoding: bytes, as: UTF8.self)
    }

    static func dosDate(_ date: Int, _ time: Int) -> Date? {
        var c = DateComponents()
        c.year = 1980 + (date >> 9)
        c.month = (date >> 5) & 0xF
        c.day = date & 0x1F
        c.hour = time >> 11
        c.minute = (time >> 5) & 0x3F
        c.second = (time & 0x1F) * 2
        guard (1...12).contains(c.month ?? 0), (1...31).contains(c.day ?? 0) else { return nil }
        return Calendar.current.date(from: c)
    }

    static func isMacMetadata(_ path: String) -> Bool {
        path.hasPrefix("__MACOSX/") || path == "__MACOSX" || (path as NSString).lastPathComponent.hasPrefix("._") || (path as NSString).lastPathComponent == ".DS_Store"
    }

    // MARK: ZIP

    static func readZip(_ url: URL, fileSize: Int64) -> QSArchiveListing? {
        guard let fh = try? FileHandle(forReadingFrom: url) else { return nil }
        defer { try? fh.close() }
        let tailLen = Int(min(fileSize, 65_557))
        let tailStart = UInt64(fileSize - Int64(tailLen))
        let tail = read(fh, at: tailStart, count: tailLen)
        guard tail.count >= 22 else { return nil }
        var eocd = -1
        var p = tail.count - 22
        while p >= 0 {
            if tail[p] == 0x50 && tail[p + 1] == 0x4B && tail[p + 2] == 0x05 && tail[p + 3] == 0x06 { eocd = p; break }
            p -= 1
        }
        guard eocd >= 0 else { return nil }
        var total = Int64(u16(tail, eocd + 10))
        var cdSize = u32(tail, eocd + 12)
        var cdOffset = u32(tail, eocd + 16)
        let commentLen = u16(tail, eocd + 20)
        var listing = QSArchiveListing(format: "ZIP")
        if commentLen > 0 && eocd + 22 + commentLen <= tail.count {
            listing.comment = decodeName(Array(tail[(eocd + 22)..<(eocd + 22 + commentLen)]), utf8Flag: false)
        }
        // ZIP64
        if total == 0xFFFF || cdSize == 0xFFFF_FFFF || cdOffset == 0xFFFF_FFFF, eocd >= 20,
           tail[eocd - 20] == 0x50, tail[eocd - 19] == 0x4B, tail[eocd - 18] == 0x06, tail[eocd - 17] == 0x07 {
            let e64off = UInt64(u64(tail, eocd - 20 + 8))
            let e64 = read(fh, at: e64off, count: 56)
            if e64.count >= 56, e64[0] == 0x50, e64[1] == 0x4B, e64[2] == 0x06, e64[3] == 0x06 {
                total = u64(e64, 32); cdSize = u64(e64, 40); cdOffset = u64(e64, 48)
            }
        }
        let cd = read(fh, at: UInt64(cdOffset), count: Int(min(cdSize, 64 * 1024 * 1024)))
        var o = 0
        var count: Int64 = 0
        while o + 46 <= cd.count && count < total {
            guard cd[o] == 0x50, cd[o + 1] == 0x4B, cd[o + 2] == 0x01, cd[o + 3] == 0x02 else { break }
            let flags = u16(cd, o + 8)
            let time = u16(cd, o + 12), date = u16(cd, o + 14)
            var csize = u32(cd, o + 20), usize = u32(cd, o + 24)
            let nameLen = u16(cd, o + 28), extraLen = u16(cd, o + 30), cmtLen = u16(cd, o + 32)
            let extAttr = u32(cd, o + 38)
            let nameStart = o + 46
            guard nameStart + nameLen <= cd.count else { break }
            var name = decodeName(Array(cd[nameStart..<(nameStart + nameLen)]), utf8Flag: flags & 0x800 != 0)
            var modified = dosDate(date, time)
            // extra fields
            var e = nameStart + nameLen
            let eEnd = min(e + extraLen, cd.count)
            while e + 4 <= eEnd {
                let id = u16(cd, e), len = u16(cd, e + 2)
                let d = e + 4
                if id == 0x0001 {
                    var q = d
                    if usize == 0xFFFF_FFFF && q + 8 <= d + len { usize = u64(cd, q); q += 8 }
                    if csize == 0xFFFF_FFFF && q + 8 <= d + len { csize = u64(cd, q); q += 8 }
                } else if id == 0x5455 && len >= 5 && cd[d] & 1 == 1 {
                    modified = Date(timeIntervalSince1970: TimeInterval(u32(cd, d + 1)))
                } else if id == 0x7075 && len > 5 && d + len <= cd.count {
                    name = String(decoding: cd[(d + 5)..<(d + len)], as: UTF8.self)
                }
                e = d + len
            }
            o = nameStart + nameLen + extraLen + cmtLen
            count += 1
            if isMacMetadata(name) { listing.hiddenMacMetadata += 1; continue }
            let isDir = name.hasSuffix("/")
            if isDir { name.removeLast() }
            let mode = Int((extAttr >> 16) & 0xFFFF)
            listing.entries.append(QSArchiveEntry(path: name, isDir: isDir, size: isDir ? nil : usize, compressedSize: isDir ? nil : csize,
                                                  modified: modified, encrypted: flags & 1 == 1, isLink: mode & 0xF000 == 0xA000))
            if listing.entries.count >= maxEntries { listing.truncated = true; break }
        }
        return listing
    }

    // MARK: TAR（串流解析，可接在解壓縮後面）

    final class TarParser {
        var entries: [QSArchiveEntry] = []
        var hidden = 0
        var done = false
        var invalid = false
        var truncated = false
        private(set) var skip: Int64 = 0
        private var buf: [UInt8] = []
        private var collect = 0
        private var collectKind: UInt8 = 0
        private var collectPad: Int64 = 0
        private var longName: String?
        private var paxPath: String?
        private var paxSize: Int64?
        private var paxMtime: Double?
        private var zeroBlocks = 0
        private var headers = 0

        func consumeSkip(_ n: Int64) { skip = max(0, skip - n) }

        func feed(_ p: UnsafeBufferPointer<UInt8>) {
            var i = 0
            let n = p.count
            while i < n && !done {
                if skip > 0 {
                    let k = Int(min(skip, Int64(n - i)))
                    skip -= Int64(k); i += k; continue
                }
                if collect > 0 {
                    let k = min(collect - buf.count, n - i)
                    buf.append(contentsOf: UnsafeBufferPointer(rebasing: p[i..<(i + k)])); i += k
                    if buf.count == collect { processMeta(); buf.removeAll(); collect = 0; skip = collectPad }
                    continue
                }
                let k = min(512 - buf.count, n - i)
                buf.append(contentsOf: UnsafeBufferPointer(rebasing: p[i..<(i + k)])); i += k
                if buf.count == 512 { processHeader(buf); buf.removeAll(keepingCapacity: true) }
            }
        }

        private static func str(_ h: [UInt8], _ a: Int, _ len: Int) -> String {
            let slice = h[a..<(a + len)]
            let end = slice.firstIndex(of: 0) ?? (a + len)
            return QSArchive.decodeName(Array(h[a..<end]), utf8Flag: false)
        }

        private static func octal(_ h: [UInt8], _ a: Int, _ len: Int) -> Int64 {
            if h[a] & 0x80 != 0 {   // base-256
                var v: Int64 = 0
                for i in (a + 1)..<(a + len) { v = v << 8 | Int64(h[i]) }
                return v
            }
            var v: Int64 = 0
            for i in a..<(a + len) {
                let c = h[i]
                if c >= 48 && c <= 55 { v = v * 8 + Int64(c - 48) } else if c == 0 || (c == 32 && v > 0) { break }
            }
            return v
        }

        private func processHeader(_ h: [UInt8]) {
            if h.allSatisfy({ $0 == 0 }) {
                zeroBlocks += 1
                if zeroBlocks >= 2 { done = true }
                return
            }
            zeroBlocks = 0
            // 驗證 checksum
            var sum: Int64 = 0
            for (i, c) in h.enumerated() { sum += (i >= 148 && i < 156) ? 32 : Int64(c) }
            if sum != Self.octal(h, 148, 8) {
                if headers == 0 { invalid = true }
                done = true
                return
            }
            headers += 1
            let size = Self.octal(h, 124, 12)
            let type = h[156]
            let pad = (512 - size % 512) % 512
            switch type {
            case 76, 120: // 'L' GNU 長檔名、'x' PAX
                collect = Int(min(size, 1_000_000)); collectKind = type; collectPad = pad + (size - Int64(collect))
                if collect == 0 { skip = pad }
                return
            case 103, 75: // 'g' 全域 PAX、'K' 長連結名
                skip = size + pad
                return
            default: break
            }
            var name = Self.str(h, 0, 100)
            if h[257] == 0x75, h[258] == 0x73, h[259] == 0x74, h[260] == 0x61, h[261] == 0x72 {
                let prefix = Self.str(h, 345, 155)
                if !prefix.isEmpty { name = prefix + "/" + name }
            }
            if let ln = longName { name = ln }
            if let pp = paxPath { name = pp }
            let realSize = paxSize ?? size
            let mtime = paxMtime ?? Double(Self.octal(h, 136, 12))
            longName = nil; paxPath = nil; paxSize = nil; paxMtime = nil
            var isDir = type == 53 || name.hasSuffix("/")
            while name.hasSuffix("/") { name.removeLast() }
            if name.hasPrefix("./") { name.removeFirst(2) }
            if name.isEmpty || name == "." { isDir = true }
            skip = (type == 49 || type == 50 || type == 53) ? 0 : realSize + (512 - realSize % 512) % 512
            if type == 49 || type == 50 { skip = size + pad }
            if name.isEmpty || name == "." { return }
            if QSArchive.isMacMetadata(name) { hidden += 1; return }
            entries.append(QSArchiveEntry(path: name, isDir: isDir, size: isDir ? nil : realSize, compressedSize: nil,
                                          modified: Date(timeIntervalSince1970: mtime), isLink: type == 50))
            if entries.count >= QSArchive.maxEntries { truncated = true; done = true }
        }

        private func processMeta() {
            if collectKind == 76 {
                longName = QSArchive.decodeName(Array(buf.prefix(while: { $0 != 0 })), utf8Flag: false)
                return
            }
            // PAX: "len key=value\n"
            var i = 0
            while i < buf.count {
                var j = i
                while j < buf.count && buf[j] != 32 { j += 1 }
                guard let len = Int(String(decoding: buf[i..<j], as: UTF8.self)), len > 0, i + len <= buf.count else { break }
                let record = String(decoding: buf[(j + 1)..<(i + len - 1)], as: UTF8.self)
                if let eq = record.firstIndex(of: "=") {
                    let key = String(record[..<eq]), value = String(record[record.index(after: eq)...])
                    switch key {
                    case "path": paxPath = value
                    case "size": paxSize = Int64(value)
                    case "mtime": paxMtime = Double(value)
                    default: break
                    }
                }
                i += len
            }
        }
    }

    static func readTar(_ url: URL) -> QSArchiveListing? {
        guard let fh = try? FileHandle(forReadingFrom: url) else { return nil }
        defer { try? fh.close() }
        let parser = TarParser()
        var offset: UInt64 = 0
        while !parser.done {
            guard let chunk = try? fh.read(upToCount: 64 * 1024), !chunk.isEmpty else { break }
            chunk.withUnsafeBytes { parser.feed($0.bindMemory(to: UInt8.self)) }
            offset += UInt64(chunk.count)
            if parser.skip > 0 {
                offset += UInt64(parser.skip)
                parser.consumeSkip(parser.skip)
                try? fh.seek(toOffset: offset)
            }
        }
        if parser.invalid { return nil }
        var l = QSArchiveListing(format: "TAR", entries: parser.entries, truncated: parser.truncated)
        l.hiddenMacMetadata = parser.hidden
        return l
    }

    // MARK: 解壓縮串流（gzip / xz）

    struct GzipInfo {
        var headerLength: Int
        var originalName: String?
        var modified: Date?
        var uncompressedSize: Int64?
    }

    static func gzipInfo(_ url: URL, fileSize: Int64) -> GzipInfo? {
        guard let fh = try? FileHandle(forReadingFrom: url) else { return nil }
        defer { try? fh.close() }
        let h = read(fh, at: 0, count: 4096)
        guard h.count >= 18, h[0] == 0x1F, h[1] == 0x8B, h[2] == 8 else { return nil }
        let flags = h[3]
        let mtime = u32(h, 4)
        var p = 10
        if flags & 4 != 0 { p += 2 + u16(h, p) }
        var name: String?
        if flags & 8 != 0 {
            let s = p
            while p < h.count && h[p] != 0 { p += 1 }
            name = decodeName(Array(h[s..<p]), utf8Flag: false); p += 1
        }
        if flags & 16 != 0 { while p < h.count && h[p] != 0 { p += 1 }; p += 1 }
        if flags & 2 != 0 { p += 2 }
        let trailer = read(fh, at: UInt64(max(0, fileSize - 4)), count: 4)
        return GzipInfo(headerLength: p, originalName: name, modified: mtime > 0 ? Date(timeIntervalSince1970: TimeInterval(mtime)) : nil,
                        uncompressedSize: trailer.count == 4 ? u32(trailer, 0) : nil)
    }

    /// 解壓縮並把輸出一段一段交給 consumer（回傳 false 即停止）。
    @discardableResult
    static func decompress(_ url: URL, algorithm: compression_algorithm, skip: Int, maxOutput: Int64,
                           consumer: (UnsafeBufferPointer<UInt8>) -> Bool) -> Int64 {
        guard let fh = try? FileHandle(forReadingFrom: url) else { return 0 }
        defer { try? fh.close() }
        try? fh.seek(toOffset: UInt64(skip))
        let cap = 256 * 1024
        let src = UnsafeMutablePointer<UInt8>.allocate(capacity: cap)
        let dst = UnsafeMutablePointer<UInt8>.allocate(capacity: cap)
        let stream = UnsafeMutablePointer<compression_stream>.allocate(capacity: 1)
        defer { src.deallocate(); dst.deallocate(); stream.deallocate() }
        guard compression_stream_init(stream, COMPRESSION_STREAM_DECODE, algorithm) == COMPRESSION_STATUS_OK else { return 0 }
        defer { compression_stream_destroy(stream) }
        stream.pointee.src_size = 0
        var flags: Int32 = 0
        var produced: Int64 = 0
        while true {
            if stream.pointee.src_size == 0 && flags == 0 {
                let chunk = (try? fh.read(upToCount: cap)) ?? Data()
                if chunk.isEmpty { flags = Int32(COMPRESSION_STREAM_FINALIZE.rawValue) }
                chunk.copyBytes(to: src, count: chunk.count)
                stream.pointee.src_ptr = UnsafePointer(src)
                stream.pointee.src_size = chunk.count
            }
            stream.pointee.dst_ptr = dst
            stream.pointee.dst_size = cap
            let status = compression_stream_process(stream, flags)
            let n = cap - stream.pointee.dst_size
            if n > 0 {
                produced += Int64(n)
                if !consumer(UnsafeBufferPointer(start: dst, count: n)) { break }
                if produced >= maxOutput { break }
            }
            if status == COMPRESSION_STATUS_END || status == COMPRESSION_STATUS_ERROR { break }
            if flags != 0 && n == 0 { break }
        }
        return produced
    }

    static func decompressHead(_ url: URL, algorithm: compression_algorithm, skip: Int, limit: Int) -> Data {
        var out = Data()
        decompress(url, algorithm: algorithm, skip: skip, maxOutput: Int64(limit)) { p in
            out.append(p); return out.count < limit
        }
        return out.prefix(limit)
    }

    static func readCompressedTar(_ url: URL, algorithm: compression_algorithm, skip: Int, format: String) -> QSArchiveListing? {
        let parser = TarParser()
        decompress(url, algorithm: algorithm, skip: skip, maxOutput: 4 * 1024 * 1024 * 1024) { p in
            parser.feed(p)
            return !parser.done
        }
        if parser.invalid || (parser.entries.isEmpty && parser.hidden == 0) { return nil }
        var l = QSArchiveListing(format: format, entries: parser.entries, truncated: parser.truncated)
        l.hiddenMacMetadata = parser.hidden
        return l
    }

    static func looksLikeTar(_ d: Data) -> Bool {
        d.count >= 262 && d[257] == 0x75 && d[258] == 0x73 && d[259] == 0x74 && d[260] == 0x61 && d[261] == 0x72
    }
}

/// 壓縮檔清單 → 樹狀檢視。
enum QSArchiveView {
    final class Node {
        var name: String
        var isDir: Bool
        var entry: QSArchiveEntry?
        var children: [String: Node] = [:]
        var size: Int64 = 0
        var compressed: Int64 = 0
        var hasCompressed = false
        init(name: String, isDir: Bool) { self.name = name; self.isDir = isDir }
    }

    static func render(_ listing: QSArchiveListing, file: QSFile, extraChips: [QSChip] = []) -> QSPage {
        let root = Node(name: "", isDir: true)
        var folders = 0, files = 0
        var totalSize: Int64 = 0, totalCompressed: Int64 = 0
        var encrypted = false
        for e in listing.entries {
            let parts = e.path.split(separator: "/").map(String.init)
            guard !parts.isEmpty else { continue }
            var node = root
            for (i, part) in parts.enumerated() {
                let last = i == parts.count - 1
                if let child = node.children[part] {
                    node = child
                } else {
                    let child = Node(name: part, isDir: !last || e.isDir)
                    if child.isDir { folders += 1 }
                    node.children[part] = child
                    node = child
                }
                if last {
                    node.entry = e
                    if !e.isDir { files += 1 }
                }
            }
            if !e.isDir {
                totalSize += e.size ?? 0
                totalCompressed += e.compressedSize ?? 0
            }
            encrypted = encrypted || e.encrypted
        }
        func aggregate(_ n: Node) {
            if !n.isDir {
                n.size = n.entry?.size ?? 0
                n.compressed = n.entry?.compressedSize ?? 0
                n.hasCompressed = n.entry?.compressedSize != nil
                return
            }
            for c in n.children.values {
                aggregate(c)
                n.size += c.size; n.compressed += c.compressed
                n.hasCompressed = n.hasCompressed || c.hasCompressed
            }
        }
        aggregate(root)

        var atts: [String: QSAttachment] = [:]
        var iconFor: [String: String] = [:]
        func icon(_ n: Node) -> String {
            let ext = n.isDir ? "/folder" : (n.name as NSString).pathExtension.lowercased()
            if let k = iconFor[ext] { return k }
            let key = "ai-\(iconFor.count)"
            let type: UTType = n.isDir ? .folder : (ext.isEmpty ? .data : (UTType(filenameExtension: ext) ?? .data))
            if iconFor.count < 80, let png = QSIcons.png(forType: type, size: 36) {
                atts[key] = QSAttachment(data: png, contentType: .png)
                iconFor[ext] = key
                return key
            }
            return iconFor["/fallback"] ?? {
                let k = "ai-fallback"
                if let png = QSIcons.png(forType: .data, size: 36) { atts[k] = QSAttachment(data: png, contentType: .png) }
                iconFor["/fallback"] = k
                return k
            }()
        }

        let openAll = listing.entries.count <= 400
        var html = "<div class=\"arc\"><div class=\"hdr\"><span>名稱</span><span class=\"num\">原始大小</span><span class=\"num\">壓縮後</span><span>修改日期</span></div>"
        func sorted(_ n: Node) -> [Node] {
            n.children.values.sorted {
                if $0.isDir != $1.isDir { return $0.isDir }
                return $0.name.localizedStandardCompare($1.name) == .orderedAscending
            }
        }
        func cells(_ n: Node, depth: Int, twisty: Bool) -> String {
            var s = "<span class=\"nm\" style=\"padding-left:\(12 + depth * 18)px\"><span class=\"tw\"></span>"
            s += "<img src=\"cid:\(icon(n))\" alt=\"\">\(QSHTML.escape(n.name))"
            if n.entry?.encrypted == true { s += " <span class=\"enc\">🔒</span>" }
            if n.entry?.isLink == true { s += " <span class=\"enc\">↪︎</span>" }
            s += "</span>"
            s += "<span class=\"num\">\(n.isDir && n.size == 0 ? "—" : QSFormat.bytes(n.size))</span>"
            s += "<span class=\"num\">\(n.hasCompressed ? QSFormat.bytes(n.compressed) : "—")</span>"
            s += "<span class=\"dt\">\(QSFormat.date(n.entry?.modified))</span>"
            return s
        }
        func emit(_ n: Node, depth: Int) {
            for c in sorted(n) {
                if c.isDir {
                    let isOpen = openAll ? depth < 3 : depth == 0
                    html += "<details\(isOpen ? " open" : "")><summary class=\"row\">\(cells(c, depth: depth, twisty: true))</summary>"
                    emit(c, depth: depth + 1)
                    html += "</details>"
                } else {
                    html += "<div class=\"row\">\(cells(c, depth: depth, twisty: false))</div>"
                }
            }
        }
        emit(root, depth: 0)
        html += "</div>"

        var page = QSPage(kind: "\(listing.format) 壓縮檔")
        page.size = CGSize(width: 860, height: 620)
        page.chips = [QSChip("\(QSFormat.number(folders)) 個資料夾、\(QSFormat.number(files)) 個檔案"), QSChip("原始 \(QSFormat.bytes(totalSize))")]
        if totalCompressed > 0 && totalSize > 0 {
            let ratio = Int((1 - Double(totalCompressed) / Double(totalSize)) * 100)
            page.chips.append(QSChip("壓縮率 \(max(0, ratio))%"))
        }
        page.chips += extraChips
        if encrypted { page.chips.append(QSChip("含加密檔案", .warn)) }
        if listing.truncated { page.notices.append(QSNotice("項目太多，只列出前 \(QSFormat.number(QSArchive.maxEntries)) 個", warn: true)) }
        if listing.hiddenMacMetadata > 0 { page.chips.append(QSChip("已隱藏 \(listing.hiddenMacMetadata) 個 macOS 中繼資料")) }
        if let c = listing.comment, !c.isEmpty { page.notices.append(QSNotice("註解：\(c)")) }
        if listing.entries.isEmpty { html = "<div class=\"empty\">壓縮檔是空的</div>" }
        page.body = html
        page.attachments = atts
        return page
    }
}
