import AppKit
import CoreGraphics
import Foundation
import PDFKit

/// PDF → 圖片（每頁一張）、純文字（無文字層時自動 OCR）、RTF／Word／HTML。
enum CVPDFConverter {
    private static func openCG(_ url: URL) throws -> CGPDFDocument {
        guard let doc = CGPDFDocument(url as CFURL) else { throw ConversionError.unreadable("PDF 檔案損毀或格式不正確。") }
        if doc.isEncrypted && !doc.isUnlocked && !doc.unlockWithPassword("") { throw ConversionError.passwordProtected }
        if doc.numberOfPages == 0 { throw ConversionError.emptyContent("PDF 沒有任何頁面。") }
        return doc
    }

    /// 以指定 DPI 將一頁 PDF 畫成點陣圖（白底，套用頁面旋轉）。
    static func render(page: CGPDFPage, dpi: Double, opaque: Bool = true) -> CGImage? {
        let box = page.getBoxRect(.cropBox)
        let rotated = abs(page.rotationAngle) % 180 == 90
        let ptSize = CGSize(width: rotated ? box.height : box.width, height: rotated ? box.width : box.height)
        let scale = dpi / 72.0
        // 避免超大頁面造成記憶體爆量：最長邊限制 12000 px
        let maxSide = 12000.0
        let effectiveScale = min(scale, maxSide / max(ptSize.width, ptSize.height, 1))
        let pw = max(1, Int((ptSize.width * effectiveScale).rounded()))
        let ph = max(1, Int((ptSize.height * effectiveScale).rounded()))
        guard let ctx = CGContext(data: nil, width: pw, height: ph, bitsPerComponent: 8, bytesPerRow: 0,
                                  space: CGColorSpace(name: CGColorSpace.sRGB)!,
                                  bitmapInfo: opaque ? CGImageAlphaInfo.noneSkipLast.rawValue : CGImageAlphaInfo.premultipliedLast.rawValue)
        else { return nil }
        if opaque {
            ctx.setFillColor(CGColor(gray: 1, alpha: 1))
            ctx.fill(CGRect(x: 0, y: 0, width: pw, height: ph))
        }
        ctx.interpolationQuality = .high
        ctx.setRenderingIntent(.defaultIntent)
        ctx.scaleBy(x: effectiveScale, y: effectiveScale)
        ctx.concatenate(page.getDrawingTransform(.cropBox, rect: CGRect(origin: .zero, size: ptSize), rotate: 0, preserveAspectRatio: true))
        ctx.drawPDFPage(page)
        return ctx.makeImage()
    }

    /// PDF → 每頁一張圖片。只有一頁時直接輸出單檔；多頁時放進「檔名-PNG」資料夾。
    static func toImages(_ input: URL, format: ConversionFormat, options: ConversionOptions, workDir: URL,
                         progress: @Sendable (Double) -> Void) throws -> [URL] {
        let doc = try openCG(input)
        let base = CVFileNaming.baseName(of: input)
        let count = doc.numberOfPages
        let ext = format.fileExtension
        if count == 1, let page = doc.page(at: 1) {
            guard let img = render(page: page, dpi: options.pdfRenderDPI, opaque: format.id != "png" && format.id != "tiff") else {
                throw ConversionError.other("PDF 頁面繪製失敗。")
            }
            let out = workDir.appendingPathComponent(CVFileNaming.fileName(base, ext))
            try CVImageConverter.write(scaled(img, maxPixel: options.imageMaxPixelSize), to: out, formatID: format.id, quality: options.imageQuality)
            progress(1)
            return [out]
        }
        let folder = workDir.appendingPathComponent("\(base)-\(format.displayName.components(separatedBy: " ").first ?? ext.uppercased())", isDirectory: true)
        try FileManager.default.createDirectory(at: folder, withIntermediateDirectories: true)
        let digits = max(2, String(count).count)
        for n in 1...count {
            try Task.checkCancellation()
            guard let page = doc.page(at: n) else { continue }
            try autoreleasepool {
                guard let img = render(page: page, dpi: options.pdfRenderDPI, opaque: format.id != "png" && format.id != "tiff") else {
                    throw ConversionError.other("第 \(n) 頁繪製失敗。")
                }
                let number = String(repeating: "0", count: max(0, digits - String(n).count)) + String(n)
                let out = folder.appendingPathComponent(CVFileNaming.fileName("\(base)-\(number)", ext))
                try CVImageConverter.write(scaled(img, maxPixel: options.imageMaxPixelSize), to: out, formatID: format.id, quality: options.imageQuality)
            }
            progress(Double(n) / Double(count))
        }
        return [folder]
    }

    private static func scaled(_ image: CGImage, maxPixel: Int?) -> CGImage {
        guard let maxPixel, max(image.width, image.height) > maxPixel else { return image }
        let s = Double(maxPixel) / Double(max(image.width, image.height))
        let w = max(1, Int(Double(image.width) * s)), h = max(1, Int(Double(image.height) * s))
        guard let ctx = CGContext(data: nil, width: w, height: h, bitsPerComponent: 8, bytesPerRow: 0,
                                  space: CGColorSpace(name: CGColorSpace.sRGB)!, bitmapInfo: CGImageAlphaInfo.premultipliedLast.rawValue)
        else { return image }
        ctx.interpolationQuality = .high
        ctx.draw(image, in: CGRect(x: 0, y: 0, width: w, height: h))
        return ctx.makeImage() ?? image
    }

    /// PDF → 純文字。若整份 PDF 沒有文字層（掃描檔），自動改用 OCR。
    static func toText(_ input: URL, forceOCR: Bool, workDir: URL, progress: @Sendable (Double) -> Void) throws -> [URL] {
        let out = workDir.appendingPathComponent(CVFileNaming.fileName(CVFileNaming.baseName(of: input), "txt"))
        guard let doc = PDFDocument(url: input) else { throw ConversionError.unreadable("PDF 檔案損毀或格式不正確。") }
        if doc.isLocked && !doc.unlock(withPassword: "") { throw ConversionError.passwordProtected }
        var pages: [String] = []
        if !forceOCR {
            for i in 0..<doc.pageCount {
                pages.append(doc.page(at: i)?.string ?? "")
            }
        }
        let hasText = pages.contains { !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
        if !hasText {
            let cg = try openCG(input)
            pages = []
            for n in 1...cg.numberOfPages {
                try Task.checkCancellation()
                guard let page = cg.page(at: n), let img = render(page: page, dpi: 220) else { pages.append(""); continue }
                pages.append(try CVImageConverter.recognizeText(in: img))
                progress(Double(n) / Double(cg.numberOfPages))
            }
            if !pages.contains(where: { !$0.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }) {
                throw ConversionError.emptyContent("PDF 中沒有文字，OCR 也辨識不到內容。")
            }
        }
        let text = pages.map { $0.trimmingCharacters(in: .newlines) }.joined(separator: "\n\n\u{0C}\n")
        try Data((text + "\n").utf8).write(to: out)
        progress(1)
        return [out]
    }

    /// PDF → 帶格式文字（各頁之間插入分頁）。
    static func attributedString(_ input: URL) throws -> NSAttributedString {
        guard let doc = PDFDocument(url: input) else { throw ConversionError.unreadable("PDF 檔案損毀或格式不正確。") }
        if doc.isLocked && !doc.unlock(withPassword: "") { throw ConversionError.passwordProtected }
        let result = NSMutableAttributedString()
        for i in 0..<doc.pageCount {
            guard let page = doc.page(at: i), let attr = page.attributedString else { continue }
            if result.length > 0 { result.append(NSAttributedString(string: "\n\u{0C}")) }
            result.append(attr)
        }
        if result.string.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
            // 掃描檔：改用 OCR 取得文字
            let cg = try openCG(input)
            let font = NSFont(name: "Helvetica Neue", size: 12) ?? NSFont.systemFont(ofSize: 12)
            for n in 1...cg.numberOfPages {
                try Task.checkCancellation()
                guard let page = cg.page(at: n), let img = render(page: page, dpi: 220) else { continue }
                let text = try CVImageConverter.recognizeText(in: img)
                if result.length > 0 { result.append(NSAttributedString(string: "\n\u{0C}")) }
                result.append(NSAttributedString(string: text, attributes: [.font: font, .foregroundColor: NSColor.black]))
            }
            if result.string.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty {
                throw ConversionError.emptyContent("PDF 中沒有文字，OCR 也辨識不到內容。")
            }
        }
        return result
    }
}
