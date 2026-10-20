import PDFKit
import SwiftUI
import UniformTypeIdentifiers
import WebKit

// HTML 轉 PDF：以 WebKit 排版成一張長頁面，再依紙張大小切頁（盡量不切到文字行），內容保持向量、可選取。

extension PDFToolCatalog {
    static let fromHTML = ToolDefinition(
        id: "pdf.from-html",
        category: .pdf,
        title: "HTML 轉 PDF",
        subtitle: "把本機的 HTML 網頁轉成分頁的 PDF，文字可選取。",
        symbol: "globe",
        tint: ToolPalette.teal,
        badge: "PDF",
        inputTypes: [.html],
        inputLabel: "HTML 檔案",
        actionTitle: "轉成 PDF",
        keywords: ["html to pdf", "web page", "webpage", "網頁", "HTML", "網頁轉PDF"],
        makeSession: { PTHTMLSession() }
    )
}

struct PTHTMLOptions: Sendable {
    var paper: PTPaperSize = .a4
    var landscape = false
    var margin = 36.0
    var backgrounds = true

    var pageSize: CGSize {
        let p = paper.portrait ?? CGSize(width: 595.28, height: 841.89)
        return landscape ? CGSize(width: p.height, height: p.width) : p
    }
}

final class PTHTMLSession: ToolSession {
    @Published var options = PTHTMLOptions()

    override func optionsView() -> AnyView { AnyView(PTHTMLPanel(session: self)) }

    override func makeJob() -> ToolJob { Self.job(options) }

    nonisolated static func job(_ o: PTHTMLOptions) -> ToolJob {
        .perFile { file, context in
            context.progress(0.05, "載入「\(file.lastPathComponent)」…")
            let page = o.pageSize
            let contentWidth = page.width - CGFloat(o.margin) * 2
            // 以 96 dpi 的 CSS 像素排版（與瀏覽器列印相同），再縮成 pt
            let cssWidth = (contentWidth / 0.75).rounded()
            let data = try await PTHTMLRenderer.render(file, cssWidth: cssWidth, backgrounds: o.backgrounds)
            try context.checkCancellation()
            context.progress(0.7, "分頁中…")
            let tmp = try context.makeWorkDirectory().appendingPathComponent("html.pdf")
            let count = try PTHTMLRenderer.paginate(data, pageSize: page, margin: CGFloat(o.margin), title: CVFileNaming.baseName(of: file), to: tmp)
            context.progress(1, "已轉成 \(count) 頁 PDF")
            return [try context.deliver(tmp, as: context.outputName(for: file, ext: "pdf"), for: file)]
        }
    }
}

@MainActor
final class PTHTMLRenderer: NSObject, WKNavigationDelegate {
    private var continuation: CheckedContinuation<Void, Error>?

    /// 載入 HTML，回傳整份內容排成單一長頁面的 PDF 資料。
    static func render(_ url: URL, cssWidth: CGFloat, backgrounds: Bool) async throws -> Data {
        let renderer = PTHTMLRenderer()
        return try await renderer.run(url, cssWidth: cssWidth, backgrounds: backgrounds)
    }

    private func run(_ url: URL, cssWidth: CGFloat, backgrounds: Bool) async throws -> Data {
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .nonPersistent()
        if !backgrounds {
            let css = "*, *::before, *::after { background: transparent !important; background-image: none !important; box-shadow: none !important; }"
            let js = "var s=document.createElement('style');s.textContent=\(String(reflecting: css));document.documentElement.appendChild(s);"
            config.userContentController.addUserScript(WKUserScript(source: js, injectionTime: .atDocumentEnd, forMainFrameOnly: true))
        }
        let web = WKWebView(frame: CGRect(x: 0, y: 0, width: cssWidth, height: 1000), configuration: config)
        web.navigationDelegate = self
        try await withCheckedThrowingContinuation { (cont: CheckedContinuation<Void, Error>) in
            continuation = cont
            web.loadFileURL(url, allowingReadAccessTo: url.deletingLastPathComponent())
            DispatchQueue.main.asyncAfter(deadline: .now() + 30) { [weak self] in
                self?.finish(ConversionError.other("網頁載入逾時（30 秒）。"))
            }
        }
        if Task.isCancelled { throw ConversionError.cancelled }
        // 等字型與圖片就緒
        _ = try? await web.evaluateJavaScript("document.fonts ? document.fonts.ready.then(() => 1) : 1")
        let heightValue = try? await web.evaluateJavaScript("Math.max(document.body ? document.body.scrollHeight : 0, document.documentElement.scrollHeight)")
        let height = max(CGFloat((heightValue as? NSNumber)?.doubleValue ?? 1000), 50)
        web.frame = CGRect(x: 0, y: 0, width: cssWidth, height: height)
        let pdfConfig = WKPDFConfiguration()
        pdfConfig.rect = CGRect(x: 0, y: 0, width: cssWidth, height: height)
        do {
            return try await web.pdf(configuration: pdfConfig)
        } catch {
            throw ConversionError.other("WebKit 無法產生 PDF：\(error.localizedDescription)")
        }
    }

    private func finish(_ error: Error?) {
        guard let cont = continuation else { return }
        continuation = nil
        if let error { cont.resume(throwing: error) } else { cont.resume() }
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) { finish(nil) }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        finish(ConversionError.unreadable("網頁載入失敗：\(error.localizedDescription)"))
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        finish(ConversionError.unreadable("網頁載入失敗：\(error.localizedDescription)"))
    }

    /// 把長頁面切成指定紙張大小的多頁；切點盡量落在文字行之間。
    nonisolated static func paginate(_ data: Data, pageSize: CGSize, margin: CGFloat, title: String, to out: URL) throws -> Int {
        guard let provider = CGDataProvider(data: data as CFData), let doc = CGPDFDocument(provider), let tall = doc.page(at: 1) else {
            throw ConversionError.other("WebKit 產生的 PDF 無法讀取。")
        }
        let box = tall.getBoxRect(.mediaBox)
        let content = CGRect(x: margin, y: margin, width: pageSize.width - margin * 2, height: pageSize.height - margin * 2)
        let scale = content.width / max(box.width, 1)
        let sliceHeight = content.height / scale
        // 文字行的上下範圍（長頁面座標）
        var lines: [(CGFloat, CGFloat)] = []
        if let kit = PDFDocument(data: data), let page = kit.page(at: 0), let all = page.selection(for: page.bounds(for: .mediaBox)) {
            lines = all.selectionsByLine().map { let b = $0.bounds(for: page); return (b.minY, b.maxY) }
        }
        guard let ctx = CGContext(out as CFURL, mediaBox: nil, [kCGPDFContextCreator: "QUICK_SEEMD", kCGPDFContextTitle: title] as CFDictionary) else {
            throw ConversionError.writeFailed("無法建立 PDF。")
        }
        var top = box.maxY
        var count = 0
        while top > box.minY + 0.5 && count < 2000 {
            var bottom = max(top - sliceHeight, box.minY)
            if bottom > box.minY {
                // 若切點穿過某一行文字，改在那一行上方切開（這一頁至少保留 75% 內容）
                let ceiling = top - sliceHeight * 0.75
                for _ in 0..<8 {
                    guard let crossing = lines.first(where: { $0.0 < bottom - 0.5 && $0.1 > bottom + 0.5 }) else { break }
                    let candidate = crossing.1 + 1
                    if candidate > ceiling { break }
                    bottom = candidate
                }
            }
            var media = CGRect(origin: .zero, size: pageSize)
            ctx.beginPage(mediaBox: &media)
            ctx.saveGState()
            ctx.clip(to: CGRect(x: content.minX, y: content.maxY - (top - bottom) * scale, width: content.width, height: (top - bottom) * scale))
            ctx.translateBy(x: content.minX, y: content.maxY)
            ctx.scaleBy(x: scale, y: scale)
            ctx.translateBy(x: -box.minX, y: -top)
            ctx.drawPDFPage(tall)
            ctx.restoreGState()
            ctx.endPage()
            count += 1
            top = bottom
        }
        ctx.closePDF()
        return count
    }
}

private struct PTHTMLPanel: View {
    @ObservedObject var session: PTHTMLSession

    var body: some View {
        VStack(alignment: .leading, spacing: 20) {
            OptionGroup(title: "紙張", systemImage: "doc") {
                VStack(alignment: .leading, spacing: 10) {
                    ChoiceChips(selection: $session.options.paper, choices: [
                        OptionChoice(value: .a4, title: "A4"),
                        OptionChoice(value: .letter, title: "Letter"),
                    ])
                    SegmentedOption(selection: $session.options.landscape, choices: [
                        OptionChoice(value: false, title: "直向"),
                        OptionChoice(value: true, title: "橫向"),
                    ])
                }
            }
            OptionGroup(title: "邊界", systemImage: "square.dashed") {
                SegmentedOption(selection: $session.options.margin, choices: [
                    OptionChoice(value: 0.0, title: "無"),
                    OptionChoice(value: 18.0, title: "窄"),
                    OptionChoice(value: 36.0, title: "標準"),
                    OptionChoice(value: 54.0, title: "寬"),
                ])
            }
            OptionGroup(title: "外觀", systemImage: "paintbrush") {
                OptionToggle(title: "列印背景色與背景圖片", subtitle: "關閉時只保留文字與圖片，較省墨水", isOn: $session.options.backgrounds)
            }
            OptionNote(text: "會讀取同一個資料夾中的圖片與樣式表；網頁引用的網路資源需要連網才會顯示。頁面在文字行之間自動分頁。")
        }
    }
}
