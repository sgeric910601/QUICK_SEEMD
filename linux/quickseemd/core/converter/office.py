"""LibreOffice 無介面轉檔（對應原版 CVExternalTools 的 soffice 部分）。

- 每次轉檔都用全新的設定檔資料夾（-env:UserInstallation），不碰使用者自己的 LibreOffice。
- 用檔案鎖讓同一時間只有一個轉檔在跑（避免多個 LibreOffice 互相干擾）。
- 輸出先放全新的暫存資料夾，確認真的有產生檔案；逾時就結束整個程序群組。
"""

from __future__ import annotations

import fcntl
import os
import tempfile
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

from .. import backends, errors

TIMEOUT_SECONDS = 300.0
LOCK_POLL_SECONDS = 0.2
CSV_FILTER = "csv:Text - txt - csv (StarCalc):44,34,76,1"
HTML_IMPORT = "HTML (StarWriter)"

# Writer 文件（含匯入成 Writer 的 HTML／純文字）用的匯出篩選器
WRITER_FILTERS = {
    "pdf": "pdf:writer_pdf_Export",
    "docx": "docx:MS Word 2007 XML",
    "doc": "doc:MS Word 97",
    "odt": "odt:writer8",
    "rtf": "rtf:Rich Text Format",
    "txt": "txt:Text (encoded):UTF8",
    "html": "html:XHTML Writer File:UTF8",
}


def lock_path() -> Path:
    return backends.cache_dir() / "soffice.lock"


@contextmanager
def soffice_lock(ctx) -> Iterator[None]:  # noqa: ANN001
    """同一時間只讓一個 LibreOffice 轉檔執行；等待時仍可取消。"""
    path = lock_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        while True:
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                ctx.check_cancelled()
                time.sleep(LOCK_POLL_SECONDS)
        yield
    finally:
        os.close(fd)  # 關閉即釋放鎖


def filter_for(fid: str, writer: bool) -> str:
    if fid == "csv":
        return CSV_FILTER
    if writer and fid in WRITER_FILTERS:
        return WRITER_FILTERS[fid]
    return fid


def run_soffice(
    source: Path, spec: str, scratch_parent: Path, ctx, infilter: str | None = None
) -> Path:  # noqa: ANN001, E501
    """轉檔並回傳產生的檔案（在全新暫存資料夾的 out/ 裡）。"""
    tool = backends.require("soffice")
    scratch = Path(tempfile.mkdtemp(prefix=".soffice-", dir=scratch_parent))
    out_dir = scratch / "out"
    out_dir.mkdir()
    profile = scratch / "profile"
    args = [tool, f"-env:UserInstallation={profile.as_uri()}", "--headless", "--norestore"]
    args += ["--nologo", "--nodefault", "--nolockcheck"]
    if infilter:
        args.append(f"--infilter={infilter}")
    args += ["--convert-to", spec, "--outdir", str(out_dir), str(source)]
    env = {"SAL_USE_VCLPLUGIN": "svp", "HOME": str(scratch)}
    with soffice_lock(ctx):
        ctx.run_process(args, "LibreOffice", timeout=TIMEOUT_SECONDS, env=env)
    produced = sorted(p for p in out_dir.iterdir() if p.is_file())
    if not produced:
        raise errors.tool_failed("LibreOffice", "沒有產生輸出檔。")
    return produced[0]


def convert_file(source: Path, fid: str, scratch_parent: Path, ctx) -> Path:  # noqa: ANN001
    """一般 Office 檔（Word／Excel／PowerPoint／iWork）→ 目標格式。"""
    writer = source.suffix.lower() in (".doc", ".docx", ".docm", ".odt", ".rtf", ".wpd")
    return run_soffice(source, filter_for(fid, writer), scratch_parent, ctx)


def strip_text_output(path: Path) -> None:
    """LibreOffice 匯出的純文字：拿掉 BOM 與物件替代字元 U+FFFC。"""
    text = path.read_text("utf-8-sig", errors="replace")
    path.write_text(text.replace("\N{OBJECT REPLACEMENT CHARACTER}", ""), "utf-8")
