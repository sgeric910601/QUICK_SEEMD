"""測試用的本機 HTTP 伺服器（只聽 127.0.0.1）：記錄所有連線與請求，用來證明預覽沒有連網。

任何方法（GET、HEAD、PROPFIND…）都記錄；連線建立就算一次，即使沒有送出完整的請求。
"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class _Server(ThreadingHTTPServer):
    daemon_threads = True

    def __init__(self, owner: RecordingServer, handler: type) -> None:
        self.owner = owner
        super().__init__(("127.0.0.1", 0), handler)

    def verify_request(self, request, client_address) -> bool:  # noqa: ANN001
        self.owner.connections += 1
        return True


class _Handler(BaseHTTPRequestHandler):
    server: _Server

    def parse_request(self) -> bool:
        ok = super().parse_request()
        if getattr(self, "command", None):
            self.server.owner.requests.append(f"{self.command} {self.path}")
        return ok

    def _reply(self) -> None:
        body = b"#EXTM3U\n"
        self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    do_GET = do_HEAD = do_POST = do_PROPFIND = do_OPTIONS = _reply  # noqa: N815

    def log_message(self, *_args: object) -> None:
        return


class RecordingServer:
    def __init__(self) -> None:
        self.requests: list[str] = []
        self.connections = 0
        self._server = _Server(self, _Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def url(self, path: str) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}{path}"

    @property
    def touched(self) -> bool:
        return bool(self.requests) or self.connections > 0

    def __enter__(self) -> RecordingServer:
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()


def hls_disguised_as_mp3(path, server: RecordingServer):  # noqa: ANN001, ANN201
    """副檔名是 .mp3、內容其實是指向測試伺服器的 HLS 播放清單。"""
    playlist = (
        "#EXTM3U\n#EXT-X-VERSION:3\n#EXT-X-TARGETDURATION:10\n#EXT-X-MEDIA-SEQUENCE:0\n"
        f"#EXTINF:10.0,\n{server.url('/segment0.ts')}\n"
        f"#EXTINF:10.0,\n{server.url('/segment1.ts')}\n#EXT-X-ENDLIST\n"
    )
    path.write_text(playlist, "utf-8")
    return path
