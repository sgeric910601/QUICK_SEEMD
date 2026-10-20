import Foundation

/// 轉換錯誤；`errorDescription` 為使用者看得懂的繁體中文。
enum ConversionError: LocalizedError, Sendable {
    case fileNotFound(String)
    case unsupportedTarget(source: String, target: String)
    case unreadable(String)
    case writeFailed(String)
    case outputFolderUnavailable(String)
    case passwordProtected
    case emptyContent(String)
    case encodingFailed(encoding: String, sample: String)
    case invalidData(String)
    case noVideoTrack
    case noAudioTrack
    case toolMissing(String)
    case toolFailed(tool: String, message: String)
    case cancelled
    case other(String)

    var errorDescription: String? {
        switch self {
        case .fileNotFound(let name):
            return "找不到檔案「\(name)」，可能已被移動或刪除。"
        case .unsupportedTarget(let source, let target):
            return "「\(source)」無法轉換成 \(target)。"
        case .unreadable(let detail):
            return "無法讀取檔案內容：\(detail)"
        case .writeFailed(let detail):
            return "寫入輸出檔失敗：\(detail)"
        case .outputFolderUnavailable(let path):
            return "無法寫入輸出位置「\(path)」，請確認資料夾存在且有寫入權限。"
        case .passwordProtected:
            return "這份 PDF 受密碼保護，請先在「預覽程式」解除鎖定後再轉換。"
        case .emptyContent(let detail):
            return "檔案沒有可轉換的內容：\(detail)"
        case .encodingFailed(let encoding, let sample):
            return "有些字元無法用 \(encoding) 表示（例如「\(sample)」），建議改用 UTF-8。"
        case .invalidData(let detail):
            return "資料格式不正確：\(detail)"
        case .noVideoTrack:
            return "這個檔案沒有影像軌，無法輸出影片或畫面。"
        case .noAudioTrack:
            return "這個檔案沒有音訊軌，無法擷取聲音。"
        case .toolMissing(let tool):
            return "找不到 \(tool)，這個格式需要先安裝它。"
        case .toolFailed(let tool, let message):
            let trimmed = message.trimmingCharacters(in: .whitespacesAndNewlines)
            let tail = trimmed.split(separator: "\n").suffix(3).joined(separator: "\n")
            return tail.isEmpty ? "\(tool) 轉換失敗。" : "\(tool) 轉換失敗：\(tail)"
        case .cancelled:
            return "已取消。"
        case .other(let message):
            return message
        }
    }
}
