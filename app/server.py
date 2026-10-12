#!/usr/bin/env python3
"""mars-slow-demo: HTTP server with configurable / random response delay for LB demos."""

from __future__ import annotations

import json
import os
import random
import socket
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return int(raw)


DELAY_MIN_MS = _env_int("DELAY_MIN_MS", 3000)
DELAY_MAX_MS = _env_int("DELAY_MAX_MS", 8000)
# Cap concurrent slow handlers so extra requests queue and total latency
# can exceed DELAY_MAX_MS. /health bypasses this. Default 1 = strict queue.
MAX_INFLIGHT = max(1, _env_int("MAX_INFLIGHT", 1))
LISTEN_HOST = os.environ.get("LISTEN_HOST", "0.0.0.0")
LISTEN_PORT = _env_int("LISTEN_PORT", 8080)
HOSTNAME = os.environ.get("HOSTNAME") or socket.gethostname()

SLOW_SLOTS = threading.Semaphore(MAX_INFLIGHT)
SLOW_WAITING = 0
SLOW_ACTIVE = 0
SLOW_LOCK = threading.Lock()


def pick_delay_ms(query: dict[str, list[str]]) -> int:
    if "delay_ms" in query and query["delay_ms"]:
        return max(0, int(query["delay_ms"][0]))
    lo = min(DELAY_MIN_MS, DELAY_MAX_MS)
    hi = max(DELAY_MIN_MS, DELAY_MAX_MS)
    if lo == hi:
        return lo
    return random.randint(lo, hi)


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:
        print(f"{self.address_string()} - {fmt % args}", file=sys.stderr, flush=True)

    def _send_json(self, status: int, body: dict) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802
        global SLOW_WAITING, SLOW_ACTIVE

        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)

        if path == "/health":
            self._send_json(200, {"ok": True})
            return

        if path in ("/", "/slow"):
            delay_ms = pick_delay_ms(query)
            t0 = time.monotonic()

            with SLOW_LOCK:
                SLOW_WAITING += 1
                waiting = SLOW_WAITING
                active = SLOW_ACTIVE

            SLOW_SLOTS.acquire()
            queue_wait_ms = int((time.monotonic() - t0) * 1000)

            with SLOW_LOCK:
                SLOW_WAITING -= 1
                SLOW_ACTIVE += 1

            try:
                time.sleep(delay_ms / 1000.0)
                total_ms = int((time.monotonic() - t0) * 1000)
                self._send_json(
                    200,
                    {
                        "ok": True,
                        "delay_ms": delay_ms,
                        "queue_wait_ms": queue_wait_ms,
                        "total_ms": total_ms,
                        "max_inflight": MAX_INFLIGHT,
                        "observed_waiting": waiting,
                        "observed_active": active,
                        "host": HOSTNAME,
                        "path": path,
                    },
                )
            finally:
                with SLOW_LOCK:
                    SLOW_ACTIVE -= 1
                SLOW_SLOTS.release()
            return

        self._send_json(404, {"ok": False, "error": "not_found", "path": path})


def main() -> None:
    if DELAY_MIN_MS < 0 or DELAY_MAX_MS < 0:
        raise SystemExit("DELAY_MIN_MS and DELAY_MAX_MS must be >= 0")
    server = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler)
    print(
        f"mars-slow-demo listening on {LISTEN_HOST}:{LISTEN_PORT} "
        f"(delay {DELAY_MIN_MS}-{DELAY_MAX_MS} ms, max_inflight={MAX_INFLIGHT}, "
        f"host={HOSTNAME})",
        flush=True,
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
