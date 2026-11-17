"""去背遮罩小程式：在獨立的去背元件環境（rembg）裡以檔案路徑執行。

用法：python -I rembg_helper.py 輸入.png 遮罩.png [輸入2.png 遮罩2.png …]
- 輸入是已轉正的 RGB／RGBA 圖片；輸出是同尺寸的灰階遮罩（白色＝主體），必須是新檔。
- 每完成一張在標準輸出印一行「done 序號」，主程式用來更新進度。
- 這個檔案不能匯入 quickseemd（它跑在另一個 Python 環境）。
"""

from __future__ import annotations

import os
import sys

MODEL = "isnet-general-use"


def _fail(message: str, code: int) -> int:
    sys.stderr.write(message + "\n")
    return code


def main(argv: list[str]) -> int:
    pairs = argv[1:]
    if not pairs or len(pairs) % 2:
        return _fail("用法：rembg_helper.py 輸入.png 遮罩.png …", 2)
    if not all(os.path.isabs(p) for p in pairs):
        return _fail("路徑必須是絕對路徑", 2)
    try:
        from PIL import Image
        from rembg import new_session, remove
    except ImportError as exc:
        return _fail(f"無法載入去背元件：{exc}", 3)
    session = new_session(MODEL)
    for index in range(0, len(pairs), 2):
        src, dst = pairs[index], pairs[index + 1]
        with Image.open(src) as im:
            mask = remove(im.convert("RGB"), session=session, only_mask=True)
        with open(dst, "xb") as fh:
            mask.convert("L").save(fh, format="PNG")
        sys.stdout.write(f"done {index // 2}\n")
        sys.stdout.flush()
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
