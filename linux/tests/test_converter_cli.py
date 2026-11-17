"""萬用轉換的命令列（對應原版 qs-convert）。"""

from __future__ import annotations

from pathlib import Path

import pytest
from conftest import make_pdf

from quickseemd import cli as main_cli
from quickseemd.core.converter import cli


def test_formats(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--formats"]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0] == "png          image     .png  PNG"
    assert "utf8         text      .(同來源)  UTF-8" in lines
    assert "json-pretty  data      .json  JSON（排版）" in lines
    assert "dmg" not in " ".join(line.split()[0] for line in lines)


def test_backends(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--backends"]) == 0
    out = capsys.readouterr().out
    assert "ffmpeg: /" in out
    assert "ImageMagick: 未安裝（sudo apt install imagemagick）" in out
    assert "圖片可寫入：png jpeg heic avif webp tiff gif bmp jp2 ico icns tga" in out
    assert "音訊可輸出：m4a alac aac wav aiff caf flac" in out
    assert "影片可輸出：mp4 mp4-hevc mov m4v mov-hevc prores" in out


def test_list_sample(samples: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--list", str(samples / "data.csv")]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].startswith("# data.csv — ") and lines[0].endswith("（UTF-8 · LF）")
    assert lines[1] == "xlsx         Excel (XLSX)"
    assert "utf8-bom     UTF-8（含 BOM）  — Windows Excel 開啟不亂碼" in lines
    assert "pdf          PDF  [LibreOffice]" in lines


def test_list_hints_and_empty(
    samples: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["--list", str(samples / "config.yaml")]) == 0
    assert "✕ JSON、Plist：目前不支援讀取 YAML（只能輸出 YAML）。" in capsys.readouterr().out
    folder = tmp_path / "f"
    folder.mkdir()
    (folder / "a.txt").write_text("x")
    (folder / ".hidden").write_text("x")
    assert cli.main(["--list", str(folder)]) == 0
    assert capsys.readouterr().out.splitlines()[0] == "# f — 資料夾（1 個項目）"


def test_list_missing(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main(["--list", str(tmp_path / "nope")]) == 1
    assert f"找不到檔案：{tmp_path / 'nope'}" in capsys.readouterr().err


def test_convert_with_progress(
    samples: Path, outdir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    code = cli.main(
        [str(samples / "data.csv"), "json", str(outdir), "--progress", "--encoding", "utf8"]
    )
    assert code == 0
    captured = capsys.readouterr()
    assert captured.out.strip() == str(outdir / "data.json")
    assert "進度 0%" in captured.err and "進度 100%" in captured.err
    assert "完成（" in captured.err and captured.err.rstrip().endswith("秒）")


def test_convert_options_are_applied(
    tmp_path: Path, outdir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from PIL import Image

    from conftest import make_image

    src = make_image(tmp_path / "p.png")
    assert cli.main([str(src), "jpg", str(outdir), "--max-size", "100", "--quality", "0.5"]) == 0
    out = Path(capsys.readouterr().out.strip())
    with Image.open(out) as img:
        assert max(img.size) == 100


def test_convert_relative_paths(
    samples: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import shutil

    shutil.copy2(samples / "app.py", tmp_path / "app.py")
    (tmp_path / "out").mkdir()
    monkeypatch.chdir(tmp_path)
    assert cli.main(["app.py", "crlf", "out"]) == 0
    assert (tmp_path / "out" / "app.py").read_bytes().count(b"\r\n") > 0


def test_convert_errors(samples: Path, outdir: Path, capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main([str(samples / "app.py"), "nope", str(outdir)]) == 1
    assert "錯誤：未知的格式代碼「nope」。" in capsys.readouterr().err
    assert cli.main([str(samples / "app.py"), "utf8", str(outdir)]) == 1
    assert "錯誤：「app.py」無法轉換成 UTF-8。" in capsys.readouterr().err


def test_unknown_option_and_usage(
    samples: Path, outdir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main([str(samples / "app.py"), "crlf", str(outdir), "--bogus", "1"]) == 2
    err = capsys.readouterr().err
    assert "未知選項：--bogus" in err and "用法：" in err
    for argv in ([], ["--list"], ["--merge-pdf", "x"], ["a", "b"]):
        assert cli.main(argv) == 2


def test_merge_pdf(
    tmp_path: Path, outdir: Path, jpg_file: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    pdf = make_pdf(tmp_path / "d.pdf", pages=1)
    assert cli.main(["--merge-pdf", str(outdir), str(jpg_file), str(pdf)]) == 0
    assert capsys.readouterr().out.strip() == str(outdir / "合併文件.pdf")
    assert cli.main(["--merge-pdf", str(outdir), str(tmp_path / "missing.txt")]) == 1
    assert "錯誤：" in capsys.readouterr().err


def test_parse_options() -> None:
    opts, progress = cli.parse_options(
        [
            "--video-size",
            "999",
            "--frame-time",
            "x",
            "--gif-fps",
            "10",
            "--max-size",
            "nope",
            "--progress",
        ]
    )
    assert opts == {"video_size": 0, "frame_time": None, "gif_fps": 10, "image_max_pixel": None}
    assert progress is True
    assert cli.parse_options(["--video-size", "720", "--dpi", "300"])[0] == {
        "video_size": 720,
        "pdf_dpi": 300.0,
    }


def test_main_cli_routes_convert(capsys: pytest.CaptureFixture[str]) -> None:
    assert main_cli.main(["convert", "--formats"]) == 0
    assert capsys.readouterr().out.startswith("png ")
