"""萬用轉換：影音路線（ffmpeg）與參數表。"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from conftest import make_audio, make_video
from PIL import Image

from quickseemd.core import backends, errors, process
from quickseemd.core.converter import convert, external, media
from quickseemd.core.model import ToolContext


def run(path: Path, fid: str, out: Path, **opts: Any) -> list[Path]:
    seen: list[float] = []
    ctx = ToolContext(out, lambda f, m: seen.append(f))
    try:
        return convert(path, fid, ctx, opts)
    finally:
        ctx.cleanup()


def streams(path: Path) -> list[dict]:
    args = [backends.require("ffprobe"), "-v", "error", "-show_streams", "-of", "json", str(path)]
    return json.loads(process.run_checked(args, "ffprobe").stdout)["streams"]


# ------------------------------------------------------------------ 參數表


def test_ffmpeg_argument_table() -> None:
    opts = {"audio_bitrate": 192000, "video_size": 0, "gif_fps": 12, "gif_width": 480}
    opts["gif_seconds"] = 15
    head = ["-hide_banner", "-nostdin", "-n", "-protocol_whitelist", "file,pipe", "-i", "/in"]
    tail = ["-progress", "pipe:1", "-nostats", "/out"]

    def args(fid: str, **extra: Any) -> list[str]:
        result = external.ffmpeg_arguments("/in", "/out", fid, {**opts, **extra})
        assert result[: len(head)] == head and result[-len(tail) :] == tail
        return result[len(head) : -len(tail)]

    assert args("mp3") == ["-vn", "-c:a", "libmp3lame", "-b:a", "192k"]
    assert args("mp3", audio_bitrate=32000) == ["-vn", "-c:a", "libmp3lame", "-b:a", "64k"]
    assert args("opus", audio_bitrate=320000) == ["-vn", "-c:a", "libopus", "-b:a", "256k"]
    assert args("ogg") == ["-vn", "-c:a", "libvorbis", "-q:a", "6"]
    assert args("alac") == ["-vn", "-c:a", "alac"]
    assert args("mkv") == ["-map", "0", "-c", "copy"]
    assert args("mkv", video_size=720)[:2] == ["-vf", external.scale_filter(720)[1]]
    assert args("mp4") == [
        "-c:v",
        "libx264",
        "-crf",
        "20",
        "-preset",
        "medium",
        "-pix_fmt",
        "yuv420p",
        "-c:a",
        "aac",
        "-b:a",
        "192k",
        "-movflags",
        "+faststart",
    ]
    assert args("m4v") == args("mp4")
    assert "-movflags" not in args("mov") and "-preset" not in args("mov")
    assert args("mp4-hevc")[:8] == [
        "-c:v",
        "libx265",
        "-crf",
        "24",
        "-tag:v",
        "hvc1",
        "-pix_fmt",
        "yuv420p",
    ]
    assert "-movflags" not in args("mov-hevc")
    assert args("prores") == ["-c:v", "prores_ks", "-profile:v", "2", "-c:a", "pcm_s16le"]
    assert args("anim-gif") == [
        "-t",
        "15.0",
        "-vf",
        "fps=12,scale=480:-1:flags=lanczos,split[a][b];[a]palettegen[p];[b][p]paletteuse",
        "-loop",
        "0",
    ]
    assert external.scale_filter(720) == [
        "-vf",
        "scale='if(gt(iw,ih),-2,min(iw,720))':'if(gt(iw,ih),min(ih,720),-2)'",
    ]


def test_pandoc_and_magick_arguments() -> None:
    assert external.pandoc_arguments("/a.md", "/a.md.out", "markdown", "md") == [
        "/a.md",
        "-f",
        "markdown",
        "-t",
        "gfm",
        "--wrap=none",
        "--sandbox",
        "-o",
        "/a.md.out",
    ]
    assert external.pandoc_arguments("/a", "/b", None, "epub") == [
        "/a",
        "--toc",
        "--sandbox",
        "-o",
        "/b",
    ]


def test_magick_arguments_name_the_coder() -> None:
    from quickseemd.core.converter import magick

    args = magick.arguments("/s/in.xcf", "/s/out.png", {"image_quality": 0.85}, False, "xcf", "png")
    assert args[: len(magick.LIMITS)] == list(magick.LIMITS)
    assert args[len(magick.LIMITS) :] == [
        "XCF:/s/in.xcf[0]",
        "-auto-orient",
        "-quality",
        "85",
        "PNG:/s/out.png",
    ]
    keep = magick.arguments(
        "/a", "/b", {"image_max_pixel": 512, "image_quality": 1}, True, "gif", "webp"
    )
    assert (
        "GIF:/a" in keep
        and ["-resize", "512x512>"] == keep[keep.index("-resize") : keep.index("-resize") + 2]
    )
    assert "-limit" in keep and "time" in keep
    for pattern in ("MVG", "MSL", "TEXT", "EPS", "PDF", "XPS", "URL", "HTTPS", "EPHEMERAL"):
        assert pattern in magick.POLICY
    assert '<policy domain="path" rights="none" pattern="@*"/>' in magick.POLICY
    with pytest.raises(errors.QSError):
        magick.coder("")


def test_magick_route_uses_safe_names_and_policy(
    tmp_path: Path, outdir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    from quickseemd.core.converter import capabilities

    fake = tmp_path / "bin"
    fake.mkdir()
    log = tmp_path / "magick.log"
    script = fake / "convert"
    script.write_text(
        "#!/bin/sh\n"
        'if [ "$1" = "-list" ]; then echo "      PNG* PNG       rw-   Portable"; echo "     WEBP* WEBP      rw-   WebP"; exit 0; fi\n'
        f'printf "%s\\n" "$@" > {log}\n'
        f'printf "%s\\n" "$MAGICK_CONFIGURE_PATH" >> {log}\n'
        'last=""; for a in "$@"; do last="$a"; done\n'
        'printf "png" > "${last#*:}"\n'
    )
    script.chmod(0o755)
    monkeypatch.setenv("QUICKSEEMD_TOOL_PATH", f"{fake}:{os.environ['QUICKSEEMD_TOOL_PATH']}")
    backends.refresh()
    capabilities.refresh()
    try:
        src = tmp_path / "art[1]%d.xcf"
        src.write_bytes(b"gimp xcf v011\x00" + b"\x00" * 64)
        [out] = run(src, "png", outdir)
    finally:
        monkeypatch.undo()
        backends.refresh()
        capabilities.refresh()
    assert out.name == "art[1]%d.png" and out.read_bytes() == b"png"
    lines = log.read_text().splitlines()
    assert any(line.startswith("XCF:") and line.endswith("/in.xcf[0]") for line in lines)
    assert any(line.startswith("PNG:") and line.endswith("/out.png") for line in lines)
    assert "-limit" in lines
    policy_dir = Path(lines[-1])
    assert policy_dir.name.startswith(".magick-")


def test_progress_parser() -> None:
    got: list[float] = []
    parse = external.progress_parser(10.0, got.append)
    parse("out_time_us=5000000")
    parse("out_time_ms=20000000")
    parse("frame=3")
    parse("out_time_us=N/A")
    assert got == [0.5, 0.99]


def test_frame_label_and_time() -> None:
    assert media.frame_label(1.0) == "1"
    assert media.frame_label(2.5) == "2.5"
    assert media.frame_label(0.0) == "0"
    assert media.frame_time(10.0, None) == 1.0
    assert media.frame_time(1.0, None) == 0.5
    assert media.frame_time(2.0, 5.0) == pytest.approx(1.95)
    assert media.frame_time(None, None) == 0.0


def test_gif_plan() -> None:
    assert media.gif_plan(100.0, {"gif_fps": 99, "gif_seconds": 60, "gif_width": 320}) == (
        30,
        60.0,
        900,
        320,
    )
    assert media.gif_plan(0.05, {}) == (12, 0.1, 1, 480)


# ------------------------------------------------------------------ 實際轉檔


def test_video_to_webm_and_mp4_downscale(video_file: Path, outdir: Path) -> None:
    [webm] = run(video_file, "webm", outdir)
    assert webm.name == "影片.webm"
    assert {s["codec_name"] for s in streams(webm)} == {"vp9", "opus"}
    [small] = run(video_file, "mov", outdir, video_size=480)
    video = next(s for s in streams(small) if s["codec_type"] == "video")
    assert (video["width"], video["height"]) == (320, 240)  # 不放大
    [down] = run(video_file, "mp4", outdir, video_size=120)
    video = next(s for s in streams(down) if s["codec_type"] == "video")
    assert video["height"] == 120 and video["width"] % 2 == 0


def test_video_frame_naming(video_file: Path, outdir: Path) -> None:
    [png] = run(video_file, "png", outdir)
    assert png.name == "影片-1s.png"
    [jpg] = run(video_file, "jpeg", outdir, frame_time=0.5, image_max_pixel=160)
    assert jpg.name == "影片-0.5s.jpg"
    with Image.open(jpg) as img:
        assert img.size == (160, 120)


def test_video_to_gif_never_upscales(video_file: Path, outdir: Path) -> None:
    [gif] = run(video_file, "anim-gif", outdir, gif_fps=5, gif_width=1080)
    assert gif.name == "影片.gif"
    with Image.open(gif) as img:
        assert img.size == (320, 240)
        assert img.n_frames == 10  # 2 秒 × 5 fps


def test_extract_audio_from_video(video_file: Path, outdir: Path) -> None:
    [m4a] = run(video_file, "m4a", outdir, audio_bitrate=128000)
    assert [s["codec_name"] for s in streams(m4a)] == ["aac"]
    [mp3] = run(video_file, "mp3", outdir)
    assert [s["codec_name"] for s in streams(mp3)] == ["mp3"]


def test_audio_conversions(audio_file: Path, outdir: Path) -> None:
    [flac] = run(audio_file, "flac", outdir)
    assert streams(flac)[0]["codec_name"] == "flac"
    [aiff] = run(audio_file, "aiff", outdir)
    assert streams(aiff)[0]["codec_name"] == "pcm_s16be"
    [caf] = run(audio_file, "caf", outdir)
    assert caf.suffix == ".caf"


def test_deep_audio_keeps_24_bit(tmp_path: Path, outdir: Path) -> None:
    src = tmp_path / "deep.wav"
    args = [backends.require("ffmpeg"), "-hide_banner", "-nostdin", "-n", "-f", "lavfi"]
    args += ["-i", "sine=duration=0.5", "-c:a", "pcm_s24le", str(src)]
    process.run_checked(args, "ffmpeg")
    [aiff] = run(src, "aiff", outdir)
    assert streams(aiff)[0]["codec_name"] == "pcm_s24be"


def test_video_without_audio_cannot_extract(tmp_path: Path, outdir: Path) -> None:
    mute = make_video(tmp_path / "mute.mp4", seconds=1, audio=False)
    from quickseemd.core.converter import profile
    from quickseemd.core.converter.formats import BY_ID

    ctx = ToolContext(outdir)
    with pytest.raises(errors.QSError) as exc:
        media.convert_audio(mute, profile.analyze(mute), BY_ID["m4a"], {}, outdir, ctx)
    assert str(exc.value) == "這個檔案沒有音訊軌，無法擷取聲音。"


def test_animated_gif_to_mp4(tmp_path: Path, outdir: Path) -> None:
    src = tmp_path / "anim.gif"
    frames = [Image.new("RGBA", (41, 31), (255, 0, 0, 0)), Image.new("RGB", (41, 31), "blue")]
    frames[0].save(src, save_all=True, append_images=frames[1:], duration=[500, 1000], loop=0)
    [mp4] = run(src, "mp4", outdir)
    video = streams(mp4)[0]
    assert (video["codec_name"], video["width"], video["height"]) == ("h264", 40, 30)
    assert float(video["duration"]) == pytest.approx(1.5, abs=0.15)
    [mov] = run(src, "mov", outdir)
    assert mov.suffix == ".mov"


def test_audio_only_file(tmp_path: Path, outdir: Path) -> None:
    voice = make_audio(tmp_path / "voice.mp4", seconds=1)
    [wav] = run(voice, "wav", outdir)
    assert streams(wav)[0]["codec_name"] == "pcm_s16le"


def test_cancel_stops_ffmpeg(video_file: Path, outdir: Path) -> None:
    import threading

    cancel = threading.Event()
    cancel.set()
    ctx = ToolContext(outdir, None, cancel)
    with pytest.raises(errors.Cancelled):
        convert(video_file, "webm", ctx, {})
    ctx.cleanup()
    assert list(outdir.iterdir()) == []
