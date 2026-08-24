"""Health server answers GET /health with 200 and a JSON status."""

from __future__ import annotations

import json
import urllib.request

from worker.health import HealthServer


def test_health_endpoint_returns_ok():
    server = HealthServer(port=0, host="127.0.0.1").start()
    try:
        url = f"http://127.0.0.1:{server.port}/health"
        with urllib.request.urlopen(url, timeout=2) as resp:  # noqa: S310 — localhost
            assert resp.status == 200
            payload = json.loads(resp.read().decode("utf-8"))
        assert payload == {"status": "ok"}
    finally:
        server.stop()


def test_unknown_path_returns_404():
    server = HealthServer(port=0, host="127.0.0.1").start()
    try:
        url = f"http://127.0.0.1:{server.port}/nope"
        try:
            urllib.request.urlopen(url, timeout=2)  # noqa: S310 — localhost
            raised = None
        except urllib.error.HTTPError as exc:
            raised = exc.code
        assert raised == 404
    finally:
        server.stop()
