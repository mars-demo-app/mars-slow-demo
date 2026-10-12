COMPOSE ?= docker compose
CURL ?= curl
LOAD_HOST ?= 209.38.1.176
LOAD_CONCURRENCY ?= 5
LOAD_DURATION ?= 72h

.PHONY: run stop logs status health load-test load-test-stop help

help:
	@echo "Targets:"
	@echo "  make run             Build and start the stack (detached)"
	@echo "  make stop            Stop and remove containers (including loadgen)"
	@echo "  make logs            Follow container logs"
	@echo "  make status          Show compose service status"
	@echo "  make health          Probe /health (fast) and / (slow)"
	@echo "  make load-test       Start loadgen container against LOAD_HOST"
	@echo "  make load-test-stop  Stop only the loadgen container"
	@echo ""
	@echo "load-test defaults: LOAD_HOST=$(LOAD_HOST) LOAD_CONCURRENCY=$(LOAD_CONCURRENCY) LOAD_DURATION=$(LOAD_DURATION)"

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

status:
	$(COMPOSE) --profile loadtest ps

health:
	@echo "==> GET /health (should be fast)"
	@$(CURL) -sS -w "\nhttp_code=%{http_code} time_total=%{time_total}s\n" http://127.0.0.1/health
	@echo ""
	@echo "==> GET / (should be slow; default 3-8s)"
	@$(CURL) -sS -w "\nhttp_code=%{http_code} time_total=%{time_total}s\n" http://127.0.0.1/

load-test:
	LOAD_HOST=$(LOAD_HOST) LOAD_CONCURRENCY=$(LOAD_CONCURRENCY) LOAD_DURATION=$(LOAD_DURATION) \
		$(COMPOSE) --profile loadtest up -d --build --force-recreate loadgen
	@echo "loadgen started -> http://$(LOAD_HOST)/  (-c $(LOAD_CONCURRENCY) -d $(LOAD_DURATION))"
	@echo "Logs: $(COMPOSE) --profile loadtest logs -f loadgen"

load-test-stop:
	$(COMPOSE) --profile loadtest rm -sf loadgen
