"""Local synthetic API proxy that drops a completed workflow response."""

import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import urlsplit

import httpx


class LostResponseProxy:
    def __init__(self, upstream: str) -> None:
        self.upstream = upstream
        self.dropped_statuses: list[int] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):  # type: ignore[no-untyped-def]
                pass  # Do not print synthetic request headers or tokens.

            def forward(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length) if length else b""
                headers = {
                    key: value for key, value in self.headers.items()
                    if key.lower() not in {"host", "connection", "content-length", "accept-encoding"}
                }
                headers["Accept-Encoding"] = "identity"
                response = httpx.request(
                    self.command, f"{outer.upstream}{self.path}", headers=headers, content=body, timeout=30
                )
                if self.command == "POST" and urlsplit(self.path).path == "/api/v1/workflows":
                    outer.dropped_statuses.append(response.status_code)
                    self.connection.shutdown(socket.SHUT_RDWR)
                    self.connection.close()
                    return
                self.send_response(response.status_code)
                content_type = response.headers.get("content-type", "application/octet-stream")
                self.send_header("Content-Type", content_type)
                self.send_header("Content-Length", str(len(response.content)))
                self.end_headers()
                self.wfile.write(response.content)

            do_GET = forward  # noqa: N815 - required by BaseHTTPRequestHandler
            do_POST = forward  # noqa: N815 - required by BaseHTTPRequestHandler

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.thread = Thread(target=self.server.serve_forever, daemon=True)

    @property
    def url(self) -> str:
        return f"http://127.0.0.1:{self.server.server_port}"

    def start(self) -> None:
        self.thread.start()

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=5)
