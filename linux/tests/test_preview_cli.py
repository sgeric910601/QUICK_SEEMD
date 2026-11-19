"""預覽核心：quickseemd render（對應原版 qs-render）。"""

from __future__ import annotations

import re
from pathlib import Path

import pytest

from quickseemd import cli as main_cli
from quickseemd.core.preview import cli

LINE = re.compile(r"^\d+ms\t\d+KB\t\d+ att\t(.+)$")

PNG = (
    b"\x89PNG\r\n\x1a\n\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
    b"\x1f\x15\xc4\x89\x00\x00\x00\rIDATx\x9cc\xf8\x0f\x00\x00\x01\x01\x00\x05\x18\xd8N\x00\x00"
    b"\x00\x00IEND\xaeB`\x82"
)


def test_render_samples(samples: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    out = tmp_path / "new" / "out"
    assert cli.main([str(samples / "app.py"), str(samples / "README.md"), str(out)]) == 0
    lines = capsys.readouterr().out.splitlines()
    assert [LINE.match(x).group(1) for x in lines] == ["app.py.html", "README.md.html"]
    html = (out / "README.md.html").read_text("utf-8")
    assert "cid:" not in html
    assert 'src="README.md.md-img-1.png"' in html
    assert (out / "README.md.md-img-1.png").read_bytes() == (samples / "icon.png").read_bytes()
    icons = [p.name for p in out.iterdir() if ".file-icon." in p.name]
    assert sorted(n.split(".file-icon.")[0] for n in icons) == ["README.md", "app.py"]
    assert int(lines[1].split("\t")[2].split()[0]) == 2


def test_usage(capsys: pytest.CaptureFixture[str]) -> None:
    assert cli.main([]) == 1
    assert cli.main(["only-one"]) == 1
    assert "用法：quickseemd render <檔案...> <輸出資料夾>" in capsys.readouterr().err


def test_spaces_and_no_overwrite(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    src = tmp_path / "my file.txt"
    src.write_text("hi\n", "utf-8")
    out = tmp_path / "out"
    out.mkdir()
    keep = out / "my_file.txt.html"
    keep.write_text("keep", "utf-8")
    assert cli.main([str(src), str(out)]) == 0
    assert keep.read_text("utf-8") == "keep"
    new = out / "my_file.txt_(1).html"
    assert new.exists()
    assert "my_file.txt_%281%29.file-icon." in new.read_text("utf-8")  # 屬性裡的檔名經網址編碼
    assert LINE.match(capsys.readouterr().out.strip()).group(1) == "my_file.txt_(1).html"


def test_cid_references_are_replaced_exactly(tmp_path: Path) -> None:
    md = tmp_path / "x.md"
    for i in range(10):
        (tmp_path / f"{i}.png").write_bytes(PNG + bytes([i]))
    md.write_text("".join(f"![{i}]({i}.png) " for i in range(10)), "utf-8")
    out = tmp_path / "out"
    assert cli.main([str(md), str(out)]) == 0
    html = (out / "x.md.html").read_text("utf-8")
    for i in range(1, 11):
        assert f'src="x.md.md-img-{i}.png"' in html
    assert (out / "x.md.md-img-10.png").read_bytes() == PNG + bytes([9])


def test_through_main_cli(
    samples: Path, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert main_cli.main(["render", str(samples / "data.csv"), str(tmp_path)]) == 0
    assert (tmp_path / "data.csv.html").exists()
    assert "data.csv.html" in capsys.readouterr().out


def test_missing_input_still_renders(tmp_path: Path) -> None:
    assert cli.main([str(tmp_path / "nope.txt"), str(tmp_path / "o")]) == 0
    assert "找不到檔案" in (tmp_path / "o" / "nope.txt.html").read_text("utf-8")


def test_output_names_cannot_break_out_of_attributes(tmp_path: Path) -> None:
    src = tmp_path / 'a" onerror="alert(1).txt'
    src.write_text("x\n", "utf-8")
    out = tmp_path / "out"
    assert cli.main([str(src), str(out)]) == 0
    html = next(out.glob("*.html")).read_text("utf-8")
    assert 'onerror="alert(1)' not in html
    assert "a%22_onerror%3D%22alert%281%29.txt.file-icon." in html
