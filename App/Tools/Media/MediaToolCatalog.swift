import SwiftUI
import UniformTypeIdentifiers

/// 影音工具（影片紫、音訊洋紅）。全部使用 macOS 內建的 AVFoundation／AudioToolbox，不需要網路；
/// MP3／OGG 與 macOS 讀不懂的影片格式（MKV、WebM…）只有在使用者自行安裝 ffmpeg 時才會使用。
enum MediaToolCatalog {
    static let tools: [ToolDefinition] = [
        convertVideo, compressVideo, trimVideo, videoToGIF, extractAudio, muteVideo, rotateVideo,
        mergeVideos, extractFrames, changeSpeed, resizeVideo, convertAudio, trimAudio,
    ]

    private static let movie: [UTType] = [.movie]
    private static let audio: [UTType] = [.audio]

    static let convertVideo = ToolDefinition(
        id: "video.convert",
        category: .media,
        title: "轉換影片格式",
        subtitle: "在 MP4、MOV、M4V、HEVC 與 ProRes 之間轉換，可同時縮小解析度。",
        symbol: "arrow.triangle.2.circlepath",
        tint: ToolPalette.video,
        badge: "MP4",
        inputTypes: movie,
        inputLabel: "影片",
        actionTitle: "轉換影片",
        keywords: ["convert", "video", "mp4", "mov", "m4v", "hevc", "h265", "h264", "prores", "轉檔", "格式"],
        quickActions: ["video.convert-mp4": { MTConvertSession.job(format: .mp4) }],
        makeSession: { MTConvertSession() }
    )

    static let compressVideo = ToolDefinition(
        id: "video.compress",
        category: .media,
        title: "壓縮影片",
        subtitle: "縮小影片檔案，方便傳訊息或寄 Email，並盡量保持畫質。",
        symbol: "arrow.down.right.and.arrow.up.left",
        tint: ToolPalette.video,
        inputTypes: movie,
        inputLabel: "影片",
        actionTitle: "壓縮影片",
        keywords: ["compress", "reduce", "shrink", "smaller", "縮小", "瘦身", "檔案大小"],
        quickActions: ["video.compress-medium": { MTCompressSession.job(MTCompressOptions()) }],
        makeSession: { MTCompressSession() }
    )

    static let trimVideo = ToolDefinition(
        id: "video.trim",
        category: .media,
        title: "剪輯影片",
        subtitle: "預覽影片並拖曳時間軸，保留或刪除其中一段。",
        symbol: "scissors",
        tint: ToolPalette.video,
        inputTypes: movie,
        inputLabel: "影片",
        actionTitle: "剪輯影片",
        keywords: ["trim", "cut", "clip", "剪片", "裁剪", "片段"],
        makeSession: { MTTrimSession(isAudio: false) }
    )

    static let videoToGIF = ToolDefinition(
        id: "video.to-gif",
        category: .media,
        title: "影片轉 GIF",
        subtitle: "把一段影片做成 GIF 動畫，可調整流暢度、寬度與色彩。",
        symbol: "photo.stack",
        tint: ToolPalette.video,
        badge: "GIF",
        inputTypes: movie,
        inputLabel: "影片",
        actionTitle: "製作 GIF",
        keywords: ["gif", "animation", "動畫", "動圖", "meme"],
        quickActions: ["video.gif-default": { MTGifSession.job(MTGifOptions()) }],
        makeSession: { MTGifSession() }
    )

    static let extractAudio = ToolDefinition(
        id: "video.extract-audio",
        category: .media,
        title: "擷取音訊",
        subtitle: "把影片的聲音存成 M4A、WAV、AIFF、FLAC 等音訊檔。",
        symbol: "waveform",
        tint: ToolPalette.video,
        badge: "M4A",
        inputTypes: movie,
        inputLabel: "影片",
        actionTitle: "擷取音訊",
        keywords: ["extract", "audio", "sound", "mp3", "m4a", "wav", "聲音", "音樂", "配樂"],
        quickActions: ["video.audio-m4a": { MTAudioOutputSession.extractJob(MTAudioOutput(format: .aac, bitrate: 192_000)) }],
        makeSession: { MTAudioOutputSession(kind: .extract) }
    )

    static let muteVideo = ToolDefinition(
        id: "video.mute",
        category: .media,
        title: "移除聲音",
        subtitle: "刪除影片的所有聲音，畫質完全不變。",
        symbol: "speaker.slash",
        tint: ToolPalette.video,
        inputTypes: movie,
        inputLabel: "影片",
        actionTitle: "移除聲音",
        keywords: ["mute", "remove audio", "silent", "靜音", "無聲", "消音"],
        quickActions: ["video.remove-audio": { MTMuteSession.job() }],
        makeSession: { MTMuteSession() }
    )

    static let rotateVideo = ToolDefinition(
        id: "video.rotate",
        category: .media,
        title: "旋轉／翻轉影片",
        subtitle: "把拍歪的影片轉正，或左右、上下翻轉。",
        symbol: "rotate.right",
        tint: ToolPalette.video,
        inputTypes: movie,
        inputLabel: "影片",
        actionTitle: "旋轉影片",
        keywords: ["rotate", "flip", "mirror", "轉向", "方向", "鏡像", "直式", "橫式"],
        quickActions: ["video.rotate-right": { MTRotateSession.job(op: .right) }],
        makeSession: { MTRotateSession() }
    )

    static let mergeVideos = ToolDefinition(
        id: "video.merge",
        category: .media,
        title: "合併影片",
        subtitle: "依順序把多個影片接成一個 MP4，尺寸不同時自動補黑邊。",
        symbol: "rectangle.stack.badge.plus",
        tint: ToolPalette.video,
        inputTypes: movie,
        inputLabel: "影片",
        minimumFiles: 2,
        reorderable: true,
        actionTitle: "合併影片",
        keywords: ["merge", "join", "combine", "concat", "串接", "結合", "接起來"],
        makeSession: { MTMergeSession() }
    )

    static let extractFrames = ToolDefinition(
        id: "video.frames",
        category: .media,
        title: "擷取畫面",
        subtitle: "從影片存出 JPG 或 PNG 圖片：指定時間、固定間隔或平均擷取。",
        symbol: "camera.viewfinder",
        tint: ToolPalette.video,
        badge: "JPG",
        inputTypes: movie,
        inputLabel: "影片",
        actionTitle: "擷取畫面",
        keywords: ["frame", "screenshot", "still", "thumbnail", "截圖", "快照", "縮圖", "圖片"],
        makeSession: { MTFramesSession() }
    )

    static let changeSpeed = ToolDefinition(
        id: "video.speed",
        category: .media,
        title: "調整影片速度",
        subtitle: "做出慢動作或快轉影片，可保持人聲音高。",
        symbol: "hare",
        tint: ToolPalette.video,
        inputTypes: movie,
        inputLabel: "影片",
        actionTitle: "調整速度",
        keywords: ["speed", "slow motion", "fast forward", "timelapse", "慢動作", "快轉", "加速", "倍速"],
        makeSession: { MTSpeedSession() }
    )

    static let resizeVideo = ToolDefinition(
        id: "video.resize",
        category: .media,
        title: "影片調整尺寸",
        subtitle: "改成 16:9、9:16、1:1 等社群比例或較小的解析度。",
        symbol: "aspectratio",
        tint: ToolPalette.video,
        inputTypes: movie,
        inputLabel: "影片",
        actionTitle: "調整尺寸",
        keywords: ["resize", "scale", "aspect", "crop", "instagram", "reels", "tiktok", "尺寸", "比例", "解析度"],
        makeSession: { MTResizeSession() }
    )

    static let convertAudio = ToolDefinition(
        id: "audio.convert",
        category: .media,
        title: "轉換音訊格式",
        subtitle: "在 M4A、ALAC、WAV、AIFF、FLAC、CAF 之間轉換，可調整音質與聲道。",
        symbol: "music.note",
        tint: ToolPalette.audio,
        badge: "M4A",
        inputTypes: audio,
        inputLabel: "音訊檔",
        actionTitle: "轉換音訊",
        keywords: ["audio", "convert", "mp3", "m4a", "aac", "wav", "flac", "aiff", "alac", "音樂", "轉檔"],
        quickActions: [
            "audio.convert-m4a": { MTAudioOutputSession.convertJob(MTAudioOutput(format: .aac, bitrate: 256_000)) },
            "audio.convert-wav": { MTAudioOutputSession.convertJob(MTAudioOutput(format: .wav)) },
        ],
        makeSession: { MTAudioOutputSession(kind: .convert) }
    )

    static let trimAudio = ToolDefinition(
        id: "audio.trim",
        category: .media,
        title: "剪輯音訊",
        subtitle: "看著波形挑出要的片段，可加上淡入淡出。",
        symbol: "waveform.badge.minus",
        tint: ToolPalette.audio,
        inputTypes: audio,
        inputLabel: "音訊檔",
        actionTitle: "剪輯音訊",
        keywords: ["trim", "cut", "audio", "ringtone", "剪輯", "鈴聲", "片段", "淡入", "淡出"],
        makeSession: { MTTrimSession(isAudio: true) }
    )
}
