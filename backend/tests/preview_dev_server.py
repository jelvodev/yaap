"""A stand-in dev server for the preview tests: it answers with the request it received as JSON, sets a
cookie of its own and one named like YAAP's, and answers a WebSocket handshake by echoing what follows in capitals."""

import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002 - the base class names it so
        return

    def answer(self, body: bytes) -> None:
        received = {
            "method": self.command,
            "path": self.path,
            "headers": [[name, value] for name, value in self.headers.items()],
            "body": body.decode("utf-8", "replace"),
            "port": self.server.server_address[1],
        }
        content = json.dumps(received).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(content)))
        self.send_header("Set-Cookie", "app_cookie=1; Path=/")
        self.send_header("Set-Cookie", "yaap_session_8080=planted; Path=/")
        self.end_headers()
        self.wfile.write(content)

    def do_GET(self) -> None:
        if self.headers.get("Upgrade", "").lower() == "websocket":
            self.send_response(101)
            self.send_header("Upgrade", "websocket")
            self.send_header("Connection", "Upgrade")
            self.end_headers()
            self.wfile.flush()
            while data := self.rfile.read1(65536):
                self.wfile.write(data.upper())
                self.wfile.flush()
            self.close_connection = True
            return
        self.answer(b"")

    def do_POST(self) -> None:
        self.answer(self.rfile.read(int(self.headers.get("Content-Length") or 0)))


server = ThreadingHTTPServer(("127.0.0.1", int(sys.argv[1])), Handler)
print("ready", flush=True)
server.serve_forever()
