import Foundation

/// 保留鍵順序的 JSON 值（Foundation 的 JSONSerialization 會打亂順序）。
indirect enum CVJSONValue {
    case null
    case bool(Bool)
    /// 保留原始數字字串，避免精度損失。
    case number(String)
    case string(String)
    case array([CVJSONValue])
    case object([(String, CVJSONValue)])

    var isScalar: Bool {
        switch self {
        case .array, .object: return false
        default: return true
        }
    }

    /// 給表格儲存格用的文字。
    var cellText: String {
        switch self {
        case .null: return ""
        case .bool(let b): return b ? "true" : "false"
        case .number(let n): return n
        case .string(let s): return s
        case .array, .object: return CVJSONWriter.write(self, pretty: false)
        }
    }
}

// MARK: - JSON 解析

/// 寬鬆的 JSON 解析器：支援 // 與 /* */ 註解、結尾逗號（JSONC）。
struct CVJSONParser {
    private let bytes: [UInt8]
    private var pos = 0
    private var line = 1

    static func parse(_ text: String) throws -> CVJSONValue {
        var parser = CVJSONParser(bytes: Array(text.utf8))
        if parser.bytes.starts(with: [0xEF, 0xBB, 0xBF]) { parser.pos = 3 }
        let value = try parser.parseValue(depth: 0)
        parser.skipWhitespace()
        if parser.pos < parser.bytes.count {
            throw ConversionError.invalidData("JSON 第 \(parser.line) 行有多餘的內容。")
        }
        return value
    }

    private init(bytes: [UInt8]) { self.bytes = bytes }

    private func error(_ message: String) -> ConversionError {
        ConversionError.invalidData("JSON 第 \(line) 行：\(message)")
    }

    private mutating func skipWhitespace() {
        while pos < bytes.count {
            let b = bytes[pos]
            if b == 0x0A { line += 1; pos += 1 }
            else if b == 0x20 || b == 0x09 || b == 0x0D { pos += 1 }
            else if b == UInt8(ascii: "/"), pos + 1 < bytes.count, bytes[pos + 1] == UInt8(ascii: "/") {
                while pos < bytes.count, bytes[pos] != 0x0A { pos += 1 }
            } else if b == UInt8(ascii: "/"), pos + 1 < bytes.count, bytes[pos + 1] == UInt8(ascii: "*") {
                pos += 2
                while pos + 1 < bytes.count, !(bytes[pos] == UInt8(ascii: "*") && bytes[pos + 1] == UInt8(ascii: "/")) {
                    if bytes[pos] == 0x0A { line += 1 }
                    pos += 1
                }
                pos += 2
            } else { break }
        }
    }

    private mutating func parseValue(depth: Int) throws -> CVJSONValue {
        guard depth < 512 else { throw error("巢狀層數太深。") }
        skipWhitespace()
        guard pos < bytes.count else { throw error("內容意外結束。") }
        switch bytes[pos] {
        case UInt8(ascii: "{"):
            pos += 1
            var members: [(String, CVJSONValue)] = []
            skipWhitespace()
            if pos < bytes.count, bytes[pos] == UInt8(ascii: "}") { pos += 1; return .object(members) }
            while true {
                skipWhitespace()
                if pos < bytes.count, bytes[pos] == UInt8(ascii: "}") { pos += 1; return .object(members) } // 結尾逗號
                guard pos < bytes.count, bytes[pos] == UInt8(ascii: "\"") else { throw error("物件的鍵必須是字串。") }
                let key = try parseString()
                skipWhitespace()
                guard pos < bytes.count, bytes[pos] == UInt8(ascii: ":") else { throw error("缺少冒號。") }
                pos += 1
                members.append((key, try parseValue(depth: depth + 1)))
                skipWhitespace()
                guard pos < bytes.count else { throw error("物件沒有結束。") }
                if bytes[pos] == UInt8(ascii: ",") { pos += 1; continue }
                if bytes[pos] == UInt8(ascii: "}") { pos += 1; return .object(members) }
                throw error("物件中缺少逗號。")
            }
        case UInt8(ascii: "["):
            pos += 1
            var items: [CVJSONValue] = []
            skipWhitespace()
            if pos < bytes.count, bytes[pos] == UInt8(ascii: "]") { pos += 1; return .array(items) }
            while true {
                skipWhitespace()
                if pos < bytes.count, bytes[pos] == UInt8(ascii: "]") { pos += 1; return .array(items) }
                items.append(try parseValue(depth: depth + 1))
                skipWhitespace()
                guard pos < bytes.count else { throw error("陣列沒有結束。") }
                if bytes[pos] == UInt8(ascii: ",") { pos += 1; continue }
                if bytes[pos] == UInt8(ascii: "]") { pos += 1; return .array(items) }
                throw error("陣列中缺少逗號。")
            }
        case UInt8(ascii: "\""):
            return .string(try parseString())
        case UInt8(ascii: "t"):
            try expect("true"); return .bool(true)
        case UInt8(ascii: "f"):
            try expect("false"); return .bool(false)
        case UInt8(ascii: "n"):
            try expect("null"); return .null
        default:
            return .number(try parseNumber())
        }
    }

    private mutating func expect(_ word: String) throws {
        let w = Array(word.utf8)
        guard pos + w.count <= bytes.count, Array(bytes[pos..<(pos + w.count)]) == w else { throw error("無法辨識的值。") }
        pos += w.count
    }

    private mutating func parseNumber() throws -> String {
        let start = pos
        if pos < bytes.count, bytes[pos] == UInt8(ascii: "-") || bytes[pos] == UInt8(ascii: "+") { pos += 1 }
        while pos < bytes.count {
            let b = bytes[pos]
            if (b >= 0x30 && b <= 0x39) || b == UInt8(ascii: ".") || b == UInt8(ascii: "e") || b == UInt8(ascii: "E")
                || b == UInt8(ascii: "-") || b == UInt8(ascii: "+") { pos += 1 } else { break }
        }
        let s = String(decoding: bytes[start..<pos], as: UTF8.self)
        guard !s.isEmpty, Double(s) != nil else { throw error("無法辨識的值。") }
        return s.hasPrefix("+") ? String(s.dropFirst()) : s
    }

    private mutating func parseString() throws -> String {
        pos += 1 // 開頭的引號
        var out = [UInt8]()
        while pos < bytes.count {
            let b = bytes[pos]
            if b == UInt8(ascii: "\"") { pos += 1; return String(decoding: out, as: UTF8.self) }
            if b == UInt8(ascii: "\\") {
                pos += 1
                guard pos < bytes.count else { break }
                let e = bytes[pos]
                pos += 1
                switch e {
                case UInt8(ascii: "\""): out.append(0x22)
                case UInt8(ascii: "\\"): out.append(0x5C)
                case UInt8(ascii: "/"): out.append(0x2F)
                case UInt8(ascii: "b"): out.append(0x08)
                case UInt8(ascii: "f"): out.append(0x0C)
                case UInt8(ascii: "n"): out.append(0x0A)
                case UInt8(ascii: "r"): out.append(0x0D)
                case UInt8(ascii: "t"): out.append(0x09)
                case UInt8(ascii: "u"):
                    var code = try hex4()
                    if (0xD800...0xDBFF).contains(code), pos + 1 < bytes.count, bytes[pos] == UInt8(ascii: "\\"), bytes[pos + 1] == UInt8(ascii: "u") {
                        pos += 2
                        let low = try hex4()
                        if (0xDC00...0xDFFF).contains(low) { code = 0x10000 + ((code - 0xD800) << 10) + (low - 0xDC00) }
                    }
                    let scalar = Unicode.Scalar(code) ?? "\u{FFFD}"
                    out.append(contentsOf: Array(String(Character(scalar)).utf8))
                default: throw error("不正確的跳脫字元。")
                }
                continue
            }
            if b == 0x0A { line += 1 }
            out.append(b)
            pos += 1
        }
        throw error("字串沒有結束。")
    }

    private mutating func hex4() throws -> UInt32 {
        guard pos + 4 <= bytes.count, let v = UInt32(String(decoding: bytes[pos..<(pos + 4)], as: UTF8.self), radix: 16) else {
            throw error("不正確的 \\u 跳脫。")
        }
        pos += 4
        return v
    }
}

// MARK: - JSON 輸出

enum CVJSONWriter {
    static func write(_ value: CVJSONValue, pretty: Bool, indentUnit: String = "  ") -> String {
        var out = ""
        emit(value, pretty: pretty, indent: "", unit: indentUnit, into: &out)
        return out
    }

    static func quote(_ s: String) -> String {
        var out = "\""
        for scalar in s.unicodeScalars {
            switch scalar {
            case "\"": out += "\\\""
            case "\\": out += "\\\\"
            case "\n": out += "\\n"
            case "\r": out += "\\r"
            case "\t": out += "\\t"
            case "\u{08}": out += "\\b"
            case "\u{0C}": out += "\\f"
            default:
                if scalar.value < 0x20 || scalar.value == 0x2028 || scalar.value == 0x2029 {
                    out += String(format: "\\u%04x", scalar.value)
                } else {
                    out.unicodeScalars.append(scalar)
                }
            }
        }
        return out + "\""
    }

    private static func emit(_ value: CVJSONValue, pretty: Bool, indent: String, unit: String, into out: inout String) {
        switch value {
        case .null: out += "null"
        case .bool(let b): out += b ? "true" : "false"
        case .number(let n): out += n
        case .string(let s): out += quote(s)
        case .array(let items):
            if items.isEmpty { out += "[]"; return }
            out += "["
            let inner = indent + unit
            for (i, item) in items.enumerated() {
                if i > 0 { out += "," }
                if pretty { out += "\n" + inner }
                emit(item, pretty: pretty, indent: inner, unit: unit, into: &out)
            }
            if pretty { out += "\n" + indent }
            out += "]"
        case .object(let members):
            if members.isEmpty { out += "{}"; return }
            out += "{"
            let inner = indent + unit
            for (i, (k, v)) in members.enumerated() {
                if i > 0 { out += "," }
                if pretty { out += "\n" + inner }
                out += quote(k) + (pretty ? ": " : ":")
                emit(v, pretty: pretty, indent: inner, unit: unit, into: &out)
            }
            if pretty { out += "\n" + indent }
            out += "}"
        }
    }
}

// MARK: - YAML 輸出

enum CVYAMLWriter {
    static func write(_ value: CVJSONValue) -> String {
        var lines: [String] = []
        switch value {
        case .object(let m) where !m.isEmpty: emitObject(m, indent: 0, into: &lines)
        case .array(let a) where !a.isEmpty: emitArray(a, indent: 0, into: &lines)
        default: lines.append(scalar(value))
        }
        return lines.joined(separator: "\n") + "\n"
    }

    private static func pad(_ n: Int) -> String { String(repeating: " ", count: n) }

    private static func emitObject(_ members: [(String, CVJSONValue)], indent: Int, into lines: inout [String]) {
        for (k, v) in members {
            let key = quoteIfNeeded(k)
            switch v {
            case .object(let m) where !m.isEmpty:
                lines.append(pad(indent) + key + ":")
                emitObject(m, indent: indent + 2, into: &lines)
            case .array(let a) where !a.isEmpty:
                lines.append(pad(indent) + key + ":")
                emitArray(a, indent: indent + 2, into: &lines)
            default:
                lines.append(pad(indent) + key + ": " + scalar(v))
            }
        }
    }

    private static func emitArray(_ items: [CVJSONValue], indent: Int, into lines: inout [String]) {
        for item in items {
            switch item {
            case .object(let m) where !m.isEmpty:
                var sub: [String] = []
                emitObject(m, indent: indent + 2, into: &sub)
                if let first = sub.first {
                    lines.append(pad(indent) + "- " + first.dropFirst(indent + 2))
                    lines.append(contentsOf: sub.dropFirst())
                }
            case .array(let a) where !a.isEmpty:
                var sub: [String] = []
                emitArray(a, indent: indent + 2, into: &sub)
                if let first = sub.first {
                    lines.append(pad(indent) + "- " + first.dropFirst(indent + 2))
                    lines.append(contentsOf: sub.dropFirst())
                }
            default:
                lines.append(pad(indent) + "- " + scalar(item))
            }
        }
    }

    private static func scalar(_ v: CVJSONValue) -> String {
        switch v {
        case .null: return "null"
        case .bool(let b): return b ? "true" : "false"
        case .number(let n): return n
        case .string(let s): return quoteIfNeeded(s)
        case .array: return "[]"
        case .object: return "{}"
        }
    }

    private static let reserved: Set<String> = ["true", "false", "yes", "no", "on", "off", "null", "~", "y", "n", ".nan", ".inf", "-.inf"]

    static func quoteIfNeeded(_ s: String) -> String {
        if s.isEmpty { return "\"\"" }
        let needs: Bool = {
            if reserved.contains(s.lowercased()) { return true }
            if Double(s) != nil || s.hasPrefix("0x") || s.hasPrefix("0o") { return true }
            if s.first == " " || s.last == " " { return true }
            if let f = s.first, "-?:,[]{}#&*!|>'\"%@`".contains(f) { return true }
            if s.contains(": ") || s.contains(" #") || s.hasSuffix(":") { return true }
            for scalar in s.unicodeScalars where scalar.value < 0x20 || scalar == "\u{7F}" || scalar == "\u{2028}" || scalar == "\u{2029}" { return true }
            if s.range(of: #"^\d{4}-\d{2}-\d{2}"#, options: .regularExpression) != nil { return true }
            return false
        }()
        return needs ? CVJSONWriter.quote(s) : s
    }
}

// MARK: - Plist

enum CVPlistBridge {
    /// XML plist（保留鍵順序）。null 值會被略過，因為 plist 不支援 null。
    static func xmlPlist(_ value: CVJSONValue) -> String {
        var out = """
        <?xml version="1.0" encoding="UTF-8"?>
        <!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
        <plist version="1.0">

        """
        emit(value, indent: "", into: &out)
        out += "</plist>\n"
        return out
    }

    private static func emit(_ value: CVJSONValue, indent: String, into out: inout String) {
        switch value {
        case .null: out += indent + "<string></string>\n"
        case .bool(let b): out += indent + (b ? "<true/>" : "<false/>") + "\n"
        case .number(let n):
            if !n.contains("."), !n.lowercased().contains("e"), Int64(n) != nil {
                out += indent + "<integer>\(n)</integer>\n"
            } else {
                out += indent + "<real>\(n)</real>\n"
            }
        case .string(let s): out += indent + "<string>\(CVXML.escape(s))</string>\n"
        case .array(let items):
            if items.isEmpty { out += indent + "<array/>\n"; return }
            out += indent + "<array>\n"
            for item in items { emit(item, indent: indent + "\t", into: &out) }
            out += indent + "</array>\n"
        case .object(let members):
            let kept = members.filter { if case .null = $0.1 { return false }; return true }
            if kept.isEmpty { out += indent + "<dict/>\n"; return }
            out += indent + "<dict>\n"
            for (k, v) in kept {
                out += indent + "\t<key>\(CVXML.escape(k))</key>\n"
                emit(v, indent: indent + "\t", into: &out)
            }
            out += indent + "</dict>\n"
        }
    }

    /// 轉成 Foundation 物件（寫二進位 plist 用）。
    static func foundationObject(_ value: CVJSONValue) -> Any {
        switch value {
        case .null: return ""
        case .bool(let b): return b
        case .number(let n):
            if !n.contains("."), !n.lowercased().contains("e"), let i = Int64(n) { return NSNumber(value: i) }
            return NSNumber(value: Double(n) ?? 0)
        case .string(let s): return s
        case .array(let a): return a.map(foundationObject)
        case .object(let m):
            var d: [String: Any] = [:]
            for (k, v) in m { if case .null = v { continue }; d[k] = foundationObject(v) }
            return d
        }
    }

    /// 讀取 plist。XML plist 以自己的解析器保留鍵順序；二進位 plist 改用系統 API（鍵依字母排序）。
    static func read(_ data: Data) throws -> CVJSONValue {
        let head = data.prefix(64)
        let isXML = String(decoding: head, as: UTF8.self).contains("<?xml") || String(decoding: head, as: UTF8.self).contains("<plist")
        if isXML, let root = CVXML.parse(data), let plist = root.firstChild(named: "plist") ?? (root.name == "plist" ? root : nil),
           let first = plist.children.first {
            return try fromXML(first)
        }
        let obj: Any
        do {
            obj = try PropertyListSerialization.propertyList(from: data, options: [], format: nil)
        } catch {
            throw ConversionError.invalidData("不是正確的 plist 檔案。")
        }
        return fromFoundation(obj)
    }

    private static func fromXML(_ node: CVXMLNode) throws -> CVJSONValue {
        switch node.name {
        case "dict":
            var members: [(String, CVJSONValue)] = []
            var pendingKey: String?
            for child in node.children {
                if child.name == "key" { pendingKey = child.text; continue }
                if let key = pendingKey { members.append((key, try fromXML(child))); pendingKey = nil }
            }
            return .object(members)
        case "array": return .array(try node.children.map(fromXML))
        case "string": return .string(node.text)
        case "integer": return .number(node.text.trimmingCharacters(in: .whitespacesAndNewlines))
        case "real":
            let t = node.text.trimmingCharacters(in: .whitespacesAndNewlines)
            return Double(t).map { $0.isFinite ? .number(t) : .string(t) } ?? .string(t)
        case "true": return .bool(true)
        case "false": return .bool(false)
        case "date": return .string(node.text.trimmingCharacters(in: .whitespacesAndNewlines))
        case "data": return .string(node.text.components(separatedBy: .whitespacesAndNewlines).joined())
        default: throw ConversionError.invalidData("plist 中有無法辨識的元素 <\(node.name)>。")
        }
    }

    static func fromFoundation(_ obj: Any) -> CVJSONValue {
        switch obj {
        case let s as String: return .string(s)
        case let n as NSNumber:
            if CFGetTypeID(n) == CFBooleanGetTypeID() { return .bool(n.boolValue) }
            if CFNumberIsFloatType(n) {
                let d = n.doubleValue
                return d.isFinite ? .number(d == d.rounded() && abs(d) < 1e15 ? String(Int64(d)) + ".0" : String(d)) : .string(String(d))
            }
            return .number(n.stringValue)
        case let d as Date:
            return .string(ISO8601DateFormatter().string(from: d))
        case let data as Data:
            return .string(data.base64EncodedString())
        case let a as [Any]:
            return .array(a.map(fromFoundation))
        case let d as [String: Any]:
            return .object(d.keys.sorted().map { ($0, fromFoundation(d[$0]!)) })
        default:
            return .string(String(describing: obj))
        }
    }
}

// MARK: - 迷你 XML DOM

final class CVXMLNode {
    let name: String
    let attributes: [String: String]
    var children: [CVXMLNode] = []
    var text = ""
    weak var parent: CVXMLNode?

    init(name: String, attributes: [String: String]) {
        self.name = name
        self.attributes = attributes
    }

    func firstChild(named n: String) -> CVXMLNode? { children.first { $0.localName == n } }
    func children(named n: String) -> [CVXMLNode] { children.filter { $0.localName == n } }

    /// 去掉命名空間前綴的名稱。
    var localName: String {
        if let idx = name.lastIndex(of: ":") { return String(name[name.index(after: idx)...]) }
        return name
    }

    func attribute(_ local: String) -> String? {
        if let v = attributes[local] { return v }
        return attributes.first { $0.key.hasSuffix(":" + local) }?.value
    }

    /// 遞迴尋找所有指定名稱的子孫。
    func descendants(named n: String, into out: inout [CVXMLNode]) {
        for c in children {
            if c.localName == n { out.append(c) }
            c.descendants(named: n, into: &out)
        }
    }
}

enum CVXML {
    static func parse(_ data: Data) -> CVXMLNode? {
        let parser = XMLParser(data: data)
        let delegate = CVXMLBuilder()
        parser.delegate = delegate
        parser.shouldProcessNamespaces = false
        parser.shouldResolveExternalEntities = false
        guard parser.parse() else { return nil }
        return delegate.root
    }

    static func escape(_ s: String) -> String {
        var out = ""
        out.reserveCapacity(s.count)
        for scalar in s.unicodeScalars {
            switch scalar {
            case "&": out += "&amp;"
            case "<": out += "&lt;"
            case ">": out += "&gt;"
            case "\"": out += "&quot;"
            default:
                // XML 1.0 不允許的控制字元直接略過
                if scalar.value < 0x20 && scalar != "\t" && scalar != "\n" && scalar != "\r" { continue }
                if scalar.value == 0xFFFE || scalar.value == 0xFFFF { continue }
                out.unicodeScalars.append(scalar)
            }
        }
        return out
    }
}

private final class CVXMLBuilder: NSObject, XMLParserDelegate {
    let root = CVXMLNode(name: "#document", attributes: [:])
    private var current: CVXMLNode?

    override init() {
        super.init()
        current = root
    }

    func parser(_ parser: XMLParser, didStartElement elementName: String, namespaceURI: String?, qualifiedName qName: String?,
                attributes attributeDict: [String: String] = [:]) {
        let node = CVXMLNode(name: elementName, attributes: attributeDict)
        node.parent = current
        current?.children.append(node)
        current = node
    }

    func parser(_ parser: XMLParser, didEndElement elementName: String, namespaceURI: String?, qualifiedName qName: String?) {
        current = current?.parent ?? root
    }

    func parser(_ parser: XMLParser, foundCharacters string: String) {
        current?.text += string
    }

    func parser(_ parser: XMLParser, foundCDATA CDATABlock: Data) {
        current?.text += String(decoding: CDATABlock, as: UTF8.self)
    }
}
