# mars-slow-demo

Nginx + a tiny slow-response app for DigitalOcean Load Balancer demos. Under concurrent load, LB response-time metrics climb so you can tell the “add more droplets” story.

## Prerequisites (once per droplet)

```bash
sudo apt-get update && sudo apt-get install -y docker.io docker-compose-v2 make git
sudo usermod -aG docker $USER   # re-login (or newgrp docker) after this
```

## Stand up

```bash
git clone <repo-url>
cd mars-slow-demo
make run
```

`make run` builds and starts the stack detached, and enables/starts the Docker daemon on boot when systemd is available. Containers use `restart: unless-stopped`, so:

- A crashed container is restarted by Docker
- After a droplet reboot, Docker brings the stack back (no need to re-run Compose) once you have run `make run` at least once

| Target | Action |
|--------|--------|
| `make run` | Build and start (`docker compose up -d --build`) |
| `make stop` | Stop and remove containers |
| `make logs` | Follow logs |
| `make status` | `docker compose ps` |
| `make health` | Probe `/health` (fast) and `/` (slow) |
| `make load-test` | Run `loadgen.sh` against `LOAD_HOST` (default `209.38.1.176`) |

## Endpoints

| Path | Behavior |
|------|----------|
| `GET /health` | Instant `200` — use this for the LB health check |
| `GET /` or `GET /slow` | Sleeps a random duration (default 3000–8000 ms), then JSON |
| `GET /?delay_ms=5000` | Fixed delay for live demos |

Tune delay via env when starting:

```bash
DELAY_MIN_MS=2000 DELAY_MAX_MS=10000 make run
```

## Load balancer checklist

1. Point the LB backend at the droplet on **port 80**
2. Health check path: **`/health`** (not `/`)
3. Confirm via LB hostname/IP:
   - `curl -sS http://<lb>/health` — fast
   - `curl -sS -w '%{time_total}\n' http://<lb>/` — slow
4. Generate load: `make load-test` (defaults to `209.38.1.176`), or `make load-test LOAD_HOST=<lb> ARGS='-c 20 -d 30m'`
5. Watch DO Load Balancer **response time** / request metrics

Sleeping holds connections open; droplet CPU may stay low. That is expected for this demo.

## Restart / reboot notes

| Event | Result |
|-------|--------|
| App or nginx crashes | Docker restarts that container |
| `make stop` | Stack stays down until `make run` |
| Droplet reboots | Docker starts and restarts the containers |
| Never ran `make run` | Nothing starts |

If containers do not return after reboot, enable Docker: `sudo systemctl enable --now docker`, then `make run` once.
