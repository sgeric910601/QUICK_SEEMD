"""使用者看得到的錯誤訊息（對應原版 ConversionError，字串照抄）。"""

from __future__ import annotations


class QSError(Exception):
    """所有顯示給使用者的錯誤；str(e) 就是要顯示的中文訊息。"""

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message

    def __str__(self) -> str:
        return self.message


class Cancelled(QSError):
    """使用者按了取消。"""

    def __init__(self) -> None:
        super().__init__("已取消。")


class SkipFile(QSError):
    """多檔處理時略過這個檔案（對應原版 ITSkip）；只處理一個檔案時當成錯誤。"""


def file_not_found(name: str) -> QSError:
    return QSError(f"找不到檔案「{name}」，可能已被移動或刪除。")


def unsupported_target(source: str, target: str) -> QSError:
    return QSError(f"「{source}」無法轉換成 {target}。")


def unreadable(detail: str) -> QSError:
    return QSError(f"無法讀取檔案內容：{detail}")


def write_failed(detail: str) -> QSError:
    return QSError(f"寫入輸出檔失敗：{detail}")


def output_folder_unavailable(path: str) -> QSError:
    return QSError(f"無法寫入輸出位置「{path}」，請確認資料夾存在且有寫入權限。")


def password_protected() -> QSError:
    return QSError("這份 PDF 受密碼保護，請先用「解鎖 PDF」移除密碼後再轉換。")


def empty_content(detail: str) -> QSError:
    return QSError(f"檔案沒有可轉換的內容：{detail}")


def encoding_failed(encoding: str, char: str) -> QSError:
    return QSError(f"有些字元無法用 {encoding} 表示（例如「{char}」），建議改用 UTF-8。")


def invalid_data(detail: str) -> QSError:
    return QSError(f"資料格式不正確：{detail}")


def no_video_track() -> QSError:
    return QSError("這個檔案沒有影像軌，無法輸出影片或畫面。")


def no_audio_track() -> QSError:
    return QSError("這個檔案沒有音訊軌，無法擷取聲音。")


def tool_missing(tool: str) -> QSError:
    return QSError(f"找不到 {tool}，這個格式需要先安裝它。")


def tool_failed(tool: str, detail: str = "") -> QSError:
    detail = detail.strip()
    if detail:
        return QSError(f"{tool} 轉換失敗：{detail}")
    return QSError(f"{tool} 轉換失敗。")
