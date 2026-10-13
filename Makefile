COMPOSE ?= docker compose
CURL ?= curl
LOAD_HOST ?= 209.38.1.176
LOAD_CONCURRENCY ?= 5
LOAD_DURATION ?= 72h
LOAD_PATH ?= /work

.PHONY: run stop logs telemetry-logs status health load-test load-test-stop help

help:
	@echo "Targets:"
	@echo "  make run             Build and start the stack (detached)"
	@echo "  make stop            Stop and remove containers (including loadgen)"
	@echo "  make logs            Follow container logs"
	@echo "  make telemetry-logs  Follow app and OTLP collector logs"
	@echo "  make status          Show compose service status"
	@echo "  make health          Probe /health and a small /work allocation"
	@echo "  make load-test       Start loadgen container against LOAD_HOST"
	@echo "  make load-test-stop  Stop only the loadgen container"
	@echo ""
	@echo "load-test defaults: LOAD_HOST=$(LOAD_HOST) LOAD_CONCURRENCY=$(LOAD_CONCURRENCY) LOAD_DURATION=$(LOAD_DURATION) LOAD_PATH=$(LOAD_PATH)"

# Enable Docker on boot when systemd is available, then bring the stack up.
run:
	@if command -v systemctl >/dev/null 2>&1; then \
		if systemctl is-enabled docker >/dev/null 2>&1; then \
			true; \
		else \
			echo "Enabling docker on boot (may prompt for sudo)..."; \
			sudo systemctl enable --now docker; \
		fi; \
		if ! systemctl is-active --quiet docker; then \
			echo "Starting docker (may prompt for sudo)..."; \
			sudo systemctl start docker; \
		fi; \
	fi
	$(COMPOSE) up -d --build
	@echo "Stack is up. Try: make health"

stop:
	$(COMPOSE) --profile loadtest down

logs:
	$(COMPOSE) --profile loadtest logs -f

telemetry-logs:
	$(COMPOSE) logs -f mars-slow-demo otel-collector

status:
	$(COMPOSE) --profile loadtest ps

health:
	@if $(COMPOSE) --profile loadtest ps --status running --services 2>/dev/null | grep -qx loadgen; then \
		echo "NOTE: loadgen is running; /work may queue or return controlled 503s."; \
		echo "      Stop it with: make load-test-stop"; \
		echo ""; \
	fi
	@echo "==> GET /health (bypasses the workload)"
	@$(CURL) -sS -w "\nhttp_code=%{http_code} time_total=%{time_total}s\n" http://127.0.0.1/health
	@echo ""
	@echo "==> GET /work (8 MiB for 250ms smoke allocation)"
	@$(CURL) -sS -w "\nhttp_code=%{http_code} time_total=%{time_total}s\n" \
		"http://127.0.0.1/work?working_set_mb=8&hold_ms=250"

load-test:
	LOAD_HOST=$(LOAD_HOST) LOAD_CONCURRENCY=$(LOAD_CONCURRENCY) LOAD_DURATION=$(LOAD_DURATION) LOAD_PATH=$(LOAD_PATH) \
		$(COMPOSE) --profile loadtest up -d --build --force-recreate loadgen
	@echo "loadgen started -> http://$(LOAD_HOST)$(LOAD_PATH)  (-c $(LOAD_CONCURRENCY) -d $(LOAD_DURATION))"
	@echo "Logs: $(COMPOSE) --profile loadtest logs -f loadgen"

load-test-stop:
	$(COMPOSE) --profile loadtest rm -sf loadgen
