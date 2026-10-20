import Foundation

extension QuickActionManifest {
    /// 右鍵選單：Media 類動作（影片 public.movie、音訊 public.audio）
    static let media: [QuickActionInfo] = [
        QuickActionInfo(id: "video.convert-mp4", title: "轉成 MP4", symbol: "arrow.triangle.2.circlepath", group: "影音",
                        inputTypes: ["public.movie"], toolID: "video.convert"),
        QuickActionInfo(id: "video.compress-medium", title: "壓縮影片（建議）", symbol: "arrow.down.right.and.arrow.up.left", group: "影音",
                        inputTypes: ["public.movie"], toolID: "video.compress"),
        QuickActionInfo(id: "video.gif-default", title: "轉成 GIF 動畫", symbol: "photo.stack", group: "影音",
                        inputTypes: ["public.movie"], toolID: "video.to-gif"),
        QuickActionInfo(id: "video.audio-m4a", title: "擷取音訊（M4A）", symbol: "waveform", group: "影音",
                        inputTypes: ["public.movie"], toolID: "video.extract-audio"),
        QuickActionInfo(id: "video.remove-audio", title: "移除聲音", symbol: "speaker.slash", group: "影音",
                        inputTypes: ["public.movie"], toolID: "video.mute"),
        QuickActionInfo(id: "video.rotate-right", title: "影片向右旋轉 90°", symbol: "rotate.right", group: "影音",
                        inputTypes: ["public.movie"], toolID: "video.rotate"),
        QuickActionInfo(id: "audio.convert-m4a", title: "轉成 M4A", symbol: "music.note", group: "影音",
                        inputTypes: ["public.audio"], toolID: "audio.convert"),
        QuickActionInfo(id: "audio.convert-wav", title: "轉成 WAV", symbol: "waveform.path", group: "影音",
                        inputTypes: ["public.audio"], toolID: "audio.convert"),
    ]
}
