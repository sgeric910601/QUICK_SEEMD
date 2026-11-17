"""擷取音訊與轉換音訊格式。"""

from __future__ import annotations

import pytest
from conftest import make_audio, make_video
from test_media_support import duration, encode_audio, note_texts, run_tool, streams

from quickseemd.core import errors
from quickseemd.core.result import format_size


def test_extract_default_preset(video_file, outdir):
    outputs, ctx = run_tool("video.extract-audio", [video_file], outdir, preset="video.audio-m4a")
    out = outputs[0]
    assert out.name == "影片.m4a"
    a = streams(out, "audio")
    assert len(a) == 1 and a[0]["codec_name"] == "aac"
    assert streams(out, "video") == []
    assert ctx.last_message == (
        f"已擷取 0:02 的音訊，存成 M4A (AAC)（{format_size(out.stat().st_size)}）"
    )


@pytest.mark.parametrize(
    ("fmt", "name", "codec", "fmt_name"),
    [
        ("alac", "影片.m4a", "alac", "mov,mp4,m4a,3gp,3g2,mj2"),
        ("wav", "影片.wav", "pcm_s16le", "wav"),
        ("aiff", "影片.aiff", "pcm_s16be", "aiff"),
        ("flac", "影片.flac", "flac", "flac"),
        ("caf", "影片.caf", "pcm_s16le", "caf"),
        ("mp3", "影片.mp3", "mp3", "mp3"),
        ("ogg", "影片.ogg", "vorbis", "ogg"),
    ],
)
def test_extract_formats(video_file, outdir, fmt, name, codec, fmt_name):
    from test_media_support import probe

    outputs, _ = run_tool("video.extract-audio", [video_file], outdir, format=fmt)
    out = outputs[0]
    assert out.name == name
    data = probe(out)
    assert data["format"]["format_name"] == fmt_name
    assert [s["codec_name"] for s in data["streams"]] == [codec]
    assert abs(float(data["format"]["duration"]) - 2.0) < 0.1


def test_extract_from_silent_video_fails(tmp_path, outdir):
    src = make_video(tmp_path / "無聲.mp4", audio=False)
    with pytest.raises(errors.QSError, match="^這個檔案沒有音訊軌，無法擷取聲音。$"):
        run_tool("video.extract-audio", [src], outdir)


def test_audio_convert_to_m4a_preset(audio_file, outdir):
    outputs, ctx = run_tool("audio.convert", [audio_file], outdir, preset="audio.convert-m4a")
    out = outputs[0]
    assert out.name == "聲音.m4a"
    a = streams(out, "audio")[0]
    assert a["codec_name"] == "aac"
    # 單聲道 AAC 最高 256 kbps
    assert int(a["bit_rate"]) <= 260_000
    sizes = f"{format_size(audio_file.stat().st_size)} → {format_size(out.stat().st_size)}"
    assert ctx.last_message == f"已轉成 M4A (AAC)：{sizes}"


def test_audio_convert_same_ext_gets_tag(audio_file, outdir):
    outputs, _ = run_tool("audio.convert", [audio_file], outdir, preset="audio.convert-wav")
    assert outputs[0].name == "聲音-已轉換.wav"


def test_audio_convert_rate_and_channels(audio_file, outdir):
    outputs, _ = run_tool(
        "audio.convert", [audio_file], outdir, format="flac", sample_rate=48_000, channels=2
    )
    a = streams(outputs[0], "audio")[0]
    assert (a["sample_rate"], a["channels"]) == ("48000", 2)


def test_audio_convert_keeps_24_bit(tmp_path, outdir):
    src = encode_audio(
        make_audio(tmp_path / "s.wav", seconds=1), tmp_path / "高.wav", "-c:a", "pcm_s24le"
    )
    outputs, _ = run_tool("audio.convert", [src], outdir, format="aiff")
    assert streams(outputs[0], "audio")[0]["codec_name"] == "pcm_s24be"
    alac, _ = run_tool("audio.convert", [src], outdir, format="alac")
    assert streams(alac[0], "audio")[0]["bits_per_raw_sample"] == "24"


def test_audio_convert_mp3_high_rate(tmp_path, outdir):
    from test_media_support import run_ffmpeg

    src = tmp_path / "96k.wav"
    run_ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=1:sample_rate=96000", str(src))
    outputs, _ = run_tool("audio.convert", [src], outdir, format="mp3", bitrate=128)
    a = streams(outputs[0], "audio")[0]
    assert a["sample_rate"] == "48000"
    assert abs(duration(outputs[0]) - 1.0) < 0.1


def test_audio_convert_batch(audio_file, tmp_path, outdir):
    other = make_audio(tmp_path / "二.wav", seconds=1)
    outputs, ctx = run_tool("audio.convert", [audio_file, other], outdir, format="ogg")
    assert [p.name for p in outputs] == ["聲音.ogg", "二.ogg"]
    assert ctx.last_message.startswith("已完成 2 個檔案，共 ")


def test_audio_notes(audio_file):
    notes = note_texts("audio.convert", [audio_file])
    assert "檔案小、幾乎所有裝置都能播放" in notes
    assert "高音質（建議）" not in notes  # 預設 256 kbps
    assert "非常高音質" in notes
    assert "預估約 64 KB" in notes
    wav = note_texts("audio.convert", [audio_file], format="wav")
    assert not any(n.startswith("預估約") for n in wav)


def test_bitrate_visibility():
    from test_media_support import tool

    opt = tool("video.extract-audio").option("bitrate")
    assert opt.is_visible({"format": "mp3"}, {})
    assert not opt.is_visible({"format": "flac"}, {})


def test_vorbis_bitrate_caps():
    from quickseemd.core.tools.media.audio_engine import vorbis_kbps

    assert vorbis_kbps(256_000, 44_100, 1) == 240
    assert vorbis_kbps(320_000, 48_000, 2) == 320
    assert vorbis_kbps(192_000, 22_050, 2) == 160
    assert vorbis_kbps(96_000, 8_000, 1) == 32
    assert vorbis_kbps(32_000, 44_100, 2) == 64


def test_ogg_from_high_rate_mono(tmp_path, outdir):
    from test_media_support import run_ffmpeg

    src = tmp_path / "96k.wav"
    run_ffmpeg("-f", "lavfi", "-i", "sine=frequency=440:duration=1:sample_rate=96000", str(src))
    outputs, _ = run_tool("audio.convert", [src], outdir, format="ogg", bitrate=320)
    a = streams(outputs[0], "audio")[0]
    assert (a["codec_name"], a["sample_rate"]) == ("vorbis", "48000")
