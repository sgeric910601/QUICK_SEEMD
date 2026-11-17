"""預覽視窗的畫面測試（隔離畫面＋私有 D-Bus）：透過 org.nemo.Preview 叫出各種檔案、按鍵、附屬視窗。

設定 QS_TEST_DISPLAY=:138 之類的隔離畫面才會執行；截圖可以用 QS_GUI_SHOT_DIR 另存一份檢查。
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from previewer_gui_support import Session, make_session, run_driver, write_service

import conftest

pytestmark = [pytest.mark.gui, pytest.mark.slow]

TEXT = "第一行：中文預覽測試\nsecond line\n" + "\n".join(f"第 {i} 行" for i in range(3, 60))


def _make_files(root: Path) -> dict[str, str]:
    import docx  # noqa: PLC0415

    files = root / "檔案"
    files.mkdir()
    text = files / "說明.txt"
    text.write_text(TEXT, "utf-8")
    big = files / "很大的紀錄.log"
    big.write_text("紀錄 " * 400_000, "utf-8")
    folder = files / "資料夾"
    folder.mkdir()
    (folder / "裡面.txt").write_text("x", "utf-8")
    fifo = files / "管線"
    os.mkfifo(fifo)
    document = docx.Document()
    document.add_heading("預覽測試文件", 0)
    document.add_paragraph("這份文件用 LibreOffice 轉成 PDF 後顯示。")
    document.save(files / "報告.docx")
    gif = files / "動畫.gif"
    _make_gif(gif)
    return {
        "gif": str(gif),
        "text": str(text),
        "big": str(big),
        "image": str(conftest.make_image(files / "相片.jpg", size=(1600, 1000), quality=90)),
        "pdf": str(conftest.make_pdf(files / "文件.pdf", pages=3)),
        "video": str(conftest.make_video(files / "影片.mp4", seconds=4, size=(640, 360))),
        "audio": str(conftest.make_audio(files / "聲音.wav", seconds=4)),
        "folder": str(folder),
        "fifo": str(fifo),
        "office": str(files / "報告.docx"),
    }


def _make_gif(path: Path) -> None:
    from PIL import Image  # noqa: PLC0415

    frames = [Image.new("RGB", (320, 200), c) for c in ("red", "green", "blue", "orange")]
    frames[0].save(path, save_all=True, append_images=frames[1:], duration=80, loop=0)


def _fake_default_app(s: Session, marker: Path) -> None:
    """「用預設程式開啟」用的假程式：只把收到的網址寫進檔案。"""
    opener = s.root / "opener.sh"
    opener.write_text(f'#!/bin/sh\nprintf "%s\\n" "$1" > "{marker}"\n', "utf-8")
    opener.chmod(0o755)
    apps = s.data / "applications"
    apps.mkdir(parents=True, exist_ok=True)
    (apps / "qs-test-open.desktop").write_text(
        "[Desktop Entry]\nType=Application\nName=測試開啟\n"
        f"Exec={opener} %u\nMimeType=text/plain;\n",
        "utf-8",
    )
    (s.config / "mimeapps.list").write_text(
        "[Default Applications]\ntext/plain=qs-test-open.desktop\n", "utf-8"
    )


@pytest.fixture(scope="module")
def report(tmp_path_factory: pytest.TempPathFactory) -> dict:
    s = make_session(tmp_path_factory.mktemp("gui"))
    files = _make_files(s.root)
    marker = s.root / "opened.txt"
    _fake_default_app(s, marker)
    write_service(
        s, "org.nemo.Preview", "/usr/bin/python3 -m quickseemd.previewer"
    )  # PYTHONPATH 指向這份原始碼（環境變數由 run_driver 設定）
    data = run_driver(s, "preview", {"files": files, "marker": str(marker)})
    assert "error" not in data, data.get("error")
    data["_files"] = files
    return data


def _assert_page(r: dict, min_colors: int = 3) -> None:
    assert r["shown"], r
    assert "_NET_WM_WINDOW_TYPE_DIALOG" in r["types"]
    assert r["colors"] >= min_colors
    assert r["reply_ms"] < 3000  # 先回覆再畫，Nemo 不用等
    # 畫面上只有預覽視窗（播放器之類不能自己另開視窗）
    others = [t for t in r["all_titles"] if t != "Openbox"]  # 視窗管理員自己的輔助視窗
    assert others == [r["title"]], r["all_titles"]


def test_text_preview(report):
    r = report["basics"]["text"]
    _assert_page(r, min_colors=10)
    # 沒有父視窗：在螢幕置中
    assert abs((r["x"] + r["width"] / 2) - 640) < 40


def test_image_preview_reuses_window_and_fits(report):
    b = report["basics"]
    r = b["image"]
    _assert_page(r, min_colors=50)
    assert r["id"] == b["text"]["id"]  # 換內容，不是開新視窗
    # 1600×1000 的圖要縮到螢幕 85% 以內（整個視窗，含標題列與邊框）
    assert r["width"] <= 1088 + 12 and r["height"] <= 680 + 12


def test_animated_gif_preview(report):
    r = report["basics"]["gif"]
    _assert_page(r, min_colors=2)
    assert r["width"] >= 320


def test_media_controls_do_not_break_the_window(report):
    r = report["media"]
    assert r["shown"] and r["still_open"]


def test_pdf_preview(report):
    _assert_page(report["basics"]["pdf"], min_colors=10)


def test_video_preview(report):
    r = report["basics"]["video"]
    _assert_page(r, min_colors=20)
    assert report["basics"]["same_file_closes"]


def test_audio_preview(report):
    _assert_page(report["basics"]["audio"])


def test_fifo_is_shown_as_info_without_hanging(report):
    _assert_page(report["basics"]["fifo"])
    # 管線之後的請求都還能處理（服務沒有卡住）
    assert report["basics"]["folder"]["shown"]


def test_folder_and_invalid_uri(report):
    _assert_page(report["basics"]["folder"])
    _assert_page(report["basics"]["invalid"])


def test_office_document_is_converted(report):
    _assert_page(report["basics"]["office"], min_colors=10)


def test_close_method(report):
    assert report["basics"]["close_method"]


@pytest.mark.parametrize("key", ["space", "Escape", "q"])
def test_keys_close_the_preview(report, key: str):
    assert report["keys"][key]


def test_fullscreen_toggle(report):
    k = report["keys"]
    assert k["fullscreen"]["width"] == 1280 and k["fullscreen"]["height"] == 800
    assert k["unfullscreen"]["width"] < 1280


def test_rapid_switch_and_double_press(report):
    r = report["rapid"]
    assert r["switch_final_title"] == "文件.pdf"
    assert r["stale_title_absent"]
    assert r["double_press_closed"]


def test_transient_for_parent_and_centered(report):
    r = report["transient"]
    assert r["shown"]
    assert r["transient_for"] == r["parent_xid"]
    parent = r["parent"]
    parent_center = (parent["x"] + parent["width"] / 2, parent["y"] + parent["height"] / 2)
    center = (r["x"] + r["width"] / 2, r["y"] + r["height"] / 2)
    assert abs(center[0] - parent_center[0]) < 60
    assert abs(center[1] - parent_center[1]) < 60
    assert r["closed"]


def test_open_with_default_app(report):
    r = report["open_button"]
    # 系統開啟本機檔案時，可能把網址換成路徑交給程式
    text = Path(report["_files"]["text"])
    assert r["opened"] in (str(text), text.as_uri())
    assert r["closed"]


def test_service_stops_cleanly(report):
    assert report["service_stopped"]
