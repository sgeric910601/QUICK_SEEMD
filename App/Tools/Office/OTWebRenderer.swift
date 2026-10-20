import AppKit
import PDFKit
import WebKit

// macOS 內建引擎的核心：系統的 系統預覽 Office 產生器（OfficeImport）能把 Word／Excel／PowerPoint
// 轉成 HTML（`qlmanage -p -o`）。我們把這份 HTML 載入離屏的 WKWebView，再分頁輸出成向量 PDF。
// 全程離線：網頁內容禁止連網（避免文件裡的遠端圖片洩漏開檔紀錄）。

enum OTSystemPreview {
    /// 產生 系統預覽 預覽（HTML 與附件），回傳 `<名稱>.qlpreview` 資料夾；系統無法產生 HTML 時回傳 nil。
    static func preview(_ input: URL, work: URL) async throws -> URL? {
        try FileManager.default.createDirectory(at: work, withIntermediateDirectories: true)
        let staged = work.appendingPathComponent("source." + OTKind.ext(input))
        if !FileManager.default.fileExists(atPath: staged.path) {
            try FileManager.default.copyItem(at: input, to: staged)
        }
        let outDir = work.appendingPathComponent("ql", isDirectory: true)
        try FileManager.default.createDirectory(at: outDir, withIntermediateDirectories: true)
        let output = try await OTProcess.run("/usr/bin/qlmanage", ["-p", "-o", outDir.path, staged.path], timeout: 180)
        let dir = outDir.appendingPathComponent(staged.lastPathComponent + ".qlpreview", isDirectory: true)
        let html = dir.appendingPathComponent("Preview.html")
        guard FileManager.default.fileExists(atPath: html.path) else {
            if !output.succeeded { return nil }
            return nil
        }
        return dir
    }
}

/// 字型對照：Office 常用但 macOS 沒有的字型，在 CSS 裡補上外觀相近的系統字型。
enum OTFontFallback {
    static let table: [(names: [String], fallback: [String])] = [
        (["calibri", "calibri light", "aptos", "aptos display", "aptos narrow", "segoe ui", "segoe ui light", "corbel", "candara", "franklin gothic book", "century gothic"],
         ["Helvetica Neue", "PingFang TC", "sans-serif"]),
        (["cambria", "cambria math", "constantia", "book antiqua", "bookman old style", "garamond", "calisto mt"],
         ["Georgia", "Songti TC", "serif"]),
        (["consolas", "lucida console", "cascadia code", "cascadia mono"], ["Menlo", "monospace"]),
        (["microsoft jhenghei", "microsoft jhenghei ui", "微軟正黑體", "微軟正黑體 ui"], ["PingFang TC", "Heiti TC", "sans-serif"]),
        (["microsoft yahei", "microsoft yahei ui", "微软雅黑", "微軟雅黑", "dengxian", "等线"], ["PingFang SC", "Heiti SC", "sans-serif"]),
        (["pmingliu", "mingliu", "新細明體", "細明體", "pmingliu-extb", "mingliu_hkscs", "細明體_hkscs"], ["Songti TC", "LiSong Pro", "serif"]),
        (["dfkai-sb", "標楷體", "biaukai"], ["BiauKai", "Kaiti TC", "STKaiti", "Songti TC", "serif"]),
        (["simsun", "nsimsun", "宋体", "新宋体"], ["Songti SC", "STSong", "serif"]),
        (["simhei", "黑体"], ["Heiti SC", "PingFang SC", "sans-serif"]),
        (["kaiti", "楷体", "kaiti_gb2312"], ["Kaiti SC", "STKaiti", "serif"]),
        (["ms mincho", "ｍｓ 明朝", "ms pmincho", "ｍｓ ｐ明朝", "yu mincho", "游明朝"], ["Hiragino Mincho ProN", "serif"]),
        (["ms gothic", "ｍｓ ゴシック", "ms pgothic", "ｍｓ ｐゴシック", "meiryo", "メイリオ", "yu gothic", "游ゴシック"], ["Hiragino Sans", "sans-serif"]),
        (["malgun gothic", "맑은 고딕", "gulim", "굴림", "dotum", "돋움"], ["Apple SD Gothic Neo", "sans-serif"]),
        (["batang", "바탕", "gungsuh"], ["AppleMyungjo", "serif"]),
    ]

    /// 預設字型（文件沒指定字型時）。
    static let defaultStack = "\"Helvetica Neue\", \"PingFang TC\", sans-serif"

    /// 把 `font-family: X` 補上後援字型。
    static func patch(_ html: String) -> String {
        guard let regex = try? NSRegularExpression(pattern: #"font-family\s*:\s*([^;}"<>]*(?:"[^"]*"[^;}"<>]*)*)"#, options: [.caseInsensitive]) else { return html }
        let ns = html as NSString
        let result = NSMutableString(string: html)
        for match in regex.matches(in: html, range: NSRange(location: 0, length: ns.length)).reversed() {
            let value = ns.substring(with: match.range(at: 1)).trimmingCharacters(in: .whitespaces)
            if value.isEmpty || value.contains("sans-serif") || value.contains("monospace") || value.hasSuffix("serif") { continue }
            let first = value.split(separator: ",").first.map {
                $0.trimmingCharacters(in: CharacterSet(charactersIn: " \"'")).lowercased()
            } ?? ""
            let extra = table.first { $0.names.contains(first) }?.fallback ?? ["\"PingFang TC\"", "sans-serif"]
            let stack = extra.map { $0.hasSuffix("serif") || $0.hasSuffix("monospace") || $0.hasPrefix("\"") ? $0 : "\"\($0)\"" }
            result.replaceCharacters(in: match.range(at: 1), with: value + ", " + stack.joined(separator: ", "))
        }
        return result as String
    }
}

/// 離屏 WKWebView：載入 HTML，再把指定區域擷取成向量 PDF 頁面。
/// 刻意不建立任何 NSWindow，也不用 NSPrintOperation：在 App 裡多一個視窗會讓 SwiftUI 重建主選單，
/// 無螢幕／背景模式下甚至會卡住主執行緒。分頁由 `pageBreaks` 依文字行與圖片位置自行計算。
@MainActor
final class OTWebRenderer: NSObject, WKNavigationDelegate {
    private let webView: WKWebView
    private var loadContinuation: CheckedContinuation<Void, Error>?
    private static var blockRules: WKContentRuleList?

    init(width: CGFloat, height: CGFloat = 800) async {
        _ = NSApplication.shared
        let config = WKWebViewConfiguration()
        config.websiteDataStore = .nonPersistent()
        config.suppressesIncrementalRendering = true
        if Self.blockRules == nil {
            Self.blockRules = try? await WKContentRuleListStore.default().compileContentRuleList(
                forIdentifier: "QuickSeemdOfficeOffline",
                encodedContentRuleList: #"[{"trigger":{"url-filter":"^(https?|ftp|wss?)://"},"action":{"type":"block"}}]"#)
        }
        if let rules = Self.blockRules { config.userContentController.add(rules) }
        let frame = NSRect(x: 0, y: 0, width: max(width, 100), height: max(height, 100))
        webView = WKWebView(frame: frame, configuration: config)
        super.init()
        webView.navigationDelegate = self
        webView.setValue(false, forKey: "drawsBackground")
        webView.appearance = NSAppearance(named: .aqua)
    }

    func close() {
        webView.navigationDelegate = nil
        webView.stopLoading()
    }

    func resize(width: CGFloat, height: CGFloat) {
        let size = NSSize(width: max(width, 100), height: max(height, 100))
        webView.frame = NSRect(origin: .zero, size: size)
    }

    // MARK: 載入

    func load(file: URL, readAccess: URL) async throws {
        try await waitForLoad { webView.loadFileURL(file, allowingReadAccessTo: readAccess) }
    }

    func load(html: String, baseURL: URL?) async throws {
        try await waitForLoad { webView.loadHTMLString(html, baseURL: baseURL) }
    }

    private var loadGeneration = 0

    private func waitForLoad(_ start: () -> Void) async throws {
        loadGeneration += 1
        let generation = loadGeneration
        try await withCheckedThrowingContinuation { (c: CheckedContinuation<Void, Error>) in
            loadContinuation = c
            start()
            Task { @MainActor [weak self] in
                try? await Task.sleep(nanoseconds: 60_000_000_000)
                guard let self, self.loadGeneration == generation else { return }
                self.finishLoad(ConversionError.other("載入預覽逾時。"))
            }
        }
        // 等圖片、字型排版完成
        _ = try? await evaluate("document.fonts ? document.fonts.status : 'loaded'")
        try? await Task.sleep(nanoseconds: 150_000_000)
    }

    private func finishLoad(_ error: Error?) {
        guard let c = loadContinuation else { return }
        loadContinuation = nil
        loadGeneration += 1
        if let error { c.resume(throwing: error) } else { c.resume() }
    }

    func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
        finishLoad(nil)
    }

    func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
        finishLoad(ConversionError.unreadable("預覽載入失敗（\(error.localizedDescription)）。"))
    }

    func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
        finishLoad(ConversionError.unreadable("預覽載入失敗（\(error.localizedDescription)）。"))
    }

    func webView(_ webView: WKWebView, decidePolicyFor navigationAction: WKNavigationAction,
                 decisionHandler: @escaping @MainActor @Sendable (WKNavigationActionPolicy) -> Void) {
        let scheme = navigationAction.request.url?.scheme?.lowercased() ?? ""
        decisionHandler(["file", "about", "data", "blob"].contains(scheme) || scheme.isEmpty ? .allow : .cancel)
    }

    // MARK: 操作

    @discardableResult
    func evaluate(_ js: String) async throws -> Any? {
        try await webView.evaluateJavaScript(js)
    }

    func addStyle(_ css: String) async {
        let escaped = css.replacingOccurrences(of: "\\", with: "\\\\").replacingOccurrences(of: "`", with: "\\`")
        _ = try? await evaluate("(function(){var s=document.createElement('style');s.textContent=`\(escaped)`;(document.head||document.documentElement).appendChild(s);return 1})()")
    }

    func json(_ js: String) async -> Any? {
        guard let text = try? await evaluate(js) as? String, let data = text.data(using: .utf8) else { return nil }
        return try? JSONSerialization.jsonObject(with: data)
    }

    /// 找出可以分頁的位置：不會切到文字行、圖片、表格列的高度。回傳每頁的 [起點, 終點)。
    func pageBreaks(pageHeight: CGFloat) async -> [(CGFloat, CGFloat)] {
        let info = await json("""
        (function(){var zones=[];var sy=window.scrollY;
        function add(r){if(r.height>0&&r.width>0){zones.push([r.top+sy,r.bottom+sy])}}
        var w=document.createTreeWalker(document.body,NodeFilter.SHOW_TEXT,null);var n,range=document.createRange();
        while((n=w.nextNode())){if(!n.nodeValue||!n.nodeValue.trim())continue;range.selectNodeContents(n);
        var rs=range.getClientRects();for(var i=0;i<rs.length;i++){add(rs[i])}}
        var els=document.querySelectorAll('img,svg,canvas,video,tr,hr');for(var k=0;k<els.length;k++){add(els[k].getBoundingClientRect())}
        var h=Math.max(document.documentElement.scrollHeight,document.body.scrollHeight);
        return JSON.stringify({h:h,z:zones})})()
        """) as? [String: Any] ?? [:]
        let total = CGFloat((info["h"] as? Double) ?? 0)
        guard total > 1 else { return [] }
        // 合併重疊的區段：區段內不能切
        let zones = ((info["z"] as? [[Double]]) ?? []).map { (CGFloat($0[0]), CGFloat($0[1])) }.sorted { $0.0 < $1.0 }
        var merged: [(CGFloat, CGFloat)] = []
        for z in zones {
            if let last = merged.last, z.0 < last.1 - 0.5 {
                merged[merged.count - 1].1 = max(last.1, z.1)
            } else {
                merged.append(z)
            }
        }
        let contentBottom = min(total, (merged.last?.1 ?? total) + 2)
        var pages: [(CGFloat, CGFloat)] = []
        var start: CGFloat = 0
        while start < contentBottom - 1 {
            let limit = start + pageHeight
            if limit >= contentBottom { pages.append((start, contentBottom)); break }
            // 最後一個在 limit 之前結束、而且不會被切到的位置
            var cut = limit
            if let straddle = merged.first(where: { $0.0 < limit && $0.1 > limit }) {
                cut = straddle.0 - 0.5
                if cut <= start + pageHeight * 0.3 { cut = limit }   // 單一元素比一頁還高：只好切開
            }
            pages.append((start, cut))
            start = cut
        }
        return pages
    }

    /// 擷取網頁中的一塊區域成為一頁向量 PDF（回傳只有一頁的文件）。
    func captureDocument(_ rect: CGRect) async throws -> PDFDocument {
        let config = WKPDFConfiguration()
        config.rect = rect
        let data = try await webView.pdf(configuration: config)
        guard let doc = PDFDocument(data: data), doc.pageCount > 0 else {
            throw ConversionError.writeFailed("無法擷取頁面。")
        }
        return doc
    }

    func capture(_ rect: CGRect) async throws -> PDFPage {
        let doc = try await captureDocument(rect)
        guard let page = doc.page(at: 0) else { throw ConversionError.writeFailed("無法擷取頁面。") }
        return page
    }
}

// MARK: - 紙張

enum OTPaper {
    static let a4 = CGSize(width: 595.28, height: 841.89)
    static let letter = CGSize(width: 612, height: 792)

    static var preferred: CGSize {
        if #available(macOS 13, *), Locale.current.measurementSystem == .us { return letter }
        return a4
    }

    /// 把量到的尺寸對齊到常見紙張（誤差幾點以內）。
    static func snap(_ size: CGSize) -> CGSize {
        let known: [CGSize] = [a4, letter, CGSize(width: 612, height: 1008), CGSize(width: 841.89, height: 1190.55),
                               CGSize(width: 498.9, height: 708.66), CGSize(width: 515.91, height: 728.5), CGSize(width: 419.53, height: 595.28)]
        for k in known {
            if abs(size.width - k.width) < 6 && abs(size.height - k.height) < 10 { return k }
            if abs(size.width - k.height) < 10 && abs(size.height - k.width) < 6 { return CGSize(width: k.height, height: k.width) }
        }
        return size
    }

    static func landscape(_ s: CGSize) -> CGSize { CGSize(width: max(s.width, s.height), height: min(s.width, s.height)) }
    static func portrait(_ s: CGSize) -> CGSize { CGSize(width: min(s.width, s.height), height: max(s.width, s.height)) }
}

// MARK: - 把擷取到的區塊排到紙張上

enum OTPageComposer {
    /// 一頁上的一個區塊：來源頁面（向量）與在紙張上的位置（左上原點）。
    struct Placement {
        let page: PDFPage
        let frame: CGRect
    }

    /// 依序把每頁的區塊畫成 PDF（白底）。
    static func write(pages: [(paper: CGSize, items: [Placement])], to out: URL) throws {
        guard let first = pages.first else { throw ConversionError.emptyContent("沒有可輸出的頁面。") }
        var box = CGRect(origin: .zero, size: first.paper)
        guard let ctx = CGContext(out as CFURL, mediaBox: &box, [kCGPDFContextCreator: "QUICK_SEEMD"] as CFDictionary) else {
            throw ConversionError.writeFailed("無法建立 PDF。")
        }
        for page in pages {
            var media = CGRect(origin: .zero, size: page.paper)
            let info = [kCGPDFContextMediaBox as String: Data(bytes: &media, count: MemoryLayout<CGRect>.size)] as CFDictionary
            ctx.beginPDFPage(info)
            for item in page.items {
                guard let cg = item.page.pageRef else { continue }
                let src = item.page.bounds(for: .mediaBox)
                ctx.saveGState()
                // 轉成左下原點
                let y = page.paper.height - item.frame.maxY
                ctx.translateBy(x: item.frame.minX, y: y)
                ctx.scaleBy(x: item.frame.width / max(src.width, 1), y: item.frame.height / max(src.height, 1))
                ctx.translateBy(x: -src.minX, y: -src.minY)
                ctx.clip(to: src)
                ctx.drawPDFPage(cg)
                ctx.restoreGState()
            }
            ctx.endPDFPage()
        }
        ctx.closePDF()
    }
}
