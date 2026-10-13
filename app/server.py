#!/usr/bin/env python3
"""A controlled memory-pressure HTTP service for scale-out demonstrations."""

from __future__ import annotations

import json
import os
import resource
import socket
import sys
import threading
import time
import uuid
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from urllib.request import urlopen


def _env_int(name: str, default: int) -> int:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return int(raw)


def _metadata(path: str) -> str:
    """Read droplet identity without requiring deployment-specific env vars."""
    try:
        with urlopen(
            f"http://169.254.169.254/metadata/v1/{path}", timeout=0.5
        ) as response:
            return response.read().decode("utf-8").strip()
    except OSError:
        return ""


WORKING_SET_MB = max(1, _env_int("WORKING_SET_MB", 96))
HOLD_MS = max(0, _env_int("HOLD_MS", 5000))
MEMORY_BUDGET_MB = max(WORKING_SET_MB, _env_int("MEMORY_BUDGET_MB", 192))
MAX_INFLIGHT = max(1, _env_int("MAX_INFLIGHT", 2))
MAX_WAITING = max(0, _env_int("MAX_WAITING", 2))
QUEUE_TIMEOUT_MS = max(0, _env_int("QUEUE_TIMEOUT_MS", 10000))
LISTEN_HOST = os.environ.get("LISTEN_HOST", "0.0.0.0")
LISTEN_PORT = _env_int("LISTEN_PORT", 8080)
DROPLET_ID = _metadata("id")
HOSTNAME = _metadata("hostname") or os.environ.get("HOSTNAME") or socket.gethostname()
DEPLOYMENT_ENVIRONMENT = os.environ.get("DEPLOYMENT_ENVIRONMENT", "demo")
LOG_PATH = os.environ.get("LOG_PATH", "/var/log/mars-memory-demo/app.jsonl")

STATE_LOCK = threading.Condition()
LOG_LOCK = threading.Lock()
ACTIVE_REQUESTS = 0
WAITING_REQUESTS = 0
MEMORY_IN_USE_MB = 0


def current_rss_mb() -> float:
    """Return current RSS on Linux and a useful fallback elsewhere."""
    try:
        with open("/proc/self/statm", encoding="ascii") as statm:
            pages = int(statm.read().split()[1])
        return round(pages * os.sysconf("SC_PAGE_SIZE") / 1024 / 1024, 1)
    except (FileNotFoundError, OSError, ValueError, IndexError):
        # Linux reports KiB; macOS reports bytes.
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        divisor = 1024 if sys.platform.startswith("linux") else 1024 * 1024
        return round(rss / divisor, 1)


def emit_log(event: str, severity: str = "INFO", **attributes: object) -> None:
    record = {
        "timestamp": datetime.now(UTC).isoformat(),
        "severity": severity,
        "event": event,
        "message": event.replace("_", " "),
        "service_name": "mars-memory-demo",
        "deployment_environment": DEPLOYMENT_ENVIRONMENT,
        "host_name": HOSTNAME,
        "droplet_id": DROPLET_ID,
        "resource_urn": f"do:droplet:{DROPLET_ID}" if DROPLET_ID else "",
        **attributes,
    }
    line = json.dumps(record, separators=(",", ":"), sort_keys=True)
    with LOG_LOCK:
        print(line, flush=True)
        if LOG_PATH:
            os.makedirs(os.path.dirname(LOG_PATH), exist_ok=True)
            with open(LOG_PATH, "a", encoding="utf-8") as log_file:
                log_file.write(line + "\n")


def allocate_and_touch(megabytes: int) -> bytearray:
    allocation = bytearray(megabytes * 1024 * 1024)
    # Touch every page so Linux commits physical memory instead of leaving
    # anonymous zero pages lazily allocated.
    for offset in range(0, len(allocation), 4096):
        allocation[offset] = 1
    if allocation:
        allocation[-1] = 1
    return allocation


class Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, fmt: str, *args) -> None:
        # Request lifecycle is emitted as structured JSON in do_GET.
        return

    def _send_json(self, status: int, body: dict) -> None:
        payload = json.dumps(body).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Connection", "close")
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self) -> None:  # noqa: N802
        global ACTIVE_REQUESTS, MEMORY_IN_USE_MB, WAITING_REQUESTS

        parsed = urlparse(self.path)
        path = parsed.path.rstrip("/") or "/"
        query = parse_qs(parsed.query)

        if path == "/health":
            self._send_json(200, {"ok": True})
            return

        if path in ("/", "/work"):
            request_id = str(uuid.uuid4())
            working_set_mb = max(
                1, int(query.get("working_set_mb", [str(WORKING_SET_MB)])[0])
            )
            hold_ms = max(0, int(query.get("hold_ms", [str(HOLD_MS)])[0]))
            started = time.monotonic()
            deadline = started + QUEUE_TIMEOUT_MS / 1000
            rejected_reason = ""

            with STATE_LOCK:
                if working_set_mb > MEMORY_BUDGET_MB:
                    rejected_reason = "memory_budget_exceeded"

                can_admit = not rejected_reason and (
                    ACTIVE_REQUESTS < MAX_INFLIGHT
                    and MEMORY_IN_USE_MB + working_set_mb <= MEMORY_BUDGET_MB
                )
                if (
                    not rejected_reason
                    and not can_admit
                    and WAITING_REQUESTS >= MAX_WAITING
                ):
                    rejected_reason = "memory_budget_exceeded"
                elif not rejected_reason and not can_admit:
                    WAITING_REQUESTS += 1
                    emit_log(
                        "request_queued",
                        request_id=request_id,
                        requested_mb=working_set_mb,
                        active_requests=ACTIVE_REQUESTS,
                        waiting_requests=WAITING_REQUESTS,
                        memory_in_use_mb=MEMORY_IN_USE_MB,
                        memory_budget_mb=MEMORY_BUDGET_MB,
                    )
                    try:
                        while not (
                            ACTIVE_REQUESTS < MAX_INFLIGHT
                            and MEMORY_IN_USE_MB + working_set_mb
                            <= MEMORY_BUDGET_MB
                        ):
                            remaining = deadline - time.monotonic()
                            if remaining <= 0:
                                rejected_reason = "queue_timeout"
                                break
                            STATE_LOCK.wait(timeout=remaining)
                    finally:
                        WAITING_REQUESTS -= 1

                if not rejected_reason:
                    ACTIVE_REQUESTS += 1
                    MEMORY_IN_USE_MB += working_set_mb

                active = ACTIVE_REQUESTS
                waiting = WAITING_REQUESTS
                in_use_mb = MEMORY_IN_USE_MB

            queue_wait_ms = int((time.monotonic() - started) * 1000)
            if rejected_reason:
                emit_log(
                    "memory_budget_exceeded",
                    "WARN",
                    request_id=request_id,
                    reason=rejected_reason,
                    status_code=503,
                    requested_mb=working_set_mb,
                    queue_wait_ms=queue_wait_ms,
                    active_requests=active,
                    waiting_requests=waiting,
                    memory_in_use_mb=in_use_mb,
                    memory_budget_mb=MEMORY_BUDGET_MB,
                    rss_mb=current_rss_mb(),
                )
                self._send_json(
                    503,
                    {
                        "ok": False,
                        "error": rejected_reason,
                        "request_id": request_id,
                        "queue_wait_ms": queue_wait_ms,
                        "memory_budget_mb": MEMORY_BUDGET_MB,
                    },
                )
                return

            emit_log(
                "request_started",
                request_id=request_id,
                requested_mb=working_set_mb,
                hold_ms=hold_ms,
                queue_wait_ms=queue_wait_ms,
                active_requests=active,
                waiting_requests=waiting,
                memory_in_use_mb=in_use_mb,
                memory_budget_mb=MEMORY_BUDGET_MB,
                rss_mb=current_rss_mb(),
            )

            allocation: bytearray | None = None
            try:
                allocation = allocate_and_touch(working_set_mb)
                time.sleep(hold_ms / 1000)
                total_ms = int((time.monotonic() - started) * 1000)
                response = {
                    "ok": True,
                    "request_id": request_id,
                    "working_set_mb": working_set_mb,
                    "hold_ms": hold_ms,
                    "queue_wait_ms": queue_wait_ms,
                    "total_ms": total_ms,
                    "memory_budget_mb": MEMORY_BUDGET_MB,
                    "host": HOSTNAME,
                    "path": path,
                }
                emit_log(
                    "request_completed",
                    request_id=request_id,
                    status_code=200,
                    requested_mb=working_set_mb,
                    hold_ms=hold_ms,
                    queue_wait_ms=queue_wait_ms,
                    duration_ms=total_ms,
                    active_requests=active,
                    waiting_requests=waiting,
                    memory_in_use_mb=in_use_mb,
                    memory_budget_mb=MEMORY_BUDGET_MB,
                    rss_mb=current_rss_mb(),
                )
                self._send_json(200, response)
            except MemoryError:
                total_ms = int((time.monotonic() - started) * 1000)
                emit_log(
                    "allocation_failed",
                    "ERROR",
                    request_id=request_id,
                    status_code=503,
                    requested_mb=working_set_mb,
                    duration_ms=total_ms,
                    rss_mb=current_rss_mb(),
                )
                self._send_json(
                    503,
                    {
                        "ok": False,
                        "error": "allocation_failed",
                        "request_id": request_id,
                    },
                )
            finally:
                allocation = None
                with STATE_LOCK:
                    ACTIVE_REQUESTS -= 1
                    MEMORY_IN_USE_MB -= working_set_mb
                    STATE_LOCK.notify_all()
            return

        self._send_json(404, {"ok": False, "error": "not_found", "path": path})


def main() -> None:
    server = ThreadingHTTPServer((LISTEN_HOST, LISTEN_PORT), Handler)
    emit_log(
        "service_started",
        listen_host=LISTEN_HOST,
        listen_port=LISTEN_PORT,
        working_set_mb=WORKING_SET_MB,
        hold_ms=HOLD_MS,
        memory_budget_mb=MEMORY_BUDGET_MB,
        max_inflight=MAX_INFLIGHT,
        max_waiting=MAX_WAITING,
        queue_timeout_ms=QUEUE_TIMEOUT_MS,
        rss_mb=current_rss_mb(),
    )
    server.serve_forever()


if __name__ == "__main__":
    main()
