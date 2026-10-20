import AppKit
import Combine
import Foundation
import QuickLookThumbnailing
import SwiftUI
import UniformTypeIdentifiers

/// 轉換佇列中的一個檔案。
struct ConverterJob: Identifiable, Equatable {
    enum Status: Equatable {
        case idle
        case queued
        case running(Double)
        case done([URL])
        case failed(String)
        case cancelled

        var isFinished: Bool {
            switch self {
            case .done, .failed, .cancelled: return true
            default: return false
            }
        }
    }

    let id = UUID()
    let url: URL
    let name: String
    var icon: NSImage
    /// 是否已換成 系統預覽 縮圖（圖片、影片、PDF…）。
    var hasThumbnail = false
    let sizeText: String
    var kind: String
    var detail: String?
    var isAnalyzing = true
    var targets: [ConversionFormat] = []
    var unavailable: [ConversionUnavailable] = []
    var targetID: String?
    var status: Status = .idle

    // 用來決定要顯示哪些選項
    var isImage = false
    var isPDF = false
    var isVideo = false
    var isAudio = false
    var isTextual = false

    var target: ConversionFormat? { targets.first { $0.id == targetID } }
    var isMergeable: Bool { isImage || isPDF }

    static func == (lhs: ConverterJob, rhs: ConverterJob) -> Bool {
        lhs.id == rhs.id && lhs.status == rhs.status && lhs.targetID == rhs.targetID && lhs.isAnalyzing == rhs.isAnalyzing
            && lhs.hasThumbnail == rhs.hasThumbnail
            && lhs.targets == rhs.targets && lhs.kind == rhs.kind && lhs.detail == rhs.detail
    }
}

/// 轉換頁面的狀態與動作。
@MainActor
final class ConverterModel: ObservableObject {
    enum OutputMode: String, CaseIterable, Identifiable {
        case sameAsSource, custom
        var id: String { rawValue }
        var title: String { self == .sameAsSource ? "與原始檔相同" : "自訂資料夾" }
    }

    /// 選項面板的區塊。
    enum OptionSection: Hashable {
        case imageQuality, imageSize, pdfResolution, videoSize, gif, frame, audioBitrate, textEncoding
    }

    struct Banner: Identifiable, Equatable {
        let id = UUID()
        let message: String
        let urls: [URL]
        let isError: Bool
    }

    @Published private(set) var jobs: [ConverterJob] = []
    @Published var selection: Set<ConverterJob.ID> = []
    @Published var options = ConversionOptions()
    @Published var outputMode: OutputMode {
        didSet { UserDefaults.standard.set(outputMode.rawValue, forKey: Self.outputModeKey) }
    }
    @Published var customOutputFolder: URL? {
        didSet { UserDefaults.standard.set(customOutputFolder?.path, forKey: Self.outputFolderKey) }
    }
    @Published private(set) var isRunning = false
    @Published private(set) var backendStatuses: [ConversionBackendStatus] = []
    @Published var banner: Banner?

    private var runTask: Task<Void, Never>?
    private var lastChosenTargetID: String?
    private static let outputModeKey = "Converter.outputMode"
    private static let outputFolderKey = "Converter.outputFolder"

    init() {
        let defaults = UserDefaults.standard
        outputMode = OutputMode(rawValue: defaults.string(forKey: Self.outputModeKey) ?? "") ?? .sameAsSource
        if let path = defaults.string(forKey: Self.outputFolderKey), CVFileNaming.isDirectory(URL(fileURLWithPath: path)) {
            customOutputFolder = URL(fileURLWithPath: path)
        } else {
            customOutputFolder = nil
        }
        refreshBackends()
    }

    // MARK: - 加入與移除

    /// 加入檔案（拖放到視窗或 Dock 圖示、Finder 服務、開啟面板…）。重複的檔案會被略過。
    func add(urls: [URL]) {
        var known = Set(jobs.map { $0.url.standardizedFileURL.path })
        var added: [ConverterJob.ID] = []
        for raw in urls {
            let url = raw.standardizedFileURL
            guard url.isFileURL, FileManager.default.fileExists(atPath: url.path), !known.contains(url.path) else { continue }
            known.insert(url.path)
            let job = makeJob(url)
            jobs.append(job)
            added.append(job.id)
            analyze(job.id, url: url)
            loadThumbnail(job.id, url: url)
        }
        if !added.isEmpty {
            selection = Set(added)
            banner = nil
        }
    }

    private func makeJob(_ url: URL) -> ConverterJob {
        let icon = NSWorkspace.shared.icon(forFile: url.path)
        icon.size = NSSize(width: 64, height: 64)
        let values = try? url.resourceValues(forKeys: [.fileSizeKey, .totalFileAllocatedSizeKey, .isDirectoryKey, .isPackageKey,
                                                       .localizedTypeDescriptionKey])
        var sizeText = ""
        if values?.isDirectory == true && values?.isPackage != true {
            sizeText = ""
        } else if let size = values?.fileSize ?? values?.totalFileAllocatedSize {
            sizeText = ByteCountFormatter.string(fromByteCount: Int64(size), countStyle: .file)
        }
        return ConverterJob(url: url, name: FileManager.default.displayName(atPath: url.path), icon: icon, sizeText: sizeText,
                            kind: values?.localizedTypeDescription ?? "")
    }

    private func analyze(_ id: ConverterJob.ID, url: URL) {
        Task.detached(priority: .userInitiated) { [weak self] in
            let profile = await ConversionEngine.analyze(url)
            let targets = ConversionEngine.plan(for: profile).map(\.format)
            let unavailable = ConversionEngine.unavailable(for: profile)
            let summary = ConversionEngine.summary(for: profile)
            guard let self else { return }
            await self.applyAnalysis(id, profile: profile, targets: targets, unavailable: unavailable, summary: summary)
        }
    }

    /// 以 系統預覽 產生縮圖取代一般檔案圖示；失敗（例如資料夾）就保留 Finder 圖示。
    private func loadThumbnail(_ id: ConverterJob.ID, url: URL) {
        // 只有圖片、影片、PDF 的縮圖比圖示更有辨識度；文字檔的縮圖多半是一張白紙
        guard let type = (try? url.resourceValues(forKeys: [.contentTypeKey]))?.contentType,
              type.conforms(to: .image) || type.conforms(to: .movie) || type.conforms(to: .pdf) else { return }
        let scale = NSScreen.main?.backingScaleFactor ?? 2
        let request = QLThumbnailGenerator.Request(fileAt: url, size: CGSize(width: 76, height: 76), scale: scale,
                                                   representationTypes: .thumbnail)
        QLThumbnailGenerator.shared.generateBestRepresentation(for: request) { [weak self] representation, _ in
            guard let cgImage = representation?.cgImage else { return }
            let size = NSSize(width: CGFloat(cgImage.width) / scale, height: CGFloat(cgImage.height) / scale)
            Task { @MainActor in
                self?.update(id) { job in
                    job.icon = NSImage(cgImage: cgImage, size: size)
                    job.hasThumbnail = true
                }
            }
        }
    }

    private func applyAnalysis(_ id: ConverterJob.ID, profile: CVInputProfile, targets: [ConversionFormat],
                               unavailable: [ConversionUnavailable], summary: ConversionInputSummary) {
        guard let index = jobs.firstIndex(where: { $0.id == id }) else { return }
        var job = jobs[index]
        job.isAnalyzing = false
        job.targets = targets
        job.unavailable = unavailable
        if !summary.kind.isEmpty { job.kind = summary.kind }
        job.detail = summary.detail
        job.isImage = profile.isImage
        job.isPDF = profile.isPDF
        job.isVideo = profile.isVideo
        job.isAudio = profile.isAudio
        job.isTextual = profile.isPlainText || profile.isMarkdown || profile.dataKind != nil
        if let current = job.targetID, !targets.contains(where: { $0.id == current }) { job.targetID = nil }
        // 延續上一次選的格式，連續轉一批同類型檔案時不必重選
        if job.targetID == nil, let last = lastChosenTargetID, targets.contains(where: { $0.id == last }) {
            job.targetID = last
        }
        jobs[index] = job
    }

    func remove(_ ids: Set<ConverterJob.ID>) {
        let removable = ids.filter { id in
            if case .running = jobs.first(where: { $0.id == id })?.status { return false }
            return true
        }
        jobs.removeAll { removable.contains($0.id) }
        selection.subtract(removable)
    }

    func removeSelected() { remove(selection) }

    func removeAll() {
        guard !isRunning else { return }
        jobs.removeAll()
        selection.removeAll()
        banner = nil
    }

    func clearFinished() {
        let finished = Set(jobs.filter { if case .done = $0.status { return true }; return false }.map(\.id))
        remove(finished)
    }

    // MARK: - 選取

    func select(_ id: ConverterJob.ID, extend: Bool) {
        if extend {
            if selection.contains(id) { selection.remove(id) } else { selection.insert(id) }
        } else {
            selection = [id]
        }
    }

    func selectAll() { selection = Set(jobs.map(\.id)) }

    /// 目前格式設定套用的對象：有選取就用選取，否則全部。
    var scopeJobs: [ConverterJob] {
        let picked = jobs.filter { selection.contains($0.id) }
        return picked.isEmpty ? jobs : picked
    }

    var scopeDescription: String {
        let picked = jobs.filter { selection.contains($0.id) }
        if picked.isEmpty || picked.count == jobs.count {
            return jobs.count == 1 ? "這個檔案" : "全部 \(jobs.count) 個檔案"
        }
        return picked.count == 1 ? "「\(picked[0].name)」" : "選取的 \(picked.count) 個檔案"
    }

    var isAnalyzingScope: Bool { scopeJobs.contains { $0.isAnalyzing } }

    /// 範圍內所有檔案共同可用的格式。
    var commonTargets: [ConversionFormat] {
        ConversionEngine.commonTargets(scopeJobs.filter { !$0.isAnalyzing }.map(\.targets))
    }

    var groupedTargets: [(category: ConversionCategory, formats: [ConversionFormat])] {
        let common = commonTargets
        return ConversionCategory.allCases.sorted { $0.order < $1.order }.compactMap { cat in
            let list = common.filter { $0.category == cat }
            return list.isEmpty ? nil : (cat, list)
        }
    }

    /// 範圍內檔案的類型是否不同（只顯示交集時提示使用者）。
    var scopeIsMixed: Bool {
        let lists = scopeJobs.filter { !$0.isAnalyzing }.map { Set($0.targets.map(\.id)) }
        guard let first = lists.first else { return false }
        return lists.contains { $0 != first }
    }

    var unavailableHints: [ConversionUnavailable] {
        var seen = Set<String>()
        var out: [ConversionUnavailable] = []
        for job in scopeJobs {
            for u in job.unavailable where !seen.contains(u.id) {
                seen.insert(u.id)
                out.append(u)
            }
        }
        return out
    }

    /// 某格式在範圍內的選取狀態：全部、部分、沒有。
    func selectionState(of format: ConversionFormat) -> (all: Bool, some: Bool) {
        let scope = scopeJobs
        guard !scope.isEmpty else { return (false, false) }
        let count = scope.filter { $0.targetID == format.id }.count
        return (count == scope.count, count > 0)
    }

    /// 為範圍內的檔案指定輸出格式。再按一次已選的格式則取消。
    func choose(_ format: ConversionFormat) {
        let state = selectionState(of: format)
        let ids = Set(scopeJobs.map(\.id))
        for index in jobs.indices where ids.contains(jobs[index].id) {
            if case .running = jobs[index].status { continue }
            if case .queued = jobs[index].status { continue }
            jobs[index].targetID = state.all ? nil : format.id
            if jobs[index].status.isFinished { jobs[index].status = .idle }
        }
        lastChosenTargetID = state.all ? nil : format.id
        banner = nil
    }

    // MARK: - 選項

    /// 依範圍內檔案已選的格式，決定要顯示哪些選項。
    var relevantOptionSections: [OptionSection] {
        var set = Set<OptionSection>()
        for job in scopeJobs {
            guard let target = job.target else { continue }
            let id = target.id
            let imageOut = target.category == .image && id != "anim-gif"
            if job.isVideo {
                switch id {
                case "anim-gif": set.insert(.gif)
                case "png", "jpeg": set.insert(.frame); set.insert(.imageSize); if id == "jpeg" { set.insert(.imageQuality) }
                default:
                    if target.category == .video { set.insert(.videoSize) }
                    if target.category == .audio, ["m4a", "aac", "mp3", "opus"].contains(id) { set.insert(.audioBitrate) }
                }
                continue
            }
            if job.isPDF && imageOut {
                set.insert(.pdfResolution)
                set.insert(.imageSize)
                if target.isLossyImage { set.insert(.imageQuality) }
                continue
            }
            if job.isImage {
                if imageOut && !["ico", "icns"].contains(id) { set.insert(.imageSize) }
                if target.isLossyImage || id == "webp" { set.insert(.imageQuality) }
                if id == "pdf" { set.insert(.imageSize) }
                if (id == "mp4" || id == "mov") { set.insert(.imageSize) }
            }
            if job.isAudio, ["m4a", "aac", "mp3", "opus"].contains(id) { set.insert(.audioBitrate) }
            if job.isTextual, [.text, .data, .document].contains(target.category) { set.insert(.textEncoding) }
        }
        let order: [OptionSection] = [.imageQuality, .imageSize, .pdfResolution, .videoSize, .gif, .frame, .audioBitrate, .textEncoding]
        return order.filter { set.contains($0) }
    }

    /// 偵測到的文字編碼（顯示在「來源編碼」旁）。
    var detectedEncodingSummary: String? {
        let details = scopeJobs.filter(\.isTextual).compactMap(\.detail)
        guard let first = details.first else { return nil }
        return details.allSatisfy({ $0 == first }) ? first : "多種編碼"
    }

    // MARK: - 合併 PDF

    var mergeableJobs: [ConverterJob] { jobs.filter { !$0.isAnalyzing && $0.isMergeable } }

    func mergeIntoPDF() {
        let sources = mergeableJobs.map(\.url)
        guard sources.count >= 2, !isRunning, let outDir = outputDirectory(for: sources[0]) else {
            if outputMode == .custom && customOutputFolder == nil { chooseOutputFolder() }
            return
        }
        isRunning = true
        banner = nil
        let options = self.options
        runTask = Task { [weak self] in
            do {
                let url = try await ConversionEngine.mergeToPDF(sources, options: options, outputDirectory: outDir)
                self?.banner = Banner(message: "已將 \(sources.count) 個檔案合併成「\(url.lastPathComponent)」", urls: [url], isError: false)
            } catch {
                self?.banner = Banner(message: "合併失敗：\(error.localizedDescription)", urls: [], isError: true)
            }
            self?.isRunning = false
        }
    }

    // MARK: - 轉換

    /// 可以開始轉換的檔案（已選格式、尚未完成）。
    var readyJobs: [ConverterJob] {
        jobs.filter { job in
            guard job.target != nil, !job.isAnalyzing else { return false }
            switch job.status {
            case .idle, .failed, .cancelled: return true
            default: return false
            }
        }
    }

    var finishedCount: Int { jobs.filter { if case .done = $0.status { return true }; return false }.count }

    /// 整體進度（顯示在轉換按鈕旁）。
    var overallProgress: Double? {
        let active = jobs.filter { job in
            switch job.status {
            case .queued, .running: return true
            default: return false
            }
        }
        guard isRunning, !active.isEmpty else { return nil }
        let sum = active.reduce(0.0) { acc, job in
            if case .running(let p) = job.status { return acc + p }
            return acc
        }
        return sum / Double(active.count)
    }

    func outputDirectory(for source: URL) -> URL? {
        switch outputMode {
        case .sameAsSource: return source.deletingLastPathComponent()
        case .custom: return customOutputFolder
        }
    }

    func convertAll() {
        guard !isRunning else { return }
        if outputMode == .custom && customOutputFolder == nil {
            chooseOutputFolder()
            return
        }
        let ids = readyJobs.map(\.id)
        guard !ids.isEmpty else { return }
        for index in jobs.indices where ids.contains(jobs[index].id) { jobs[index].status = .queued }
        isRunning = true
        banner = nil
        let options = self.options
        runTask = Task { [weak self] in
            var succeeded = 0
            var failed = 0
            for id in ids {
                guard let self else { return }
                if Task.isCancelled { break }
                guard let job = self.jobs.first(where: { $0.id == id }), let format = job.target,
                      let outDir = self.outputDirectory(for: job.url) else { continue }
                self.update(id) { $0.status = .running(0) }
                let reporter = CVProgressRelay { [weak self] value in self?.setProgress(id, value) }
                do {
                    let urls = try await ConversionEngine.convert(job.url, to: format, options: options, outputDirectory: outDir,
                                                                  progress: { reporter.send($0) })
                    self.update(id) { $0.status = .done(urls) }
                    succeeded += 1
                } catch {
                    if Task.isCancelled || (error as? ConversionError).map({ if case .cancelled = $0 { return true }; return false }) == true {
                        self.update(id) { $0.status = .cancelled }
                    } else {
                        self.update(id) { $0.status = .failed(error.localizedDescription) }
                        failed += 1
                    }
                }
            }
            guard let self else { return }
            for index in self.jobs.indices where self.jobs[index].status == .queued { self.jobs[index].status = .idle }
            self.isRunning = false
            if succeeded + failed > 1 {
                let message = failed == 0 ? "全部完成，共 \(succeeded) 個檔案。" : "完成 \(succeeded) 個，\(failed) 個失敗。"
                let urls = self.jobs.filter { ids.contains($0.id) }.flatMap { job -> [URL] in
                    if case .done(let u) = job.status { return u }
                    return []
                }
                self.banner = Banner(message: message, urls: urls, isError: failed > 0)
            }
        }
    }

    func cancel() {
        runTask?.cancel()
    }

    private func update(_ id: ConverterJob.ID, _ body: (inout ConverterJob) -> Void) {
        guard let index = jobs.firstIndex(where: { $0.id == id }) else { return }
        body(&jobs[index])
    }

    private func setProgress(_ id: ConverterJob.ID, _ value: Double) {
        guard let index = jobs.firstIndex(where: { $0.id == id }), case .running(let old) = jobs[index].status else { return }
        let clamped = min(1, max(0, value))
        if clamped - old >= 0.01 || clamped >= 1 { jobs[index].status = .running(clamped) }
    }

    // MARK: - 面板與 Finder

    func openFiles() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = true
        panel.canChooseDirectories = true
        panel.allowsMultipleSelection = true
        panel.treatsFilePackagesAsDirectories = false
        panel.prompt = "加入"
        panel.message = "選擇要轉換的檔案或資料夾"
        panel.begin { [weak self] response in
            guard response == .OK else { return }
            let urls = panel.urls
            Task { @MainActor in self?.add(urls: urls) }
        }
    }

    func chooseOutputFolder() {
        let panel = NSOpenPanel()
        panel.canChooseFiles = false
        panel.canChooseDirectories = true
        panel.canCreateDirectories = true
        panel.allowsMultipleSelection = false
        panel.prompt = "選擇"
        panel.message = "選擇轉換後檔案的輸出位置"
        if let current = customOutputFolder { panel.directoryURL = current }
        panel.begin { [weak self] response in
            guard response == .OK, let url = panel.url else { return }
            Task { @MainActor in
                self?.customOutputFolder = url
                self?.outputMode = .custom
            }
        }
    }

    func reveal(_ urls: [URL]) {
        let existing = urls.filter { FileManager.default.fileExists(atPath: $0.path) }
        guard !existing.isEmpty else { return }
        NSWorkspace.shared.activateFileViewerSelecting(existing)
    }

    func open(_ url: URL) {
        NSWorkspace.shared.open(url)
    }

    func refreshBackends() {
        ConversionEngine.refreshBackends()
        backendStatuses = ConversionEngine.backendStatuses
        // 重新偵測後更新每個檔案的可用格式
        for job in jobs where !job.isAnalyzing { analyze(job.id, url: job.url) }
    }

    var availableBackendCount: Int { backendStatuses.filter(\.isAvailable).count }
}

/// 把背景執行緒的進度回報轉到主執行緒。
final class CVProgressRelay: @unchecked Sendable {
    private let handler: @MainActor (Double) -> Void
    private let lock = NSLock()
    private var lastSent = -1.0

    init(_ handler: @escaping @MainActor (Double) -> Void) {
        self.handler = handler
    }

    func send(_ value: Double) {
        lock.lock()
        guard value - lastSent >= 0.005 || value >= 1 else { lock.unlock(); return }
        lastSent = value
        lock.unlock()
        let handler = self.handler
        Task { @MainActor in handler(value) }
    }
}
