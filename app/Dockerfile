FROM python:3.12-slim

WORKDIR /app
COPY server.py .

ENV LISTEN_HOST=0.0.0.0 \
    LISTEN_PORT=8080 \
    DELAY_MIN_MS=3000 \
    DELAY_MAX_MS=8000 \
    PYTHONUNBUFFERED=1

EXPOSE 8080
CMD ["python", "server.py"]
