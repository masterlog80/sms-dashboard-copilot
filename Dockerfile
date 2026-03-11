# ── Build stage ────────────────────────────────────────────────────────────
FROM python:3.12-slim AS builder

WORKDIR /build

# Install build tools needed for some Python packages
RUN apt-get update && apt-get install -y --no-install-recommends gcc && \
    rm -rf /var/lib/apt/lists/*

COPY app/requirements.txt .
RUN pip install --no-cache-dir --prefix=/install -r requirements.txt

# ── Runtime stage ───────────────────────────────────────────────────────────
FROM python:3.12-slim

LABEL org.opencontainers.image.title="sms-dashboard" \
      org.opencontainers.image.description="SMS Dashboard – read GSM modem SMS messages via web UI" \
      org.opencontainers.image.source="https://github.com/masterlog80/sms-dashboard-copilot"

# Runtime dependencies for pyserial (no extra packages needed on slim)
RUN apt-get update && apt-get install -y --no-install-recommends \
    udev \
    && rm -rf /var/lib/apt/lists/*

# Copy installed Python packages from builder
COPY --from=builder /install /usr/local

# App source
WORKDIR /app
COPY app/ /app/
COPY static/ /app/static/

# Data directory for SQLite database
RUN mkdir -p /data && chmod 777 /data

# Non-root user for security
RUN useradd --no-create-home --shell /bin/false appuser
# Allow appuser to access serial ports (dialout group GID=20 on Debian)
RUN usermod -aG dialout appuser || true
USER appuser

ENV SERIAL_PORT=/dev/ttyUSB0 \
    SERIAL_BAUD=9600 \
    DB_PATH=/data/sms.db \
    STATIC_DIR=/app/static \
    LOG_LEVEL=INFO \
    PYTHONUNBUFFERED=1

EXPOSE 5000

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:5000/api/health')"

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--threads", "4", "--timeout", "120", "wsgi:app"]
