"""Local synthetic API proxy that drops a completed workflow response."""

import socket
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from threading import Thread
from urllib.parse import urlsplit

import httpx


class LostResponseProxy:
    def __init__(self, upstream: str, *, drop_case_update: bool = False, gateway_error: bool = False) -> None:
        self.upstream = upstream
        self.drop_case_update = drop_case_update
        self.gateway_error = gateway_error
        self.dropped_statuses: list[int] = []
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, format, *args):  # type: ignore[no-untyped-def]
                pass  # Do not print synthetic request headers or tokens.

            def forward(self) -> None:
                length = int(self.headers.get("Content-Length", "0"))
                body = self.rfile.read(length) if length else b""
                headers = {
                    key: value
                    for key, value in self.headers.items()
                    if key.lower() not in {"host", "connection", "content-length", "accept-encoding"}
                }
                headers["Accept-Encoding"] = "identity"
                response = httpx.request(
                    self.command, f"{outer.upstream}{self.path}", headers=headers, content=body, timeout=30
                )
                path = urlsplit(self.path).path
                drop_workflow = (
                    not outer.drop_case_update and self.command == "POST" and path == "/api/v1/workflows"
                )
                drop_case = (
                    outer.drop_case_update and self.command == "PATCH" and path.startswith("/api/v1/cases/")
                )
                if drop_workflow or drop_case:
                    outer.dropped_statuses.append(response.status_code)
                    if outer.gateway_error:
                        self.send_response(502)
                        self.send_header("Content-Length", "0")
                        self.end_headers()
                        return
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
            do_PATCH = forward  # noqa: N815 - required by BaseHTTPRequestHandler

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
