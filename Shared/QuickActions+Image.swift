import Foundation

extension QuickActionManifest {
    /// 右鍵選單：Image 類動作（對應 ImageToolCatalog 各工具的 quickActions，使用預設選項）
    static let image: [QuickActionInfo] = [
        QuickActionInfo(id: "image.to-jpg", title: "轉成 JPG", symbol: "photo", group: "圖片",
                        inputTypes: ["public.image"], toolID: "image.convert"),
        QuickActionInfo(id: "image.to-png", title: "轉成 PNG", symbol: "photo.on.rectangle", group: "圖片",
                        inputTypes: ["public.image"], toolID: "image.convert"),
        QuickActionInfo(id: "image.to-heic", title: "轉成 HEIC", symbol: "photo.stack", group: "圖片",
                        inputTypes: ["public.image"], toolID: "image.convert"),
        QuickActionInfo(id: "image.compress-recommended", title: "壓縮圖片（建議）", symbol: "arrow.down.right.and.arrow.up.left",
                        group: "圖片", inputTypes: ["public.image"], toolID: "image.compress"),
        QuickActionInfo(id: "image.resize-half", title: "縮小成 50%", symbol: "arrow.down.left.and.arrow.up.right", group: "圖片",
                        inputTypes: ["public.image"], toolID: "image.resize"),
        QuickActionInfo(id: "image.remove-bg-transparent", title: "移除背景", symbol: "person.and.background.dotted", group: "圖片",
                        inputTypes: ["public.image"], toolID: "image.remove-bg"),
        QuickActionInfo(id: "image.rotate-right", title: "向右旋轉 90°", symbol: "rotate.right", group: "圖片",
                        inputTypes: ["public.image"], toolID: "image.rotate"),
        QuickActionInfo(id: "image.strip-location", title: "移除相片位置資訊", symbol: "location.slash", group: "圖片",
                        inputTypes: ["public.image"], toolID: "image.strip-metadata"),
    ]
}
