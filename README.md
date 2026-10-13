# mars-memory-demo

A controlled memory-pressure service for DigitalOcean scaling demos. Each
request allocates and touches a configurable working set, holds it briefly,
then releases it. The app rejects excess work before OOM and emits structured
logs that correlate with the droplet's directly exported memory metrics.

The intended story is:

1. One LB backend reaches its memory budget, queues requests, and emits
   `memory_budget_exceeded` logs.
2. Add droplets to the LB pool.
3. Per-droplet memory pressure, queue duration, and rejection count fall.

## Prerequisites (once per droplet)

```bash
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2 make git
sudo usermod -aG docker $USER   # re-login (or newgrp docker) after this
```

## Stand up

```bash
git clone <repo-url>
cd mars-slow-demo
cp .env.example .env
# Set DROPLET_ID, TENANT_ID, OTLP_ENDPOINT, and OTLP_AUTHORIZATION.
make run
```

`make run` starts nginx, the memory workload, and a local OpenTelemetry
Collector. It enables/starts Docker on boot when systemd is available.
Containers use `restart: unless-stopped`.

- A crashed container is restarted by Docker
- After a droplet reboot, Docker brings the stack back (no need to re-run Compose) once you have run `make run` at least once

| Target | Action |
|--------|--------|
| `make run` | Build and start (`docker compose up -d --build`) |
| `make stop` | Stop and remove containers (including loadgen) |
| `make logs` | Follow logs |
| `make telemetry-logs` | Follow structured app logs and Collector export logs |
| `make status` | `docker compose ps` |
| `make health` | Probe `/health` and a small `/work` allocation |
| `make load-test` | Start loadgen **container** against `LOAD_HOST` (defaults below) |
| `make load-test-stop` | Stop only the loadgen container |

Load-test defaults: `LOAD_HOST=209.38.1.176`, `LOAD_CONCURRENCY=5`,
`LOAD_DURATION=72h`, `LOAD_PATH=/work`.

## Endpoints

| Path | Behavior |
|------|----------|
| `GET /health` | Instant `200`; bypasses memory admission and is the LB health path |
| `GET /work` or `GET /` | Allocate/touch memory, hold it, then return allocation and queue timings |
| `GET /work?working_set_mb=32&hold_ms=1000` | Per-request override for a small manual test |

Default pressure controls:

- `WORKING_SET_MB=96` per admitted request
- `MEMORY_BUDGET_MB=192` (two requests can allocate)
- `MAX_INFLIGHT=2`, `MAX_WAITING=2`
- `HOLD_MS=5000`, `QUEUE_TIMEOUT_MS=10000`
- container limit `APP_CONTAINER_MEMORY_LIMIT=384m`

The fifth concurrent request is rejected with a controlled `503` while two
run and two wait. Tune these values in `.env`; keep the application budget
comfortably below the container limit.

## OTLP logs

The app writes JSONL to a shared volume. The local Collector parses it,
persists its read offset, attaches:

- `service.name=mars-memory-demo`
- `resource.urn=do:droplet:<DROPLET_ID>`
- `tenant.urn=do:team:<TENANT_ID>`
- standard `host.id`, `cloud.resource_id`, and environment attributes

and exports with OTLP/HTTP. Configure `OTLP_ENDPOINT` and
`OTLP_AUTHORIZATION` in `.env`; no token belongs in git. The Collector also
uses a sampled debug exporter, so `make telemetry-logs` confirms parsing even
when the remote endpoint is unavailable.

Events are `request_queued`, `request_started`, `request_completed`,
`memory_budget_exceeded`, and `allocation_failed`. They include request ID,
status, queue/duration, working-set and in-use MiB, RSS, and active/waiting
counts.

## Load balancer checklist

1. Point the LB backend at the droplet on **port 80**
2. Health check path: **`/health`** (not `/`)
3. Confirm via LB hostname/IP:
   - `curl -sS http://<lb>/health` — fast
   - `curl -sS -w '%{time_total}\n' http://<lb>/work` — memory work
4. Generate load (Docker): `make load-test`  
   Overrides: `make load-test LOAD_HOST=<lb> LOAD_CONCURRENCY=10 LOAD_DURATION=24h`
   Logs: `docker compose --profile loadtest logs -f loadgen`
5. Compare OTLP rejection/queue logs with `do.droplets.memory_utilization`
   and `do.droplets.memory_available`.
6. Add another droplet to the LB pool and compare the same fixed load.

For clean host metrics, run `make load-test` on a separate machine. Running
the generator on the measured droplet adds its own (small) resource usage.

## Repeatable one-vs-multiple-droplet demo

1. Deploy this stack to one LB backend. Set that machine's unique
   `DROPLET_ID`/`DROPLET_NAME` and the shared `TENANT_ID` in `.env`.
2. From a separate machine, run the fixed profile:

   ```bash
   make load-test LOAD_HOST=<lb-ip> LOAD_CONCURRENCY=5 LOAD_DURATION=30m
   ```

3. Let it run for at least five metric collection intervals. Record:
   - peak `do.droplets.memory_utilization`
   - minimum `do.droplets.memory_available`
   - `memory_budget_exceeded` count
   - request queue and duration fields from completion logs
4. Deploy an identical second droplet with its own identity, add it to the LB,
   and wait until `/health` is green.
5. Run the same load profile without changing concurrency or memory settings.

With even load distribution, each backend should admit fewer overlapping
working sets. The dashboard should show lower per-droplet memory pressure and
fewer rejections/shorter queues. Keep the input and time window fixed; changing
the workload while scaling makes the comparison ambiguous.

## Restart / reboot notes

| Event | Result |
|-------|--------|
| App or nginx crashes | Docker restarts that container |
| `make stop` | Stack stays down until `make run` |
| Droplet reboots | Docker starts and restarts the containers |
| Never ran `make run` | Nothing starts |

If containers do not return after reboot, enable Docker: `sudo systemctl enable --now docker`, then `make run` once.
