#!/usr/bin/env python3
"""Serve the frontend and proxy /jobs to the tunneled intake API."""

from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import urllib.error
import urllib.request

ROOT = Path(__file__).resolve().parent
API = "http://127.0.0.1:8000"
PORT = 3000
HOP = {"host", "content-length", "connection", "transfer-encoding"}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT), **kwargs)

    def do_GET(self):
        if self.path.startswith("/jobs"):
            self.proxy()
            return
        super().do_GET()

    def do_POST(self):
        if self.path.startswith("/jobs"):
            self.proxy()
            return
        self.send_error(404)

    def proxy(self):
        """Forward the request to Component A and copy the response back."""
        length = int(self.headers.get("Content-Length") or 0)
        body = self.rfile.read(length) if length else None
        headers = {
            key: value
            for key, value in self.headers.items()
            if key.lower() not in HOP
        }
        # Avoid gzip/br from the browser so the relayed body matches Content-Length.
        headers["Accept-Encoding"] = "identity"
        request = urllib.request.Request(
            API + self.path, data=body, method=self.command, headers=headers
        )
        # Intake POST waits on Claude; polling GETs should fail fast.
        timeout = 600 if self.command == "POST" else 30
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                self._relay(response.status, response.headers, response.read())
        except urllib.error.HTTPError as exc:
            self._relay(exc.code, exc.headers, exc.read())
        except urllib.error.URLError:
            payload = b'{"detail":"intake API is not reachable on localhost:8000"}'
            self._relay(502, {"Content-Type": "application/json"}, payload)

    def _relay(self, status, headers, body):
        self.send_response(status)
        content_type = "application/json"
        if headers is not None:
            content_type = headers.get("Content-Type") or content_type
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        print(f"[frontend] {self.address_string()} {fmt % args}")


def main():
    server = ThreadingHTTPServer(("127.0.0.1", PORT), Handler)
    print(f"Open http://127.0.0.1:{PORT}  (API proxied to {API})", flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
