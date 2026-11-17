"""PDF 工具共用的開檔、頁碼、頁面大小、文件資訊與選項小工具（對應原版 PTSupport）。"""

from __future__ import annotations

import datetime as _dt
import logging
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

import pikepdf

from ... import errors, pagerange
from ...naming import base_name
from ...options import Info, Kind, Option, Values

log = logging.getLogger(__name__)

PDF_TYPES: tuple[str, ...] = ("application/pdf",)
INPUT_LABEL = "PDF 檔案"
A4: tuple[float, float] = (595.28, 841.89)
LETTER: tuple[float, float] = (612.0, 792.0)
PRODUCER = "QUICK_SEEMD"
INFO_KEYS: tuple[str, ...] = ("Title", "Author", "Subject", "Keywords", "Creator")

# 原版 PTInk：顏色值用 "#rrggbb"（COLOR 選項），畫圖時換回原版的精確 RGB
INKS: dict[str, tuple[str, tuple[float, float, float]]] = {
    "#808080": ("灰色", (0.5, 0.5, 0.5)),
    "#db2924": ("紅色", (0.86, 0.16, 0.14)),
    "#1a5ccc": ("藍色", (0.10, 0.36, 0.80)),
    "#000000": ("黑色", (0.0, 0.0, 0.0)),
    "#1c8c45": ("綠色", (0.11, 0.55, 0.27)),
}
INK_GRAY, INK_RED, INK_BLUE, INK_BLACK, INK_GREEN = INKS

# 九宮格位置（原版 PTGridPosition，row 0 在上）
GRID_TITLES: tuple[str, ...] = (
    "左上",
    "上方置中",
    "右上",
    "左側",
    "正中央",
    "右側",
    "左下",
    "下方置中",
    "右下",
)


# ---------------------------------------------------------------- 開檔


def restricted_message(name: str) -> str:
    return f"「{name}」設有權限限制（禁止修改或複製），請先用「解鎖 PDF」輸入權限密碼。"


def locked_message(name: str) -> str:
    return f"「{name}」受密碼保護，請先用「解鎖 PDF」工具移除密碼後再試。"


def full_access(pdf: pikepdf.Pdf) -> bool:
    """擁有者身分，或 8 項權限全部允許（對應原版 accessPermissions & 0xFF == 0xFF）。"""
    if not pdf.is_encrypted or pdf.owner_password_matched:
        return True
    a = pdf.allow
    return all(
        (
            a.accessibility,
            a.extract,
            a.modify_annotation,
            a.modify_assembly,
            a.modify_form,
            a.modify_other,
            a.print_lowres,
            a.print_highres,
        )
    )


def _open_raw(path: Path, password: str) -> pikepdf.Pdf:
    try:
        return pikepdf.open(path, password=password)
    except pikepdf.PasswordError:
        raise
    except (pikepdf.PdfError, OSError, ValueError) as exc:
        log.info("無法開啟 %s：%s", path, exc)
        raise errors.unreadable(f"「{path.name}」不是有效的 PDF，或檔案已損毀。") from exc


def open_pdf(path: Path, password: str | None = None) -> pikepdf.Pdf:
    """開啟 PDF（先試空白密碼，再試指定的密碼）；錯誤訊息照原版 PTPDF.open。"""
    path = Path(path)
    if not path.is_file():
        raise errors.file_not_found(path.name)
    pdf: pikepdf.Pdf | None = None
    for pw in ([password] if password else []) + [""]:
        try:
            pdf = _open_raw(path, pw)
            break
        except pikepdf.PasswordError:
            continue
    if pdf is None:
        raise errors.QSError(locked_message(path.name))
    if len(pdf.pages) == 0:
        pdf.close()
        raise errors.empty_content(f"「{path.name}」沒有任何頁面。")
    if not full_access(pdf):
        pdf.close()
        raise errors.QSError(restricted_message(path.name))
    return pdf


def is_locked(path: Path) -> bool:
    """需要開啟密碼（空白密碼打不開）。"""
    try:
        with pikepdf.open(path):
            return False
    except pikepdf.PasswordError:
        return True
    except (pikepdf.PdfError, OSError, ValueError):
        return False


def page_list(text: str, count: int, path: Path) -> list[int]:
    """頁碼範圍（空白＝全部），不正確時丟出原版的中文訊息。"""
    pages = pagerange.parse(str(text or ""), count)
    if pages is None:
        raise errors.QSError(f"頁碼範圍「{text}」不正確：「{Path(path).name}」共 {count} 頁。")
    return pages


def padded(n: int, total: int) -> str:
    """檔名中的頁碼補零：7 頁 → "07"。"""
    return str(n).zfill(max(2, len(str(total))))


# ---------------------------------------------------------------- 頁面大小與座標


def _rect(value: Any) -> tuple[float, float, float, float] | None:
    try:
        x0, y0, x1, y1 = (float(v) for v in value)
    except (TypeError, ValueError):
        return None
    return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)


def crop_box(page: pikepdf.Page | pikepdf.Dictionary) -> tuple[float, float, float, float]:
    """裁切框（與媒體框取交集，和 Quartz 的 getBoxRect(.cropBox) 一樣）。"""
    obj = page.obj if isinstance(page, pikepdf.Page) else page
    media = _rect(_inherited(obj, "/MediaBox")) or (0.0, 0.0, *A4)
    crop = _rect(_inherited(obj, "/CropBox")) or media
    x0, y0 = max(crop[0], media[0]), max(crop[1], media[1])
    x1, y1 = min(crop[2], media[2]), min(crop[3], media[3])
    if x1 - x0 < 1 or y1 - y0 < 1:
        return media
    return x0, y0, x1, y1


def _inherited(obj: pikepdf.Dictionary, key: str) -> Any:
    current: Any = obj
    for _ in range(32):
        if current is None or not isinstance(current, pikepdf.Dictionary):
            return None
        if key in current:
            return current[key]
        current = current.get("/Parent")
    return None


def rotation(page: pikepdf.Page | pikepdf.Dictionary) -> int:
    """頁面的 /Rotate（含繼承），正規化成 0／90／180／270。"""
    obj = page.obj if isinstance(page, pikepdf.Page) else page
    try:
        r = int(_inherited(obj, "/Rotate") or 0)
    except (TypeError, ValueError):
        r = 0
    r %= 360
    return r - r % 90


def user_unit(page: pikepdf.Page | pikepdf.Dictionary) -> float:
    obj = page.obj if isinstance(page, pikepdf.Page) else page
    try:
        u = float(obj.get("/UserUnit", 1))
    except (TypeError, ValueError):
        return 1.0
    return u if u > 0 else 1.0


def visual_size(page: pikepdf.Page | pikepdf.Dictionary) -> tuple[float, float]:
    """頁面「看起來」的大小（pt，已套用旋轉與 UserUnit）。"""
    x0, y0, x1, y1 = crop_box(page)
    u = user_unit(page)
    w, h = (x1 - x0) * u, (y1 - y0) * u
    return (h, w) if rotation(page) in (90, 270) else (w, h)


# ---------------------------------------------------------------- 文件資訊


def _text(value: Any) -> str:
    try:
        return str(value).strip()
    except (TypeError, ValueError, UnicodeDecodeError):
        return ""


def read_info(pdf: pikepdf.Pdf) -> dict[str, str]:
    """標題、作者、主旨、關鍵字、建立程式（空白的不列）。"""
    out: dict[str, str] = {}
    try:
        info = pdf.docinfo
    except (pikepdf.PdfError, ValueError):
        return out
    for key in INFO_KEYS:
        v = _text(info.get("/" + key, "")) if ("/" + key) in info else ""
        if v:
            out[key] = v
    return out


def pdf_date(when: _dt.datetime | None = None) -> str:
    """PDF 日期字串（UTC，與原版 PTPDFWriter.pdfDate 相同格式）。"""
    t = (when or _dt.datetime.now(_dt.timezone.utc)).astimezone(_dt.timezone.utc)
    return "D:" + t.strftime("%Y%m%d%H%M%S") + "Z"


def write_info(pdf: pikepdf.Pdf, info: dict[str, str]) -> None:
    """換成新的文件資訊：5 個欄位＋Producer＋建立／修改日期（原版 PTPDFWriter 的寫法）。"""
    date = pdf_date()
    fresh = pikepdf.Dictionary()
    for key in INFO_KEYS:
        v = info.get(key, "")
        if v:
            fresh["/" + key] = pikepdf.String(v)
    fresh["/Producer"] = pikepdf.String(PRODUCER)
    fresh["/CreationDate"] = pikepdf.String(date)
    fresh["/ModDate"] = pikepdf.String(date)
    pdf.trailer.Info = pdf.make_indirect(fresh)


def save_pdf(pdf: pikepdf.Pdf, out: Path, **kwargs: Any) -> None:
    """寫出新檔（不加密、新的 /ID）。out 一定是自己的暫存路徑。"""
    try:
        pdf.save(out, encryption=False, **kwargs)
    except (pikepdf.PdfError, OSError, ValueError) as exc:
        raise errors.write_failed(str(exc)) from exc


def verify_pages(out: Path, count: int) -> None:
    """重新開啟輸出檔確認頁數（原版寫完都會檢查）。"""
    try:
        with pikepdf.open(out) as check:
            n = len(check.pages)
    except (pikepdf.PdfError, OSError) as exc:
        raise errors.write_failed(f"無法讀回輸出的 PDF：{exc}") from exc
    if n != count:
        raise errors.write_failed("輸出的 PDF 頁數不符。")


# ---------------------------------------------------------------- 選項面板用的檔案資訊


def _metadata(pdf: pikepdf.Pdf) -> dict[str, str]:
    info = read_info(pdf)
    return {
        "title": info.get("Title", ""),
        "author": info.get("Author", ""),
        "subject": info.get("Subject", ""),
        "keywords": info.get("Keywords", ""),
        "creator": info.get("Creator", ""),
    }


def _has_fonts(pdf: pikepdf.Pdf, limit: int = 50) -> bool:
    """快速判斷：前幾頁的資源裡有沒有字型（不解析內容）。"""
    for i, page in enumerate(pdf.pages):
        if i >= limit:
            break
        res = page.obj.get("/Resources")
        if isinstance(res, pikepdf.Dictionary) and "/Font" in res:
            return True
        xobjs = res.get("/XObject") if isinstance(res, pikepdf.Dictionary) else None
        if isinstance(xobjs, pikepdf.Dictionary):
            for x in xobjs.values():
                r = x.get("/Resources") if isinstance(x, pikepdf.Stream) else None
                if isinstance(r, pikepdf.Dictionary) and "/Font" in r:
                    return True
    return False


def _single_info(path: Path) -> Info:
    try:
        with pikepdf.open(path) as pdf:
            return {
                "page_count": len(pdf.pages),
                "page_sizes": [list(visual_size(p)) for p in pdf.pages],
                "encrypted": bool(pdf.is_encrypted),
                "has_text": _has_fonts(pdf),
                "metadata": _metadata(pdf),
            }
    except pikepdf.PasswordError:
        return {"encrypted": True}
    except (pikepdf.PdfError, OSError, ValueError):
        return {}


def _scan(path: Path) -> tuple[int, bool, bool]:
    """（頁數, 需要密碼, 有加密）。"""
    try:
        with pikepdf.open(path) as pdf:
            return len(pdf.pages), False, bool(pdf.is_encrypted)
    except pikepdf.PasswordError:
        return 0, True, True
    except (pikepdf.PdfError, OSError, ValueError):
        return 0, False, False


def inspect_pdfs(files: Sequence[Path]) -> Info:
    """選項面板與檢查用的檔案資訊（原版 PTSession.filesDidChange）。"""
    paths = [Path(f) for f in files]
    pdfs = [p for p in paths if p.suffix.lower() == ".pdf"]
    info: Info = {"file_count": len(paths), "locked": [], "encrypted_names": []}
    total_pages = 0
    total_size = 0
    for p in pdfs:
        count, locked, encrypted = _scan(p)
        total_pages += count
        if locked:
            info["locked"].append(p.name)
        if encrypted:
            info["encrypted_names"].append(p.name)
    for p in paths:
        try:
            total_size += p.stat().st_size
        except OSError:
            pass
    info["total_pages"] = total_pages
    info["total_size"] = total_size
    if paths:
        info["first_base"] = base_name(paths[0])
    if len(pdfs) == 1 and len(paths) == 1:
        info.update(_single_info(pdfs[0]))
    return info


def locked_reason(info: Info) -> str | None:
    """原版 PTSession.validate：有受密碼保護的檔案就不能執行。"""
    locked = info.get("locked") or []
    if locked:
        return f"「{locked[0]}」受密碼保護，請先用「解鎖 PDF」移除密碼"
    return None


def page_count_of(info: Info) -> int | None:
    n = info.get("page_count")
    return int(n) if isinstance(n, int) else None


def range_reason(text: str, info: Info, message: str = "頁碼範圍不正確") -> str | None:
    """頁碼範圍欄位的檢查（只有選一個檔案、知道頁數時才檢查）。"""
    n = page_count_of(info)
    if n is not None and pagerange.parse(str(text or ""), n) is None:
        return message
    return None


def make_validator(
    extra: Callable[[list[Path], Values, Info], str | None] | None = None,
) -> Callable[[list[Path], Values, Info], str | None]:
    """先檢查受密碼保護的檔案，再做工具自己的檢查。"""

    def validate(files: list[Path], values: Values, info: Info) -> str | None:
        reason = locked_reason(info)
        if reason or extra is None:
            return reason
        return extra(files, values, info)

    return validate


# ---------------------------------------------------------------- 選項小工具


def range_caption(key: str) -> Callable[[Values, Info], tuple[str, bool] | None]:
    def note(values: Values, info: Info) -> tuple[str, bool] | None:
        return pagerange.caption(str(values.get(key) or ""), page_count_of(info))

    return note


def page_range_option(key: str, label: str, **kw: Any) -> Option:
    """PageRangeField：預留文字「全部頁面」，下方顯示範圍說明。"""
    return Option(
        key=key,
        kind=Kind.PAGE_RANGE,
        label=label,
        default="",
        placeholder=pagerange.PLACEHOLDER,
        group=kw.pop("group", "頁面"),
        group_icon=kw.pop("group_icon", "doc.on.doc"),
        **kw,
    )


def as_int(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def as_float(value: Any, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def as_bool(value: Any, default: bool) -> bool:
    if isinstance(value, bool):
        return value
    if value is None:
        return default
    if isinstance(value, str):
        return value.strip().lower() in ("1", "true", "yes", "on")
    return bool(value)


def ink_rgb(value: Any, default: str = INK_BLACK) -> tuple[float, float, float]:
    """顏色值（"#rrggbb"）→ RGB 0–1；原版的顏色用原本的精確數值。"""
    text = str(value or default).lower()
    if text in INKS:
        return INKS[text][1]
    try:
        raw = text.lstrip("#")
        return tuple(int(raw[i : i + 2], 16) / 255 for i in (0, 2, 4))  # type: ignore[return-value]
    except (ValueError, IndexError):
        return INKS[default][1]
