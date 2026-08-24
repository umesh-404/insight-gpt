"""Tiny stdlib health endpoint so compose can healthcheck the worker.

A background thread serves ``GET /health`` -> ``200 {"status": "ok"}`` on a
configurable port (default 8090). Deliberately stdlib-only (no FastAPI/uvicorn) —
the worker's job is scheduling, and a healthcheck should add no heavy dependency
or failure surface. Any other path returns 404.
"""

from __future__ import annotations

import json
import logging
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

logger = logging.getLogger(__name__)


class _HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:  # noqa: N802 — http.server API
        if self.path.rstrip("/") in ("", "/health"):
            body = json.dumps({"status": "ok"}).encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
        else:
            self.send_response(404)
            self.end_headers()

    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        # Route access logs through logging at debug, not stderr.
        logger.debug("health %s", format % args)


class HealthServer:
    """Serves ``/health`` on a daemon thread; ``start()`` / ``stop()``."""

    def __init__(self, port: int = 8090, host: str = "0.0.0.0") -> None:  # noqa: S104
        self._host = host
        self._port = port
        self._httpd: ThreadingHTTPServer | None = None
        self._thread: threading.Thread | None = None

    @property
    def port(self) -> int:
        # Reflects the bound port (useful when constructed with port 0 in tests).
        return self._httpd.server_address[1] if self._httpd else self._port

    def start(self) -> HealthServer:
        self._httpd = ThreadingHTTPServer((self._host, self._port), _HealthHandler)
        self._thread = threading.Thread(
            target=self._httpd.serve_forever, name="worker-health", daemon=True
        )
        self._thread.start()
        logger.info("health server listening on %s:%d/health", self._host, self.port)
        return self

    def stop(self) -> None:
        if self._httpd is not None:
            self._httpd.shutdown()
            self._httpd.server_close()
            self._httpd = None
        if self._thread is not None:
            self._thread.join(timeout=2)
            self._thread = None
