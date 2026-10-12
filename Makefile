COMPOSE ?= docker compose
CURL ?= curl

.PHONY: run stop logs status health help

help:
	@echo "Targets:"
	@echo "  make run     Build and start the stack (detached)"
	@echo "  make stop    Stop and remove containers"
	@echo "  make logs    Follow container logs"
	@echo "  make status  Show compose service status"
	@echo "  make health  Probe /health (fast) and / (slow)"

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
	$(COMPOSE) down

logs:
	$(COMPOSE) logs -f

status:
	$(COMPOSE) ps

health:
	@echo "==> GET /health (should be fast)"
	@$(CURL) -sS -w "\nhttp_code=%{http_code} time_total=%{time_total}s\n" http://127.0.0.1/health
	@echo ""
	@echo "==> GET / (should be slow; default 3-8s)"
	@$(CURL) -sS -w "\nhttp_code=%{http_code} time_total=%{time_total}s\n" http://127.0.0.1/
