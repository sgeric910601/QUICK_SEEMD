"""HTML → PDF（WeasyPrint），在獨立的子程序裡執行，才能逾時、取消。

原版用 TextKit 分頁：A4、邊界 64 pt（文件自己有指定時用文件的）。這裡以使用者樣式表指定同樣的預設值，
文件自己的 @page 規則會蓋過它。為了安全只允許讀取 data: 與輸入檔所在資料夾裡的檔案，
不會連上網路。

用法（由轉換引擎呼叫）：python3 -I htmlpdf.py <輸入.html> <輸出.pdf> <基準資料夾>
這個檔案不使用相對匯入，可以單獨執行。
"""

from __future__ import annotations

import io
import logging
import os
import sys
from pathlib import Path
from urllib.parse import unquote, urlparse

PAGE_CSS = "@page { size: A4; margin: 64pt; }"
GENERATOR = "QUICK_SEEMD"


def make_fetcher(allowed: Path):  # noqa: ANN201
    """只放行 data: 與 allowed 資料夾內的本機檔案。"""
    from weasyprint import default_url_fetcher  # noqa: PLC0415

    root = os.path.realpath(allowed)

    def fetch(url: str, *args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        if url.startswith("data:"):
            return default_url_fetcher(url, *args, **kwargs)
        parsed = urlparse(url)
        if parsed.scheme == "file":
            real = os.path.realpath(unquote(parsed.path))
            if real == root or real.startswith(root + os.sep):
                return default_url_fetcher(url, *args, **kwargs)
        raise ValueError(f"已封鎖外部資源：{url}")

    return fetch


def render(html_path: Path, out_pdf: Path, base_dir: Path) -> None:
    """相對路徑（例如 Markdown 裡的圖片）以 base_dir 為準，也只允許讀 base_dir 裡的檔案。"""
    from weasyprint import CSS, HTML  # noqa: PLC0415

    logging.getLogger("weasyprint").setLevel(logging.ERROR)
    logging.getLogger("fontTools").setLevel(logging.ERROR)
    doc = HTML(
        file_obj=io.BytesIO(html_path.read_bytes()),  # 依 <meta charset> 判斷編碼
        base_url=base_dir.resolve().as_uri() + "/",
        url_fetcher=make_fetcher(base_dir),
    ).render(stylesheets=[CSS(string=PAGE_CSS)])
    doc.metadata.generator = GENERATOR
    with open(out_pdf, "xb") as fh:
        doc.write_pdf(fh)


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        sys.stderr.write("用法：htmlpdf.py <輸入.html> <輸出.pdf> <允許讀取的資料夾>\n")
        return 2
    try:
        render(Path(argv[0]), Path(argv[1]), Path(argv[2]))
    except Exception as exc:  # noqa: BLE001 — 子程序：把原因寫到 stderr 交給呼叫端顯示
        sys.stderr.write(f"{exc}\n")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
