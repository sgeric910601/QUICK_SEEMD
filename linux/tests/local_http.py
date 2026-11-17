"""測試用的本機 HTTP 伺服器（只聽 127.0.0.1）：記錄收到的請求，用來證明預覽沒有連網。"""

from __future__ import annotations

import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class RecordingServer:
    def __init__(self) -> None:
        self.requests: list[str] = []
        owner = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 — http.server 規定的名稱
                owner.requests.append(self.path)
                body = b"#EXTM3U\n"
                self.send_response(200)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *_args: object) -> None:
                return

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    def url(self, path: str) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}{path}"

    def __enter__(self) -> RecordingServer:
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._server.shutdown()
        self._server.server_close()
