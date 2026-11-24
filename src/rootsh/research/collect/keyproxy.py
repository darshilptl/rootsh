"""Host-side proxy that adds the NVIDIA NIM API key outside the sandbox.

The sandboxed agent must never hold a long-lived API key: port 443 is open
inside the sandbox, so an injected agent could send it anywhere. Instead
OpenCode in the sandbox talks plain HTTP to this proxy on 127.0.0.1 with a
per-collection *access token* as its "API key". The proxy checks that token,
replaces it with the real key, and forwards only allowlisted API paths to the
NVIDIA endpoint over HTTPS. The real key exists only in this host process
(read from $NVIDIA_API_KEY, which the author sets) and is never logged.
"""

from __future__ import annotations

import hmac
import http.client
import secrets
import ssl
import sys
import threading
from collections.abc import Callable
from dataclasses import dataclass, field
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

UPSTREAM_HOST = "integrate.api.nvidia.com"  # from the OpenCode v2.3.0 binary [RUN]
ALLOWED_PATHS = frozenset({"/v1/chat/completions", "/v1/models"})
MAX_BODY = 16 * 1024**2
UPSTREAM_TIMEOUT_S = 300
# Hop-by-hop and credential headers are never forwarded as received.
_DROP_REQUEST = frozenset(
    {"host", "authorization", "connection", "content-length", "proxy-authorization", "cookie"}
)
_DROP_RESPONSE = frozenset({"connection", "transfer-encoding", "content-length", "set-cookie"})

type Connect = Callable[[], http.client.HTTPConnection]


def https_upstream() -> http.client.HTTPConnection:
    return http.client.HTTPSConnection(
        UPSTREAM_HOST, timeout=UPSTREAM_TIMEOUT_S, context=ssl.create_default_context()
    )


@dataclass
class ProxyStats:
    """Counts only; never request content or credentials."""

    forwarded: int = 0
    refused: int = 0
    dropped: int = 0  # client hung up mid-request (e.g. an idle keep-alive closed)
    lock: threading.Lock = field(default_factory=threading.Lock)

    def drop(self) -> None:
        with self.lock:
            self.dropped += 1

    def add(self, *, forwarded: bool) -> None:
        with self.lock:
            if forwarded:
                self.forwarded += 1
            else:
                self.refused += 1


def make_handler(
    api_key: str, access_token: str, connect: Connect, stats: ProxyStats
) -> type[BaseHTTPRequestHandler]:
    expected = f"Bearer {access_token}".encode()

    class Handler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def log_message(self, format: str, *args: object) -> None:
            """Silent: request lines could carry content; nothing is logged."""

        def _refuse(self, code: int, reason: str) -> None:
            stats.add(forwarded=False)
            body = reason.encode()
            self.send_response(code)
            self.send_header("Content-Type", "text/plain")
            self.send_header("Content-Length", str(len(body)))
            self.send_header("Connection", "close")
            self.end_headers()
            self.wfile.write(body)
            self.close_connection = True

        def _forward(self, method: str) -> None:
            path = self.path.split("?", 1)[0]
            if path not in ALLOWED_PATHS:
                self._refuse(403, "rootsh keyproxy: path not allowed")
                return
            given = (self.headers.get("Authorization") or "").encode()
            if not hmac.compare_digest(given, expected):
                self._refuse(401, "rootsh keyproxy: bad access token")
                return
            length = int(self.headers.get("Content-Length") or 0)
            if length < 0 or length > MAX_BODY:
                self._refuse(413, "rootsh keyproxy: body too large")
                return
            body = self.rfile.read(length) if length else None
            headers = {k: v for k, v in self.headers.items() if k.lower() not in _DROP_REQUEST}
            headers["Authorization"] = f"Bearer {api_key}"
            upstream = connect()
            try:
                upstream.request(method, self.path, body=body, headers=headers)
                response = upstream.getresponse()
                stats.add(forwarded=True)
                self.send_response(response.status)
                for key, value in response.getheaders():
                    if key.lower() not in _DROP_RESPONSE:
                        self.send_header(key, value)
                self.send_header("Transfer-Encoding", "chunked")
                self.end_headers()
                # Stream chunk by chunk: completions arrive as server-sent events.
                while chunk := response.read1(65536):
                    self.wfile.write(b"%x\r\n%s\r\n" % (len(chunk), chunk))
                    self.wfile.flush()
                self.wfile.write(b"0\r\n\r\n")
            except OSError:
                self.close_connection = True
            finally:
                upstream.close()

        def do_POST(self) -> None:
            self._forward("POST")

        def do_GET(self) -> None:
            self._forward("GET")

    return Handler


class _Server(ThreadingHTTPServer):
    """A client that hangs up is normal (OpenCode closes idle keep-alive
    connections); count it instead of printing socketserver's traceback into
    the collection log. Anything else keeps the default report."""

    stats: ProxyStats

    def handle_error(self, request: object, client_address: object) -> None:
        if isinstance(sys.exc_info()[1], ConnectionError):
            self.stats.drop()
            return
        super().handle_error(request, client_address)  # type: ignore[arg-type]


@dataclass
class KeyProxy:
    """A running proxy; `stop()` it when collection ends."""

    server: ThreadingHTTPServer
    thread: threading.Thread
    access_token: str
    stats: ProxyStats

    @property
    def port(self) -> int:
        return int(self.server.server_address[1])

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}/v1"

    def stop(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=10)


def start(api_key: str, connect: Connect = https_upstream) -> KeyProxy:
    if not api_key.strip():
        raise ValueError("an NVIDIA API key is required")
    token = secrets.token_urlsafe(32)
    stats = ProxyStats()
    server = _Server(("127.0.0.1", 0), make_handler(api_key.strip(), token, connect, stats))
    server.stats = stats
    server.daemon_threads = True
    thread = threading.Thread(target=server.serve_forever, name="rootsh-keyproxy", daemon=True)
    thread.start()
    return KeyProxy(server, thread, token, stats)
