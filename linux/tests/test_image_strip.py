"""移除相片資訊（exiftool -o，不就地修改）與重新編碼的備援。"""

from __future__ import annotations

import pytest
from conftest import make_image
from PIL import Image
from test_image_kit import CAMERA_TAGS, digest, exiftool_json, run_tool, tag_names, tool, with_metadata

from quickseemd.core import backends, errors
from quickseemd.core.tools.image import formats, metadata

PRIVATE_EXTRA = (
    "-XMP-exif:GPSLatitude=10",
    "-XMP-iptcExt:LocationCreatedCity=Paris",
    "-XMP-iptcExt:LocationShownSublocation=Montmartre",
    "-XMP-photoshop:State=TW",
    "-XMP-dc:Creator=Me",
)


def _leaks(path, mode):
    bad = []
    for key, value in exiftool_json(path).items():
        group, _s, name = key.partition(":")
        if group in ("System", "ExifTool", "Composite", "SourceFile") or key == "SourceFile":
            continue
        location = name.startswith(("GPS", "LocationCreated", "LocationShown")) or name in (
            "City",
            "State",
            "Location",
            "Sub-location",
        )
        private = group in ("XMP", "IPTC") or "Comment" in name or "secret" in str(value)
        if location or (mode == "all" and (private or name in ("Model", "Make", "Artist"))):
            bad.append(key)
    return bad


@pytest.fixture
def photo(tmp_path):
    base = make_image(tmp_path / "base.jpg", size=(200, 100), quality=90)
    return with_metadata(
        base, tmp_path / "旅行.jpg", *CAMERA_TAGS, *PRIVATE_EXTRA, "-Comment=secret", "-Orientation=6", "-n"
    )


def test_location_mode_keeps_camera_and_is_lossless(photo, outdir):
    before = digest(photo)
    outs, ctx = run_tool("image.strip-metadata", [photo], outdir, preset="image.strip-location")
    out = outs[0]
    assert out.name == "旅行-已移除位置.jpg"
    assert _leaks(out, "location") == []
    names = tag_names(out)
    assert {"Model", "DateTimeOriginal", "Orientation"} <= names
    assert ctx.last_message == "已移除：位置（GPS）（未重新壓縮）"
    assert digest(photo) == before
    assert not list(photo.parent.glob("*_original"))
    with Image.open(photo) as a, Image.open(out) as b:
        assert a.tobytes() == b.tobytes()  # 影像資料完全相同


def test_all_mode_removes_everything_but_orientation(photo, outdir):
    outs, ctx = run_tool("image.strip-metadata", [photo], outdir)
    out = outs[0]
    assert out.name == "旅行-已移除資訊.jpg"
    assert _leaks(out, "all") == []
    assert exiftool_json(out)["EXIF:Orientation"] == "Rotate 90 CW"
    assert ctx.last_message == (
        "已移除：位置（GPS）、相機與鏡頭、拍攝時間、拍攝參數、作者與版權、編輯軟體（未重新壓縮）"
    )


def test_png_text_and_xmp_removed(tmp_path, outdir):
    base = make_image(tmp_path / "a.png", size=(64, 48))
    leaky = with_metadata(
        base, tmp_path / "圖.png", "-PNG:Comment=secret", "-XMP-exif:GPSLatitude=10", "-Model=X"
    )
    for mode in ("location", "all"):
        outs, _ = run_tool("image.strip-metadata", [leaky], outdir, mode=mode)
        assert _leaks(outs[0], mode) == []
        assert outs[0].suffix == ".png"


def test_icc_profile_kept(tmp_path, outdir):
    from PIL import ImageCms

    icc = ImageCms.ImageCmsProfile(ImageCms.createProfile("sRGB")).tobytes()
    p = tmp_path / "icc.jpg"
    Image.new("RGB", (32, 32), "red").save(p, icc_profile=icc, quality=90)
    meta = with_metadata(p, tmp_path / "m.jpg", *CAMERA_TAGS)
    outs, _ = run_tool("image.strip-metadata", [meta], outdir)
    with Image.open(outs[0]) as im:
        assert im.info.get("icc_profile")


def test_no_metadata_messages(jpg_file, outdir):
    _, ctx = run_tool("image.strip-metadata", [jpg_file], outdir)
    assert ctx.last_message == "沒有找到相片資訊，已輸出副本"
    _, ctx = run_tool("image.strip-metadata", [jpg_file], outdir, mode="location")
    assert ctx.last_message == "沒有找到位置資訊，已輸出副本"


def test_tiff_camera_tags_removed(tmp_path, outdir):
    base = tmp_path / "a.tif"
    Image.new("RGB", (40, 30), "blue").save(base, compression="tiff_lzw")
    meta = with_metadata(base, tmp_path / "m.tif", *CAMERA_TAGS)
    outs, _ = run_tool("image.strip-metadata", [meta], outdir)
    assert _leaks(outs[0], "all") == []


def test_heic_location(tmp_path, outdir):
    if not formats.is_writable("heic"):
        pytest.skip("沒有 heif-enc")
    png = make_image(tmp_path / "a.png", size=(64, 48))
    outs, _ = run_tool("image.convert", [png], tmp_path, format="heic")
    meta = with_metadata(outs[0], tmp_path / "m.heic", *CAMERA_TAGS)
    for mode in ("location", "all"):
        outs2, _ = run_tool("image.strip-metadata", [meta], outdir, mode=mode)
        assert outs2[0].suffix == ".heic"
        assert _leaks(outs2[0], mode) == []


def test_fallback_reencode_without_exiftool(photo, outdir, monkeypatch):
    names_before = tag_names(photo)
    assert "Model" in names_before
    real = backends.find
    monkeypatch.setattr(backends, "find", lambda key: None if key == "exiftool" else real(key))
    outs, ctx = run_tool("image.strip-metadata", [photo], outdir)
    monkeypatch.undo()
    # 沒有 exiftool 時用 Pillow 讀 EXIF 列出類別；重新編碼所以沒有「未重新壓縮」
    assert ctx.last_message == (
        "已移除：位置（GPS）、相機與鏡頭、拍攝時間、拍攝參數、作者與版權、編輯軟體"
    )
    assert _leaks(outs[0], "all") == []
    with Image.open(outs[0]) as im:
        assert im.size == (100, 200)  # 重新編碼時像素已轉正
    assert outs[0].stat().st_size <= photo.stat().st_size * 1.05 + 2048


def test_bmp_reencoded(tmp_path, outdir):
    p = make_image(tmp_path / "a.bmp", size=(40, 30))
    outs, ctx = run_tool("image.strip-metadata", [p], outdir)
    assert outs[0].name == "a-已移除資訊.bmp"


def test_categories_and_inspect(photo):
    cats = metadata.categories(photo, "all")
    assert cats == ["位置（GPS）", "相機與鏡頭", "拍攝時間", "拍攝參數", "作者與版權", "編輯軟體"]
    assert metadata.categories(photo, "location") == ["位置（GPS）"]
    t = tool("image.strip-metadata")
    info = t.inspect([photo])
    assert info["found"] == cats
    note = t.option("_found").note(t.defaults(), info)
    assert note == ("位置（GPS）、相機與鏡頭、拍攝時間、拍攝參數、作者與版權、編輯軟體", False)
    assert t.option("_found").note(t.defaults(), {**info, "found": []}) == ("沒有找到相片資訊。", False)
    assert not t.option("_found").is_visible(t.defaults(), {"count": 2})


def test_other_metadata_category(tmp_path):
    base = make_image(tmp_path / "a.jpg", quality=90)
    other = with_metadata(base, tmp_path / "o.jpg", "-ImageDescription=hello")
    assert metadata.categories(other, "all") == ["其他中繼資料"]


def test_missing_file_errors(tmp_path):
    with pytest.raises(errors.QSError, match="找不到檔案"):
        metadata.strip(tmp_path / "nope.jpg", "all", tmp_path / "x")
