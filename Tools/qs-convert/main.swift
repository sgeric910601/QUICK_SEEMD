// qs-convert：QUICK_SEEMD 轉換引擎的命令列測試工具。
//
// 編譯（在 QUICK_SEEMD/ 目錄下）：
//   xcrun swiftc -swift-version 5 -target arm64-apple-macos14.0 \
//     App/Converter/*.swift Tools/qs-convert/main.swift -o /tmp/qs-convert
//
// 用法：
//   qs-convert --list <檔案>                       列出可轉換的格式
//   qs-convert <檔案> <格式代碼> <輸出資料夾> [選項]   轉換並印出輸出路徑
//   qs-convert --merge-pdf <輸出資料夾> <檔案…>       將圖片／PDF 合併成一份 PDF
//   qs-convert --backends                          列出外部引擎偵測結果
//   qs-convert --formats                           列出所有格式代碼
//
// 選項：--quality 0.8  --max-size 1024  --dpi 150  --video-size 720  --frame-time 1.5
//       --gif-fps 10  --gif-width 320  --gif-seconds 3  --bitrate 128000  --encoding big5  --progress

import Foundation

func printErr(_ s: String) {
    FileHandle.standardError.write(Data((s + "\n").utf8))
}

func usage() -> Never {
    printErr("""
    用法：
      qs-convert --list <檔案>
      qs-convert <檔案> <格式代碼> <輸出資料夾> [--quality q] [--max-size px] [--dpi n] [--video-size 480|720|1080|2160]
                 [--frame-time s] [--gif-fps n] [--gif-width px] [--gif-seconds s] [--bitrate bps] [--encoding name] [--progress]
      qs-convert --merge-pdf <輸出資料夾> <檔案…>
      qs-convert --backends
      qs-convert --formats
    """)
    exit(2)
}

nonisolated(unsafe) var showProgress = false

/// 進度每跨過 10% 印一次到 stderr。
final class ProgressPrinter: @unchecked Sendable {
    private let lock = NSLock()
    private var lastBucket = -1
    func report(_ p: Double) {
        let bucket = Int((p * 10).rounded(.down))
        lock.lock(); defer { lock.unlock() }
        guard bucket > lastBucket else { return }
        lastBucket = bucket
        printErr("進度 \(Int(p * 100))%")
    }
}

func parseOptions(_ args: ArraySlice<String>) -> ConversionOptions {
    var o = ConversionOptions()
    var i = args.startIndex
    while i < args.endIndex {
        let key = args[i]
        let value = i + 1 < args.endIndex ? args[i + 1] : ""
        switch key {
        case "--quality": o.imageQuality = Double(value) ?? o.imageQuality
        case "--max-size": o.imageMaxPixelSize = Int(value)
        case "--dpi": o.pdfRenderDPI = Double(value) ?? o.pdfRenderDPI
        case "--video-size": o.videoSize = ConversionVideoSize(rawValue: Int(value) ?? 0) ?? .original
        case "--frame-time": o.frameTime = Double(value)
        case "--gif-fps": o.gifFramesPerSecond = Int(value) ?? o.gifFramesPerSecond
        case "--gif-width": o.gifMaxWidth = Int(value) ?? o.gifMaxWidth
        case "--gif-seconds": o.gifMaxDuration = Double(value) ?? o.gifMaxDuration
        case "--bitrate": o.audioBitrate = Int(value) ?? o.audioBitrate
        case "--encoding": o.sourceEncoding = ConversionTextEncoding(rawValue: value) ?? .auto
        case "--progress": showProgress = true; i += 1; continue
        default:
            printErr("未知選項：\(key)")
            usage()
        }
        i += 2
    }
    return o
}

func absoluteURL(_ path: String) -> URL {
    URL(fileURLWithPath: path, relativeTo: URL(fileURLWithPath: FileManager.default.currentDirectoryPath)).standardizedFileURL
}

func runCLI() async -> Int32 {
    let args = Array(CommandLine.arguments.dropFirst())
    guard let first = args.first else { usage() }

    switch first {
    case "--formats":
        for f in ConversionFormats.all {
            print("\(f.id.padding(toLength: 12, withPad: " ", startingAt: 0)) \(f.category.rawValue.padding(toLength: 9, withPad: " ", startingAt: 0)) .\(f.fileExtension.isEmpty ? "(同來源)" : f.fileExtension)  \(f.displayName)")
        }
        return 0

    case "--backends":
        for s in ConversionEngine.backendStatuses {
            print("\(s.backend.displayName): \(s.executable?.path ?? "未安裝（\(s.installHint)）")")
        }
        print("ImageIO 可寫入：\(ConversionFormats.nativeWritableImageFormats.map(\.id).joined(separator: " "))")
        print("音訊可輸出：\(CVMediaConverter.nativeAudioFormats.map(\.id).joined(separator: " "))")
        print("影片可輸出：\(CVMediaConverter.nativeVideoFormats.map(\.id).joined(separator: " "))")
        return 0

    case "--list":
        guard args.count >= 2 else { usage() }
        let url = absoluteURL(args[1])
        guard FileManager.default.fileExists(atPath: url.path) else {
            printErr("找不到檔案：\(url.path)")
            return 1
        }
        let profile = await ConversionEngine.analyze(url)
        let summary = ConversionEngine.summary(for: profile)
        print("# \(url.lastPathComponent) — \(summary.kind)\(summary.detail.map { "（\($0)）" } ?? "")")
        let targets = ConversionEngine.plan(for: profile)
        if targets.isEmpty { print("（沒有可用的轉換）") }
        for t in targets {
            let f = t.format
            var line = "\(f.id.padding(toLength: 12, withPad: " ", startingAt: 0)) \(f.displayName)"
            if let note = f.note { line += "  — \(note)" }
            if f.backend != .native { line += "  [\(f.backend.displayName)]" }
            print(line)
        }
        for u in ConversionEngine.unavailable(for: profile) {
            print("✕ \(u.formats)：\(u.reason)")
        }
        return 0

    case "--merge-pdf":
        guard args.count >= 3 else { usage() }
        let outDir = absoluteURL(args[1])
        let inputs = args.dropFirst(2).map(absoluteURL)
        do {
            let url = try await ConversionEngine.mergeToPDF(inputs, outputDirectory: outDir)
            print(url.path)
            return 0
        } catch {
            printErr("錯誤：\(error.localizedDescription)")
            return 1
        }

    default:
        guard args.count >= 3 else { usage() }
        let input = absoluteURL(args[0])
        let formatID = args[1]
        let outDir = absoluteURL(args[2])
        let options = parseOptions(args.dropFirst(3))
        let start = Date()
        do {
            let printer = ProgressPrinter()
            let outputs = try await ConversionEngine.convert(input, toFormatID: formatID, options: options, outputDirectory: outDir) { p in
                if showProgress { printer.report(p) }
            }
            for o in outputs { print(o.path) }
            printErr(String(format: "完成（%.2f 秒）", Date().timeIntervalSince(start)))
            return 0
        } catch {
            printErr("錯誤：\(error.localizedDescription)")
            return 1
        }
    }
}

// HTML 匯入等工作需要主執行緒的 RunLoop，所以讓主執行緒跑 RunLoop，轉換在 Task 中進行。
Task {
    let code = await runCLI()
    exit(code)
}
CFRunLoopRun()
