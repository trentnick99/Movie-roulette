"""Stand-alone HTTP server that impersonates the TMDB API for browser end-to-end tests.

    python -m tests.fake_tmdb_server 8799

Serves /3/... (JSON from tests.fake_tmdb) and /t/p/<size>/<file>.jpg (plain generated placeholder art).
Never used by the application itself.
"""
from __future__ import annotations

import hashlib
import struct
import sys
import zlib
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import httpx

from tests.fake_tmdb import FakeTMDB

fake = FakeTMDB(valid_key="e2e-test-key-0001")


def placeholder_png(name: str, w: int = 200, h: int = 300) -> bytes:
    """A two-tone vertical gradient derived from the name (no artwork)."""
    d = hashlib.sha1(name.encode()).digest()
    top, bottom = d[0:3], d[3:6]
    rows = []
    for y in range(h):
        t = y / (h - 1)
        px = bytes(int(top[i] * (1 - t) * 0.6 + bottom[i] * t * 0.35) for i in range(3))
        rows.append(b"\x00" + px * w)
    raw = zlib.compress(b"".join(rows), 6)

    def chunk(kind: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0)) + chunk(b"IDAT", raw)
            + chunk(b"IEND", b""))


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):  # noqa: N802
        if self.path.startswith("/t/p/"):
            body = placeholder_png(self.path)
            self.send_response(200)
            self.send_header("Content-Type", "image/png")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        req = httpx.Request("GET", "https://api.themoviedb.org" + self.path, headers=dict(self.headers))
        resp = fake.handler(req)
        body = resp.content
        self.send_response(resp.status_code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, *args):
        pass


if __name__ == "__main__":
    port = int(sys.argv[1]) if len(sys.argv) > 1 else 8799
    ThreadingHTTPServer(("127.0.0.1", port), Handler).serve_forever()
