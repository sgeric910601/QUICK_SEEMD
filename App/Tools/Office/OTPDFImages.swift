import CoreGraphics
import Foundation
import PDFKit

// 找出 PDF 頁面上圖片的位置（掃描內容串流的 cm／q／Q／Do 運算子），再從頁面畫面裁出那一塊。
// 這樣不必解碼各種圖片格式，裁出來的就是使用者在 PDF 上看到的樣子。

private final class OTImageScanState {
    var ctm = CGAffineTransform.identity
    var stack: [CGAffineTransform] = []
    var rects: [CGRect] = []
    var depth = 0
    var table: CGPDFOperatorTableRef?
}

enum OTPDFImages {
    /// 頁面座標（未旋轉、左下原點）中每張圖的外框。
    static func placements(_ page: CGPDFPage) -> [CGRect] {
        guard let table = CGPDFOperatorTableCreate() else { return [] }
        let state = OTImageScanState()
        state.table = table
        CGPDFOperatorTableSetCallback(table, "q") { _, info in
            guard let info else { return }
            let s = Unmanaged<OTImageScanState>.fromOpaque(info).takeUnretainedValue()
            s.stack.append(s.ctm)
        }
        CGPDFOperatorTableSetCallback(table, "Q") { _, info in
            guard let info else { return }
            let s = Unmanaged<OTImageScanState>.fromOpaque(info).takeUnretainedValue()
            if let t = s.stack.popLast() { s.ctm = t }
        }
        CGPDFOperatorTableSetCallback(table, "cm") { scanner, info in
            guard let info else { return }
            let s = Unmanaged<OTImageScanState>.fromOpaque(info).takeUnretainedValue()
            var v = [CGPDFReal](repeating: 0, count: 6)
            for i in (0..<6).reversed() { if !CGPDFScannerPopNumber(scanner, &v[i]) { return } }
            s.ctm = CGAffineTransform(a: v[0], b: v[1], c: v[2], d: v[3], tx: v[4], ty: v[5]).concatenating(s.ctm)
        }
        CGPDFOperatorTableSetCallback(table, "EI") { _, info in
            guard let info else { return }
            let s = Unmanaged<OTImageScanState>.fromOpaque(info).takeUnretainedValue()
            s.rects.append(CGRect(x: 0, y: 0, width: 1, height: 1).applying(s.ctm))
        }
        CGPDFOperatorTableSetCallback(table, "Do") { scanner, info in
            guard let info else { return }
            let s = Unmanaged<OTImageScanState>.fromOpaque(info).takeUnretainedValue()
            var namePtr: UnsafePointer<CChar>?
            guard CGPDFScannerPopName(scanner, &namePtr), let namePtr else { return }
            let content = CGPDFScannerGetContentStream(scanner)
            guard let object = CGPDFContentStreamGetResource(content, "XObject", namePtr) else { return }
            var stream: CGPDFStreamRef?
            guard CGPDFObjectGetValue(object, .stream, &stream), let stream, let dict = CGPDFStreamGetDictionary(stream) else { return }
            var subtype: UnsafePointer<CChar>?
            guard CGPDFDictionaryGetName(dict, "Subtype", &subtype), let subtype else { return }
            switch String(cString: subtype) {
            case "Image":
                s.rects.append(CGRect(x: 0, y: 0, width: 1, height: 1).applying(s.ctm))
            case "Form" where s.depth < 8:
                var matrix = CGAffineTransform.identity
                var array: CGPDFArrayRef?
                if CGPDFDictionaryGetArray(dict, "Matrix", &array), let array, CGPDFArrayGetCount(array) == 6 {
                    var m = [CGPDFReal](repeating: 0, count: 6)
                    for i in 0..<6 { _ = CGPDFArrayGetNumber(array, i, &m[i]) }
                    matrix = CGAffineTransform(a: m[0], b: m[1], c: m[2], d: m[3], tx: m[4], ty: m[5])
                }
                var resources: CGPDFDictionaryRef?
                if !CGPDFDictionaryGetDictionary(dict, "Resources", &resources) { resources = dict }
                guard let resources, let table = s.table else { return }
                let saved = s.ctm
                let savedStack = s.stack
                s.ctm = matrix.concatenating(s.ctm)
                s.depth += 1
                let inner = CGPDFContentStreamCreateWithStream(stream, resources, content)
                let sub = CGPDFScannerCreate(inner, table, info)
                CGPDFScannerScan(sub)
                s.depth -= 1
                s.ctm = saved
                s.stack = savedStack
            default:
                break
            }
        }
        let content = CGPDFContentStreamCreateWithPage(page)
        let scanner = CGPDFScannerCreate(content, table, Unmanaged.passUnretained(state).toOpaque())
        CGPDFScannerScan(scanner)
        let crop = page.getBoxRect(.cropBox)
        return state.rects.map { $0.intersection(crop) }.filter { !$0.isNull && $0.width > 1 && $0.height > 1 }
    }

    /// 頁面上的圖片（顯示座標＋JPEG 資料）。太小的、蓋滿整頁的（背景或掃描底圖）會略過。
    static func extract(_ page: PDFPage, dpi: Double = 200) -> [OTImageBlock] {
        guard let cg = page.pageRef else { return [] }
        let size = OTPDFText.displaySize(page)
        let pageArea = size.width * size.height
        var rects = placements(cg).map { OTPDFText.toDisplay($0, page: page) }
            .filter { $0.width >= 16 && $0.height >= 16 && $0.width * $0.height < pageArea * 0.85 }
        // 重疊很多的合成一張（例如由好幾塊拼成的圖）
        var merged: [CGRect] = []
        for r in rects.sorted(by: { $0.width * $0.height > $1.width * $1.height }) {
            if let i = merged.firstIndex(where: { $0.intersection(r).width * $0.intersection(r).height > r.width * r.height * 0.5 }) {
                merged[i] = merged[i].union(r)
            } else {
                merged.append(r)
            }
        }
        rects = merged.sorted { $0.minY < $1.minY }
        guard !rects.isEmpty, let image = CVPDFConverter.render(page: cg, dpi: dpi) else { return [] }
        let sx = CGFloat(image.width) / size.width, sy = CGFloat(image.height) / size.height
        var result: [OTImageBlock] = []
        for r in rects {
            let px = CGRect(x: r.minX * sx, y: r.minY * sy, width: r.width * sx, height: r.height * sy).integral
            guard let crop = image.cropping(to: px), let data = try? OTPDFConvert.encode(crop, jpegQuality: 0.9) else { continue }
            result.append(OTImageBlock(frame: r, data: data))
        }
        return result
    }
}
