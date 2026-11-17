"""保護 PDF（密碼與權限）、解鎖 PDF、編輯 PDF 資訊（對應原版 PTSecurity.swift）。"""

from __future__ import annotations

import base64
import logging
import re
import secrets
from pathlib import Path

import pikepdf

from ... import errors
from ...model import PDF, ToolContext, ToolDefinition, per_file
from ...options import Info, Kind, Option, Values, note
from .common import (
    INPUT_LABEL,
    PDF_TYPES,
    as_bool,
    full_access,
    inspect_pdfs,
    make_validator,
    open_pdf,
    pdf_date,
    save_pdf,
)

log = logging.getLogger(__name__)

# ================================================================ 保護


def is_ascii(text: str) -> bool:
    """只有可列印的 ASCII 字元（0x20–0x7E）。"""
    return all(0x20 <= ord(ch) < 0x7F for ch in text)


def _restricted(values: Values) -> bool:
    return not all(
        as_bool(values.get(k), True) for k in ("allow_print", "allow_copy", "allow_edit")
    )


def password_problem(values: Values) -> str | None:
    """原版 PTPasswordCheck.problem。"""
    password = str(values.get("password") or "")
    owner = str(values.get("owner_password") or "")
    if not password:
        return "請輸入開啟密碼"
    if not is_ascii(password):
        return "密碼只能使用英文字母、數字與符號"
    if len(password.encode("utf-8")) > 32:
        return "密碼最多 32 個字元"
    if password != str(values.get("confirm") or ""):
        return "兩次輸入的密碼不一致"
    if owner:
        if not is_ascii(owner):
            return "權限密碼只能使用英文字母、數字與符號"
        if owner == password:
            return "權限密碼不能與開啟密碼相同"
    return None


def random_password() -> str:
    """18 個隨機位元組的 base64（/ 換成 x、+ 換成 y）。"""
    raw = base64.b64encode(secrets.token_bytes(18)).decode("ascii")
    return raw.replace("/", "x").replace("+", "y")


def _permissions(values: Values) -> pikepdf.Permissions:
    printing = as_bool(values.get("allow_print"), True)
    copying = as_bool(values.get("allow_copy"), True)
    editing = as_bool(values.get("allow_edit"), True)
    return pikepdf.Permissions(
        accessibility=True,
        print_lowres=printing,
        print_highres=printing,
        extract=copying,
        modify_other=editing,
        modify_assembly=editing,
        modify_annotation=editing,
        modify_form=editing,
    )


def _owner_password(values: Values) -> str:
    """有限制時用指定的或隨機的權限密碼；沒有限制時與開啟密碼相同。"""
    owner = str(values.get("owner_password") or "")
    if _restricted(values):
        return owner or random_password()
    return owner or str(values.get("password") or "")


def _protect_file(file: Path, values: Values, ctx: ToolContext) -> list[Path]:
    problem = password_problem(values)
    if problem:
        raise errors.QSError(problem + "。")
    with open_pdf(file) as doc:
        if doc.is_encrypted:
            raise errors.QSError(f"「{file.name}」已經加密，請先解鎖後再重新設定密碼。")
        ctx.progress(0.2, f"加密「{file.name}」…")
        enc = pikepdf.Encryption(
            owner=_owner_password(values),
            user=str(values.get("password")),
            R=6,
            allow=_permissions(values),
        )
        tmp = ctx.make_work_dir(file) / "protected.pdf"
        try:
            doc.save(tmp, encryption=enc)
        except (pikepdf.PdfError, OSError, ValueError) as exc:
            raise errors.write_failed(str(exc)) from exc
    _verify_locked(tmp)
    return [ctx.deliver(tmp, ctx.output_name(file, "已加密", "pdf"), file)]


def _verify_locked(path: Path) -> None:
    try:
        with pikepdf.open(path):
            pass
    except pikepdf.PasswordError:
        return
    except pikepdf.PdfError as exc:
        raise errors.write_failed("加密失敗，請再試一次。") from exc
    raise errors.write_failed("加密失敗，請再試一次。")


def run_protect(files: list[Path], values: Values, ctx: ToolContext) -> list[Path]:
    return per_file(files, ctx, lambda f, sub: _protect_file(f, values, sub), skip_errors=False)


def _confirm_note(values: Values, info: Info) -> tuple[str, bool] | None:
    password = str(values.get("password") or "")
    confirm = str(values.get("confirm") or "")
    if confirm and confirm != password:
        return "兩次輸入的密碼不一致", True
    if password and not is_ascii(password):
        return "請只使用英文字母、數字與符號", True
    return "開啟檔案時需要輸入這組密碼。請記住它，忘記就無法開啟。", False


_KEY = {"group": "開啟密碼", "group_icon": "key"}
_PERM = {"group": "權限", "group_icon": "hand.raised"}

PROTECT = ToolDefinition(
    id="pdf.protect",
    category=PDF,
    title="保護 PDF",
    subtitle="設定開啟密碼，並可限制列印、複製或修改內容。",
    icon="lock.doc",
    run=run_protect,
    input_types=PDF_TYPES,
    input_label=INPUT_LABEL,
    action_title="加密 PDF",
    keywords=(
        "protect",
        "encrypt",
        "password",
        "lock",
        "permissions",
        "加密",
        "密碼",
        "保護",
        "權限",
    ),
    badge="PDF",
    options=(
        Option("password", Kind.PASSWORD, "密碼", "", placeholder="輸入密碼", **_KEY),
        Option(
            "confirm",
            Kind.PASSWORD,
            "再輸入一次",
            "",
            placeholder="確認密碼",
            note=_confirm_note,
            **_KEY,
        ),
        Option("allow_print", Kind.TOGGLE, "允許列印", True, **_PERM),
        Option("allow_copy", Kind.TOGGLE, "允許複製文字與圖片", True, **_PERM),
        Option("allow_edit", Kind.TOGGLE, "允許修改、註解與整理頁面", True, **_PERM),
        Option(
            "owner_password",
            Kind.PASSWORD,
            "擁有者密碼",
            "",
            placeholder="留白則自動產生",
            caption="用這組密碼開啟可取得完整權限。留白時會自動產生隨機密碼，之後將無法解除限制。",
            group="權限密碼（選填）",
            group_icon="key.horizontal",
            visible=lambda v, i: _restricted(v),
        ),
        note(
            "使用 256 位元 AES 的 PDF 加密，一般 PDF 閱讀程式都能開啟。權限限制需閱讀程式配合遵守。",
            group_icon="lock.shield",
        ),
    ),
    inspect=inspect_pdfs,
    validate=make_validator(lambda f, v, i: password_problem(v)),
)

# ================================================================ 解鎖


def _probe(file: Path) -> pikepdf.Pdf | None:
    """不用密碼開啟；需要開啟密碼時回傳 None。"""
    if not file.is_file():
        raise errors.file_not_found(file.name)
    try:
        return pikepdf.open(file)
    except pikepdf.PasswordError:
        return None
    except (pikepdf.PdfError, OSError, ValueError) as exc:
        raise errors.unreadable(f"「{file.name}」不是有效的 PDF，或檔案已損毀。") from exc


def _open_unlocked(file: Path, password: str, probe: pikepdf.Pdf | None) -> pikepdf.Pdf:
    try:
        pdf = pikepdf.open(file, password=password)
    except pikepdf.PasswordError as exc:
        if probe is None:
            raise errors.QSError(f"「{file.name}」的密碼不正確。") from exc
        return probe  # 只有權限限制、密碼不是擁有者密碼：沿用不用密碼開啟的版本
    if probe is not None:
        probe.close()
    return pdf


def _unlock_file(file: Path, values: Values, ctx: ToolContext) -> list[Path]:
    probe = _probe(file)
    if probe is not None and not probe.is_encrypted:
        probe.close()
        raise errors.QSError(f"「{file.name}」沒有加密，不需要解鎖。")
    ctx.progress(0.1, f"解鎖「{file.name}」…")
    with _open_unlocked(file, str(values.get("password") or ""), probe) as pdf:
        if not full_access(pdf):
            raise errors.QSError(
                f"「{file.name}」設有權限限制，請輸入「權限（擁有者）密碼」才能完整解鎖。"
            )
        ctx.progress(0.3, "寫出不需密碼的副本…")
        tmp = ctx.make_work_dir(file) / "unlocked.pdf"
        save_pdf(pdf, tmp)
    try:
        with pikepdf.open(tmp) as check:
            still = check.is_encrypted
    except pikepdf.PdfError:
        still = True
    if still:
        raise errors.write_failed(f"無法移除「{file.name}」的加密。")
    return [ctx.deliver(tmp, ctx.output_name(file, "已解鎖", "pdf"), file)]


def run_unlock(files: list[Path], values: Values, ctx: ToolContext) -> list[Path]:
    return per_file(files, ctx, lambda f, sub: _unlock_file(f, values, sub), skip_errors=False)


def _encrypted_note(values: Values, info: Info) -> tuple[str, bool] | None:
    names = info.get("encrypted_names") or []
    return ("已加密：" + "、".join(names), False) if names else None


UNLOCK = ToolDefinition(
    id="pdf.unlock",
    category=PDF,
    title="解鎖 PDF",
    subtitle="輸入密碼後移除 PDF 的開啟密碼與權限限制，另存成不需密碼的檔案。",
    icon="lock.open",
    run=run_unlock,
    input_types=PDF_TYPES,
    input_label=INPUT_LABEL,
    action_title="解鎖 PDF",
    keywords=("unlock", "decrypt", "remove password", "解鎖", "解密", "移除密碼"),
    badge="PDF",
    options=(
        Option(
            "password",
            Kind.PASSWORD,
            "PDF 密碼",
            "",
            placeholder="開啟密碼或權限密碼",
            caption="只有開啟密碼時輸入開啟密碼即可；若檔案還限制了列印或複製，請輸入權限（擁有者）密碼。",
            group="密碼",
            group_icon="key",
        ),
        note("", key="_unlock_encrypted", note=_encrypted_note, group_icon="lock.fill"),
        note("解鎖後會另存一份不需要密碼的 PDF，原始檔案保持不變。請只解鎖你有權使用的文件。"),
    ),
    inspect=inspect_pdfs,
    validate=lambda f, v, i: None if str(v.get("password") or "") else "請輸入 PDF 的密碼",
)

# ================================================================ 文件資訊

_FIELDS = (
    ("title", "Title"),
    ("author", "Author"),
    ("subject", "Subject"),
    ("creator", "Creator"),
)


def _new_docinfo(pdf: pikepdf.Pdf, values: Values) -> pikepdf.Dictionary:
    fresh = pikepdf.Dictionary()
    for key, name in _FIELDS:
        v = str(values.get(key) or "").strip()
        if v:
            fresh["/" + name] = pikepdf.String(v)
    words = [w.strip() for w in re.split(r"[,，]", str(values.get("keywords") or ""))]
    words = [w for w in words if w]
    if words:
        fresh.Keywords = pikepdf.String(", ".join(words))
    fresh.ModDate = pikepdf.String(pdf_date())
    created = pdf.docinfo.get("/CreationDate") if "/Info" in pdf.trailer else None
    if created is not None:
        fresh.CreationDate = pikepdf.String(str(created))
    return fresh


def _sync_xmp(pdf: pikepdf.Pdf) -> None:
    """XMP 與文件資訊保持一致（閱讀程式可能優先顯示 XMP）。"""
    if "/Metadata" not in pdf.Root:
        return
    try:
        with pdf.open_metadata(set_pikepdf_as_editor=False, update_docinfo=False) as meta:
            meta.load_from_docinfo(pdf.docinfo, delete_missing=True)
    except (pikepdf.PdfError, ValueError, OSError) as exc:
        log.warning("XMP 無法更新，改為移除：%s", exc)
        del pdf.Root["/Metadata"]


def _metadata_file(file: Path, values: Values, ctx: ToolContext) -> list[Path]:
    clear = as_bool(values.get("clear_all"), False)
    with open_pdf(file) as pdf:
        ctx.progress(0.3, f"更新「{file.name}」的文件資訊…")
        if clear:
            if "/Info" in pdf.trailer:
                del pdf.trailer["/Info"]
            if "/Metadata" in pdf.Root:
                del pdf.Root["/Metadata"]
        else:
            pdf.trailer.Info = pdf.make_indirect(_new_docinfo(pdf, values))
            _sync_xmp(pdf)
        tmp = ctx.make_work_dir(file) / "metadata.pdf"
        save_pdf(pdf, tmp)
    tag = "已清除資訊" if clear else "已更新資訊"
    return [ctx.deliver(tmp, ctx.output_name(file, tag, "pdf"), file)]


def run_metadata(files: list[Path], values: Values, ctx: ToolContext) -> list[Path]:
    return per_file(files, ctx, lambda f, sub: _metadata_file(f, values, sub), skip_errors=False)


def _metadata_inspect(files: list[Path]) -> Info:
    info = inspect_pdfs(files)
    if "metadata" not in info and files:
        first = inspect_pdfs(files[:1])
        if "metadata" in first:
            info["metadata"] = first["metadata"]
    return info


def _metadata_prefill(files: list[Path], info: Info) -> Values:
    meta = info.get("metadata")
    return dict(meta) if isinstance(meta, dict) else {}


def _not_clear(values: Values, info: Info) -> bool:
    return not as_bool(values.get("clear_all"), False)


_DOC = {"group": "文件資訊", "group_icon": "info.circle", "visible": _not_clear}

METADATA = ToolDefinition(
    id="pdf.metadata",
    category=PDF,
    title="編輯 PDF 資訊",
    subtitle="修改 PDF 的標題、作者、主旨與關鍵字，或清除所有文件資訊。",
    icon="info.circle",
    run=run_metadata,
    input_types=PDF_TYPES,
    input_label=INPUT_LABEL,
    action_title="儲存資訊",
    keywords=(
        "metadata",
        "properties",
        "title",
        "author",
        "info",
        "內容資訊",
        "屬性",
        "作者",
        "標題",
    ),
    badge="PDF",
    options=(
        Option(
            "clear_all",
            Kind.TOGGLE,
            "清除所有文件資訊",
            False,
            caption="移除標題、作者等資料，保護隱私",
        ),
        Option("title", Kind.TEXT, "標題", "", placeholder="文件標題", **_DOC),
        Option("author", Kind.TEXT, "作者", "", placeholder="作者姓名", **_DOC),
        Option("subject", Kind.TEXT, "主旨", "", placeholder="文件主旨", **_DOC),
        Option("keywords", Kind.TEXT, "關鍵字", "", placeholder="以逗號分隔", **_DOC),
        Option("creator", Kind.TEXT, "建立程式", "", placeholder="例如 Microsoft Word", **_DOC),
        note(
            "欄位已帶入第一個檔案目前的資訊；選了多個檔案時，會套用相同的資訊。留白的欄位會被移除。",
            visible=_not_clear,
        ),
    ),
    inspect=_metadata_inspect,
    prefill=_metadata_prefill,
    validate=make_validator(),
)
