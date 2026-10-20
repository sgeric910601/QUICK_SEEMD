import Foundation

/// 文字編碼與換行轉換。
enum CVTextConverter {
    static func convert(_ input: URL, to format: ConversionFormat, options: ConversionOptions, workDir: URL) throws -> [URL] {
        let decoded = try CVTextCodec.decode(input, forced: options.sourceEncoding.encoding)
        var text = decoded.text
        var encoding = decoded.encoding
        var bom = decoded.hadBOM
        switch format.id {
        case "utf8": encoding = .utf8; bom = false
        case "utf8-bom": encoding = .utf8; bom = true
        case "utf16": encoding = .utf16LittleEndian; bom = true
        case "big5": encoding = CVTextCodec.big5CP950; bom = false
        case "lf": text = CVTextCodec.normalizeLineEndings(text, to: .lf)
        case "crlf": text = CVTextCodec.normalizeLineEndings(text, to: .crlf)
        default: throw ConversionError.unsupportedTarget(source: input.lastPathComponent, target: format.displayName)
        }
        if encoding == .utf16 || encoding == .utf16BigEndian || encoding == .utf32 { encoding = .utf16LittleEndian; bom = true }
        let data = try CVTextCodec.encode(text, as: encoding, bom: bom)
        let out = workDir.appendingPathComponent(input.lastPathComponent)
        try data.write(to: out)
        return [out]
    }
}

/// 壓縮、解壓縮與磁碟映像：ditto、bsdtar、gzip、bzip2、hdiutil（皆為 macOS 內建）。
enum CVArchiveConverter {
    static let ditto = "/usr/bin/ditto"
    static let tar = "/usr/bin/tar"
    static let hdiutil = "/usr/bin/hdiutil"
    static let noAppleDouble = ["COPYFILE_DISABLE": "1"]

    static func convert(_ input: URL, profile: CVInputProfile, to format: ConversionFormat, workDir: URL,
                        progress: @escaping @Sendable (Double) -> Void) async throws -> [URL] {
        let base = CVFileNaming.baseName(of: input)
        if format.id == "extract" {
            return [try await extract(input, profile: profile, workDir: workDir)]
        }
        // 壓縮檔 → 另一種壓縮檔：先解開再重新打包
        var source = input
        var scratch: URL?
        defer { if let scratch { try? FileManager.default.removeItem(at: scratch) } }
        if profile.archiveKind != nil {
            let tmp = try CVFileNaming.makeScratchDirectory()
            scratch = tmp
            source = try await extract(input, profile: profile, workDir: tmp)
            progress(0.4)
        }
        let out = workDir.appendingPathComponent(CVFileNaming.fileName(base, format.fileExtension))
        let parent = source.deletingLastPathComponent()
        let name = source.lastPathComponent
        switch format.id {
        case "zip":
            // 不帶 __MACOSX／._ 檔，Windows 解壓也乾淨
            try await CVProcess.runChecked(ditto, ["-c", "-k", "--norsrc", "--noextattr", "--noqtn", "--noacl", "--keepParent", source.path, out.path],
                                           toolName: "ditto")
        case "tgz":
            try await CVProcess.runChecked(tar, ["-czf", out.path, "-C", parent.path, name], toolName: "tar", environment: noAppleDouble)
        case "7z":
            try await CVProcess.runChecked(tar, ["--format", "7zip", "-cf", out.path, "-C", parent.path, name], toolName: "tar", environment: noAppleDouble)
        case "dmg":
            var folder = source
            if !CVFileNaming.isDirectory(source) || CVFileNaming.isPackage(source) {
                // hdiutil 需要資料夾：把單一檔案放進暫存資料夾
                let holder = try CVFileNaming.makeScratchDirectory().appendingPathComponent(base, isDirectory: true)
                try FileManager.default.createDirectory(at: holder, withIntermediateDirectories: true)
                try FileManager.default.copyItem(at: source, to: holder.appendingPathComponent(name))
                folder = holder
            }
            defer { if folder != source { try? FileManager.default.removeItem(at: folder.deletingLastPathComponent()) } }
            let volume = String(base.prefix(27))
            try await CVProcess.runChecked(hdiutil, ["create", "-quiet", "-volname", volume, "-srcfolder", folder.path,
                                                     "-format", "UDZO", "-fs", "HFS+", out.path], toolName: "hdiutil")
        default:
            throw ConversionError.unsupportedTarget(source: input.lastPathComponent, target: format.displayName)
        }
        progress(1)
        return [out]
    }

    /// 解壓縮到 workDir。若封存檔只包一個最上層項目就直接回傳它，否則包進以檔名命名的資料夾。
    static func extract(_ input: URL, profile: CVInputProfile, workDir: URL) async throws -> URL {
        let base = CVFileNaming.baseName(of: input)
        switch profile.archiveKind {
        case .gzip, .bzip2:
            let tool = profile.archiveKind == .gzip ? "/usr/bin/gzip" : "/usr/bin/bzip2"
            let out = workDir.appendingPathComponent(base)
            try await CVProcess.runChecked(tool, ["-dc", input.path], toolName: (tool as NSString).lastPathComponent, stdoutFile: out)
            return out
        case .xz:
            // macOS 沒有 xz 指令，但 bsdtar 讀得懂 .tar.xz；單純 .xz 檔需要先改名成 .tar.xz 才不會被誤判
            break
        default: break
        }
        let staging = workDir.appendingPathComponent(".extract-\(UUID().uuidString)", isDirectory: true)
        try FileManager.default.createDirectory(at: staging, withIntermediateDirectories: true)
        if profile.archiveKind == .zip {
            try await CVProcess.runChecked(ditto, ["-x", "-k", input.path, staging.path], toolName: "ditto")
        } else {
            try await CVProcess.runChecked(tar, ["-xf", input.path, "-C", staging.path], toolName: "tar")
        }
        let fm = FileManager.default
        let junk: Set<String> = ["__MACOSX", ".DS_Store"]
        for item in (try? fm.contentsOfDirectory(atPath: staging.path)) ?? [] where junk.contains(item) {
            try? fm.removeItem(at: staging.appendingPathComponent(item))
        }
        let items = ((try? fm.contentsOfDirectory(atPath: staging.path)) ?? []).filter { !$0.hasPrefix("._") }
        if items.isEmpty {
            try? fm.removeItem(at: staging)
            throw ConversionError.emptyContent("壓縮檔裡沒有任何檔案。")
        }
        if items.count == 1 {
            let only = staging.appendingPathComponent(items[0])
            let dest = CVFileNaming.uniqueURL(in: workDir, fileName: items[0])
            try fm.moveItem(at: only, to: dest)
            try? fm.removeItem(at: staging)
            return dest
        }
        let dest = CVFileNaming.uniqueURL(in: workDir, fileName: base)
        try fm.moveItem(at: staging, to: dest)
        return dest
    }
}
