import SwiftUI

/// 圖片工具。各工具的定義、選項面板與背景工作分別在 IT*Tool.swift；
/// 共用的讀寫、色彩與相片資訊處理在 ITCore.swift。
enum ImageToolCatalog {
    static let tools: [ToolDefinition] = [
        convert,
        compress,
        resize,
        crop,
        rotate,
        removeBackground,
        watermark,
        blurFaces,
        ocr,
        stripMetadata,
        upscale,
        makeIcon,
    ]
}
