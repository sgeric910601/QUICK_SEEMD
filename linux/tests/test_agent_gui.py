"""背景程式的整合測試：隔離畫面＋私有 D-Bus（只認得我們自己的服務）。

執行：先在 :130 以上的畫面跑 Xvfb，再 QS_TEST_DISPLAY=:137 python3 -m pytest tests/test_agent_gui.py
背景程式由 D-Bus 自動啟動（和正式安裝一樣是 python3 -m quickseemd.agent）。
拖曳用 XTest 模擬，來源視窗是 GTK3 與 GTK4 程式；都只在隔離畫面上動作。
"""

from __future__ import annotations

import os
import select
import subprocess
import time
from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from agent_fakes import MODULE_NAME, write_fake_tools

pytestmark = pytest.mark.gui

TESTS_DIR = Path(__file__).resolve().parent
LINUX_DIR = TESTS_DIR.parent
DRIVER = TESTS_DIR / "agent_gui_driver.py"
PYTHON = "/usr/bin/python3"
SERVICE = "dev.quickseemd.Agent"
RING_CENTER = (640, 420)
HUD_BOX = (906, 10, 1266, 300)
SHELF_BOX = (1012, 402, 1258, 548)
SOURCE_READY_TIMEOUT = 15.0
SHOT_TIMEOUT = 10.0
LABEL_RADIUS = 138

BUS_CONF = """<!DOCTYPE busconfig PUBLIC "-//freedesktop//DTD D-Bus Bus Configuration 1.0//EN"
 "http://www.freedesktop.org/standards/dbus/1.0/busconfig.dtd">
<busconfig>
  <type>session</type>
  <listen>unix:tmpdir=/tmp</listen>
  <servicedir>{servicedir}</servicedir>
  <auth>EXTERNAL</auth>
  <policy context="default">
    <allow send_destination="*" eavesdrop="true"/>
    <allow eavesdrop="true"/>
    <allow own="*"/>
  </policy>
</busconfig>
"""


def wait_for(cond: Callable[[], object], timeout: float = 15.0, step: float = 0.1) -> object:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = cond()
        if value:
            return value
        time.sleep(step)
    return cond()


class Session:
    """一條私有匯流排與它自動啟動的背景程式。"""

    def __init__(self, root: Path, fake: bool) -> None:
        self.root = root
        self.files = root / "files"
        self.files.mkdir(parents=True)
        self.log = root / "agent.log"
        self.launcher_log = root / "launcher.log"
        self.env = self._environment(fake)
        self.daemon: subprocess.Popen[bytes] | None = None
        self.address = ""
        self.conn = None
        self.stopped = False

    def _environment(self, fake: bool) -> dict[str, str]:
        data_home = Path(os.environ["XDG_DATA_HOME"])
        services = data_home / "dbus-1" / "services"
        services.mkdir(parents=True, exist_ok=True)
        (services / f"{SERVICE}.service").write_text(
            f"[D-BUS Service]\nName={SERVICE}\nExec={PYTHON} -m quickseemd.agent\n"
        )
        self.conf = self.root / "bus.conf"
        self.conf.write_text(BUS_CONF.format(servicedir=services))
        env = os.environ.copy()
        paths = [str(LINUX_DIR)]
        if fake:
            write_fake_tools(self.root / "fake")
            paths.append(str(self.root / "fake"))
            env["QUICKSEEMD_EXTRA_TOOLS"] = MODULE_NAME
        else:
            env.pop("QUICKSEEMD_EXTRA_TOOLS", None)
        env["PYTHONPATH"] = os.pathsep.join(paths)
        env["QUICKSEEMD_LOG_FILE"] = str(self.log)
        return env

    def start(self) -> None:
        from gi.repository import Gio  # noqa: PLC0415

        self.daemon = subprocess.Popen(  # noqa: S603
            ["/usr/bin/dbus-daemon", f"--config-file={self.conf}", "--nofork", "--print-address=1"],
            stdout=subprocess.PIPE,
            env=self.env,
        )
        assert self.daemon.stdout is not None
        self.address = self.daemon.stdout.readline().decode().strip()
        assert self.address, "私有 D-Bus 沒有啟動"
        self.env["DBUS_SESSION_BUS_ADDRESS"] = self.address
        flags = (
            Gio.DBusConnectionFlags.AUTHENTICATION_CLIENT
            | Gio.DBusConnectionFlags.MESSAGE_BUS_CONNECTION
        )
        self.conn = Gio.DBusConnection.new_for_address_sync(self.address, flags, None, None)

    def call(self, method: str, signature: str | None = None, args: tuple = ()) -> object:
        from gi.repository import Gio, GLib  # noqa: PLC0415

        value = GLib.Variant(signature, args) if signature else None
        reply = self.conn.call_sync(
            SERVICE,
            "/dev/quickseemd/Agent",
            SERVICE,
            method,
            value,
            None,
            Gio.DBusCallFlags.NONE,
            30000,
            None,
        )
        return reply.unpack() if reply is not None else None

    def has_owner(self) -> bool:
        from gi.repository import Gio, GLib  # noqa: PLC0415

        reply = self.conn.call_sync(
            "org.freedesktop.DBus",
            "/org/freedesktop/DBus",
            "org.freedesktop.DBus",
            "NameHasOwner",
            GLib.Variant("(s)", (SERVICE,)),
            None,
            Gio.DBusCallFlags.NONE,
            5000,
            None,
        )
        return bool(reply.unpack()[0])

    def driver(self, *args: str, timeout: float = 60) -> str:
        out = subprocess.run(  # noqa: S603
            [PYTHON, str(DRIVER), *args],
            env=self.env,
            capture_output=True,
            timeout=timeout,
            check=True,
        )
        return out.stdout.decode("utf-8", errors="replace")

    def source(self, kind: str, path: Path, *pos: int) -> subprocess.Popen[bytes]:
        proc = subprocess.Popen(  # noqa: S603
            [PYTHON, str(DRIVER), kind, str(path), *[str(p) for p in pos]],
            env=self.env,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
        assert proc.stdout is not None
        ready, _w, _x = select.select([proc.stdout], [], [], SOURCE_READY_TIMEOUT)
        if not ready or proc.stdout.readline().strip() != b"READY":
            proc.terminate()
            proc.wait(timeout=10)
            pytest.fail("拖曳來源視窗沒有出現")
        return proc

    def wait_shot(self, path: Path, box: tuple[int, int, int, int], minimum: int) -> int:
        """反覆截圖，直到指定範圍的亮像素夠多（面板或暫存畫出來了）或逾時。"""
        count = 0
        deadline = time.monotonic() + SHOT_TIMEOUT
        while time.monotonic() < deadline:
            self.driver("shot", str(path))
            count = _light_pixels(path, box)
            if count > minimum:
                break
            time.sleep(0.2)
        return count

    def log_text(self) -> str:
        try:
            return self.log.read_text("utf-8")
        except OSError:
            return ""

    def stop(self) -> None:
        if self.stopped:
            return
        self.stopped = True
        if self.conn is not None and self.has_owner():
            try:
                self.call("Quit")
            except Exception:  # noqa: BLE001 — 已經結束也沒關係
                pass
            wait_for(lambda: not self.has_owner(), timeout=10)
        if self.daemon is not None:
            self.daemon.terminate()
            self.daemon.wait(timeout=10)


def _session(tmp_path_factory: pytest.TempPathFactory, fake: bool) -> Iterator[Session]:
    if not os.environ.get("QS_TEST_DISPLAY"):
        pytest.skip("需要隔離畫面")
    s = Session(tmp_path_factory.mktemp("agent-gui"), fake)
    s.start()
    assert s.call("Ping")[0]
    yield s
    s.stop()


@pytest.fixture(scope="module")
def session(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Session]:
    yield from _session(tmp_path_factory, fake=True)


@pytest.fixture
def fake_app_launcher(session: Session) -> Iterator[Path]:
    """假的 ~/.local/bin/quickseemd：把收到的參數記下來。"""
    path = Path.home() / ".local" / "bin" / "quickseemd"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(f'#!/bin/sh\necho "$@" >> "{session.launcher_log}"\n')
    path.chmod(0o755)
    yield session.launcher_log
    path.unlink()


def _light_pixels(png: Path, box: tuple[int, int, int, int]) -> int:
    from PIL import Image  # noqa: PLC0415

    with Image.open(png) as img:
        region = img.convert("L").crop(box)
        return sum(1 for v in region.getdata() if v > 200)


def _sector_point(index: int, count: int) -> tuple[int, int]:
    import math  # noqa: PLC0415

    angle = math.radians(index * 360 / count)
    cx, cy = RING_CENTER
    return int(cx + LABEL_RADIUS * math.sin(angle)), int(cy - LABEL_RADIUS * math.cos(angle))


def _drag(s: Session, key: str, hold: float, target: tuple[int, int], shot: Path | None = None):
    args = ["drag", "140", "140", str(RING_CENTER[0]), str(RING_CENTER[1]), key, str(hold)]
    args += [str(target[0]), str(target[1])]
    if shot is not None:
        args += ["shot", str(shot)]
    s.driver(*args)


# ------------------------------------------------------------------ D-Bus 與進度面板


def test_ping_and_second_instance_exits(session: Session) -> None:
    assert session.call("Ping")[0]
    proc = subprocess.run(  # noqa: S603
        [PYTHON, "-m", "quickseemd.agent"], env=session.env, timeout=30, capture_output=True
    )
    assert proc.returncode == 0
    assert session.has_owner()


def test_run_action_queue_and_hud(session: Session, tmp_path: Path) -> None:
    src = session.files / "報告.txt"
    src.write_text("內容", "utf-8")
    session.call("RunAction", "(sas)", ("other.zip", [str(src)]))
    session.call("RunAction", "(sas)", ("image.rotate-right", [str(src)]))
    session.call("RunAction", "(sas)", ("bogus.action", [str(src)]))
    session.call("Notify", "(sss)", ("已拷貝路徑", "link", str(src)))
    assert wait_for(lambda: (session.files / "報告.zip").exists())
    assert wait_for(lambda: "不認得動作「bogus.action」" in session.log_text())
    assert session.wait_shot(tmp_path / "hud.png", HUD_BOX, 20000) > 20000
    assert src.read_text("utf-8") == "內容"


def test_invalid_arguments_are_rejected(session: Session) -> None:
    from gi.repository import GLib  # noqa: PLC0415

    with pytest.raises(GLib.Error, match="未知的格式"):
        session.call("Convert", "(sas)", ("no-such-format", ["/"]))
    # 相對路徑與不存在的檔案直接略過，不會出錯
    session.call("RunAction", "(sas)", ("other.zip", ["relative.txt", "/no/such/file"]))


def test_add_to_shelf_shows_shelf(session: Session, tmp_path: Path) -> None:
    f = session.files / "暫存.txt"
    f.write_text("x")
    session.call("AddToShelf", "(as)", ([str(f), str(session.files)],))
    # 浮動暫存出現在螢幕右緣往內 270、垂直置中
    assert session.wait_shot(tmp_path / "shelf.png", SHELF_BOX, 15000) > 15000


# ------------------------------------------------------------------ 拖曳輪盤


def test_drag_from_own_shelf_is_ignored(session: Session, tmp_path: Path) -> None:
    f = session.files / "架上.txt"
    f.write_text("x")
    session.call("AddToShelf", "(as)", ([str(f)],))
    assert session.wait_shot(tmp_path / "shelf.png", SHELF_BOX, 5000) > 5000
    before = session.log_text().count("輪盤出現")
    # 浮動暫存第一列（視窗在 1010,400；標題列約 40 像素高）
    session.driver("drag", "1090", "465", "640", "420", "Alt_L", "0.1", "640", "282")
    assert wait_for(lambda: "忽略自己發起的拖曳" in session.log_text(), timeout=5)
    assert session.log_text().count("輪盤出現") == before
    assert not (session.files / "架上.zip").exists()


def test_tap_alt_opens_tools_wheel_and_drop_runs_action(session: Session, tmp_path: Path) -> None:
    f = session.files / "拖曳.txt"
    f.write_text("拖曳", "utf-8")
    src = session.source("source3", f, 40, 40)
    try:
        shot = tmp_path / "ring.png"
        _drag(session, "Alt_L", 0.1, _sector_point(0, 2), shot)
        assert wait_for(lambda: (session.files / "拖曳.zip").exists())
    finally:
        src.terminate()
        out = src.communicate(timeout=10)[0].decode()
    assert "DELETE-REQUESTED" not in out
    assert f.exists()
    assert _light_pixels(shot, (450, 230, 830, 610)) > 50000


def test_long_press_does_not_open_wheel(session: Session) -> None:
    f = session.files / "長按.txt"
    f.write_text("x")
    src = session.source("source3", f, 40, 40)
    before = session.log_text().count("輪盤出現")
    try:
        _drag(session, "Shift_L", 0.7, _sector_point(0, 2))
    finally:
        src.terminate()
        src.wait(timeout=10)
    assert session.log_text().count("輪盤出現") == before


def test_tap_control_copy_puts_files_on_clipboard(session: Session) -> None:
    f = session.files / "拷貝.txt"
    f.write_text("x")
    src = session.source("source3", f, 40, 40)
    try:
        _drag(session, "Control_L", 0.1, _sector_point(2, 6))
    finally:
        src.terminate()
        src.wait(timeout=10)
    text = session.driver("clipboard", "x-special/gnome-copied-files")
    assert text == "copy\n" + f.as_uri()
    assert session.driver("clipboard", "UTF8_STRING") == str(f)


def test_gtk4_source_center_drop_opens_app(session: Session, fake_app_launcher: Path) -> None:
    f = session.files / "中心.txt"
    f.write_text("x")
    src = session.source("source4", f)
    try:
        cx, cy = str(RING_CENTER[0]), str(RING_CENTER[1])
        session.driver("drag", "100", "100", cx, cy, "Shift_L", "0.1", cx, cy)
    finally:
        src.terminate()
        src.wait(timeout=10)
    assert wait_for(lambda: fake_app_launcher.exists() and fake_app_launcher.read_text())
    assert fake_app_launcher.read_text().strip() == f"open {f}"


def test_disabling_wheel_in_settings_stops_it(session: Session) -> None:
    from quickseemd.core import settings  # noqa: PLC0415

    settings.save({"wheel.enabled": False})
    try:
        assert wait_for(lambda: "wheel.enabled" in session.log_text())
        f = session.files / "停用.txt"
        f.write_text("x")
        src = session.source("source3", f, 40, 40)
        before = session.log_text().count("輪盤出現")
        try:
            _drag(session, "Alt_L", 0.1, _sector_point(0, 2))
        finally:
            src.terminate()
            src.wait(timeout=10)
        assert session.log_text().count("輪盤出現") == before
        assert not (session.files / "停用.zip").exists()
    finally:
        settings.save({"wheel.enabled": True})


# ------------------------------------------------------------------ 真的工具與結束


@pytest.fixture(scope="module")
def real_session(tmp_path_factory: pytest.TempPathFactory, session: Session) -> Iterator[Session]:
    session.stop()
    yield from _session(tmp_path_factory, fake=False)


def test_real_zip_action_and_quit(real_session: Session) -> None:
    src = real_session.files / "真的.txt"
    src.write_text("真的內容", "utf-8")
    real_session.call("RunAction", "(sas)", ("other.zip", [str(src)]))
    out = real_session.files / "真的.zip"
    assert wait_for(lambda: out.exists(), timeout=30)
    import zipfile  # noqa: PLC0415

    assert wait_for(lambda: zipfile.is_zipfile(out))
    real_session.call("Quit")
    assert wait_for(lambda: not real_session.has_owner(), timeout=10)
