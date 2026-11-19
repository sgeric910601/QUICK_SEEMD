"""預覽視窗的畫面測試（隔離畫面＋私有 D-Bus）：透過 org.nemo.Preview 叫出各種檔案、按鍵、附屬視窗。

設定 QS_TEST_DISPLAY=:138 之類的隔離畫面才會執行；截圖可以用 QS_GUI_SHOT_DIR 另存一份檢查。
"""

from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path

import pytest
from local_http import RecordingServer, hls_disguised_as_mp3
from previewer_gui_support import Session, make_session, run_driver, write_service

import conftest

pytestmark = [pytest.mark.gui, pytest.mark.slow]

TEXT = "第一行：中文預覽測試\nsecond line\n" + "\n".join(f"第 {i} 行" for i in range(3, 60))


def _make_files(root: Path, server: RecordingServer) -> dict[str, str]:
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
        **_engine_files(files, server),
        "hls": str(hls_disguised_as_mp3(files / "track01.mp3", server)),
        "http_uri": server.url("/遠端文件.txt"),
        "font": str(make_block_font(files / "方塊字型.ttf")),
        "short_audio": str(conftest.make_audio(files / "短聲音.wav", seconds=2)),
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


def make_block_font(path: Path) -> Path:
    """每個字都是實心方塊的字型：真的載入時樣張會是一大片黑色，用像素就能判斷。"""
    from fontTools.fontBuilder import FontBuilder  # noqa: PLC0415
    from fontTools.pens.ttGlyphPen import TTGlyphPen  # noqa: PLC0415

    from quickseemd.core.preview import font  # noqa: PLC0415

    chars = set("".join(font.GLYPHS + font.SAMPLES)) - {" "}
    builder = FontBuilder(1000, isTTF=True)
    builder.setupGlyphOrder([".notdef", "space", "block"])
    cmap = {ord(c): "block" for c in chars}
    cmap[32] = "space"
    builder.setupCharacterMap(cmap)
    pen = TTGlyphPen(None)
    pen.moveTo((0, -200))
    pen.lineTo((0, 800))
    pen.lineTo((1000, 800))
    pen.lineTo((1000, -200))
    pen.closePath()
    empty = TTGlyphPen(None).glyph()
    builder.setupGlyf({".notdef": empty, "space": empty, "block": pen.glyph()})
    builder.setupHorizontalMetrics({".notdef": (500, 0), "space": (500, 0), "block": (1000, 0)})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable({"familyName": "QSBlocks", "styleName": "Regular"})
    builder.setupOS2(sTypoAscender=800, sTypoDescender=-200, usWinAscent=800, usWinDescent=200)
    builder.setupPost()
    builder.save(str(path))
    return path


def _engine_files(files: Path, server: RecordingServer) -> dict[str, str]:
    """交給真正預覽核心的檔案（Markdown 有本機與網路圖片、程式碼、表格、JSON、壓縮檔）。"""
    pic = conftest.make_image(files / "插圖.png", size=(200, 120))
    md = files / "說明文件.md"
    md.write_text(
        f"# 預覽測試\n\n本機圖片：![插圖]({pic.name})\n\n"
        f"網路圖片（不能載入）：![遠端]({server.url('/remote.png')})\n\n"
        "<script>document.title='壞'</script>\n\n```python\nprint('你好')\n```\n",
        "utf-8",
    )
    code = files / "程式.py"
    code.write_text("def 問候(名字: str) -> str:\n    return f'你好，{名字}'\n" * 20, "utf-8")
    csv = files / "表格.csv"
    csv.write_text("名稱,數量\n" + "".join(f"項目{i},{i * 3}\n" for i in range(40)), "utf-8")
    data = files / "資料.json"
    data.write_text(json.dumps({"名稱": "測試", "清單": list(range(30))}, ensure_ascii=False))
    archive = files / "壓縮檔.zip"
    with zipfile.ZipFile(archive, "x") as z:
        for i in range(12):
            z.writestr(f"資料夾/檔案{i}.txt", "內容" * i)
    keys = {"markdown": md, "code": code, "csv": csv, "json": data, "zip": archive}
    out = {k: str(v) for k, v in keys.items()}
    out["engine_pages"] = sorted(keys)  # type: ignore[assignment]
    return out


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
    marker = s.root / "opened.txt"
    _fake_default_app(s, marker)
    write_service(
        s, "org.nemo.Preview", "/usr/bin/python3 -m quickseemd.previewer"
    )  # PYTHONPATH 指向這份原始碼（環境變數由 run_driver 設定）
    with RecordingServer() as server:  # 預覽過程中任何連到這裡的請求都是錯誤
        files = _make_files(s.root, server)
        data = run_driver(s, "preview", {"files": files, "marker": str(marker)})
    assert "error" not in data, data.get("error")
    data["_files"] = files
    data["_network"] = {"requests": server.requests, "connections": server.connections}
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


def test_media_controls(report):
    r = report["media"]
    assert r["shown"]
    assert r["ended"]  # 播完：按鈕變成「播放」
    assert r["replaying"]  # 按下：從頭播放（變回「暫停」）
    assert r["paused"]
    assert r["still_open"]


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
    assert r["after_double_press"]
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


@pytest.mark.parametrize("key", ["markdown", "code", "csv", "json", "zip"])
def test_real_preview_engine_pages(report, key: str):
    _assert_page(report["engine"][key], min_colors=8)


def test_font_specimen_uses_the_embedded_font(report):
    """字型檔透過自訂網址提供並真的套用：方塊字型讓樣張變成一大片黑色。"""
    font = report["font_stream"]["font"]
    _assert_page(font)
    baseline = report["basics"]["text"]["dark_ratio"]
    assert font["dark_ratio"] > 0.15, font["dark_ratio"]
    assert font["dark_ratio"] > 3 * baseline


def test_unsupported_uri_is_refused(report):
    _assert_page(report["basics"]["http"])


def test_hls_disguised_as_mp3_shows_error(report):
    _assert_page(report["font_stream"]["hls"])


def test_nothing_touched_the_network(report):
    """HLS 偽裝檔、http 網址、Markdown 裡的網路圖片：測試伺服器都不能收到任何連線。"""
    assert report["_network"] == {"requests": [], "connections": 0}


def test_window_manager_was_ready(report):
    assert report["wm_ready"]
