"""D-Bus 介面 dev.quickseemd.Agent 的方法分派與輸入檢查（不需要匯流排）。"""

from __future__ import annotations

from pathlib import Path

import pytest

from quickseemd import __version__
from quickseemd.agent import service as svc
from quickseemd.core import ipc


class Handler:
    def __init__(self):
        self.calls: list[tuple] = []

    def run_action(self, action, paths):
        self.calls.append(("run_action", action, paths))

    def convert(self, fmt, paths):
        self.calls.append(("convert", fmt, paths))

    def notify(self, title, icon, message):
        self.calls.append(("notify", title, icon, message))

    def add_to_shelf(self, paths):
        self.calls.append(("add_to_shelf", paths))

    def quit(self):
        self.calls.append(("quit",))


@pytest.fixture
def service():
    h = Handler()
    return svc.AgentService(h, dispatch=lambda fn: fn()), h


def test_run_action_validates_paths(service, tmp_path):
    s, h = service
    f = tmp_path / "a.pdf"
    f.write_text("x")
    assert s.handle("RunAction", ("pdf.rotate-right", [str(f), "relative", str(tmp_path / "x")]))
    assert h.calls == [("run_action", "pdf.rotate-right", [f])]


def test_run_action_without_valid_paths_is_ignored(service):
    s, h = service
    s.handle("RunAction", ("pdf.rotate-right", ["relative", "/no/such/file"]))
    assert h.calls == []


def test_too_many_paths_is_invalid_args(service):
    s, _h = service
    with pytest.raises(svc.ServiceError) as exc:
        s.handle("RunAction", ("x", ["/"] * (ipc.MAX_PATHS + 1)))
    assert exc.value.name == svc.INVALID_ARGS


def test_overlong_action_id_is_rejected(service, tmp_path):
    s, _h = service
    with pytest.raises(svc.ServiceError):
        s.handle("RunAction", ("a" * 500, [str(tmp_path)]))


def test_convert_requires_known_format(service, tmp_path):
    s, h = service
    s.handle("Convert", ("png", [str(tmp_path)]))
    assert h.calls == [("convert", "png", [tmp_path])]
    with pytest.raises(svc.ServiceError) as exc:
        s.handle("Convert", ("no-such", [str(tmp_path)]))
    assert "未知的格式" in exc.value.message


def test_notify_trims_and_sanitises(service):
    s, h = service
    s.handle("Notify", ("標題" * 200, "doc.on.doc", "訊息"))
    ((name, title, icon, message),) = h.calls
    assert len(title) == svc.MAX_TITLE
    assert icon == "doc.on.doc"
    assert message == "訊息"
    h.calls.clear()
    s.handle("Notify", ("t", "../../etc/passwd", "m" * 5000))
    ((_n, _t, icon, message),) = h.calls
    assert icon == ""
    assert len(message) == svc.MAX_MESSAGE


def test_add_to_shelf(service, tmp_path):
    s, h = service
    s.handle("AddToShelf", ([str(tmp_path)],))
    assert h.calls == [("add_to_shelf", [tmp_path])]


def test_ping_returns_version(service):
    s, _h = service
    assert s.handle("Ping", ()) == ("s", __version__)


def test_quit(service):
    s, h = service
    s.handle("Quit", ())
    assert h.calls == [("quit",)]


def test_unknown_method(service):
    s, _h = service
    with pytest.raises(svc.ServiceError) as exc:
        s.handle("Explode", ())
    assert exc.value.name == svc.UNKNOWN_METHOD


def test_wrong_argument_types(service):
    s, _h = service
    with pytest.raises(svc.ServiceError):
        s.handle("RunAction", (1, "not a list"))


def test_paths_are_normalised(service, tmp_path):
    s, h = service
    (tmp_path / "d").mkdir()
    s.handle("AddToShelf", ([str(tmp_path / "d" / ".." / "d")],))
    assert h.calls == [("add_to_shelf", [Path(tmp_path / "d")])]


def test_interface_xml_matches_ipc():
    info = svc.interface_info()
    assert info.name == ipc.AGENT_INTERFACE
    assert {m.name for m in info.methods} == {
        "RunAction",
        "Convert",
        "Notify",
        "AddToShelf",
        "Ping",
        "Quit",
    }
