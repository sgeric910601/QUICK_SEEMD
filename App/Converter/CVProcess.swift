import Foundation

/// 執行外部指令（afconvert、ditto、tar、ffmpeg…），支援取消與逐行輸出。
enum CVProcess {
    struct Output: Sendable {
        let status: Int32
        let stdout: Data
        let stderr: Data

        var stdoutText: String { String(decoding: stdout, as: UTF8.self) }
        var stderrText: String { String(decoding: stderr, as: UTF8.self) }
        var succeeded: Bool { status == 0 }
    }

    /// 執行指令並等待結束。取消 Task 時會終止子程序。
    /// - Parameters:
    ///   - stdoutFile: 若提供，stdout 直接寫入這個檔案（例如 gzip -dc）。
    ///   - onStdoutLine: 逐行回呼（例如 ffmpeg -progress）。
    static func run(
        _ executable: String,
        _ arguments: [String],
        environment: [String: String]? = nil,
        currentDirectory: URL? = nil,
        stdoutFile: URL? = nil,
        onStdoutLine: (@Sendable (String) -> Void)? = nil
    ) async throws -> Output {
        let process = Process()
        process.executableURL = URL(fileURLWithPath: executable)
        process.arguments = arguments
        if let environment {
            process.environment = ProcessInfo.processInfo.environment.merging(environment) { _, new in new }
        }
        if let currentDirectory { process.currentDirectoryURL = currentDirectory }
        process.standardInput = FileHandle.nullDevice

        let outPipe = Pipe()
        let errPipe = Pipe()
        let outFileHandle: FileHandle?
        if let stdoutFile {
            FileManager.default.createFile(atPath: stdoutFile.path, contents: nil)
            guard let handle = try? FileHandle(forWritingTo: stdoutFile) else {
                throw ConversionError.writeFailed(stdoutFile.lastPathComponent)
            }
            outFileHandle = handle
            process.standardOutput = handle
        } else {
            outFileHandle = nil
            process.standardOutput = outPipe
        }
        process.standardError = errPipe

        let box = CVProcessBox(process: process)
        let exited = DispatchGroup()
        exited.enter()
        process.terminationHandler = { _ in exited.leave() }
        return try await withTaskCancellationHandler {
            try await withCheckedThrowingContinuation { (cont: CheckedContinuation<Output, Error>) in
                let group = DispatchGroup()
                let collected = CVProcessCollected()
                do {
                    try process.run()
                } catch {
                    try? outFileHandle?.close()
                    cont.resume(throwing: ConversionError.toolFailed(tool: (executable as NSString).lastPathComponent,
                                                                     message: error.localizedDescription))
                    return
                }
                box.markLaunched()
                if stdoutFile == nil {
                    group.enter()
                    DispatchQueue.global(qos: .utility).async {
                        let handle = outPipe.fileHandleForReading
                        var pending = Data()
                        while true {
                            let chunk = handle.availableData
                            if chunk.isEmpty { break }
                            collected.appendOut(chunk)
                            if let onStdoutLine {
                                pending.append(chunk)
                                while let nl = pending.firstIndex(of: 0x0A) {
                                    let lineData = pending[pending.startIndex..<nl]
                                    pending.removeSubrange(pending.startIndex...nl)
                                    onStdoutLine(String(decoding: lineData, as: UTF8.self))
                                }
                            }
                        }
                        if let onStdoutLine, !pending.isEmpty {
                            onStdoutLine(String(decoding: pending, as: UTF8.self))
                        }
                        group.leave()
                    }
                }
                group.enter()
                DispatchQueue.global(qos: .utility).async {
                    let data = errPipe.fileHandleForReading.readDataToEndOfFile()
                    collected.appendErr(data)
                    group.leave()
                }
                DispatchQueue.global(qos: .utility).async {
                    exited.wait()
                    // 子程序結束後，管線最多再等 5 秒（避免被它啟動的其他程序佔住管線而永遠等下去）
                    _ = group.wait(timeout: .now() + 5)
                    try? outFileHandle?.close()
                    if box.wasCancelled {
                        cont.resume(throwing: ConversionError.cancelled)
                    } else {
                        cont.resume(returning: Output(status: process.terminationStatus,
                                                      stdout: collected.out, stderr: collected.err))
                    }
                }
            }
        } onCancel: {
            box.cancel()
        }
    }

    /// 執行並在失敗時丟出含 stderr 的錯誤。
    @discardableResult
    static func runChecked(
        _ executable: String,
        _ arguments: [String],
        toolName: String? = nil,
        environment: [String: String]? = nil,
        currentDirectory: URL? = nil,
        stdoutFile: URL? = nil,
        onStdoutLine: (@Sendable (String) -> Void)? = nil
    ) async throws -> Output {
        let output = try await run(executable, arguments, environment: environment, currentDirectory: currentDirectory,
                                   stdoutFile: stdoutFile, onStdoutLine: onStdoutLine)
        guard output.succeeded else {
            let message = output.stderrText.isEmpty ? output.stdoutText : output.stderrText
            throw ConversionError.toolFailed(tool: toolName ?? (executable as NSString).lastPathComponent, message: message)
        }
        return output
    }
}

private final class CVProcessBox: @unchecked Sendable {
    private let lock = NSLock()
    private let process: Process
    private var launched = false
    private var cancelled = false

    init(process: Process) { self.process = process }

    var wasCancelled: Bool { lock.lock(); defer { lock.unlock() }; return cancelled }

    func markLaunched() {
        lock.lock()
        launched = true
        let shouldTerminate = cancelled
        lock.unlock()
        if shouldTerminate { process.terminate() }
    }

    func cancel() {
        lock.lock()
        cancelled = true
        let isLaunched = launched
        lock.unlock()
        if isLaunched, process.isRunning { process.terminate() }
    }
}

private final class CVProcessCollected: @unchecked Sendable {
    private let lock = NSLock()
    private var outData = Data()
    private var errData = Data()

    func appendOut(_ d: Data) { lock.lock(); outData.append(d); lock.unlock() }
    func appendErr(_ d: Data) { lock.lock(); errData.append(d); lock.unlock() }
    var out: Data { lock.lock(); defer { lock.unlock() }; return outData }
    var err: Data { lock.lock(); defer { lock.unlock() }; return errData }
}
