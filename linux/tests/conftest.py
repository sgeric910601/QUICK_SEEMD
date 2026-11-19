"""測試防護與共用測試資料。

這個檔案在任何專案模組載入前執行，先把環境隔離：
- HOME、XDG 各資料夾、XDG_RUNTIME_DIR 全部改到暫存資料夾（不會寫到真的設定、自動啟動、D-Bus 服務檔）
- D-Bus 指向不存在的位址、設定改用記憶體、不連輔助功能服務
- 會碰真系統的指令（nemo、systemctl、gdbus…）在搜尋路徑最前面放「擋下」的替身
- 只准對測試自己啟動的子程序送訊號
- DISPLAY 只能是未設定，或 QS_TEST_DISPLAY 指定的 :120 以上隔離畫面
- 記憶體上限：整個測試程序（含所有子程序）的資料區上限預設 6 GB（QS_TEST_MEM_LIMIT_GB），
  壓力測試失控時只會這個程序自己失敗，不會吃光整台電腦的記憶體
"""

from __future__ import annotations

import os
import shutil
import signal
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest

# ------------------------------------------------------------------ 環境隔離（載入時立刻執行）

REAL_HOME = os.path.expanduser("~")
REAL_DATA = os.environ.get("XDG_DATA_HOME") or os.path.join(REAL_HOME, ".local", "share")
TEST_ROOT = Path(tempfile.mkdtemp(prefix="qs-test-"))
BLOCKED_COMMANDS = (
    "nemo",
    "nautilus",
    "systemctl",
    "busctl",
    "gdbus",
    "dbus-send",
    "dbus-launch",
    "xdg-open",
    "gio",
    "gsettings",
    "dconf",
    "pkill",
    "killall",
    "kill",
    "notify-send",
    "xdotool",
    "pkexec",
    "sudo",
    "gtk-launch",
    "xdg-mime",
    "update-desktop-database",
    "gtk-update-icon-cache",
)
BLOCK_EXIT = 97
LINUX_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = LINUX_DIR.parent
SAMPLES_DIR = REPO_DIR / "Samples"


def _isolate() -> None:
    home = TEST_ROOT / "home"
    dirs = {
        "XDG_CONFIG_HOME": home / ".config",
        "XDG_DATA_HOME": home / ".local" / "share",
        "XDG_CACHE_HOME": home / ".cache",
        "XDG_STATE_HOME": home / ".local" / "state",
    }
    for p in dirs.values():
        p.mkdir(parents=True, exist_ok=True)
    runtime = TEST_ROOT / "run"
    runtime.mkdir(mode=0o700)
    # 模型與去背元件：唯讀借用真的（以符號連結）
    qs_data = dirs["XDG_DATA_HOME"] / "quickseemd"
    qs_data.mkdir()
    for name in ("models", "venv-rembg"):
        real = Path(REAL_DATA) / "quickseemd" / name
        if real.exists():
            (qs_data / name).symlink_to(real)
    shim = TEST_ROOT / "blocked-bin"
    shim.mkdir()
    for cmd in BLOCKED_COMMANDS:
        script = shim / cmd
        script.write_text(
            f"#!/bin/sh\necho 'blocked by test guard: {cmd}' >&2\nexit {BLOCK_EXIT}\n"
        )
        script.chmod(0o755)
    env = os.environ
    env["HOME"] = str(home)
    for k, v in dirs.items():
        env[k] = str(v)
    env["XDG_RUNTIME_DIR"] = str(runtime)
    env["DBUS_SESSION_BUS_ADDRESS"] = "unix:path=/nonexistent/quickseemd-test-bus"
    env["GSETTINGS_BACKEND"] = "memory"
    env["GIO_USE_VFS"] = "local"
    env["NO_AT_BRIDGE"] = "1"
    env["QS_TEST_GUARD"] = "1"
    env.pop("WAYLAND_DISPLAY", None)
    env.pop("DBUS_SYSTEM_BUS_ADDRESS", None)
    test_display = env.get("QS_TEST_DISPLAY", "")
    if test_display:
        num = test_display.lstrip(":").split(".")[0]
        if not num.isdigit() or int(num) < 120:
            raise RuntimeError("QS_TEST_DISPLAY 必須是 :120 以上的隔離畫面")
        env["DISPLAY"] = test_display
    else:
        env.pop("DISPLAY", None)
    env["PATH"] = f"{shim}:/usr/local/bin:/usr/bin:/bin"
    env["QUICKSEEMD_TOOL_PATH"] = f"{shim}:/usr/local/bin:/usr/bin:/bin"
    sys.path.insert(0, str(LINUX_DIR))


DEFAULT_MEM_LIMIT_GB = 6.0


def _cap_memory() -> int:
    """限制資料區（heap 與私有可寫映射）大小；子程序會繼承，降低後就無法再調高。"""
    import resource  # noqa: PLC0415

    limit = int(float(os.environ.get("QS_TEST_MEM_LIMIT_GB", DEFAULT_MEM_LIMIT_GB)) * 1024**3)
    _soft, hard = resource.getrlimit(resource.RLIMIT_DATA)
    if hard != resource.RLIM_INFINITY:
        limit = min(limit, hard)
    resource.setrlimit(resource.RLIMIT_DATA, (limit, limit))
    return limit


_isolate()
MEM_LIMIT_BYTES = _cap_memory()

# ------------------------------------------------------------------ 訊號防護

CHILD_PIDS: set[int] = set()
_real_kill = os.kill
_real_killpg = os.killpg
_RealPopen = subprocess.Popen


class _GuardedPopen(_RealPopen):  # type: ignore[misc,valid-type]
    def __init__(self, *args, **kwargs):  # noqa: ANN002, ANN003
        super().__init__(*args, **kwargs)
        CHILD_PIDS.add(self.pid)


def _allowed(pid: int) -> bool:
    from quickseemd.core import process  # noqa: PLC0415

    return pid > 0 and (pid in CHILD_PIDS or pid in process.SPAWNED)


def _guarded_kill(pid: int, sig: int) -> None:
    if sig != 0 and not _allowed(pid):
        raise PermissionError(f"測試防護：不准對 pid {pid} 送訊號 {sig}")
    _real_kill(pid, sig)


def _guarded_killpg(pgid: int, sig: int) -> None:
    if sig != 0 and not _allowed(pgid):
        raise PermissionError(f"測試防護：不准對程序群組 {pgid} 送訊號 {sig}")
    _real_killpg(pgid, sig)


subprocess.Popen = _GuardedPopen  # type: ignore[misc]
os.kill = _guarded_kill
os.killpg = _guarded_killpg


def pytest_sessionfinish(session, exitstatus):  # noqa: ANN001, ARG001
    shutil.rmtree(TEST_ROOT, ignore_errors=True)


def pytest_collection_modifyitems(config, items):  # noqa: ANN001, ARG001
    if os.environ.get("QS_TEST_DISPLAY"):
        return
    skip = pytest.mark.skip(reason="需要隔離畫面：設定 QS_TEST_DISPLAY=:120 以上")
    for item in items:
        if "gui" in item.keywords:
            item.add_marker(skip)


# ------------------------------------------------------------------ 共用測試資料


@pytest.fixture
def samples() -> Path:
    return SAMPLES_DIR


@pytest.fixture
def outdir(tmp_path: Path) -> Path:
    d = tmp_path / "out"
    d.mkdir()
    return d


def make_pdf(path: Path, pages: int = 3, text: bool = True, size=(595.28, 841.89)) -> Path:
    """用 cairo＋Pango 產生有中文文字層的 PDF（每頁寫「第 N 頁」與英文）。"""
    import cairo  # noqa: PLC0415
    import gi  # noqa: PLC0415

    gi.require_version("Pango", "1.0")
    gi.require_version("PangoCairo", "1.0")
    from gi.repository import Pango, PangoCairo  # noqa: PLC0415

    surface = cairo.PDFSurface(str(path), *size)
    cr = cairo.Context(surface)
    for i in range(pages):
        cr.set_source_rgb(1, 1, 1)
        cr.paint()
        if text:
            layout = PangoCairo.create_layout(cr)
            layout.set_font_description(Pango.FontDescription("Noto Sans CJK TC 24"))
            layout.set_text(f"第 {i + 1} 頁 Page {i + 1} 測試文字", -1)
            cr.set_source_rgb(0, 0, 0)
            cr.move_to(72, 72)
            PangoCairo.show_layout(cr, layout)
        cr.set_source_rgb(0.2, 0.4, 0.8)
        cr.rectangle(72, 200, 200, 120)
        cr.fill()
        cr.show_page()
    surface.finish()
    return path


def make_image(path: Path, size=(640, 480), mode: str = "RGB", **save_kw) -> Path:
    """Pillow 產生的漸層測試圖；副檔名決定格式。"""
    from PIL import Image, ImageDraw  # noqa: PLC0415

    img = Image.new(mode, size, (255, 255, 255, 0) if mode == "RGBA" else "white")
    draw = ImageDraw.Draw(img)
    w, h = size
    for x in range(0, w, 8):
        shade = int(255 * x / max(w - 1, 1))
        fill = (shade, 120, 255 - shade, 255) if mode == "RGBA" else (shade, 120, 255 - shade)
        draw.rectangle([x, 0, x + 7, h // 2], fill=fill)
    draw.ellipse(
        [w // 4, h // 2, w * 3 // 4, h - 10],
        fill=(220, 40, 40, 255) if mode == "RGBA" else (220, 40, 40),
    )
    img.save(path, **save_kw)
    return path


def _ffmpeg() -> str:
    from quickseemd.core import backends  # noqa: PLC0415

    return backends.require("ffmpeg")


def make_video(
    path: Path, seconds: float = 2.0, size=(320, 240), audio: bool = True, fps: int = 25
) -> Path:
    from quickseemd.core import process  # noqa: PLC0415

    args = [
        _ffmpeg(),
        "-hide_banner",
        "-nostdin",
        "-n",
        "-f",
        "lavfi",
        "-i",
        f"testsrc=duration={seconds}:size={size[0]}x{size[1]}:rate={fps}",
    ]
    if audio:
        args += ["-f", "lavfi", "-i", f"sine=frequency=440:duration={seconds}"]
    args += ["-c:v", "libx264", "-pix_fmt", "yuv420p", "-g", str(fps)]
    if audio:
        args += ["-c:a", "aac", "-shortest"]
    args.append(str(path))
    process.run_checked(args, "ffmpeg")
    return path


def make_audio(path: Path, seconds: float = 2.0) -> Path:
    from quickseemd.core import process  # noqa: PLC0415

    process.run_checked(
        [
            _ffmpeg(),
            "-hide_banner",
            "-nostdin",
            "-n",
            "-f",
            "lavfi",
            "-i",
            f"sine=frequency=440:duration={seconds}",
            str(path),
        ],
        "ffmpeg",
    )
    return path


@pytest.fixture
def pdf_file(tmp_path: Path) -> Path:
    return make_pdf(tmp_path / "文件.pdf")


@pytest.fixture
def jpg_file(tmp_path: Path) -> Path:
    return make_image(tmp_path / "相片.jpg", quality=92)


@pytest.fixture
def png_file(tmp_path: Path) -> Path:
    return make_image(tmp_path / "透明.png", mode="RGBA")


@pytest.fixture
def video_file(tmp_path: Path) -> Path:
    return make_video(tmp_path / "影片.mp4")


@pytest.fixture
def audio_file(tmp_path: Path) -> Path:
    return make_audio(tmp_path / "聲音.wav")
