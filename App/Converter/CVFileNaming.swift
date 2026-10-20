import Foundation

/// 輸出檔命名與暫存資料夾管理。所有輸出都先寫到暫存資料夾，最後再「搬」到目的地，
/// 搬移時若同名就改用「名稱 (1).副檔名」，因此絕不覆蓋既有檔案。
/// 產生 PDF 後的共用後處理（例如修正中文字的 ToUnicode 對應）。由主程式在啟動時設定。
enum CVPDFPostProcess {
    static var handler: ((URL) -> Void)?

    static func apply(to url: URL) {
        guard let handler, url.pathExtension.lowercased() == "pdf", !CVFileNaming.isDirectory(url) else { return }
        handler(url)
    }
}

enum CVFileNaming {
    /// 多段副檔名（.tar.gz 等）視為一個副檔名。
    static let compoundExtensions = ["tar.gz", "tar.bz2", "tar.xz", "tar.zst", "tar.lz"]

    /// 去掉副檔名後的檔名（支援 .tar.gz）。
    static func baseName(of url: URL) -> String {
        let name = url.lastPathComponent
        let lower = name.lowercased()
        for ext in compoundExtensions where lower.hasSuffix("." + ext) {
            return String(name.dropLast(ext.count + 1))
        }
        let base = url.deletingPathExtension().lastPathComponent
        return base.isEmpty ? name : base
    }

    /// 拆出檔名與副檔名（副檔名可能是 tar.gz 或空字串）。
    static func split(_ fileName: String) -> (base: String, ext: String) {
        let lower = fileName.lowercased()
        for ext in compoundExtensions where lower.hasSuffix("." + ext) && fileName.count > ext.count + 1 {
            return (String(fileName.dropLast(ext.count + 1)), String(fileName.suffix(ext.count)))
        }
        let ns = fileName as NSString
        let ext = ns.pathExtension
        if ext.isEmpty || ns.deletingPathExtension.isEmpty { return (fileName, "") }
        return (ns.deletingPathExtension, ext)
    }

    /// 組合檔名，避免空副檔名時多出句點。
    static func fileName(_ base: String, _ ext: String) -> String {
        ext.isEmpty ? base : "\(base).\(ext)"
    }

    /// 檔名中不能出現的字元換成安全字元。
    static func sanitize(_ name: String) -> String {
        var s = name.replacingOccurrences(of: "/", with: "-").replacingOccurrences(of: ":", with: "-")
        s = s.trimmingCharacters(in: .whitespacesAndNewlines)
        if s.hasPrefix(".") { s = "_" + s.dropFirst() }
        return s.isEmpty ? "未命名" : s
    }

    /// 回傳目的資料夾中尚未存在的路徑：name.ext、name (1).ext、name (2).ext…
    static func uniqueURL(in directory: URL, fileName: String) -> URL {
        let fm = FileManager.default
        let (base, ext) = split(fileName)
        var candidate = directory.appendingPathComponent(fileName)
        var n = 1
        while fm.fileExists(atPath: candidate.path) || isBrokenSymlink(candidate) {
            candidate = directory.appendingPathComponent(self.fileName("\(base) (\(n))", ext))
            n += 1
        }
        return candidate
    }

    private static func isBrokenSymlink(_ url: URL) -> Bool {
        (try? FileManager.default.destinationOfSymbolicLink(atPath: url.path)) != nil
    }

    /// 將暫存輸出搬到目的資料夾，絕不覆蓋。
    /// 使用 renamex_np(RENAME_EXCL) 原子性搬移：若同時有其他轉換搶到同一個名稱，就換下一個編號。
    /// （FileManager.moveItem 在同一磁碟上會直接取代既有檔案，不能用。）
    static func commit(_ item: URL, into directory: URL) throws -> URL {
        let fm = FileManager.default
        for _ in 0..<1000 {
            let dest = uniqueURL(in: directory, fileName: item.lastPathComponent)
            let result = item.withUnsafeFileSystemRepresentation { src in
                dest.withUnsafeFileSystemRepresentation { dst in
                    renamex_np(src, dst, UInt32(RENAME_EXCL))
                }
            }
            if result == 0 { return dest }
            let code = errno
            if code == EEXIST || code == ENOTEMPTY { continue }
            if code == EXDEV {
                // 暫存區在不同磁碟：改用複製（copyItem 遇到同名會失敗，不會覆蓋）
                do {
                    try fm.copyItem(at: item, to: dest)
                    try? fm.removeItem(at: item)
                    return dest
                } catch let error as CocoaError where error.code == .fileWriteFileExists {
                    continue
                } catch {
                    throw ConversionError.writeFailed(error.localizedDescription)
                }
            }
            throw ConversionError.writeFailed(String(cString: strerror(code)))
        }
        throw ConversionError.writeFailed("找不到可用的檔名。")
    }

    /// 在與目的地同一個磁碟上建立暫存資料夾（搬移才會是瞬間完成）。
    static func makeWorkDirectory(near directory: URL) throws -> URL {
        let fm = FileManager.default
        if let url = try? fm.url(for: .itemReplacementDirectory, in: .userDomainMask, appropriateFor: directory, create: true) {
            return url
        }
        let url = fm.temporaryDirectory.appendingPathComponent("QuickSeemdConvert-\(UUID().uuidString)", isDirectory: true)
        try fm.createDirectory(at: url, withIntermediateDirectories: true)
        return url
    }

    /// 單純的暫存資料夾（不需搬移的中間產物）。
    static func makeScratchDirectory() throws -> URL {
        let url = FileManager.default.temporaryDirectory
            .appendingPathComponent("QuickSeemdConvert-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: url, withIntermediateDirectories: true)
        return url
    }

    static func isDirectory(_ url: URL) -> Bool {
        var isDir: ObjCBool = false
        return FileManager.default.fileExists(atPath: url.path, isDirectory: &isDir) && isDir.boolValue
    }

    /// 是否為「套件」（.rtfd、.app…）——Finder 當成單一檔案。
    static func isPackage(_ url: URL) -> Bool {
        (try? url.resourceValues(forKeys: [.isPackageKey]))?.isPackage ?? false
    }
}
