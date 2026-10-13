FROM python:3.12-slim

WORKDIR /app
COPY server.py .

ENV LISTEN_HOST=0.0.0.0 \
    LISTEN_PORT=8080 \
    WORKING_SET_MB=96 \
    HOLD_MS=5000 \
    MEMORY_BUDGET_MB=192 \
    MAX_INFLIGHT=2 \
    MAX_WAITING=2 \
    QUEUE_TIMEOUT_MS=10000 \
    LOG_PATH=/var/log/mars-memory-demo/app.jsonl \
    PYTHONUNBUFFERED=1

EXPOSE 8080
CMD ["python", "server.py"]
