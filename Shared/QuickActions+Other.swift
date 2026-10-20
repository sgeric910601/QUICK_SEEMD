import Foundation

extension QuickActionManifest {
    /// 右鍵選單：其他類動作
    static let other: [QuickActionInfo] = [
        QuickActionInfo(id: "other.zip", title: "壓縮成 ZIP", symbol: "doc.zipper", group: "其他",
                        inputTypes: ["public.item"], toolID: "other.compress", combinesFiles: true),
        QuickActionInfo(id: "other.unzip", title: "解壓縮", symbol: "archivebox", group: "其他",
                        inputTypes: ["public.archive", "public.zip-archive", "org.gnu.gnu-zip-archive"], toolID: "other.extract"),
        QuickActionInfo(id: "other.to-utf8", title: "轉成 UTF-8 文字", symbol: "character.textbox", group: "其他",
                        inputTypes: ["public.plain-text"], toolID: "other.text-encoding"),
        QuickActionInfo(id: "other.read-qr", title: "讀取 QR Code", symbol: "qrcode.viewfinder", group: "其他",
                        inputTypes: ["public.image"], toolID: "other.read-qr"),
    ]
}
