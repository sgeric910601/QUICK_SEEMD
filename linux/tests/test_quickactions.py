from __future__ import annotations

from quickseemd.core import quickactions as qa
from quickseemd.core.filetypes import FOLDER_MIME, FileRef

PDF = FileRef("/x/a.pdf", "application/pdf")
PDF2 = FileRef("/x/b.pdf", "application/pdf")
JPG = FileRef("/x/c.jpg", "image/jpeg")
MP4 = FileRef("/x/d.mp4", "video/mp4")
ZIP = FileRef("/x/e.zip", "application/zip")
TXT = FileRef("/x/f.txt", "text/plain")
PY = FileRef("/x/g.py", "text/x-python")
DIR = FileRef("/x/folder", FOLDER_MIME, True)


def ids(actions):
    return [a.id for a in actions]


def test_manifest_has_38_actions_and_10_disabled_office():
    assert len(qa.ACTIONS) == 38
    assert len({a.id for a in qa.ACTIONS}) == 38
    disabled = [a for a in qa.ACTIONS if not a.enabled]
    assert len(disabled) == 10 and all(a.group == "Office" for a in disabled)


def test_menu_single_pdf_hides_merge():
    got = ids(qa.menu_actions([PDF]))
    assert "pdf.merge-pdfs" not in got
    assert got[:2] == ["pdf.compress-recommended", "pdf.to-jpg"]
    assert "other.zip" in got and "office.pdf-to-docx" not in got


def test_menu_two_pdfs_shows_merge():
    assert "pdf.merge-pdfs" in ids(qa.menu_actions([PDF, PDF2]))


def test_menu_folder_only_zip():
    assert ids(qa.menu_actions([DIR])) == ["other.zip"]


def test_menu_single_image_keeps_images_to_pdf():
    # 原版規則只看 id 是否含 merge，「圖片合併成 PDF」單張也會出現
    assert "pdf.images-to-pdf" in ids(qa.menu_actions([JPG]))


def test_menu_mixed_types():
    got = ids(qa.menu_actions([PDF, JPG]))
    assert got == ["other.zip"]


def test_text_and_code_get_utf8():
    assert "other.to-utf8" in ids(qa.menu_actions([TXT]))
    assert "other.to-utf8" in ids(qa.menu_actions([PY]))


def test_archive_unzip():
    assert "other.unzip" in ids(qa.menu_actions([ZIP]))


def test_wheel_uses_minimum_files():
    mins = {"pdf.merge": 2}
    assert "pdf.merge-pdfs" not in ids(qa.wheel_actions([PDF], mins))
    assert "pdf.merge-pdfs" in ids(qa.wheel_actions([PDF, PDF2], mins))


def test_grouped_keeps_order():
    groups = qa.grouped(qa.menu_actions([MP4]))
    assert [g for g, _ in groups] == ["影音", "其他"]


def test_wheel_skips_actions_without_registered_tool():
    got = ids(qa.wheel_actions([PDF], {}, registered={"pdf.compress"}))
    assert got == ["pdf.compress-recommended"]
