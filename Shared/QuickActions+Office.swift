import Foundation

extension QuickActionManifest {
    /// 右鍵選單：Office 類動作（主程式依同樣的代碼在 OfficeToolCatalog 找到對應的工作，使用自動選擇的最佳引擎）
    static let office: [QuickActionInfo] = [
        QuickActionInfo(id: "office.to-pdf", title: "轉成 PDF", symbol: "doc.richtext", group: "Office",
                        inputTypes: officeWordTypes + officeExcelTypes + officePowerPointTypes, toolID: "office.word-to-pdf"),
        QuickActionInfo(id: "office.pdf-to-docx", title: "轉成 Word（.docx）", symbol: "doc.text", group: "Office",
                        inputTypes: ["com.adobe.pdf"], toolID: "office.pdf-to-word"),
        QuickActionInfo(id: "office.pdf-to-xlsx", title: "轉成 Excel（.xlsx）", symbol: "tablecells", group: "Office",
                        inputTypes: ["com.adobe.pdf"], toolID: "office.pdf-to-excel"),
        QuickActionInfo(id: "office.pdf-to-pptx", title: "轉成 PowerPoint（.pptx）", symbol: "play.rectangle", group: "Office",
                        inputTypes: ["com.adobe.pdf"], toolID: "office.pdf-to-ppt"),
        QuickActionInfo(id: "office.doc-to-docx", title: "升級成 .docx", symbol: "arrow.up.doc", group: "Office",
                        inputTypes: ["com.microsoft.word.doc"], toolID: "office.word-convert"),
        QuickActionInfo(id: "office.xls-to-xlsx", title: "升級成 .xlsx", symbol: "arrow.up.doc", group: "Office",
                        inputTypes: ["com.microsoft.excel.xls"], toolID: "office.excel-convert"),
        QuickActionInfo(id: "office.ppt-to-png", title: "轉成 PNG 圖片（每張投影片）", symbol: "photo.on.rectangle", group: "Office",
                        inputTypes: officePowerPointTypes, toolID: "office.ppt-convert"),
        QuickActionInfo(id: "office.ppt-to-jpg", title: "轉成 JPG 圖片（每張投影片）", symbol: "photo", group: "Office",
                        inputTypes: officePowerPointTypes, toolID: "office.ppt-convert"),
        QuickActionInfo(id: "office.ppt-to-pptx", title: "升級成 .pptx", symbol: "arrow.up.doc", group: "Office",
                        inputTypes: ["com.microsoft.powerpoint.ppt"], toolID: "office.ppt-convert"),
        QuickActionInfo(id: "office.excel-to-csv", title: "轉成 CSV", symbol: "tablecells", group: "Office",
                        inputTypes: officeExcelTypes, toolID: "office.excel-convert"),
    ]

    private static let officeWordTypes = [
        "com.microsoft.word.doc", "org.openxmlformats.wordprocessingml.document",
        "org.openxmlformats.wordprocessingml.document.macroenabled", "public.rtf", "org.oasis-open.opendocument.text",
    ]
    private static let officeExcelTypes = [
        "com.microsoft.excel.xls", "org.openxmlformats.spreadsheetml.sheet",
        "org.openxmlformats.spreadsheetml.sheet.macroenabled", "org.oasis-open.opendocument.spreadsheet",
    ]
    private static let officePowerPointTypes = [
        "com.microsoft.powerpoint.ppt", "org.openxmlformats.presentationml.presentation",
        "org.openxmlformats.presentationml.presentation.macroenabled", "org.openxmlformats.presentationml.slideshow",
        "org.oasis-open.opendocument.presentation",
    ]
}
