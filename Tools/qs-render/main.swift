import Foundation

// 用法：qs-render <檔案> <輸出資料夾>
// 產生 <輸出資料夾>/<檔名>.html 與附件，方便在瀏覽器檢查預覽外觀。
let args = CommandLine.arguments
guard args.count >= 3 else {
    print("用法：qs-render <檔案...> <輸出資料夾>")
    exit(1)
}
let outDir = URL(fileURLWithPath: args.last!)
try? FileManager.default.createDirectory(at: outDir, withIntermediateDirectories: true)
for path in args[1..<(args.count - 1)] {
    let url = URL(fileURLWithPath: path)
    let start = Date()
    let doc = QSPreviewEngine.render(url: url)
    let ms = Int(Date().timeIntervalSince(start) * 1000)
    let base = url.lastPathComponent.replacingOccurrences(of: " ", with: "_")
    var html = doc.html
    for (name, att) in doc.attachments {
        let ext = att.contentType.preferredFilenameExtension ?? "bin"
        let fileName = "\(base).\(name).\(ext)"
        try? att.data.write(to: outDir.appendingPathComponent(fileName))
        html = html.replacingOccurrences(of: "cid:\(name)", with: fileName)
    }
    let out = outDir.appendingPathComponent(base + ".html")
    try? html.write(to: out, atomically: true, encoding: .utf8)
    print("\(ms)ms\t\(html.utf8.count / 1024)KB\t\(doc.attachments.count) att\t\(out.lastPathComponent)")
}
