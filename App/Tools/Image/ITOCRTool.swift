import AppKit
import SwiftUI
import Vision

// MARK: - 圖片文字辨識（OCR）

enum ITOCRLanguage: String, Sendable, CaseIterable {
    case traditional, simplified, japanese, korean, english, automatic

    var title: String {
        switch self {
        case .traditional: return "繁中＋英"
        case .simplified: return "簡中＋英"
        case .japanese: return "日文"
        case .korean: return "韓文"
        case .english: return "英文"
        case .automatic: return "自動偵測"
        }
    }

    var codes: [String] {
        switch self {
        case .traditional: return ["zh-Hant", "en-US"]
        case .simplified: return ["zh-Hans", "en-US"]
        case .japanese: return ["ja-JP", "en-US"]
        case .korean: return ["ko-KR", "en-US"]
        case .english: return ["en-US"]
        case .automatic: return []
        }
    }

    /// 「快速」模式只支援拉丁字母語言
    var supportsFast: Bool { self == .english }
}

enum ITOCROutput: String, Sendable {
    case txt, docx, rtf

    var title: String {
        switch self {
        case .txt: return "純文字 TXT"
        case .docx: return "Word DOCX"
        case .rtf: return "RTF"
        }
    }
}

struct ITOCROptions: Sendable {
    var language: ITOCRLanguage = .traditional
    var accurate = true
    var output: ITOCROutput = .txt
    var combine = false
    var languageCorrection = true
}

extension ImageToolCatalog {
    static let ocr = ToolDefinition(
        id: "image.ocr",
        category: .image,
        title: "圖片文字辨識（OCR）",
        subtitle: "把截圖、照片、掃描裡的文字辨識成可編輯的文字，支援繁中、簡中、日文、韓文與英文，可存成 TXT 或 Word。",
        symbol: "text.viewfinder",
        tint: ToolPalette.word,
        badge: "TXT",
        inputTypes: [.image],
        inputLabel: "圖片",
        actionTitle: "辨識文字",
        keywords: ["ocr", "text recognition", "extract text", "scan", "文字辨識", "辨識", "擷取文字", "掃描", "轉文字"],
        makeSession: { ITOCRSession() }
    )
}

final class ITOCRSession: ToolSession {
    @Published var language: ITOCRLanguage = .traditional
    @Published var accurate = true
    @Published var output: ITOCROutput = .txt
    @Published var combine = false
    @Published var languageCorrection = true
    @Published var fileCount = 0

    override func filesDidChange(_ files: [URL]) { fileCount = files.count }

    override func optionsView() -> AnyView { AnyView(ITOCROptionsView(session: self)) }

    override func makeJob() -> ToolJob {
        Self.job(ITOCROptions(language: language, accurate: accurate || !language.supportsFast, output: output,
                              combine: combine && fileCount > 1, languageCorrection: languageCorrection))
    }

    nonisolated static func job(_ o: ITOCROptions) -> ToolJob {
        ToolJob { files, context in
            var sections: [(name: String, text: String)] = []
            var outputs: [URL] = []
            var empty = 0
            for (i, file) in files.enumerated() {
                try context.checkCancellation()
                let sub = context.segment(i, of: files.count)
                sub.progress(0.05, "辨識中：\(file.lastPathComponent)（第一次使用需準備辨識模型，可能較久）")
                let source = try ITLoader.load(file)
                let text = try recognize(source.image, o)
                if text.isEmpty { empty += 1 }
                if o.combine {
                    sections.append((file.lastPathComponent, text))
                } else {
                    if text.isEmpty && files.count == 1 {
                        throw ConversionError.emptyContent("在圖片中找不到可辨識的文字。")
                    }
                    if !text.isEmpty {
                        outputs.append(try write([(file.lastPathComponent, text)], o, headings: false, for: file, context: sub))
                    }
                }
                sub.progress(1, text.isEmpty ? "沒有辨識到文字：\(file.lastPathComponent)" : "辨識出 \(text.count) 個字")
            }
            if o.combine, let first = files.first {
                if sections.allSatisfy({ $0.text.isEmpty }) { throw ConversionError.emptyContent("在這些圖片中找不到可辨識的文字。") }
                outputs.append(try write(sections, o, headings: true, for: first, context: context))
            }
            if empty > 0 && files.count > 1 { context.progress(1, "\(empty) 張圖片沒有辨識到文字") }
            return outputs
        }
    }

    /// 依位置排序成一行一行的文字（由上而下、由左而右）。
    nonisolated static func recognize(_ image: CGImage, _ o: ITOCROptions) throws -> String {
        let request = VNRecognizeTextRequest()
        request.recognitionLevel = o.accurate ? .accurate : .fast
        request.usesLanguageCorrection = o.languageCorrection
        if o.language == .automatic {
            request.automaticallyDetectsLanguage = true
        } else {
            let supported = (try? request.supportedRecognitionLanguages()) ?? []
            let langs = o.language.codes.filter { supported.isEmpty || supported.contains($0) }
            if !langs.isEmpty { request.recognitionLanguages = langs }
            request.automaticallyDetectsLanguage = false
        }
        let handler = VNImageRequestHandler(cgImage: image, options: [:])
        do {
            try handler.perform([request])
        } catch {
            throw ConversionError.other("文字辨識失敗：\(error.localizedDescription)")
        }
        let items = (request.results ?? []).compactMap { obs -> (CGRect, String)? in
            guard let text = obs.topCandidates(1).first?.string else { return nil }
            return (obs.boundingBox, text)
        }
        let sorted = items.sorted { a, b in
            if abs(a.0.midY - b.0.midY) > min(a.0.height, b.0.height) * 0.5 { return a.0.midY > b.0.midY }
            return a.0.minX < b.0.minX
        }
        var lines: [String] = []
        var lastBox: CGRect?
        for (box, text) in sorted {
            if let last = lastBox, abs(box.midY - last.midY) <= min(box.height, last.height) * 0.5, !lines.isEmpty {
                lines[lines.count - 1] += " " + text
            } else {
                if let last = lastBox, last.minY - box.maxY > last.height * 1.8, !lines.isEmpty { lines.append("") } // 段落間距
                lines.append(text)
            }
            lastBox = box
        }
        return lines.joined(separator: "\n").trimmingCharacters(in: .whitespacesAndNewlines)
    }

    nonisolated static func write(_ sections: [(name: String, text: String)], _ o: ITOCROptions, headings: Bool,
                                  for input: URL, context: ToolContext) throws -> URL {
        let work = try context.makeWorkDirectory()
        defer { try? FileManager.default.removeItem(at: work) }
        let ext = o.output.rawValue
        let tmp = work.appendingPathComponent("text.\(ext)")
        switch o.output {
        case .txt:
            let body = sections.map { s in
                headings ? "＝＝ \(s.name) ＝＝\n\(s.text.isEmpty ? "（沒有辨識到文字）" : s.text)" : s.text
            }.joined(separator: "\n\n")
            try Data((body + "\n").utf8).write(to: tmp)
        case .docx, .rtf:
            let doc = NSMutableAttributedString()
            let bodyFont = NSFont(name: "PingFangTC-Regular", size: 12) ?? NSFont.systemFont(ofSize: 12)
            let headFont = NSFont(name: "PingFangTC-Semibold", size: 15) ?? NSFont.boldSystemFont(ofSize: 15)
            let para = NSMutableParagraphStyle()
            para.lineSpacing = 3
            para.paragraphSpacing = 4
            for (i, s) in sections.enumerated() {
                if headings {
                    doc.append(NSAttributedString(string: (i > 0 ? "\n" : "") + s.name + "\n",
                                                  attributes: [.font: headFont, .paragraphStyle: para]))
                }
                doc.append(NSAttributedString(string: (s.text.isEmpty ? "（沒有辨識到文字）" : s.text) + "\n",
                                              attributes: [.font: bodyFont, .paragraphStyle: para]))
            }
            let type: NSAttributedString.DocumentType = o.output == .docx ? .officeOpenXML : .rtf
            let data = try doc.data(from: NSRange(location: 0, length: doc.length), documentAttributes: [.documentType: type])
            try data.write(to: tmp)
        }
        let tag = headings && sections.count > 1 ? "文字辨識（\(sections.count) 張）" : "文字"
        return try ITPipeline.deliver(file: tmp, for: input, context: context, tag: tag, ext: ext)
    }
}

private struct ITOCROptionsView: View {
    @ObservedObject var session: ITOCRSession

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            OptionGroup(title: "語言", systemImage: "character.book.closed") {
                ChoiceChips(selection: $session.language, choices: ITOCRLanguage.allCases.map { OptionChoice(value: $0, title: $0.title) })
            }
            OptionGroup(title: "辨識方式", systemImage: "scope") {
                ChoiceCards(selection: $session.accurate, choices: [
                    OptionChoice(value: true, title: "精確", subtitle: "最準確，支援中日韓文", systemImage: "scope", badge: "推薦"),
                    OptionChoice(value: false, title: "快速", subtitle: "速度快，只適用英文等拉丁字母", systemImage: "hare"),
                ])
                if !session.accurate && !session.language.supportsFast {
                    Text("所選語言需要精確模式，會自動改用精確辨識。").font(.caption).foregroundStyle(.orange)
                }
                OptionToggle(title: "自動校正拼字", subtitle: "依語言修正常見的辨識錯誤", isOn: $session.languageCorrection)
            }
            OptionGroup(title: "輸出", systemImage: "doc.text") {
                SegmentedOption(selection: $session.output, choices: [
                    OptionChoice(value: .txt, title: "TXT"),
                    OptionChoice(value: .docx, title: "Word"),
                    OptionChoice(value: .rtf, title: "RTF"),
                ])
                Text(session.output.title).font(.caption).foregroundStyle(.secondary)
                if session.fileCount > 1 {
                    OptionToggle(title: "合併成一個檔案", subtitle: "依圖片順序，每張加上檔名標題", isOn: $session.combine)
                }
            }
            OptionNote(text: "使用 macOS 內建的文字辨識，完全離線處理。文字清楚、對比高的圖片效果最好。", systemImage: "lock.shield")
        }
    }
}
