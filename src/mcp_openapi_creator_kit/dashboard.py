"""Token-protected loopback HTTP host for the generated catalog dashboard."""
from __future__ import annotations

import secrets
import threading
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import urlsplit


@dataclass(frozen=True)
class DashboardInfo:
    url: str
    generation: int


class _LoopbackServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = False


class DashboardHost:
    def __init__(self):
        self._lock = threading.RLock()
        self._server: _LoopbackServer | None = None
        self._thread: threading.Thread | None = None
        self._token = secrets.token_urlsafe(32)
        self._html = b""
        self._generation = 0

    def _handler(self):
        owner = self

        class Handler(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, _format, *_args):
                return

            def end_headers(self):
                self.send_header("Cache-Control", "no-store, max-age=0")
                self.send_header("Pragma", "no-cache")
                self.send_header("X-Content-Type-Options", "nosniff")
                self.send_header("X-Frame-Options", "DENY")
                self.send_header("Referrer-Policy", "no-referrer")
                self.send_header("Cross-Origin-Resource-Policy", "same-origin")
                self.send_header(
                    "Content-Security-Policy",
                    "default-src 'none'; script-src 'unsafe-inline'; "
                    "style-src 'unsafe-inline'; img-src data:; "
                    "connect-src 'none'; base-uri 'none'; form-action 'none'; "
                    "frame-ancestors 'none'",
                )
                super().end_headers()

            def do_GET(self):
                self._serve(include_body=True)

            def do_HEAD(self):
                self._serve(include_body=False)

            def _serve(self, include_body: bool):
                with owner._lock:
                    port = owner._server.server_port if owner._server else 0
                    allowed_hosts = {f"127.0.0.1:{port}", f"localhost:{port}"}
                    host = self.headers.get("Host", "").lower()
                    expected_path = f"/{owner._token}"
                    requested_path = urlsplit(self.path).path.rstrip("/")
                    if host not in allowed_hosts:
                        self.send_error(421, "Misdirected Request")
                        return
                    if not secrets.compare_digest(requested_path, expected_path):
                        self.send_error(404, "Not Found")
                        return
                    body = owner._html
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                if include_body:
                    self.wfile.write(body)

        return Handler

    def publish(self, html: str) -> DashboardInfo:
        with self._lock:
            self._html = html.encode("utf-8")
            self._generation += 1
            if self._server is None:
                self._server = _LoopbackServer(
                    ("127.0.0.1", 0), self._handler())
                self._thread = threading.Thread(
                    target=self._server.serve_forever,
                    name="mcp-openapi-creator-dashboard",
                    daemon=True,
                )
                self._thread.start()
            return self.info()

    def info(self) -> DashboardInfo:
        with self._lock:
            if self._server is None:
                raise RuntimeError("dashboard has not been started")
            return DashboardInfo(
                url=f"http://127.0.0.1:{self._server.server_port}/{self._token}",
                generation=self._generation,
            )

    @property
    def running(self) -> bool:
        with self._lock:
            return self._server is not None

    def close(self):
        with self._lock:
            server = self._server
            thread = self._thread
            self._server = None
            self._thread = None
        if server is not None:
            server.shutdown()
            server.server_close()
        if thread is not None and thread is not threading.current_thread():
            thread.join(timeout=5)
