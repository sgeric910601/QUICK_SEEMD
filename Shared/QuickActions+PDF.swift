import Foundation

extension QuickActionManifest {
    /// 右鍵選單：PDF 類動作（對應 PDFToolCatalog 各工具的 quickActions，以預設選項執行）
    static let pdf: [QuickActionInfo] = [
        QuickActionInfo(id: "pdf.compress-recommended", title: "壓縮 PDF（建議）", symbol: "arrow.down.right.and.arrow.up.left",
                        group: "PDF", inputTypes: ["com.adobe.pdf"], toolID: "pdf.compress"),
        QuickActionInfo(id: "pdf.merge-pdfs", title: "合併成一個 PDF", symbol: "rectangle.stack.badge.plus",
                        group: "PDF", inputTypes: ["com.adobe.pdf"], toolID: "pdf.merge", combinesFiles: true),
        QuickActionInfo(id: "pdf.to-jpg", title: "轉成 JPG 圖片", symbol: "photo.on.rectangle.angled",
                        group: "PDF", inputTypes: ["com.adobe.pdf"], toolID: "pdf.to-images"),
        QuickActionInfo(id: "pdf.split-pages", title: "每一頁拆成一個 PDF", symbol: "scissors",
                        group: "PDF", inputTypes: ["com.adobe.pdf"], toolID: "pdf.split"),
        QuickActionInfo(id: "pdf.ocr-searchable", title: "轉成可搜尋的 PDF（OCR）", symbol: "text.viewfinder",
                        group: "PDF", inputTypes: ["com.adobe.pdf"], toolID: "pdf.ocr"),
        QuickActionInfo(id: "pdf.rotate-right", title: "向右旋轉 90°", symbol: "rotate.right",
                        group: "PDF", inputTypes: ["com.adobe.pdf"], toolID: "pdf.rotate"),
        QuickActionInfo(id: "pdf.add-page-numbers", title: "加上頁碼", symbol: "number.square",
                        group: "PDF", inputTypes: ["com.adobe.pdf"], toolID: "pdf.page-numbers"),
        QuickActionInfo(id: "pdf.images-to-pdf", title: "合併成一個 PDF", symbol: "photo.stack",
                        group: "PDF", inputTypes: ["public.image"], toolID: "pdf.from-images", combinesFiles: true),
    ]
}
