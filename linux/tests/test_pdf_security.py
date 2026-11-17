"""保護 PDF、解鎖 PDF、編輯 PDF 資訊。"""

from __future__ import annotations

import pikepdf
import pytest

from quickseemd.core import errors
from quickseemd.core.tools.pdf import security
from test_pdf_helpers import digest, encrypt, make_rich_pdf, pdftotext, run_tool, tool

# ------------------------------------------------------------------ 保護


def _protect(**kw):
    values = {"password": "abc123", "confirm": "abc123"}
    values.update(kw)
    return values


def test_protect_basic(tmp_path, pdf_file):
    before = digest(pdf_file)
    run = run_tool("pdf.protect", [pdf_file], _protect())
    out = run.outputs[0]
    assert out.name == "文件-已加密.pdf"
    with pytest.raises(pikepdf.PasswordError):
        pikepdf.open(out)
    with pikepdf.open(out, password="abc123") as pdf:
        assert pdf.encryption.bits == 256
        assert pdf.owner_password_matched  # 沒有限制時擁有者密碼＝開啟密碼
        assert pdf.allow.extract and pdf.allow.print_highres and pdf.allow.modify_assembly
    assert "第 1 頁" in pdftotext_with_password(out, "abc123")
    assert digest(pdf_file) == before


def pdftotext_with_password(path, password):
    import subprocess

    return subprocess.run(
        ["/usr/bin/pdftotext", "-upw", password, "-enc", "UTF-8", str(path), "-"],
        capture_output=True,
        check=True,
    ).stdout.decode()


def test_protect_restrictions_and_owner(tmp_path, pdf_file):
    run = run_tool(
        "pdf.protect",
        [pdf_file],
        _protect(allow_print=False, allow_copy=False, owner_password="boss"),
    )
    out = run.outputs[0]
    with pikepdf.open(out, password="abc123") as pdf:
        assert not pdf.owner_password_matched
        a = pdf.allow
        assert not a.print_lowres and not a.print_highres and not a.extract
        assert a.accessibility and a.modify_other and a.modify_annotation and a.modify_form
    with pikepdf.open(out, password="boss") as pdf:
        assert pdf.owner_password_matched


def test_protect_random_owner_when_restricted(pdf_file):
    run = run_tool("pdf.protect", [pdf_file], _protect(allow_edit=False))
    with pikepdf.open(run.outputs[0], password="abc123") as pdf:
        assert not pdf.owner_password_matched
        assert not pdf.allow.modify_other and not pdf.allow.modify_assembly
    pw = security.random_password()
    assert len(pw) == 24 and "/" not in pw and "+" not in pw


def test_protect_validation():
    check = security.password_problem
    assert check({"password": ""}) == "請輸入開啟密碼"
    assert check({"password": "密碼", "confirm": "密碼"}) == "密碼只能使用英文字母、數字與符號"
    assert check({"password": "a" * 33, "confirm": "a" * 33}) == "密碼最多 32 個字元"
    assert check({"password": "abc", "confirm": "abd"}) == "兩次輸入的密碼不一致"
    assert check({"password": "abc", "confirm": "abc", "owner_password": "管理"}) == (
        "權限密碼只能使用英文字母、數字與符號"
    )
    assert check({"password": "abc", "confirm": "abc", "owner_password": "abc"}) == (
        "權限密碼不能與開啟密碼相同"
    )
    assert check({"password": "abc", "confirm": "abc"}) is None
    t = tool("pdf.protect")
    assert t.validate([], {**t.defaults(), "password": "x", "confirm": "y"}, {"locked": []}) == (
        "兩次輸入的密碼不一致"
    )
    owner = t.option("owner_password")
    assert not owner.is_visible(t.defaults(), {})
    assert owner.is_visible({**t.defaults(), "allow_copy": False}, {})
    note = t.option("confirm").note
    assert note({"password": "a", "confirm": "b"}, {}) == ("兩次輸入的密碼不一致", True)
    assert note({"password": "中", "confirm": ""}, {}) == ("請只使用英文字母、數字與符號", True)
    assert note({"password": "", "confirm": ""}, {})[1] is False


def test_protect_job_errors(tmp_path, pdf_file):
    with pytest.raises(errors.QSError, match="兩次輸入的密碼不一致。"):
        run_tool("pdf.protect", [pdf_file], {"password": "a", "confirm": "b"})
    enc = encrypt(pdf_file, tmp_path / "已鎖.pdf")
    with pytest.raises(errors.QSError, match="「已鎖.pdf」已經加密，請先解鎖後再重新設定密碼。"):
        run_tool("pdf.protect", [enc], _protect())


# ------------------------------------------------------------------ 解鎖


def test_unlock_open_password(tmp_path):
    src = make_rich_pdf(tmp_path / "書.pdf", pages=3)
    locked = encrypt(src, tmp_path / "鎖.pdf", user="u1", owner="o1")
    before = digest(locked)
    run = run_tool("pdf.unlock", [locked], {"password": "u1"})
    out = run.outputs[0]
    assert out.name == "鎖-已解鎖.pdf"
    with pikepdf.open(out) as pdf:
        assert not pdf.is_encrypted
        assert "/StructTreeRoot" in pdf.Root and "/Outlines" in pdf.Root
    assert "第 2 頁" in pdftotext(out)
    assert digest(locked) == before


def test_unlock_permissions_need_owner(tmp_path, pdf_file):
    limited = encrypt(pdf_file, tmp_path / "限.pdf", owner="o1", extract=False)
    with pytest.raises(
        errors.QSError, match="「限.pdf」設有權限限制，請輸入「權限（擁有者）密碼」才能完整解鎖。"
    ):
        run_tool("pdf.unlock", [limited], {"password": "wrong"})
    run = run_tool("pdf.unlock", [limited], {"password": "o1"})
    with pikepdf.open(run.outputs[0]) as pdf:
        assert not pdf.is_encrypted


def test_unlock_errors(tmp_path, pdf_file):
    with pytest.raises(errors.QSError, match="「文件.pdf」沒有加密，不需要解鎖。"):
        run_tool("pdf.unlock", [pdf_file], {"password": "x"})
    locked = encrypt(pdf_file, tmp_path / "鎖.pdf", user="u1")
    with pytest.raises(errors.QSError, match="「鎖.pdf」的密碼不正確。"):
        run_tool("pdf.unlock", [locked], {"password": "nope"})
    bad = tmp_path / "壞.pdf"
    bad.write_bytes(b"garbage")
    with pytest.raises(errors.QSError, match="「壞.pdf」不是有效的 PDF，或檔案已損毀。"):
        run_tool("pdf.unlock", [bad], {"password": "x"})


def test_unlock_definition(tmp_path, pdf_file):
    t = tool("pdf.unlock")
    locked = encrypt(pdf_file, tmp_path / "鎖.pdf", user="u1")
    info = t.inspect([locked])
    # 解鎖工具允許選擇受密碼保護的檔案
    assert t.validate([locked], {"password": "u1"}, info) is None
    assert t.validate([locked], {"password": ""}, info) == "請輸入 PDF 的密碼"
    enc_note = t.option("_unlock_encrypted").note
    assert enc_note({}, info) == ("已加密：鎖.pdf", False)
    assert enc_note({}, {"encrypted_names": []}) is None


# ------------------------------------------------------------------ 文件資訊


def test_metadata_update(tmp_path):
    src = make_rich_pdf(tmp_path / "資.pdf", pages=3)
    t = tool("pdf.metadata")
    info = t.inspect([src])
    pre = t.prefill([src], info)
    assert pre["title"] == "原始標題" and pre["author"] == "作者甲"
    values = {**t.defaults(), **pre, "subject": " 主旨 ", "keywords": "甲，乙, 丙", "author": ""}
    run = run_tool("pdf.metadata", [src], values)
    out = run.outputs[0]
    assert out.name == "資-已更新資訊.pdf"
    with pikepdf.open(out) as pdf:
        d = pdf.docinfo
        assert str(d.Title) == "原始標題" and str(d.Subject) == "主旨"
        assert str(d.Keywords) == "甲, 乙, 丙"
        assert "/Author" not in d
        assert "/ModDate" in d
    assert "第 1 頁" in pdftotext(out)


def test_metadata_clear_all(tmp_path):
    src = make_rich_pdf(tmp_path / "資.pdf", pages=3)
    with pikepdf.open(src, allow_overwriting_input=True) as pdf:
        with pdf.open_metadata(set_pikepdf_as_editor=False) as meta:
            meta["dc:title"] = "XMP 標題"
        pdf.save(src)
    run = run_tool("pdf.metadata", [src], {"clear_all": True, "title": "不該寫入"})
    out = run.outputs[0]
    assert out.name == "資-已清除資訊.pdf"
    with pikepdf.open(out) as pdf:
        assert len(pdf.docinfo.keys()) == 0
        assert "/Metadata" not in pdf.Root


def test_metadata_syncs_xmp(tmp_path):
    src = make_rich_pdf(tmp_path / "x.pdf", pages=3)
    with pikepdf.open(src, allow_overwriting_input=True) as pdf:
        with pdf.open_metadata(set_pikepdf_as_editor=False) as meta:
            meta["dc:title"] = "舊的 XMP 標題"
        pdf.save(src)
    run = run_tool("pdf.metadata", [src], {"title": "新標題"})
    with pikepdf.open(run.outputs[0]) as pdf:
        meta = pdf.open_metadata()
        assert meta.get("dc:title") == "新標題"


def test_metadata_options_visibility():
    t = tool("pdf.metadata")
    assert not t.option("title").is_visible({"clear_all": True}, {})
    assert t.option("title").is_visible({"clear_all": False}, {})
    assert t.prefill([], {}) == {}
